"""localworker.py — the single consumer of the local task line.

One thread, one task at a time. That is not a simplification to revisit later:
it is the shape of the resource. `llama_queue_concurrency` already gates direct
calls for the same reason, and a second worker would only move contention from
the queue into the model.

**Agent mode is the case this exists for.** In `engine_mode: "agent"` the local
model never loads — `ensure_running()` and `check_health()` both return False,
and `invoke()` short-circuits to an extractive fallback so background pipelines
never hang on a model that isn't there. That is right for work the daemon starts
on its own. It is wrong for a task an agent explicitly queued *for the local
model*: answering "I didn't run it" to a request whose entire point was to run
it locally is not a fallback, it is a silent no-op.

So queued tasks take a different path: `force_local=True` through the engine,
which starts llama.cpp on demand and calls it directly. The cost is real and
worth stating — llama.cpp and BGE-m3 share one GPU, and this is exactly the
contention that moving to `engine_mode: "agent"` was meant to remove. It is paid
only while the line is non-empty, and only because something asked for it.

Which is also why the worker unloads the model again. Left alone, one queued
task pins the GPU until the next daemon restart — measured on this machine,
3838 -> 15386 MiB for a single 12-word prompt. After `local_idle_shutdown_sec`
with an empty line, the model is stopped and the next task pays an 8s reload.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from . import localqueue

logger = logging.getLogger("localworker")

#: How long to sleep when the line is empty. Long enough that an idle daemon is
#: doing nothing measurable, short enough that a submitted task starts promptly.
IDLE_POLL_SECONDS = 2.0


class LocalTaskWorker:
    """Drains the local task line on a background thread."""

    def __init__(self, engine, poll_seconds: float = IDLE_POLL_SECONDS,
                 idle_shutdown_sec: int | None = None):
        self._engine = engine
        self._poll = poll_seconds
        # None means "read it from config" — passing it explicitly is for tests,
        # which should not depend on whatever this machine happens to be set to.
        if idle_shutdown_sec is None:
            cfg = getattr(engine, "cfg", None)
            idle_shutdown_sec = getattr(cfg, "local_idle_shutdown_sec", 0)
        self._idle_shutdown_sec = idle_shutdown_sec
        self._idle_since: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # Anything left `running` belongs to a worker that no longer exists.
        localqueue.requeue_orphans()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="local-task-worker", daemon=True
        )
        self._thread.start()
        logger.info("local task worker started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        # Its own event loop: this thread is not the daemon's, and the engine's
        # httpx client is async. Same reasoning as jobs.submit's asyncio.run —
        # a client born in one loop must not be awaited from another.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self._stop.is_set():
                task = localqueue.claim_next()
                if task is None:
                    self._maybe_unload_idle_model()
                    # Event.wait rather than sleep: stop() interrupts an idle
                    # worker immediately instead of after a full poll interval.
                    self._stop.wait(self._poll)
                    continue
                self._idle_since = None
                self._run_one(loop, task)
                # Start the idle clock from when the line went quiet, not from
                # the last claim attempt — otherwise polling keeps resetting it
                # and the model never unloads.
                self._idle_since = time.time()
        finally:
            try:
                loop.run_until_complete(self._engine.aclose())
            except Exception:
                pass
            loop.close()

    def _maybe_unload_idle_model(self) -> None:
        """Unload the local model once the line has been empty long enough.

        Guarded to agent mode: there the model is loaded only because something
        queued work for it, so an empty line means nothing needs it. In
        local/hybrid mode it is the engine every other caller uses, and pulling
        it out from under them trades an idle minute for a cold start on the
        next call.

        _shutdown() itself only stops a process this engine started, so a
        llama.cpp someone else is running by hand is never touched.
        """
        if not self._idle_shutdown_sec or self._idle_since is None:
            return
        if not getattr(getattr(self._engine, "cfg", None), "is_agent_mode", False):
            return
        if time.time() - self._idle_since < self._idle_shutdown_sec:
            return

        self._idle_since = None
        try:
            if self._engine._is_healthy():
                logger.info(
                    "local task line idle for %ds — unloading the local model",
                    self._idle_shutdown_sec,
                )
                self._engine._shutdown()
        except Exception as e:
            # An unload that fails is a held GPU, not a broken queue: the next
            # task still runs against whatever is (or isn't) loaded.
            logger.warning("could not unload the idle local model: %s", e)

    def _run_one(self, loop, task: dict) -> None:
        started = time.time()
        try:
            result = loop.run_until_complete(
                self._engine.invoke(
                    task["prompt"],
                    system=task.get("system", ""),
                    task=task.get("task", "default"),
                    force_local=True,
                )
            )
            localqueue.finish(task["id"], result=result)
            logger.info(
                "local task %s done in %.1fs (%s)",
                task["id"], time.time() - started, task.get("submitted_by", "unknown"),
            )
        except Exception as e:
            # A failed task must reach a terminal state, or it stays `running`
            # forever and the next restart quietly requeues it into the same
            # failure. The submitter reads the reason off the record.
            localqueue.finish(task["id"], error=f"{type(e).__name__}: {e}")
            logger.warning("local task %s failed: %s", task["id"], e)
