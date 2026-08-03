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
    for seconds in (100.0, 110.0, 900.0):   # one outlier must not dominate
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
