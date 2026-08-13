"""/api/vault/graph excludes generated articles unless asked for them.

The route defaulted to *including* them while `_build_vault_graph`'s signature,
its docstring and the frontend all assumed the opposite — and since the
frontend sends no `generated` param, the route's default decided every graph
the dashboard ever drew.

The effect on the real vault: nodes are capped at the 1500 most recent, and
3427 of 3629 notes are graph_build articles, so the cap filled with generated
articles — which carry no wikilinks between them — and crowded out the
hand-written notes holding all of them. 238 nodes / 2962 edges excluded,
against 1500 nodes / 13 edges included. The dashboard drew the 13-edge version.

Also covers the disconnect handling in the same dispatcher, since both are
about what these handlers do when the caller is not a well-behaved test client.
"""

import http.client
import json

import pytest

from delegation_core import dashboard_api
from delegation_core.config import Config
from delegation_core.tracker import ProcessTracker


@pytest.fixture
def server(monkeypatch, tmp_path):
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    cfg = Config(vault_path=str(vault_path), vault_folders=["reference"])

    calls = []

    def fake_build(cfg_arg, include_generated=False, **kw):
        calls.append(include_generated)
        return {"nodes": [], "edges": [], "generated_excluded": 0 if include_generated else 7}

    monkeypatch.setattr(dashboard_api, "_build_vault_graph", fake_build)
    monkeypatch.setattr(dashboard_api, "_cfg", cfg)
    monkeypatch.setattr(dashboard_api, "_tracker", ProcessTracker(tmp_path / "processes.json"))

    srv = dashboard_api.serve_in_process(cfg, object(), None, port=0)
    yield srv.server_address[1], calls
    srv.shutdown()
    srv.server_close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    return res.status, body


def test_defaults_to_excluding_generated(server):
    """What the frontend asks for, since it passes no parameter at all."""
    port, calls = server
    status, body = _get(port, "/api/vault/graph")
    assert status == 200
    assert calls == [False]
    assert body["generated_excluded"] == 7


def test_generated_1_still_opts_in(server):
    port, calls = server
    assert _get(port, "/api/vault/graph?generated=1")[0] == 200
    assert calls == [True]


def test_generated_0_is_explicit_exclusion(server):
    port, calls = server
    assert _get(port, "/api/vault/graph?generated=0")[0] == 200
    assert calls == [False]


def test_client_disconnect_is_not_logged_as_a_failure(server, caplog, monkeypatch):
    """A client hanging up mid-response produced two tracebacks: one from the
    write that failed, one from trying to send a 500 down the same dead socket.
    Harmless in a sidecar nobody watched; this now runs in the daemon."""
    import logging

    def dead_socket(*a, **kw):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(dashboard_api._Handler, "_send_json", dead_socket)

    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    with caplog.at_level(logging.DEBUG, logger="dashboard_api"):
        conn.request("GET", "/api/vault/graph")
        with pytest.raises(Exception):
            conn.getresponse()
        conn.close()

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("client disconnected" in r.message for r in caplog.records)
