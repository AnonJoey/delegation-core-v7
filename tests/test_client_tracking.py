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


def test_list_connected_clients_ignores_files_missing_last_seen(tmp_path):
    """A heartbeat file that's valid JSON but missing "last_seen" (e.g. written
    by a future/older schema version) hits the same `except Exception: continue`
    path as a corrupt file via the KeyError on data["last_seen"] — must not
    crash the whole /api/clients response over one bad entry."""
    (tmp_path / "55555.json").write_text(
        json.dumps({"pid": 55555, "client_name": "no-last-seen"}), encoding="utf-8"
    )
    assert ct.list_connected_clients() == []


def test_list_connected_clients_sorts_multiple_clients_newest_first(tmp_path):
    now = datetime.now(timezone.utc)

    def _write(pid, name, seconds_ago):
        (tmp_path / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "client_name": name, "client_version": "1.0",
            "first_seen": (now - timedelta(seconds=seconds_ago)).isoformat(),
            "last_seen": (now - timedelta(seconds=seconds_ago)).isoformat(),
            "tool_calls": 1,
        }), encoding="utf-8")

    _write(111, "older-client", seconds_ago=60)
    _write(222, "newer-client", seconds_ago=5)

    clients = ct.list_connected_clients()
    assert [c["client_name"] for c in clients] == ["newer-client", "older-client"]


def test_list_connected_clients_keeps_entry_just_under_stale_threshold(tmp_path):
    """A session updated just inside SESSION_STALE_SECONDS must still show as
    connected. (Deliberately not testing the exact `age == SESSION_STALE_SECONDS`
    instant — real wall-clock time elapses between writing the fixture and
    list_connected_clients() computing `now`, which makes an exact-equality
    boundary test flaky by construction; a comfortable margin is used instead.)
    """
    just_under = {
        "pid": 33333, "client_name": "boundary-client", "client_version": "1.0",
        "first_seen": "2020-01-01T00:00:00+00:00",
        "last_seen": (datetime.now(timezone.utc)
                      - timedelta(seconds=ct.SESSION_STALE_SECONDS - 5)).isoformat(),
        "tool_calls": 1,
    }
    (tmp_path / "33333.json").write_text(json.dumps(just_under), encoding="utf-8")
    clients = ct.list_connected_clients()
    assert len(clients) == 1
    assert clients[0]["client_name"] == "boundary-client"


def test_cleanup_own_session_file_removes_this_pid(monkeypatch, tmp_path):
    import os
    fake_path = tmp_path / f"{os.getpid()}.json"
    fake_path.write_text("{}", encoding="utf-8")
    ct.cleanup_own_session_file()
    assert not fake_path.exists()
