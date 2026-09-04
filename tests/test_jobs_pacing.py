"""jobs.py duration history — the pacing signal task_status() reports.

Added because a calling agent polling a background job had only elapsed_seconds
to work with, which cannot distinguish "20s in, 8 minutes to go" from "nearly
done". In practice that meant polling a 7-minute graph build every 30s, or
giving up on task_status() and watching the output directory from a shell.

Durations persist to ~/.delegation_core/job_durations.json, so tests point that
module constant at tmp_path — otherwise they would read and overwrite the real
history on this machine.
"""

import json

import pytest

from delegation_core import jobs


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_DURATIONS_PATH", tmp_path / "job_durations.json")


def test_typical_seconds_is_none_before_any_run():
    assert jobs.typical_seconds("graph_build") is None


def test_records_duration_of_a_completed_job():
    jobs._record_duration("graph_build", 120.0)
    assert jobs.typical_seconds("graph_build") == 120.0


def test_typical_seconds_uses_the_median_not_the_last_run():
    """The median resists a single slow run — which is right, and not enough.

    This test used to carry the comment "one outlier must not dominate", and
    that premise was wrong about this machine. Measured on 2026-09-03, the
    stored `vault_reindex` history was::

        [3.5, 1.9, 2.9, 1.7, 2.1, 5.4, 5.7, 642.3, 2.9, 69.8]

    The big runs there are not outliers. They are full reindexes, and they cost
    that much every time someone passes force=True. Discounting them as noise is
    how `typical_seconds` came to answer "3.2" about a run that took 178.4s.

    The median stays as it is: it is the right summary of ONE distribution, and
    `_record_duration` is not the place to decide there are two. What changed is
    that callers no longer see it alone — see the `duration_hint` tests below,
    and the mode-keyed bucket in `vault_reindex_bg`.
    """
    for seconds in (100.0, 110.0, 900.0):
        jobs._record_duration("vault_reindex", seconds)
    assert jobs.typical_seconds("vault_reindex") == 110.0


def test_history_is_capped_and_keeps_the_most_recent_runs():
    for seconds in range(1, 16):
        jobs._record_duration("ingest_folder", float(seconds))
    history = json.loads(jobs._DURATIONS_PATH.read_text(encoding="utf-8"))["ingest_folder"]
    assert len(history) == jobs._DURATIONS_KEEP
    assert history[-1] == 15.0


def test_tasks_are_tracked_separately():
    jobs._record_duration("graph_build", 400.0)
    jobs._record_duration("ingest_folder", 5.0)
    assert jobs.typical_seconds("graph_build") == 400.0
    assert jobs.typical_seconds("ingest_folder") == 5.0


def test_corrupt_history_file_yields_no_hint_rather_than_raising():
    jobs._DURATIONS_PATH.write_text("{ not json", encoding="utf-8")
    assert jobs.typical_seconds("graph_build") is None


def test_non_numeric_entries_are_ignored():
    jobs._DURATIONS_PATH.write_text(json.dumps({"graph_build": ["oops", 60.0]}), encoding="utf-8")
    assert jobs.typical_seconds("graph_build") == 60.0


def test_successful_job_records_a_duration():
    job_id = jobs.submit("unit_test_task", lambda: "ok")
    for _ in range(200):
        if jobs.get(job_id)["status"] == "done":
            break
        import time
        time.sleep(0.01)
    assert jobs.get(job_id)["status"] == "done"
    assert jobs.typical_seconds("unit_test_task") is not None


def test_failed_job_records_nothing():
    """A job that raised after 2s says nothing about how long the work takes."""
    def _boom():
        raise RuntimeError("nope")

    job_id = jobs.submit("failing_task", _boom)
    for _ in range(200):
        if jobs.get(job_id)["status"] == "error":
            break
        import time
        time.sleep(0.01)
    assert jobs.get(job_id)["status"] == "error"
    assert jobs.typical_seconds("failing_task") is None


