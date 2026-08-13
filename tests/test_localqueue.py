"""The local task line: several agents in, one model out.

What matters here is not that a list round-trips, but the three properties the
queue exists to provide — that a task survives the daemon that accepted it, that
two claimants can never take the same task, and that a restart cannot strand one
as permanently `running`.
"""

import json
import threading

import pytest

from delegation_core import localqueue


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(localqueue, "STORE_PATH", tmp_path / "local_tasks.json")
    return tmp_path / "local_tasks.json"


def test_submitted_task_is_queued_and_attributed():
    t = localqueue.submit("summarise this", submitted_by="antigravity")
    assert t["status"] == "queued"
    assert t["submitted_by"] == "antigravity"
    assert localqueue.get(t["id"])["prompt"] == "summarise this"


def test_survives_a_restart(store):
    """The point of a file-backed store: jobs.py's in-memory registry cannot do
    this, which is why a scheduled task could not live there."""
    t = localqueue.submit("outlives the session")
    assert store.exists()
    assert json.loads(store.read_text())[0]["id"] == t["id"]
    # A "restart" is just another reader of the same file.
    assert localqueue.get(t["id"])["prompt"] == "outlives the session"


def test_empty_prompt_is_rejected():
    with pytest.raises(ValueError):
        localqueue.submit("   ")


def test_claim_is_fifo_and_marks_running():
    first = localqueue.submit("one")
    localqueue.submit("two")
    claimed = localqueue.claim_next()
    assert claimed["id"] == first["id"]
    assert claimed["status"] == "running"
    assert localqueue.get(first["id"])["started"] is not None


def test_a_task_is_claimed_exactly_once_under_concurrency():
    """The property the whole design rests on. Two threads racing for one slot
    must not both get work — there is one model behind this."""
    localqueue.submit("only one of you")
    claims = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        got = localqueue.claim_next()
        if got:
            claims.append(got["id"])

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claims) == 1


def test_scheduled_task_is_not_claimed_before_its_time():
    localqueue.submit("later", run_after="2999-01-01T00:00:00+00:00")
    assert localqueue.claim_next() is None


def test_scheduled_task_becomes_claimable_once_due():
    t = localqueue.submit("soon", run_after="2000-01-01T00:00:00+00:00")
    assert t["status"] == "scheduled"
    claimed = localqueue.claim_next()
    assert claimed is not None and claimed["id"] == t["id"]


def test_finish_records_result_and_error_separately():
    a = localqueue.submit("a")
    b = localqueue.submit("b")
    localqueue.claim_next()
    localqueue.finish(a["id"], result="the answer")
    localqueue.claim_next()
    localqueue.finish(b["id"], error="ValueError: nope")

    assert localqueue.get(a["id"])["status"] == "done"
    assert localqueue.get(a["id"])["result"] == "the answer"
    assert localqueue.get(b["id"])["status"] == "error"
    assert localqueue.get(b["id"])["error"] == "ValueError: nope"


def test_cancel_only_applies_before_it_starts():
    t = localqueue.submit("cancel me")
    assert localqueue.cancel(t["id"])["status"] == "cancelled"
    assert localqueue.claim_next() is None

    running = localqueue.submit("already going")
    localqueue.claim_next()
    # Cancelling a running task would mean killing a process shared with every
    # other queued task, so it is refused rather than pretended.
    assert localqueue.cancel(running["id"])["status"] == "running"


def test_restart_returns_interrupted_tasks_to_the_line():
    t = localqueue.submit("interrupted by a restart")
    localqueue.claim_next()
    assert localqueue.get(t["id"])["status"] == "running"

    assert localqueue.requeue_orphans() == 1
    back = localqueue.get(t["id"])
    assert back["status"] == "queued"
    assert back["started"] is None


def test_finished_tasks_are_pruned_to_a_bound(monkeypatch):
    monkeypatch.setattr(localqueue, "KEEP_FINISHED", 3)
    for i in range(6):
        t = localqueue.submit(f"task {i}")
        localqueue.claim_next()
        localqueue.finish(t["id"], result="ok")
    assert len(localqueue.list_tasks(status="done")) == 3
    # A queued task is never pruned, however old the line gets.
    live = localqueue.submit("still waiting")
    for i in range(4):
        t = localqueue.submit(f"more {i}")
        localqueue.claim_next()
        localqueue.finish(t["id"], result="ok")
    assert localqueue.get(live["id"]) is not None


def test_corrupt_store_is_set_aside_not_fatal(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{ this is not json")
    assert localqueue.list_tasks() == []
    assert store.with_suffix(".json.corrupt").exists()
    # And the queue keeps working afterwards.
    assert localqueue.submit("after the damage")["status"] == "queued"


def test_stats_counts_by_status():
    localqueue.submit("q1")
    done = localqueue.submit("q2")
    localqueue.claim_next()
    localqueue.claim_next()
    localqueue.finish(done["id"], result="ok")
    s = localqueue.stats()
    assert s["total"] == 2
    assert s["by_status"]["done"] == 1
