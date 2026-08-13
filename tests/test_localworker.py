"""The worker that drains the local task line.

The engine is faked here on purpose — these tests are about scheduling and
lifecycle, not about llama.cpp. What they pin is that work reaches the model one
at a time, that `force_local` is actually passed (without it, agent mode returns
an extractive summary and the task silently does nothing), and that a failure
still lands in a terminal state.
"""

import threading
import time

import pytest

from delegation_core import localqueue
from delegation_core.localworker import LocalTaskWorker


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(localqueue, "STORE_PATH", tmp_path / "local_tasks.json")


class FakeEngine:
    """Records how it was called and how many callers were inside at once."""

    def __init__(self, delay=0.05, fail=False):
        self.calls = []
        self.delay = delay
        self.fail = fail
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    async def invoke(self, prompt, system="", task="default", force_local=False, **kw):
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            self.calls.append({"prompt": prompt, "system": system,
                               "task": task, "force_local": force_local})
            time.sleep(self.delay)
            if self.fail:
                raise RuntimeError("llama.cpp is not answering")
            return f"answer to {prompt}"
        finally:
            with self._lock:
                self.concurrent -= 1

    async def aclose(self):
        pass


def _drain(worker, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_runs_a_queued_task_and_stores_the_result():
    engine = FakeEngine()
    t = localqueue.submit("summarise the vault", system="be terse", task="synthesize")
    worker = LocalTaskWorker(engine, poll_seconds=0.01)
    worker.start()
    try:
        assert _drain(worker, lambda: localqueue.get(t["id"])["status"] == "done")
    finally:
        worker.stop()

    record = localqueue.get(t["id"])
    assert record["result"] == "answer to summarise the vault"
    assert engine.calls[0]["system"] == "be terse"
    assert engine.calls[0]["task"] == "synthesize"


def test_forces_the_local_model():
    """Without force_local, agent mode answers with an extractive fallback — the
    task would report done having never reached the model it was queued for."""
    engine = FakeEngine()
    localqueue.submit("run this locally")
    worker = LocalTaskWorker(engine, poll_seconds=0.01)
    worker.start()
    try:
        assert _drain(worker, lambda: len(engine.calls) == 1)
    finally:
        worker.stop()
    assert engine.calls[0]["force_local"] is True


def test_tasks_run_one_at_a_time():
    """The reason the queue exists: one model, one slot."""
    engine = FakeEngine(delay=0.05)
    for i in range(4):
        localqueue.submit(f"task {i}")
    worker = LocalTaskWorker(engine, poll_seconds=0.01)
    worker.start()
    try:
        assert _drain(worker, lambda: len(engine.calls) == 4, timeout=10)
    finally:
        worker.stop()
    assert engine.max_concurrent == 1


def test_tasks_run_in_submission_order():
    engine = FakeEngine(delay=0.01)
    ids = [localqueue.submit(f"task {i}")["id"] for i in range(3)]
    worker = LocalTaskWorker(engine, poll_seconds=0.01)
    worker.start()
    try:
        assert _drain(worker, lambda: len(engine.calls) == 3, timeout=10)
    finally:
        worker.stop()
    assert [c["prompt"] for c in engine.calls] == ["task 0", "task 1", "task 2"]


def test_a_failing_task_reaches_a_terminal_state():
    """Left `running`, it would be requeued by the next restart straight back
    into the same failure."""
    engine = FakeEngine(fail=True)
    t = localqueue.submit("this will fail")
    worker = LocalTaskWorker(engine, poll_seconds=0.01)
    worker.start()
    try:
        assert _drain(worker, lambda: localqueue.get(t["id"])["status"] == "error")
    finally:
        worker.stop()
    assert "llama.cpp is not answering" in localqueue.get(t["id"])["error"]


def test_scheduled_task_is_left_alone_until_due():
    engine = FakeEngine()
    t = localqueue.submit("much later", run_after="2999-01-01T00:00:00+00:00")
    worker = LocalTaskWorker(engine, poll_seconds=0.01)
    worker.start()
    try:
        time.sleep(0.2)
    finally:
        worker.stop()
    assert engine.calls == []
    assert localqueue.get(t["id"])["status"] == "scheduled"


def test_start_requeues_tasks_orphaned_by_a_restart():
    engine = FakeEngine()
    t = localqueue.submit("interrupted")
    localqueue.claim_next()          # simulate the previous daemon dying mid-task
    assert localqueue.get(t["id"])["status"] == "running"

    worker = LocalTaskWorker(engine, poll_seconds=0.01)
    worker.start()
    try:
        assert _drain(worker, lambda: localqueue.get(t["id"])["status"] == "done")
    finally:
        worker.stop()


def test_stop_is_prompt_when_idle():
    """Idle waiting uses Event.wait, not sleep, so shutdown is not held hostage
    to a full poll interval."""
    worker = LocalTaskWorker(FakeEngine(), poll_seconds=30)
    worker.start()
    began = time.time()
    worker.stop(timeout=5)
    assert time.time() - began < 2.0
    assert not worker.running
