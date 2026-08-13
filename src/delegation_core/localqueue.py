"""localqueue.py — the task line in front of the one local model.

Several agents connect to this daemon at once (Claude Code, Antigravity/Gemini,
whatever else speaks MCP). They are parallel; the local model is not. One
llama.cpp process on one GPU serves one request at a time, and four agents
discovering that simultaneously is how you get four callers blocked on a socket
with no idea where they are in line.

So work for the local model is submitted rather than called: an agent enqueues a
task, gets an id back immediately, and goes on doing other things. A single
worker drains the line in order. That inverts the failure mode — instead of
agents queueing invisibly inside an HTTP request, the queue is a first-class
object they can list, poll, and cancel.

Two things follow from the daemon being long-lived and the clients not being:

**The store is on disk.** ``jobs.py`` keeps its registry in memory, which is
right for a job whose submitter is waiting on it — if the daemon restarts, that
caller is gone too. A scheduled task is the opposite: it outlives the session
that asked for it, so it has to survive a restart. Same reasoning as
``processes.json``.

**Tasks carry who asked.** With one client, "the queue" needed no attribution.
With several, the first question about a queued task is which agent put it
there, so ``submitted_by`` is recorded at submit time from the MCP session.

Scheduling is the same object with a ``run_after``: a task is not eligible until
its time arrives. That is deliberately not a second mechanism — a scheduled task
and a queued one differ by one timestamp, and one line means one place where
ordering, persistence and cancellation are implemented.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("localqueue")

STORE_PATH = Path.home() / ".delegation_core" / "local_tasks.json"

#: Terminal states. A task in one of these is never claimed again.
DONE_STATES = ("done", "error", "cancelled")

#: How many finished tasks to keep. The line is a work queue, not a log — the
#: vault is where anything worth keeping ends up. Without a cap this file grows
#: without bound, and it is read whole on every claim.
KEEP_FINISHED = 200

_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> list[dict]:
    try:
        with STORE_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        # A corrupt store must not take the daemon down with it: the queue is an
        # accessory to a server whose real job is the vault. Move it aside so the
        # next write starts clean and the damaged file is still there to look at.
        logger.error("local task store unreadable (%s) — starting a new one", e)
        try:
            STORE_PATH.replace(STORE_PATH.with_suffix(".json.corrupt"))
        except OSError:
            pass
        return []


def _write(tasks: list[dict]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace: the worker and the MCP tools write this file from different
    # threads, and a half-written store is indistinguishable from a corrupt one.
    tmp = STORE_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(tasks, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(STORE_PATH)


def _prune(tasks: list[dict]) -> list[dict]:
    finished = [t for t in tasks if t["status"] in DONE_STATES]
    if len(finished) <= KEEP_FINISHED:
        return tasks
    drop = {t["id"] for t in sorted(finished, key=lambda t: t.get("finished") or "")[
        : len(finished) - KEEP_FINISHED
    ]}
    return [t for t in tasks if t["id"] not in drop]


def submit(prompt: str, system: str = "", task: str = "default",
           submitted_by: str = "unknown", run_after: str = "",
           note: str = "") -> dict:
    """Put a task in the line. Returns the stored record, including its id.

    `run_after` is an ISO-8601 timestamp; until it passes the task is
    `scheduled` rather than `queued` and the worker skips it.
    """
    if not prompt.strip():
        raise ValueError("a task needs a prompt")

    record = {
        "id": uuid.uuid4().hex[:12],
        "status": "scheduled" if run_after else "queued",
        "prompt": prompt,
        "system": system,
        "task": task,
        "submitted_by": submitted_by,
        "note": note,
        "created": _now(),
        "run_after": run_after,
        "started": None,
        "finished": None,
        "result": None,
        "error": None,
    }
    with _lock:
        tasks = _read()
        tasks.append(record)
        _write(_prune(tasks))
    logger.info("queued local task %s from %s (%s)", record["id"], submitted_by, task)
    return record


def get(task_id: str) -> dict | None:
    with _lock:
        for t in _read():
            if t["id"] == task_id:
                return t
    return None


def list_tasks(status: str = "", limit: int = 50) -> list[dict]:
    """Newest first. `status` filters; "" returns everything."""
    with _lock:
        tasks = _read()
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    return sorted(tasks, key=lambda t: t["created"], reverse=True)[:limit]


def cancel(task_id: str) -> dict | None:
    """Cancel a task that has not started. A running one is left alone.

    Interrupting mid-generation would mean killing the llama.cpp process, which
    is shared with every other queued task — the cure is worse than the wait.
    """
    with _lock:
        tasks = _read()
        for t in tasks:
            if t["id"] != task_id:
                continue
            if t["status"] in DONE_STATES or t["status"] == "running":
                return t
            t["status"] = "cancelled"
            t["finished"] = _now()
            _write(tasks)
            return t
    return None


def claim_next(now: str = "") -> dict | None:
    """Take the oldest runnable task and mark it running, atomically.

    Claiming and marking are one critical section on purpose: two workers (or a
    worker and a restarted worker) that both read before either writes would
    both run the same task against the same single-slot model.
    """
    now = now or _now()
    with _lock:
        tasks = _read()
        for t in sorted(tasks, key=lambda t: t["created"]):
            if t["status"] == "scheduled" and t["run_after"] and t["run_after"] <= now:
                t["status"] = "queued"
            if t["status"] != "queued":
                continue
            t["status"] = "running"
            t["started"] = _now()
            _write(tasks)
            return dict(t)
        # Persist any scheduled -> queued promotions even when nothing was
        # claimed, so a listing reflects what the worker already knows.
        _write(tasks)
    return None


def finish(task_id: str, result: str = "", error: str = "") -> dict | None:
    with _lock:
        tasks = _read()
        for t in tasks:
            if t["id"] != task_id:
                continue
            t["status"] = "error" if error else "done"
            t["result"] = result or None
            t["error"] = error or None
            t["finished"] = _now()
            _write(_prune(tasks))
            return dict(t)
    return None


def requeue_orphans() -> int:
    """Return tasks left `running` by a dead daemon to the line.

    A restart kills the worker mid-task, and the record on disk still says
    running — nothing will ever finish it, and it blocks nothing, so it would
    simply sit there looking active forever. Called once at startup.
    """
    with _lock:
        tasks = _read()
        orphans = [t for t in tasks if t["status"] == "running"]
        for t in orphans:
            t["status"] = "queued"
            t["started"] = None
        if orphans:
            _write(tasks)
    if orphans:
        logger.info("returned %d interrupted task(s) to the queue", len(orphans))
    return len(orphans)


def stats() -> dict:
    with _lock:
        tasks = _read()
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return {"total": len(tasks), "by_status": counts}
