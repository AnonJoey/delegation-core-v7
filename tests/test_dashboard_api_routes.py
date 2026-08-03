"""dashboard_api.py's remaining GET routes: /api/status, /api/clients,
/api/vault/tree, /api/vault/search, /api/graphs, and the do_GET 404 fallback.

test_dashboard_api.py only covers _build_vault_graph directly, and
test_dashboard_api_{cors,processes,vault_note}.py cover CORS, the process
endpoints, and /api/vault/note — but nothing previously drove an actual HTTP
request through do_GET's dispatch table for these five routes, so a typo'd
route string or a broken query-param pass-through here would only be caught
manually, by actually clicking around the dashboard. A lightweight FakeVault
stands in for VaultManager (no BGE/ChromaDB), same fake-object convention as
test_graphbridge.py's FakeVaultManager.
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from delegation_core import dashboard_api
from delegation_core.config import Config
from delegation_core.tracker import ProcessTracker


class FakeVault:
    def __init__(self):
        self.list_notes_calls = []
        self.list_notes_in_calls = []
        self.find_calls = []
        self.search_calls = []

    def list_notes(self, folder, limit=20):
        self.list_notes_calls.append((folder, limit))
        return [{"title": f"note-in-{folder}", "path": f"{folder}/x.md"}]

    def count_notes(self, folder):
        return 5

    def list_directories(self):
        # Nested on purpose: the flat-list bug this replaced hid exactly this shape.
        return [
            {"path": "reference", "name": "reference", "depth": 0, "count": 2},
            {"path": "reference/graphs", "name": "graphs", "depth": 1, "count": 0},
            {"path": "reference/graphs/repo", "name": "repo", "depth": 2, "count": 900},
        ]

    def list_notes_in(self, dir_rel, offset=0, limit=200):
        self.list_notes_in_calls.append((dir_rel, offset, limit))
        if dir_rel == "nope":
            return {"error": "Not a directory: nope"}
        return {"dir": dir_rel, "total": 900, "offset": offset, "limit": limit,
                "has_more": offset + 2 < 900,
                "notes": [{"title": "a", "path": f"{dir_rel}/a.md"},
                          {"title": "b", "path": f"{dir_rel}/b.md"}]}

    def find_notes(self, query, limit=30):
        self.find_calls.append((query, limit))
        return [{"title": query, "path": f"reference/{query}.md", "match_rank": 0}]

    def search(self, query, limit=5):
        self.search_calls.append((query, limit))
        return [{"title": "hit", "path": "reference/hit.md", "similarity": 0.9}]

    def get_stats(self):
        return {"indexed_notes": 3}


@pytest.fixture
def server(monkeypatch, tmp_path):
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    cfg = Config(vault_path=str(vault_path), vault_folders=["reference", "decisions"])
    fake_vault = FakeVault()
    monkeypatch.setattr(dashboard_api, "_cfg", cfg)
    monkeypatch.setattr(dashboard_api, "_vault", fake_vault)
    monkeypatch.setattr(dashboard_api, "_tracker", ProcessTracker(tmp_path / "processes.json"))

    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_api._Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port, cfg, fake_vault
    srv.shutdown()
    srv.server_close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    return res.status, body


def test_status_reports_vault_and_engine_state(server, monkeypatch):
    """_status() makes a real `requests.get(cfg.llama_url + "/health")` call —
    stub it out rather than relying on nothing (or something!) actually
    listening on the configured llama_port. This dev machine happens to run a
    real llama.cpp on the default port 8181, so without this stub the test's
    result silently depended on host state instead of dashboard_api's own
    logic — exactly the kind of environment-coupled flakiness real/local
    services must be mocked away from."""
    def fake_get(url, timeout=3):
        class Resp:
            status_code = 200
        return Resp()
    monkeypatch.setattr("requests.get", fake_get)

    port, cfg, _ = server
    status, body = _get(port, "/api/status")
    assert status == 200
    assert body["vault_ok"] is True  # tmp_path vault dir exists
    assert body["binary_ok"] is False  # no llama_binary configured
    assert body["chroma_indexed_notes"] == 3
    assert body["llama_state"] == "online"


def test_status_reports_llama_offline_when_health_check_fails(server, monkeypatch):
    def fake_get(url, timeout=3):
        raise ConnectionError("refused")
    monkeypatch.setattr("requests.get", fake_get)

    port, _, _ = server
    status, body = _get(port, "/api/status")
    assert status == 200
    assert body["llama_state"] == "offline"


def test_clients_endpoint_returns_empty_list_with_no_sessions(server, monkeypatch, tmp_path):
    from delegation_core import client_tracking
    monkeypatch.setattr(client_tracking, "SESSIONS_DIR", tmp_path / "no-such-sessions-dir")
    port, _, _ = server
    status, body = _get(port, "/api/clients")
    assert status == 200
    assert body == {"clients": []}


def test_vault_tree_returns_directory_shape_including_nested_ones(server):
    """The route used to return the newest 1000 notes per top-level folder and
    no hierarchy, so 3661 notes three levels down were unreachable."""
    port, _, _ = server
    status, body = _get(port, "/api/vault/tree")
    assert status == 200
    paths = [d["path"] for d in body["directories"]]
    assert paths == ["reference", "reference/graphs", "reference/graphs/repo"]
    assert body["directories"][2]["depth"] == 2
    assert body["directories"][2]["count"] == 900


def test_vault_notes_pages_one_directory(server):
    port, _, fake_vault = server
    status, body = _get(port, "/api/vault/notes?dir=reference/graphs/repo&offset=0&limit=2")
    assert status == 200
    assert body["total"] == 900
    assert body["has_more"] is True
    assert fake_vault.list_notes_in_calls == [("reference/graphs/repo", 0, 2)]


def test_vault_notes_requires_a_dir(server):
    port, _, _ = server
    status, body = _get(port, "/api/vault/notes")
    assert status == 400
    assert "error" in body


def test_vault_notes_propagates_a_bad_directory_as_400(server):
    port, _, _ = server
    status, body = _get(port, "/api/vault/notes?dir=nope")
    assert status == 400
    assert "error" in body


def test_vault_notes_rejects_non_integer_paging(server):
    port, _, _ = server
    status, _ = _get(port, "/api/vault/notes?dir=reference&offset=abc")
    assert status == 400


def test_vault_find_does_a_literal_lookup(server):
    """Distinct from /api/vault/search: no embeddings, no similarity cutoff.
    The semantic endpoint did not return the exact title of a note written
    minutes earlier in its top 3."""
    port, _, fake_vault = server
    status, body = _get(port, "/api/vault/find?q=AIAgent&limit=5")
    assert status == 200
    assert body["count"] == 1
    assert body["results"][0]["match_rank"] == 0
    assert fake_vault.find_calls == [("AIAgent", 5)]


def test_vault_find_requires_a_query(server):
    port, _, _ = server
    status, body = _get(port, "/api/vault/find?q=%20")
    assert status == 400
    assert "error" in body


def test_vault_search_passes_query_and_limit_through(server):
    port, _, fake_vault = server
    status, body = _get(port, "/api/vault/search?q=budget&limit=3")
    assert status == 200
    assert body["query"] == "budget"
    assert body["results"][0]["title"] == "hit"
    assert fake_vault.search_calls == [("budget", 3)]


def test_vault_search_missing_q_returns_400(server):
    port, _, _ = server
    status, body = _get(port, "/api/vault/search")
    assert status == 400
    assert "error" in body


def test_vault_search_defaults_limit_to_five_when_not_given(server):
    port, _, fake_vault = server
    _get(port, "/api/vault/search?q=budget")
    assert fake_vault.search_calls == [("budget", 5)]


def test_graphs_endpoint_reports_empty_registry_when_none_built(server, monkeypatch, tmp_path):
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "graphs-config-dir")
    port, _, _ = server
    status, body = _get(port, "/api/graphs")
    assert status == 200
    assert body == {"count": 0, "graphs": {}}


def test_unknown_get_route_returns_404(server):
    port, _, _ = server
    status, body = _get(port, "/api/nonexistent")
    assert status == 404
    assert "error" in body


def test_graphs_get_missing_name_returns_400(server):
    port, _, _ = server
    status, body = _get(port, "/api/graphs/get")
    assert status == 400
    assert "error" in body


def test_graphs_affected_missing_name_or_query_returns_400(server):
    port, _, _ = server
    status, body = _get(port, "/api/graphs/affected?name=foo")
    assert status == 400
    assert "error" in body


def test_config_get_returns_config_dict(server):
    port, cfg, _ = server
    status, body = _get(port, "/api/config")
    assert status == 200
    assert "config" in body
    assert body["config"]["engine_mode"] == cfg.engine_mode


def test_config_update_saves_new_settings(server):
    port, cfg, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = json.dumps({"engine_mode": "hybrid", "search_threshold": 0.65})
    conn.request("POST", "/api/config/update", body=payload, headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    assert res.status == 200
    assert body["ok"] is True
    assert cfg.engine_mode == "hybrid"
    assert cfg.search_threshold == 0.65


def test_purge_orphans_endpoint(server):
    port, _, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/api/system/purge_orphans", body="{}", headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    assert res.status == 200
    assert body["ok"] is True
    assert "purged_sessions" in body


def test_vault_search_non_numeric_limit_returns_clean_500_not_a_crash(server):
    """`int((query.get("limit") or ["5"])[0])` raises ValueError for a
    non-numeric limit; do_GET's blanket try/except must turn that into a
    JSON 500 rather than an unhandled exception killing the request thread
    or leaking a raw traceback to the client."""
    port, _, _ = server
    status, body = _get(port, "/api/vault/search?q=x&limit=notanumber")
    assert status == 500
    assert "error" in body
