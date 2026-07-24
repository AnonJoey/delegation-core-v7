"""dashboard_api.py's process tracker endpoints: real HTTP requests against a real
ThreadingHTTPServer bound to an ephemeral port — these are BaseHTTPRequestHandler
methods that need a genuine request context (self.headers/rfile/wfile), so unlike
_build_vault_graph this isn't cleanly unit-testable by calling a method directly.

Only dashboard_api._tracker is set up (isolated to a tmp_path ProcessTracker) —
_cfg/_vault stay None, which is fine since the process routes never touch them.
"""

import json
import http.client
from http.server import ThreadingHTTPServer

import pytest

from delegation_core import dashboard_api
from delegation_core.tracker import ProcessTracker


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_api, "_tracker", ProcessTracker(tmp_path / "processes.json"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_api._Handler)
    port = srv.server_address[1]
    import threading
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()
    srv.server_close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    return res.status, body


def _post(port, path, payload):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload).encode("utf-8")
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    result = json.loads(res.read())
    conn.close()
    return res.status, result


def _post_raw(port, path, raw_bytes, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=raw_bytes, headers=headers or {})
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res.status, body


def test_create_process(server):
    status, body = _post(server, "/api/processes/create",
                          {"name": "Test", "description": "desc", "steps": ["a", "b"]})
    assert status == 201
    assert body["name"] == "Test"
    assert len(body["steps"]) == 2


def test_create_process_requires_name(server):
    status, body = _post(server, "/api/processes/create", {"description": "no name"})
    assert status == 400
    assert "error" in body


def test_create_process_rejects_non_string_steps(server):
    status, body = _post(server, "/api/processes/create", {"name": "X", "steps": [1, 2]})
    assert status == 400
    assert "error" in body


def test_list_processes_defaults_to_active(server):
    _post(server, "/api/processes/create", {"name": "Active one"})
    status, body = _get(server, "/api/processes")
    assert status == 200
    assert body["count"] == 1
    assert body["processes"][0]["name"] == "Active one"


def test_list_processes_query_filter(server):
    _post(server, "/api/processes/create", {"name": "Vendor migration"})
    _post(server, "/api/processes/create", {"name": "Unrelated"})
    status, body = _get(server, "/api/processes?query=vendor")
    assert status == 200
    assert body["count"] == 1


def test_get_process_by_id(server):
    _, created = _post(server, "/api/processes/create", {"name": "Findable"})
    status, body = _get(server, f"/api/processes/get?id={created['id']}")
    assert status == 200
    assert body["name"] == "Findable"


def test_get_process_missing_returns_404(server):
    status, body = _get(server, "/api/processes/get?id=proc_doesnotexist")
    assert status == 404


def test_get_process_missing_id_param_returns_400(server):
    status, body = _get(server, "/api/processes/get")
    assert status == 400


def test_update_process_step_done(server):
    _, created = _post(server, "/api/processes/create", {"name": "X", "steps": ["one", "two"]})
    status, body = _post(server, "/api/processes/update",
                          {"process_id": created["id"], "step_done": 0})
    assert status == 200
    assert body["steps"][0]["done"] is True


def test_update_process_rejects_bool_step_done(server):
    """JSON true/false must not silently become step_done=1/0 (bool is an int
    subclass in Python — this is exactly the kind of client-side typo the
    explicit isinstance(step_done, bool) guard exists to catch)."""
    _, created = _post(server, "/api/processes/create", {"name": "X", "steps": ["one"]})
    status, body = _post(server, "/api/processes/update",
                          {"process_id": created["id"], "step_done": True})
    assert status == 400
    assert "error" in body


def test_update_process_rejects_invalid_status(server):
    _, created = _post(server, "/api/processes/create", {"name": "X"})
    status, body = _post(server, "/api/processes/update",
                          {"process_id": created["id"], "status": "not-a-real-status"})
    assert status == 400


def test_update_process_rejects_unhashable_status(server):
    """A JSON array/object for "status" must not raise TypeError from the `in`
    check against the VALID_STATUSES set — it should fail validation cleanly."""
    _, created = _post(server, "/api/processes/create", {"name": "X"})
    status, body = _post(server, "/api/processes/update",
                          {"process_id": created["id"], "status": ["active"]})
    assert status == 400
    assert "error" in body


def test_update_process_missing_id_returns_400(server):
    status, body = _post(server, "/api/processes/update", {"note": "no id given"})
    assert status == 400


def test_update_process_missing_returns_404(server):
    status, body = _post(server, "/api/processes/update",
                          {"process_id": "proc_doesnotexist", "note": "x"})
    assert status == 404


def test_post_missing_body_returns_400(server):
    status, body = _post_raw(server, "/api/processes/create", b"", headers={"Content-Length": "0"})
    assert status == 400


def test_post_invalid_json_returns_400(server):
    raw = b"not valid json{{{"
    status, body = _post_raw(server, "/api/processes/create", raw,
                              headers={"Content-Length": str(len(raw))})
    assert status == 400


def test_post_non_object_json_returns_400(server):
    raw = b"[1, 2, 3]"
    status, body = _post_raw(server, "/api/processes/create", raw,
                              headers={"Content-Length": str(len(raw))})
    assert status == 400


def test_post_oversized_body_returns_413(server):
    huge = json.dumps({"name": "x" * 2_000_000}).encode("utf-8")
    status, body = _post_raw(server, "/api/processes/create", huge,
                              headers={"Content-Length": str(len(huge))})
    assert status == 413


def test_unknown_post_route_returns_404(server):
    status, body = _post(server, "/api/nonexistent", {})
    assert status == 404
