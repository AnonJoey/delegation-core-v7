"""relink_folder_bg — work that outlasts the caller's patience needs a job id.

The synchronous tool embeds every note in a folder against every other. On a
31-note folder that ran past the MCP client's 300s idle timeout: the call was
aborted client-side and the connection dropped, while the server kept working
on a job nobody was listening for. Both relink calls made that day "failed"
from the caller's view and both actually succeeded.
"""

import json

import pytest

import delegation_core.server as server
from delegation_core.config import Config


@pytest.fixture
def vault(tmp_path, monkeypatch):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Fixes"])
    (tmp_path / "Fixes").mkdir()

    class _V:
        pass

    v = _V()
    v.cfg = cfg
    monkeypatch.setattr(server, "_vault", v)
    return tmp_path


def test_it_returns_a_job_id_without_waiting(vault, monkeypatch):
    submitted = {}

    def fake_submit(task_name, fn, *args, **kwargs):
        submitted["task"] = task_name
        submitted["args"] = args
        submitted["kwargs"] = kwargs
        return "deadbeef"

    monkeypatch.setattr(server.jobs, "submit", fake_submit)
    out = json.loads(_run(server.relink_folder_bg("Fixes", max_links_per_note=3)))

    assert out["job_id"] == "deadbeef"
    assert out["status"] == "running"
    assert submitted["task"] == "relink_folder"
    assert submitted["kwargs"]["max_links_per_note"] == 3


def test_the_containment_check_is_not_lost_in_the_background_path(vault, monkeypatch):
    """The bg variant must reject an escaping folder exactly as the sync one
    does. A background entry point that skips it is a path-traversal hole, not
    a bug with a wrong number in it."""
    called = []
    monkeypatch.setattr(server.jobs, "submit",
                        lambda *a, **k: called.append(a) or "nope")

    out = json.loads(_run(server.relink_folder_bg("../outside")))
    assert "error" in out
    assert not called, "nothing may be scheduled for a path outside the vault"


def test_both_relink_entry_points_share_one_containment_check():
    import inspect
    src = inspect.getsource(server)
    assert src.count("_vault_subfolder_error(folder)") >= 2, (
        "sync and bg must both go through the shared check"
    )


def _run(coro):
    import asyncio
    return asyncio.run(coro)
