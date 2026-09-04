"""task_status() must not answer a question the history cannot support.

Found by running the tool, not by reading it. On 2026-09-03 a
`vault_reindex_bg()` submitted from an agent session reported::

    typical_seconds: 3.2      check_again_in_seconds: 30

and then ran for 178.4 seconds. The advice to check back in 30s was repeated at
33s, 92s and 125s elapsed, because the old rule was
``max(int(typical - elapsed) + 5, 30)`` and degenerates to a flat 30s beat the
moment a job outlives the median. jobs.py's own module docstring names that
exact behaviour — "polling every 30s through a 7-minute build" — as the thing
this feature exists to remove.

The cause was not the arithmetic. `vault_reindex_bg` computes
``mode = "full" if force else "incremental"``, returns that mode to the caller
in its own message, and then submitted under the flat name ``"vault_reindex"``,
so full reindexes (642.3s on this machine) and incremental ones (1.7s to 5.7s)
piled into one history and the median described neither.

These tests cover the two halves separately: the bucket key here, the statistic
in test_jobs_pacing.py.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import delegation_core.server as server
from delegation_core import jobs


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_DURATIONS_PATH", tmp_path / "job_durations.json")


@pytest.fixture
def submitted(monkeypatch):
    """Capture the task name without running any real reindex."""
    seen = {}

    def fake_submit(task_name, fn, *args, **kwargs):
        seen["task"] = task_name
        return "deadbeef"

    class _VaultQueNuncaRoda:
        def reindex_vault(self, force: bool = False) -> int:  # pragma: no cover
            raise AssertionError("fake_submit must intercept before any work runs")

    monkeypatch.setattr(server.jobs, "submit", fake_submit)
    monkeypatch.setattr(server, "_vault", _VaultQueNuncaRoda())
    return seen


# ── the bucket key ──────────────────────────────────────────────────────────


def test_incremental_and_full_do_not_share_a_duration_bucket(submitted):
    _run(server.vault_reindex_bg(force=False))
    incremental = submitted["task"]
    _run(server.vault_reindex_bg(force=True))
    full = submitted["task"]

    assert incremental != full, (
        "a full reindex costs two orders of magnitude more than an incremental "
        "one; sharing a history makes the median describe neither"
    )


def test_the_bucket_key_carries_the_mode_the_caller_was_told(submitted):
    """The mode is already in the response. It must be the same word."""
    out = json.loads(_run(server.vault_reindex_bg(force=True)))
    assert out["mode"] == "full"
    assert submitted["task"].endswith(":full")


def test_the_bucket_key_still_names_the_task(submitted):
    _run(server.vault_reindex_bg(force=False))
    assert submitted["task"].startswith("vault_reindex")


# ── what a running job reports ──────────────────────────────────────────────


def _running_job(monkeypatch, task: str, started_seconds_ago: float):
    from datetime import datetime, timedelta
    started = (datetime.now() - timedelta(seconds=started_seconds_ago)).isoformat()
    monkeypatch.setattr(server.jobs, "get", lambda job_id: {
        "job_id": job_id, "task": task, "status": "running",
        "started": started, "finished": None, "result": None, "error": None,
    })


def test_a_wide_history_reports_both_ends_and_says_the_median_is_weak(monkeypatch):
    for s in (3.5, 1.9, 2.9, 1.7, 2.1, 5.4, 5.7, 642.3, 2.9, 69.8):
        jobs._record_duration("vault_reindex", s)
    _running_job(monkeypatch, "vault_reindex", started_seconds_ago=125.0)

    out = json.loads(_run(server.task_status("j1")))

    assert out["typical_seconds"] == 3.2
    assert out["fastest_seconds"] == 1.7
    assert out["slowest_seconds"] == 642.3
    assert "predicts little" in out["estimate_note"]


def test_a_wide_history_does_not_advise_the_old_flat_30s(monkeypatch):
    """The regression this whole file exists for."""
    for s in (3.5, 1.9, 2.9, 1.7, 2.1, 5.4, 5.7, 642.3, 2.9, 69.8):
        jobs._record_duration("vault_reindex", s)
    _running_job(monkeypatch, "vault_reindex", started_seconds_ago=125.0)

    out = json.loads(_run(server.task_status("j1")))

    assert out["check_again_in_seconds"] > 30, (
        "125s into a job whose recorded worst case is 642s, 30 is the answer "
        "that made an agent poll four times to learn nothing"
    )


def test_a_tight_history_stays_quiet_about_the_spread(monkeypatch):
    """A task that really does take about the same time every run must not be
    decorated with a caveat it has not earned."""
    for s in (1.7, 2.1, 2.9, 5.4, 5.7):
        jobs._record_duration("vault_reindex:incremental", s)
    _running_job(monkeypatch, "vault_reindex:incremental", started_seconds_ago=1.0)

    out = json.loads(_run(server.task_status("j1")))

    assert "typical_seconds" in out
    assert "estimate_note" not in out
    assert "slowest_seconds" not in out


def test_no_history_still_means_no_hint_at_all(monkeypatch):
    """Silence, not a fabricated estimate. Unchanged behaviour, pinned."""
    _running_job(monkeypatch, "nunca_rodou", started_seconds_ago=5.0)

    out = json.loads(_run(server.task_status("j1")))

    assert "typical_seconds" not in out
    assert "check_again_in_seconds" not in out
    assert out["elapsed_seconds"] == 5


def test_a_finished_job_carries_no_pacing_advice(monkeypatch):
    for s in (1.0, 2.0, 3.0):
        jobs._record_duration("vault_reindex", s)
    monkeypatch.setattr(server.jobs, "get", lambda job_id: {
        "job_id": job_id, "task": "vault_reindex", "status": "done",
        "started": "2026-09-03T22:06:16", "finished": "2026-09-03T22:09:15",
        "result": 12, "error": None,
    })

    out = json.loads(_run(server.task_status("j1")))

    assert out["status"] == "done"
    assert "check_again_in_seconds" not in out
