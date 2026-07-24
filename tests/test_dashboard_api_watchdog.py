"""Tests for dashboard_api's --parent-pid watchdog (_start_parent_watchdog).

The real mechanism was integration-tested live (SIGKILL the Tauri app ->
sidecar self-exits); these pin the unit-level contract against regressions
without touching the real process table: psutil is faked at module level,
and the "exit" side effects (server.shutdown / os._exit) are observed via
recorded fakes rather than actually firing.
"""

import threading
import time

import psutil
import pytest

from delegation_core import dashboard_api


class FakeServer:
    def __init__(self):
        self.shutdown_called = threading.Event()

    def shutdown(self):
        self.shutdown_called.set()


class FakeParent:
    def __init__(self, alive=True, status=psutil.STATUS_RUNNING):
        self._alive = alive
        self._status = status

    def is_running(self):
        return self._alive

    def status(self):
        return self._status


@pytest.fixture
def fast_watchdog(monkeypatch):
    """Shrink the 2s poll and neuter the os._exit backstop so tests observe
    server.shutdown() being scheduled instead of the process dying."""
    real_sleep = time.sleep

    def quick_sleep(seconds):
        real_sleep(min(seconds, 0.05))

    exited = threading.Event()
    monkeypatch.setattr("time.sleep", quick_sleep)
    monkeypatch.setattr("os._exit", lambda code: exited.set())
    yield exited
    # Join every armed watchdog BEFORE monkeypatch restores the real os._exit:
    # a leaked daemon thread reaching the real os._exit(0) kills pytest itself
    # mid-suite with a deceptively green exit code (observed directly — the
    # suite died at 40% reporting success).
    deadline = time.monotonic() + 5
    for t in threading.enumerate():
        if t.name == "parent-watchdog":
            t.join(timeout=max(0.1, deadline - time.monotonic()))
    assert not any(
        t.name == "parent-watchdog" and t.is_alive() for t in threading.enumerate()
    ), "watchdog thread still alive at teardown — would os._exit the test runner"


def test_watchdog_fires_shutdown_when_parent_dies(monkeypatch, fast_watchdog):
    parent = FakeParent(alive=True)
    monkeypatch.setattr(psutil, "Process", lambda pid: parent)
    server = FakeServer()

    dashboard_api._start_parent_watchdog(12345, server)
    # Parent alive: shutdown must NOT fire while it's running.
    assert not server.shutdown_called.wait(0.3)

    parent._alive = False
    assert server.shutdown_called.wait(3.0), "watchdog never reacted to parent death"


def test_watchdog_treats_zombie_parent_as_dead(monkeypatch, fast_watchdog):
    parent = FakeParent(alive=True, status=psutil.STATUS_ZOMBIE)
    monkeypatch.setattr(psutil, "Process", lambda pid: parent)
    server = FakeServer()

    dashboard_api._start_parent_watchdog(12345, server)
    assert server.shutdown_called.wait(3.0), "zombie parent not treated as dead"


def test_watchdog_shuts_down_immediately_when_parent_already_gone(monkeypatch, fast_watchdog):
    def raise_no_such(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", raise_no_such)
    server = FakeServer()

    dashboard_api._start_parent_watchdog(99999, server)
    assert server.shutdown_called.wait(3.0), "already-dead parent didn't trigger shutdown"


def test_watchdog_survives_psutil_errors_as_death(monkeypatch, fast_watchdog):
    parent = FakeParent(alive=True)

    def flaky_is_running():
        raise psutil.AccessDenied(pid=12345)

    parent.is_running = flaky_is_running
    monkeypatch.setattr(psutil, "Process", lambda pid: parent)
    server = FakeServer()

    dashboard_api._start_parent_watchdog(12345, server)
    assert server.shutdown_called.wait(3.0), "psutil.Error during poll not treated as parent death"


def _run_with_stubbed_server(monkeypatch, tmp_path, parent_pid):
    """Drive the real run() far enough to see whether it arms the watchdog,
    with the server loop and heavyweight init stubbed out."""
    calls = []
    monkeypatch.setattr(dashboard_api, "_start_parent_watchdog",
                        lambda pid, server: calls.append(pid))

    class InstantServer:
        server_address = ("127.0.0.1", 12345)

        def __init__(self, *a, **k):
            pass

        def serve_forever(self):
            pass  # return immediately instead of blocking

        def server_close(self):
            pass

    monkeypatch.setattr(dashboard_api, "ThreadingHTTPServer", InstantServer)

    class FakeCfg:
        vault_path = str(tmp_path)
        processes_path = tmp_path / "processes.json"

        def is_configured(self):
            return True

    class FakeVault:
        def __init__(self, cfg):
            pass

        def _init(self):
            pass

    monkeypatch.setattr("delegation_core.config.Config.load", staticmethod(lambda: FakeCfg()))
    monkeypatch.setattr("delegation_core.vault.VaultManager", FakeVault)
    monkeypatch.setattr("delegation_core.tracker.ProcessTracker", lambda path: object())

    dashboard_api.run(port=0, parent_pid=parent_pid)
    return calls


def test_run_arms_watchdog_only_when_parent_pid_given(monkeypatch, tmp_path):
    assert _run_with_stubbed_server(monkeypatch, tmp_path, parent_pid=4242) == [4242]
    assert _run_with_stubbed_server(monkeypatch, tmp_path, parent_pid=None) == []


def test_cli_rejects_non_integer_parent_pid():
    # Exercises the real __main__ argparse block: a non-int --parent-pid must
    # exit 2 before run() is ever reached (fast — no server, no BGE init).
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "delegation_core.dashboard_api", "--parent-pid", "notanint"],
        capture_output=True, timeout=30,
    )
    assert proc.returncode == 2
    assert b"--parent-pid" in proc.stderr
