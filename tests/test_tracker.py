"""ProcessTracker: create/list/update/get against an isolated tmp_path store —
this class already takes its store_path as an explicit constructor argument,
so no monkeypatching of global config state is needed here.
"""

import pytest

from delegation_core.tracker import ProcessTracker, _find_process


@pytest.fixture
def tracker(tmp_path):
    return ProcessTracker(tmp_path / "processes.json")


def test_create_returns_new_process_with_steps(tracker):
    proc = tracker.create("Test process", description="desc", steps=["step a", "step b"])
    assert proc["name"] == "Test process"
    assert proc["status"] == "active"
    assert len(proc["steps"]) == 2
    assert proc["steps"][0] == {"index": 0, "description": "step a", "done": False, "completed_at": None}


def test_get_by_exact_id(tracker):
    proc = tracker.create("A")
    assert tracker.get(proc["id"])["name"] == "A"


def test_get_by_unique_prefix(tracker):
    proc = tracker.create("A")
    prefix = proc["id"][:len("proc_") + 2]
    assert tracker.get(prefix)["id"] == proc["id"]


def test_get_missing_returns_none(tracker):
    assert tracker.get("proc_doesnotexist") is None


def test_list_processes_defaults_to_active_only(tracker):
    tracker.create("Active one")
    done = tracker.create("Done one")
    tracker.update(done["id"], status="done")

    active = tracker.list_processes(status="active")
    assert len(active) == 1
    assert active[0]["name"] == "Active one"

    everything = tracker.list_processes(status="all")
    assert len(everything) == 2


def test_list_processes_query_matches_name_description_and_notes(tracker):
    proc = tracker.create("Vendor migration", description="move off legacy vendor")
    tracker.update(proc["id"], note="vendor confirmed the new API")
    tracker.create("Unrelated")

    assert len(tracker.list_processes(query="vendor")) == 1
    assert len(tracker.list_processes(query="nonexistent-term")) == 0


def test_update_marks_step_done(tracker):
    proc = tracker.create("A", steps=["one", "two"])
    updated = tracker.update(proc["id"], step_done=0)
    assert updated["steps"][0]["done"] is True
    assert updated["steps"][0]["completed_at"] is not None
    assert updated["steps"][1]["done"] is False


def test_update_out_of_range_step_done_is_ignored_not_raised(tracker):
    proc = tracker.create("A", steps=["one"])
    updated = tracker.update(proc["id"], step_done=99)
    assert updated["steps"][0]["done"] is False


def test_update_invalid_status_is_ignored(tracker):
    proc = tracker.create("A")
    updated = tracker.update(proc["id"], status="not-a-real-status")
    assert updated["status"] == "active"


def test_update_valid_status_changes_it(tracker):
    proc = tracker.create("A")
    updated = tracker.update(proc["id"], status="paused")
    assert updated["status"] == "paused"


def test_update_missing_process_returns_none(tracker):
    assert tracker.update("proc_doesnotexist", note="x") is None


def test_state_persists_across_tracker_instances(tmp_path):
    store = tmp_path / "processes.json"
    proc = ProcessTracker(store).create("Persisted")
    reopened = ProcessTracker(store)
    assert reopened.get(proc["id"])["name"] == "Persisted"


def test_read_corrupt_store_returns_empty_not_raises(tracker):
    """_read() must degrade gracefully (log + return []) rather than propagate
    a JSONDecodeError — a truncated write (crash mid-os.replace, disk full,
    manual edit) must not take down every tool that reads the tracker."""
    tracker.store_path.write_text("{not valid json", encoding="utf-8")
    assert tracker.list_processes(status="all") == []
    assert tracker.get("proc_anything") is None


def test_list_processes_sorted_newest_updated_first(tracker):
    tracker._write([
        {"id": "proc_a", "name": "A", "description": "", "status": "active",
         "created": "2026-01-01T00:00:00", "updated": "2026-01-01T00:00:00",
         "steps": [], "notes": []},
        {"id": "proc_b", "name": "B", "description": "", "status": "active",
         "created": "2026-01-02T00:00:00", "updated": "2026-01-02T00:00:00",
         "steps": [], "notes": []},
    ])
    result = tracker.list_processes(status="active")
    assert [p["id"] for p in result] == ["proc_b", "proc_a"]


def test_summary_reports_counts_and_step_progress(tracker):
    a = tracker.create("Active with steps", steps=["one", "two"])
    tracker.update(a["id"], step_done=0)
    tracker.create("Active no steps")
    paused = tracker.create("Paused one")
    tracker.update(paused["id"], status="paused")
    done = tracker.create("Done one")
    tracker.update(done["id"], status="done")

    summary = tracker.summary()
    assert summary["active_count"] == 2
    assert summary["paused_count"] == 1
    names = {p["name"]: p for p in summary["active"]}
    assert names["Active with steps"]["steps_done"] == "1/2"
    assert names["Active no steps"]["steps_done"] == "open"


def test_summary_caps_active_list_at_five(tracker):
    for i in range(7):
        tracker.create(f"Process {i}")
    summary = tracker.summary()
    assert summary["active_count"] == 7
    assert len(summary["active"]) == 5


def test_find_process_ambiguous_prefix_uses_first_match_and_logs_warning(caplog):
    """Two ids sharing a prefix (possible since ids are only 6 hex chars) must
    resolve deterministically (first match) rather than raise or pick randomly,
    per the documented fallback behavior in _find_process's docstring."""
    processes = [
        {"id": "proc_aaaa11", "name": "first"},
        {"id": "proc_aaaa22", "name": "second"},
    ]
    with caplog.at_level("WARNING"):
        result = _find_process(processes, "proc_aaaa")
    assert result["name"] == "first"
    assert "Ambiguous" in caplog.text


def test_find_process_no_match_returns_none():
    assert _find_process([{"id": "proc_aaaa11"}], "proc_zzzz") is None
