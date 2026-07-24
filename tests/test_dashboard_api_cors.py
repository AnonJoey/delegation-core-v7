"""dashboard_api.py's CORS origin allowlist.

The server has no auth and binds only to 127.0.0.1, but that alone doesn't
stop a page loaded from a totally different site in the user's regular
browser from fetch()-ing a guessed local port — Access-Control-Allow-Origin
is what actually stops that page's JS from reading the response (and, via
the preflight it unlocks, from a state-changing POST executing at all). The
original implementation sent `Access-Control-Allow-Origin: *` unconditionally,
granting that to any website that guessed the port. Found during the
post-Task-Tracker code re-scan, not from running anything — worth pinning
down with a real test given it's a security property, not just behavior.
"""

import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from delegation_core import dashboard_api
from delegation_core.tracker import ProcessTracker


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_api, "_tracker", ProcessTracker(tmp_path / "processes.json"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_api._Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()
    srv.server_close()


def _get_with_origin(port, origin):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Origin": origin} if origin else {}
    conn.request("GET", "/api/processes", headers=headers)
    res = conn.getresponse()
    res.read()
    allow_origin = res.getheader("Access-Control-Allow-Origin")
    conn.close()
    return allow_origin


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:1430",
    "http://127.0.0.1:5173",
    "http://localhost:1430",
    "tauri://localhost",
    "http://tauri.localhost",
])
def test_allows_local_and_tauri_origins(server, origin):
    assert _get_with_origin(server, origin) == origin


@pytest.mark.parametrize("origin", [
    "https://evil.com",
    "http://evil.com:1430",
    "http://127.0.0.1.evil.com:1430",  # subdomain trick, not actually 127.0.0.1
    "https://127.0.0.1:1430",  # wrong scheme
    "null",
])
def test_rejects_non_local_origins(server, origin):
    assert _get_with_origin(server, origin) is None


def test_no_origin_header_gets_no_cors_header(server):
    """Direct/non-browser requests (curl, this test suite's own requests to the
    other test files) don't send Origin at all — should just work without the
    header, not error."""
    assert _get_with_origin(server, None) is None


def test_options_preflight_honors_same_allowlist(server):
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    conn.request("OPTIONS", "/api/processes/create", headers={"Origin": "https://evil.com"})
    res = conn.getresponse()
    res.read()
    assert res.getheader("Access-Control-Allow-Origin") is None
    conn.close()

    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    conn.request("OPTIONS", "/api/processes/create", headers={"Origin": "http://127.0.0.1:1430"})
    res = conn.getresponse()
    res.read()
    assert res.getheader("Access-Control-Allow-Origin") == "http://127.0.0.1:1430"
    conn.close()
