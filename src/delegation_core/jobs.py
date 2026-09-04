"""
jobs.py — In-process background job store.

Any module can submit a blocking function as a daemon thread and get a job_id
back immediately. The server polls status via task_status().

Job IDs are in-memory only — they do not survive a server restart.

Completed run times ARE persisted, per task name, so task_status() can tell a
caller how long this kind of job usually takes. A calling agent otherwise has
only elapsed_seconds to go on and has no way to distinguish "started 20s ago,
will take 8 minutes" from "nearly done" — in practice that means polling every
30s through a 7-minute build, or abandoning the tool and watching the output
directory from a shell instead.
"""

import json
import logging
import os
import statistics
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("jobs")

_jobs: dict = {}
_lock = threading.Lock()

# When this process's job store came up. A job_id that is absent is either a
# typo or a casualty of a restart, and those want opposite responses from the
# caller — this is the one fact that separates them: submit a job, get an id
# back, and if the store is younger than the id you are holding, the daemon
# restarted underneath you and the work's outcome is unknown, not failed.
STARTED_AT = datetime.now()

# Rolling history of completed durations per task name. Small and advisory —
# a corrupt or missing file just means callers get no hint, never an error.
_DURATIONS_PATH = Path.home() / ".delegation_core" / "job_durations.json"
_DURATIONS_KEEP = 10

# The durations file is read-modify-written by whichever job thread happens to
# finish, and jobs.submit hands every task its own daemon thread. Two finishing
# together both read the old file, both append their own entry, and the second
# write erases the first — the classic lost update, on a file whose whole job is
# to accumulate a history.
#
# `_lock` above guards the in-memory `_jobs` dict and nothing else, so it is not
# this lock. A separate one keeps the two concerns apart: a slow disk write must
# not block a status poll.
#
# localqueue.py, which stores the same kind of thing, already does both halves
# of this correctly (an RLock plus tmp+fsync+os.replace). This module was the
# one JSON store in the project with neither.
_durations_lock = threading.Lock()


def _load_durations() -> dict:
    try:
        data = json.loads(_DURATIONS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        # Advisory data: a corrupt file must not raise. But it must not vanish
        # silently either — before this, a truncated write turned into "no
        # history" for every task at once, and the only symptom was task_status
        # quietly dropping check_again_in_seconds from its answers.
        logger.warning("job duration history unreadable (%s) — starting empty", e)
        return {}
    return data if isinstance(data, dict) else {}


def _record_duration(task_name: str, seconds: float) -> None:
    try:
        with _durations_lock:
            data = _load_durations()
            history = [s for s in data.get(task_name, []) if isinstance(s, (int, float))]
            history.append(round(seconds, 1))
            data[task_name] = history[-_DURATIONS_KEEP:]
            _DURATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Atomic, for the same reason localqueue is: write_text truncates
            # the real file first, so a crash or a full disk mid-write leaves a
            # half-written store that is indistinguishable from a corrupt one,
            # and _load_durations then reads it as "no history at all".
            tmp = _DURATIONS_PATH.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, _DURATIONS_PATH)
    except Exception as e:  # advisory only — never fail a job over telemetry
        logger.debug("Could not record duration for %s: %s", task_name, e)


#: Ratio between the slowest and fastest recorded run above which this task's
#: history is treated as covering work of more than one size, and the median
#: stops being a usable prediction. An order of magnitude, deliberately not
#: tighter: 5.7s against 1.7s is the same job on a quiet and a busy disk, while
#: 642.3s against 1.7s is not the same job at all.
_SPREAD_IS_WIDE = 10.0


def typical_seconds(task_name: str) -> float | None:
    """Median run time of this task's last completed runs, or None if unknown."""
    history = _load_durations().get(task_name) or []
    history = [s for s in history if isinstance(s, (int, float))]
    return round(statistics.median(history), 1) if history else None


