"""client_tracking.py: session heartbeat file writing + stale-entry filtering.

Exercises _record()/list_connected_clients() directly with a fake MiddlewareContext
(no real MCP session/transport needed) and monkeypatches SESSIONS_DIR to tmp_path
so tests never touch the real ~/.delegation_core/sessions/.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from delegation_core import client_tracking as ct


@dataclass
class FakeClientInfo:
    name: str
    version: str


@dataclass
class FakeParams:
    clientInfo: FakeClientInfo


@dataclass
class FakeSession:
    client_params: FakeParams


@dataclass
class FakeFastMCPContext:
    session: FakeSession


@dataclass
class FakeMiddlewareContext:
    fastmcp_context: FakeFastMCPContext | None
    method: str = "tools/call"


@pytest.fixture(autouse=True)
def isolated_sessions_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "SESSIONS_DIR", tmp_path)
    return tmp_path


def _fake_context(name="claude-code", version="1.0.0", method="tools/call"):
    return FakeMiddlewareContext(
        fastmcp_context=FakeFastMCPContext(session=FakeSession(client_params=FakeParams(
            clientInfo=FakeClientInfo(name=name, version=version)))),
        method=method,
    )


def test_record_writes_session_file(tmp_path):
    middleware = ct.ClientTrackingMiddleware()
    middleware._record(_fake_context(name="claude-code", version="2.1.0"))

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["client_name"] == "claude-code"
    assert data["client_version"] == "2.1.0"
    assert data["tool_calls"] == 1


def test_record_increments_tool_calls_only_for_tool_call_method():
    middleware = ct.ClientTrackingMiddleware()
    middleware._record(_fake_context(method="tools/call"))
    middleware._record(_fake_context(method="tools/call"))
    middleware._record(_fake_context(method="initialize"))

    data = json.loads(middleware._path.read_text())
    assert data["tool_calls"] == 2


def test_record_preserves_first_seen_across_updates():
    middleware = ct.ClientTrackingMiddleware()
    middleware._record(_fake_context())
    first_seen = json.loads(middleware._path.read_text())["first_seen"]

    middleware._record(_fake_context())
    assert json.loads(middleware._path.read_text())["first_seen"] == first_seen


def test_record_skips_silently_when_no_fastmcp_context():
    middleware = ct.ClientTrackingMiddleware()
    middleware._record(FakeMiddlewareContext(fastmcp_context=None))
    assert not middleware._path.exists()


def test_list_connected_clients_returns_fresh_entries(tmp_path):
    middleware = ct.ClientTrackingMiddleware()
    middleware._record(_fake_context(name="claude-desktop"))

    clients = ct.list_connected_clients()
    assert len(clients) == 1
    assert clients[0]["client_name"] == "claude-desktop"
    assert "seconds_since_active" in clients[0]


def test_list_connected_clients_drops_stale_entries(tmp_path):
    stale = {
        "pid": 12345, "client_name": "old-client", "client_version": "0.1",
        "first_seen": "2020-01-01T00:00:00+00:00",
        "last_seen": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "tool_calls": 3,
    }
    (tmp_path / "12345.json").write_text(json.dumps(stale), encoding="utf-8")
    assert ct.list_connected_clients() == []


def test_list_connected_clients_ignores_corrupt_files(tmp_path):
    (tmp_path / "99999.json").write_text("not valid json", encoding="utf-8")
    assert ct.list_connected_clients() == []


def test_cleanup_own_session_file_removes_this_pid(monkeypatch, tmp_path):
    import os
    fake_path = tmp_path / f"{os.getpid()}.json"
    fake_path.write_text("{}", encoding="utf-8")
    ct.cleanup_own_session_file()
    assert not fake_path.exists()