# ── duration_hint: what the history can honestly support ────────────────────
#
# Every number below is a real measurement from this machine on 2026-09-03, not
# a made-up fixture. The bug these cover was found by watching task_status lie
# about a job that was running at the time.


def test_hint_is_none_without_history():
    assert jobs.duration_hint("nunca_rodou") is None


def test_hint_carries_the_middle_and_both_ends():
    for s in (2.0, 4.0, 6.0):
        jobs._record_duration("t", s)
    hint = jobs.duration_hint("t")
    assert hint["typical_seconds"] == 4.0
    assert hint["fastest_seconds"] == 2.0
    assert hint["slowest_seconds"] == 6.0
    assert hint["runs_recorded"] == 3


def test_tight_history_is_not_flagged_as_wide():
    """5.7s against 1.7s is one job on a quiet and a busy disk."""
    for s in (1.7, 2.1, 2.9, 5.4, 5.7):
        jobs._record_duration("vault_reindex", s)
    assert jobs.duration_hint("vault_reindex")["spread_is_wide"] is False


def test_the_real_reindex_history_is_flagged_as_wide():
    """The exact list that was on disk when task_status said 3.2s and the job
    took 178.4s."""
    for s in (3.5, 1.9, 2.9, 1.7, 2.1, 5.4, 5.7, 642.3, 2.9, 69.8):
        jobs._record_duration("vault_reindex", s)
    hint = jobs.duration_hint("vault_reindex")
    assert hint["typical_seconds"] == 3.2      # the number that misled
    assert hint["slowest_seconds"] == 642.3    # the number that would not have
    assert hint["spread_is_wide"] is True


def test_a_single_run_is_never_wide():
    """One run has no spread to measure; wide would be an invented claim."""
    jobs._record_duration("t", 500.0)
    assert jobs.duration_hint("t")["spread_is_wide"] is False


def test_zero_duration_does_not_raise_or_claim_a_spread():
    """A run recorded as 0.0 would make slowest/fastest a ZeroDivisionError."""
    jobs._record_duration("t", 0.0)
    jobs._record_duration("t", 900.0)
    assert jobs.duration_hint("t")["spread_is_wide"] is False


# ── next_check_seconds: the advice that used to degenerate ──────────────────


def _hint(typical, fastest, slowest):
    return {"typical_seconds": typical, "fastest_seconds": fastest,
            "slowest_seconds": slowest, "runs_recorded": 5, "spread_is_wide": True}


def test_before_the_expected_finish_it_aims_just_past_it():
    assert jobs.next_check_seconds(_hint(200.0, 100.0, 600.0), elapsed=100.0) == 105


def test_past_the_median_it_aims_at_the_slowest_run_not_a_flat_beat():
    """The case that motivated this. Old rule: max(int(3.2-125)+5, 30) == 30,
    advising a 30s poll on a job whose recorded worst case was 642s away."""
    assert jobs.next_check_seconds(_hint(3.2, 1.7, 642.3), elapsed=125.0) == 522


def test_past_every_recorded_run_it_backs_off_instead_of_flooring():
    """Nothing in the history describes this run any more. The old rule returned
    30 here forever, which is the behaviour jobs.py's docstring exists to stop."""
    assert jobs.next_check_seconds(_hint(3.2, 1.7, 642.3), elapsed=1000.0) == 500


def test_the_backoff_is_capped():
    assert jobs.next_check_seconds(_hint(3.2, 1.7, 10.0), elapsed=100_000.0) == 600


def test_never_advises_a_poll_sooner_than_the_floor():
    assert jobs.next_check_seconds(_hint(4.0, 2.0, 6.0), elapsed=5.9) == 30


def test_a_tight_history_still_gets_sane_advice():
    """The narrow case must not regress: 2s in on a 4s median job."""
    assert jobs.next_check_seconds(_hint(4.0, 2.0, 6.0), elapsed=2.0) == 30