def duration_hint(task_name: str) -> dict | None:
    """What this task's history can honestly say about how long it will take.

    `typical_seconds` alone answers a question the history often cannot support.
    Measured on this machine on 2026-09-03, the stored runs were::

        vault_reindex [3.5, 1.9, 2.9, 1.7, 2.1, 5.4, 5.7, 642.3, 2.9, 69.8]
        ingest_folder [2.0, 1.1, 1.5, 238.8, 1168.3, 84.7, 5.6, 24.1, 2241.0, 13.1]
        graph_build   [21.3, 16.8, 224.4, 177.4, 297.3, 141.2, 565.4, 15.4]

    None of the three is one distribution. They are two: a small job and a large
    one sharing a task name. The median of the mixture described neither — a
    reindex started while writing this reported ``typical_seconds: 3.2`` and ran
    for 178.4s, a 56x miss, and the advice that came with it was to check back in
    30 seconds.

    So the hint carries the spread as well as the middle, and says outright when
    the two ends are far enough apart that the middle predicts nothing. A caller
    that is told "between 1.7s and 642.3s" knows to wait long and check rarely;
    a caller told "3.2s" does not, and spends its turns finding out.

    Returns None when there is no history at all — the same silence
    `typical_seconds` keeps, for the same reason.
    """
    history = [s for s in (_load_durations().get(task_name) or [])
               if isinstance(s, (int, float))]
    if not history:
        return None
    fastest, slowest = min(history), max(history)
    # A zero or negative fastest would make the ratio meaningless (or raise);
    # a run that fast carries no information about the spread either way.
    wide = fastest > 0 and (slowest / fastest) >= _SPREAD_IS_WIDE
    return {
        "typical_seconds": round(statistics.median(history), 1),
        "fastest_seconds": round(fastest, 1),
        "slowest_seconds": round(slowest, 1),
        "runs_recorded": len(history),
        "spread_is_wide": wide,
    }


#: Never advise a poll sooner than this: below it the caller spends a turn per
#: answer for no new information.
_MIN_POLL_WAIT = 30
#: Never advise a wait longer than this, however long the job has run. A job
#: that has outlived every recorded run is also the one most likely to be stuck,
#: and an hour of silence is too long to notice that.
_MAX_POLL_WAIT = 600


def next_check_seconds(hint: dict, elapsed: float) -> int:
    """How long to wait before polling this job again.

    The rule this replaces was ``max(int(typical - elapsed) + 5, 30)``, which
    aims at the median and then, once the job outlives it, floors at a flat 30s
    beat that never grows. That is the exact behaviour jobs.py's own module
    docstring says this feature exists to remove: "polling every 30s through a
    7-minute build".

    Three regimes, in order:

    * **Before the expected finish** — aim just past it, as before.
    * **Past the median but inside the slowest run on record** — the job is not
      late, it is one of the big ones; aim just past *that* instead.
    * **Past everything ever recorded** — nothing in the history describes this
      run any more, so stop pretending to predict and back off geometrically
      (half the elapsed time), capped, so a job that runs all night is polled a
      handful of times rather than a hundred.
    """
    typical = hint["typical_seconds"]
    slowest = hint["slowest_seconds"]
    if elapsed < typical:
        return max(int(typical - elapsed) + 5, _MIN_POLL_WAIT)
    if elapsed < slowest:
        return max(int(slowest - elapsed) + 5, _MIN_POLL_WAIT)
    return min(max(int(elapsed / 2), _MIN_POLL_WAIT), _MAX_POLL_WAIT)


def submit(task_name: str, fn, *args, **kwargs) -> str:
    """Run fn(*args, **kwargs) in a daemon thread. Returns a job_id immediately."""
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "task": task_name,
            "status": "running",
            "started": datetime.now().isoformat(),
            "finished": None,
            "result": None,
            "error": None,
        }

    def _worker():
        started_at = datetime.now()
        try:
            result = fn(*args, **kwargs)
            update = {"status": "done", "result": result, "finished": datetime.now().isoformat()}
        except Exception as e:
            logger.error("Background job %s (%s) failed: %s", job_id, task_name, e)
            update = {"status": "error", "error": str(e), "finished": datetime.now().isoformat()}
        # Recorded before the status flip so that a caller observing "done"
        # always sees the run already reflected in typical_seconds(). Only
        # successful runs shape the estimate: a job that raised after 2s says
        # nothing about how long the work actually takes.
        if update["status"] == "done":
            _record_duration(task_name, (datetime.now() - started_at).total_seconds())
        with _lock:
            _jobs[job_id].update(update)

    threading.Thread(target=_worker, daemon=True, name=f"job-{job_id}").start()
    logger.info("Submitted background job %s: %s", job_id, task_name)
    return job_id


def get(job_id: str) -> dict | None:
    """Return a snapshot of a job dict, or None if not found."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def running_count() -> int:
    """Return the number of currently running background jobs."""
    with _lock:
        return sum(1 for j in _jobs.values() if j["status"] == "running")
