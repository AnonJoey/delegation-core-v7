"""dashboard_api.py's llama.cpp start/stop endpoints (commit 334d1e0):
POST /api/llama/start, POST /api/llama/stop, and the _find_llama_process
selection logic they depend on.

Same real-HTTP-against-ephemeral-ThreadingHTTPServer pattern as
test_dashboard_api_processes.py / test_dashboard_api_routes.py.

SAFETY: nothing here may ever touch the real process table for termination —
this machine runs a real MCP server, and the real llama.cpp is deliberately
stopped. Every stop test monkeypatches _find_llama_process to return a fake;
the _find_llama_process tests monkeypatch psutil.process_iter itself, so no
real psutil.Process object is ever created, terminated, or waited on.
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import psutil
import pytest

from delegation_core import dashboard_api
from delegation_core.config import Config


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_api._Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()
    srv.server_close()


def _post(port, path):
    # Neither llama endpoint reads a request body.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path)
    res = conn.getresponse()
    body = json.loads(res.read())
    conn.close()
    return res.status, body


# ── POST /api/llama/start ────────────────────────────────────────────────────

class FakeEngine:
    """Stands in for DelegationEngine: records the _start_locked call (which the
    handler makes on a background thread) via Events rather than sleeps."""

    def __init__(self):
        self.started = threading.Event()   # set as soon as _start_locked runs
        self.release = threading.Event()   # gate: _start_locked blocks until set

    def _start_locked(self):
        self.started.set()
        self.release.wait(timeout=10)
        return True


def test_llama_start_returns_starting_and_calls_start_locked_in_background(server, monkeypatch):
    """The whole point of the background thread is that the HTTP response comes
    back immediately even though _start_locked can block ~90s polling health.
    The fake blocks on an Event, so if the handler ever calls _start_locked
    inline, this request would hang until the 5s client timeout — proving the
    response arrived while _start_locked was still running."""
    engine = FakeEngine()
    monkeypatch.setattr(dashboard_api, "_get_engine", lambda: engine)

    status, body = _post(server, "/api/llama/start")

    assert status == 200
    assert body == {"status": "starting"}
    # _start_locked runs on its own thread — give it a bounded moment to start.
    assert engine.started.wait(timeout=5), "_start_locked was never called"
    # The response above already came back while release was still unset,
    # which is only possible if _start_locked ran off the request thread.
    assert not engine.release.is_set()
    engine.release.set()  # unblock the worker thread for clean teardown


def test_llama_start_goes_through_the_lazy_get_engine_accessor(server, monkeypatch):
    """_get_engine is the lazy accessor — the route must go through it (so the
    atexit-unregister logic in _get_engine applies) rather than reaching for
    dashboard_api._engine directly."""
    calls = []
    engine = FakeEngine()
    engine.release.set()  # don't block at all in this test

    def fake_get_engine():
        calls.append(1)
        return engine

    monkeypatch.setattr(dashboard_api, "_get_engine", fake_get_engine)
    _post(server, "/api/llama/start")
    assert calls == [1]
    assert engine.started.wait(timeout=5)


# ── POST /api/llama/stop ─────────────────────────────────────────────────────

class FakeProc:
    """Records terminate/wait/kill; configurable to raise from wait/terminate."""

    def __init__(self, wait_raises=None, terminate_raises=None):
        self.calls = []
        self._wait_raises = wait_raises
        self._terminate_raises = terminate_raises

    def terminate(self):
        self.calls.append("terminate")
        if self._terminate_raises:
            raise self._terminate_raises

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        if self._wait_raises:
            raise self._wait_raises

    def kill(self):
        self.calls.append("kill")


def test_llama_stop_reports_not_running_when_no_process_found(server, monkeypatch):
    monkeypatch.setattr(dashboard_api, "_find_llama_process", lambda: None)
    status, body = _post(server, "/api/llama/stop")
    assert status == 200
    assert body == {"status": "not_running"}


def test_llama_stop_terminates_then_waits(server, monkeypatch):
    proc = FakeProc()
    monkeypatch.setattr(dashboard_api, "_find_llama_process", lambda: proc)
    status, body = _post(server, "/api/llama/stop")
    assert status == 200
    assert body == {"status": "stopped"}
    assert proc.calls == ["terminate", ("wait", 8)]
    assert "kill" not in proc.calls


def test_llama_stop_kills_after_wait_timeout_and_still_returns_200(server, monkeypatch):
    """A process that ignores SIGTERM must get SIGKILL, and the endpoint must
    still answer cleanly rather than surfacing TimeoutExpired as a 500."""
    proc = FakeProc(wait_raises=psutil.TimeoutExpired(8))
    monkeypatch.setattr(dashboard_api, "_find_llama_process", lambda: proc)
    status, body = _post(server, "/api/llama/stop")
    assert status == 200
    assert body == {"status": "stopped"}
    assert proc.calls == ["terminate", ("wait", 8), "kill"]


def test_llama_stop_tolerates_process_exiting_between_find_and_terminate(server, monkeypatch):
    """NoSuchProcess from terminate() (the process died on its own in the race
    window) is the desired outcome, not an error — expect a clean 'stopped'."""
    proc = FakeProc(terminate_raises=psutil.NoSuchProcess(4242))
    monkeypatch.setattr(dashboard_api, "_find_llama_process", lambda: proc)
    status, body = _post(server, "/api/llama/stop")
    assert status == 200
    assert body == {"status": "stopped"}
    assert proc.calls == ["terminate"]  # wait/kill never reached


# ── _find_llama_process selection logic ──────────────────────────────────────
# Direct function tests — no HTTP server needed. psutil.process_iter is faked
# out entirely; no real process is ever inspected.

class FakeIterProc:
    def __init__(self, cmdline):
        self.info = {"cmdline": cmdline}


class RaisingIterProc:
    """Accessing .info raises, mimicking a process that vanished mid-iteration."""

    @property
    def info(self):
        raise psutil.NoSuchProcess(999)


@pytest.fixture
def llama_cfg(monkeypatch, tmp_path):
    cfg = Config(llama_binary=str(tmp_path / "bin" / "llama-server"), llama_port=8181)
    monkeypatch.setattr(dashboard_api, "_cfg", cfg)
    return cfg


def _patch_process_iter(monkeypatch, procs):
    def fake_process_iter(attrs=None):
        return iter(procs)
    monkeypatch.setattr(psutil, "process_iter", fake_process_iter)


def test_find_llama_process_matches_binary_name_and_port(llama_cfg, monkeypatch):
    target = FakeIterProc(["/usr/local/bin/llama-server", "--model", "m.gguf", "--port", "8181"])
    _patch_process_iter(monkeypatch, [target])
    assert dashboard_api._find_llama_process() is target


def test_find_llama_process_skips_wrong_binary(llama_cfg, monkeypatch):
    # Port matches but argv[0] is a different program — must not be selected
    # (killing an unrelated process that merely mentions "8181" would be bad).
    wrong = FakeIterProc(["/usr/bin/python3", "some_script.py", "--port", "8181"])
    _patch_process_iter(monkeypatch, [wrong])
    assert dashboard_api._find_llama_process() is None


def test_find_llama_process_skips_right_binary_wrong_port(llama_cfg, monkeypatch):
    # Same binary serving a different port (e.g. someone's second model) must
    # not be stopped by this dashboard's button.
    other = FakeIterProc(["/usr/local/bin/llama-server", "--port", "9999"])
    _patch_process_iter(monkeypatch, [other])
    assert dashboard_api._find_llama_process() is None


def test_find_llama_process_skips_empty_cmdline(llama_cfg, monkeypatch):
    # Kernel threads / permission-restricted processes report no cmdline.
    _patch_process_iter(monkeypatch, [FakeIterProc([]), FakeIterProc(None)])
    assert dashboard_api._find_llama_process() is None


def test_find_llama_process_survives_process_vanishing_mid_iteration(llama_cfg, monkeypatch):
    """A NoSuchProcess raised for one entry must not abort the scan — the real
    match later in the iteration should still be found."""
    target = FakeIterProc(["/usr/local/bin/llama-server", "--port", "8181"])
    _patch_process_iter(monkeypatch, [RaisingIterProc(), target])
    assert dashboard_api._find_llama_process() is target
