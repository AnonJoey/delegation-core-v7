"""engine.py's local-model queue + server.py's _run_or_queue routing.

One HTTP daemon now fronts every MCP client, so llama.cpp — a single process —
can be asked for work by several clients at once. These tests pin the two halves
of the answer: the gate that serialises access, and the tool-level decision to
either run inline or hand back a job_id.

The gate is deliberately a threading primitive rather than an asyncio one,
because jobs.submit() runs asyncio.run() on a fresh loop inside a daemon thread;
test_queue_serialises_across_event_loops is what would fail if that ever
regressed to an asyncio.Semaphore.
"""

import asyncio
import json
import threading
import time

import httpx
import pytest

from delegation_core import engine as E
from delegation_core.config import Config


@pytest.fixture(autouse=True)
def clean_gate():
    E._reset_queue_for_tests()
    yield
    E._reset_queue_for_tests()


def _cfg(concurrency=1):
    return Config(vault_path="/tmp", llama_binary="x", llama_model="y",
                  engine_mode="local", llama_queue_concurrency=concurrency)


class _FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


@pytest.fixture
def instrumented_engine(monkeypatch):
    """A DelegationEngine whose inference is a 50ms sleep, recording its windows."""
    spans, lock = [], threading.Lock()

    async def fake_post(self, url, **kw):
        start = time.perf_counter()
        await asyncio.sleep(0.05)
        with lock:
            spans.append((start, time.perf_counter()))
        return _FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(E.DelegationEngine, "ensure_running",
                        lambda self: asyncio.sleep(0, result=True))
    return spans


def _max_overlap(spans):
    """How many inference windows were ever open at the same instant."""
    return max(
        sum(1 for s2, e2 in spans if s2 < end - 1e-9 and e2 > start + 1e-9)
        for start, end in spans
    )


def test_queue_serialises_concurrent_callers(instrumented_engine):
    spans = instrumented_engine
    eng = E.DelegationEngine(_cfg(concurrency=1))

    async def main():
        await asyncio.gather(*(eng.invoke("p", task="compress") for _ in range(4)))

    asyncio.run(main())
    assert len(spans) == 4
    assert _max_overlap(spans) == 1


def test_queue_honours_configured_concurrency(instrumented_engine):
    spans = instrumented_engine
    eng = E.DelegationEngine(_cfg(concurrency=2))

    async def main():
        await asyncio.gather(*(eng.invoke("p", task="compress") for _ in range(4)))

    asyncio.run(main())
    assert _max_overlap(spans) == 2


def test_queue_serialises_across_event_loops(instrumented_engine):
    """jobs.submit() runs asyncio.run() in a daemon thread, so callers arrive on
    different event loops. An asyncio.Semaphore would raise here instead of
    queueing — the gate has to be loop-agnostic."""
    spans = instrumented_engine
    eng = E.DelegationEngine(_cfg(concurrency=1))

    async def one():
        await eng.invoke("p", task="compress")

    threads = [threading.Thread(target=lambda: asyncio.run(one())) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(spans) == 3
    assert _max_overlap(spans) == 1


def test_queue_stats_drain_back_to_zero(instrumented_engine):
    eng = E.DelegationEngine(_cfg(concurrency=1))
    seen = []

    async def watcher():
        for _ in range(6):
            seen.append(E.queue_stats())
            await asyncio.sleep(0.02)

    async def main():
        await asyncio.gather(
            *(eng.invoke("p", task="compress") for _ in range(3)), watcher()
        )

    asyncio.run(main())
    assert max(s["waiting"] for s in seen) >= 1     # callers really did queue
    assert max(s["running"] for s in seen) == 1     # never more than the gate allows
    assert E.queue_stats() == {"waiting": 0, "running": 0}


def test_agent_mode_never_touches_the_queue(monkeypatch):
    """Agent mode has no local model, so invoke() returns its extractive
    fallback before reaching the gate — the queue must stay untouched."""
    cfg = Config(vault_path="/tmp", llama_binary="x", llama_model="y",
                 engine_mode="agent")
    eng = E.DelegationEngine(cfg)

    out = asyncio.run(eng.invoke("some prompt text", task="compress"))
    assert isinstance(out, str)
    assert E._model_gate is None          # never even armed
    assert E.queue_stats() == {"waiting": 0, "running": 0}


# ── server-side routing ──────────────────────────────────────────────────────

def test_run_or_queue_runs_inline_when_gate_is_free(monkeypatch):
    from delegation_core import server

    class _Stub:
        cfg = _cfg(concurrency=1)

    monkeypatch.setattr(server, "_engine", _Stub())

    async def make_result(engine):
        return json.dumps({"ran": "inline"})

    out = asyncio.run(server._run_or_queue("compress", make_result))
    assert json.loads(out) == {"ran": "inline"}


def test_run_or_queue_returns_job_id_when_gate_is_busy(monkeypatch):
    """Blocking here would be the wrong call: the client applies mcp_timeout_sec
    (60s by default) to the tool call, so a caller queued behind long
    generations would report a dead server rather than a busy one."""
    from delegation_core import server

    class _Stub:
        cfg = _cfg(concurrency=1)

    monkeypatch.setattr(server, "_engine", _Stub())
    monkeypatch.setattr(server, "_local_model_queue_stats",
                        lambda: {"waiting": 2, "running": 1})

    submitted = {}

    def fake_submit(task_name, fn, *args, **kwargs):
        submitted["task"] = task_name
        # Close the coroutine we are not going to run, so the test does not
        # emit "coroutine was never awaited".
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
        return "job123"

    monkeypatch.setattr(server.jobs, "submit", fake_submit)

    async def make_result(engine):
        return json.dumps({"ran": "inline"})

    out = json.loads(asyncio.run(server._run_or_queue("compress", make_result)))
    assert out["status"] == "queued"
    assert out["job_id"] == "job123"
    assert out["task"] == "compress"
    assert out["queue"] == {"waiting": 2, "running": 1}
    assert submitted["task"] == "compress"
