"""
tracker.py — Persistent process registry.

Processes survive server restarts and span multiple sessions.
Stored at ~/.delegation_core/processes.json.
Thread-safe via a threading.Lock; writes are atomic (write-then-rename).
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("tracker")

VALID_STATUSES = {"active", "paused", "done", "cancelled"}


def _find_process(processes: list, process_id: str) -> dict | None:
    """Find a process by exact ID, falling back to a prefix match.

    An exact match always wins. The fallback is prefix-only: IDs are minted as
    "proc_" + a random hex tail, so callers naturally abbreviate from the front
    (e.g. "proc_a1b2"). A suffix match added nothing a prefix match couldn't do
    and actively caused collisions — every "proc_*" id ends in hex, so a bare
    hex fragment could match unrelated processes. v5.1 patch: dropped the
    endswith() arm that the 2026-06-11 fix had removed and v5 reintroduced.
    """
    for p in processes:
        if p["id"] == process_id:
            return p
    matches = [p for p in processes if p["id"].startswith(process_id)]
    if len(matches) > 1:
        logger.warning(
            "Ambiguous process id '%s' matches %d processes: %s — using %s",
            process_id, len(matches),
            ", ".join(p["id"] for p in matches),
            matches[0]["id"],
        )
    return matches[0] if matches else None


class ProcessTracker:
    def __init__(self, store_path: Path):
        self.store_path = store_path
        self._lock = threading.Lock()
        if not self.store_path.exists():
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self._write([])

    # ── public API ────────────────────────────────────────────────────────────

    def create(self, name: str, description: str = "", steps: list | None = None) -> dict:
        with self._lock:
            processes = self._read()
            proc = {
                "id": "proc_" + uuid.uuid4().hex[:6],
                "name": name,
                "description": description,
                "status": "active",
                "created": _now(),
                "updated": _now(),
                "steps": [
                    {"index": i, "description": s, "done": False, "completed_at": None}
                    for i, s in enumerate(steps or [])
                ],
                "notes": [],
            }
            processes.append(proc)
            self._write(processes)
            logger.info("Process created: %s (%s)", proc["id"], name)
            return proc

    def list_processes(self, status: str = "active", query: str = "") -> list:
        processes = self._read()
        if status != "all":
            processes = [p for p in processes if p["status"] == status]
        if query:
            q = query.lower()
            processes = [
                p for p in processes
                if q in p["name"].lower() or q in p["description"].lower()
                or any(q in n["text"].lower() for n in p["notes"])
            ]
        return sorted(processes, key=lambda p: p["updated"], reverse=True)

    def get(self, process_id: str) -> dict | None:
        return _find_process(self._read(), process_id)

    def update(
        self,
        process_id: str,
        note: str = "",
        step_done: int = -1,
        status: str = "",
    ) -> dict | None:
        with self._lock:
            processes = self._read()
            proc = _find_process(processes, process_id)
            if proc is None:
                return None

            changed = False

            if note:
                proc["notes"].append({"text": note, "timestamp": _now()})
                changed = True

            if step_done >= 0:
                steps = proc["steps"]
                if step_done < len(steps):
                    steps[step_done]["done"] = True
                    steps[step_done]["completed_at"] = _now()
                    changed = True
                else:
                    logger.warning(
                        "step_done=%d out of range for %s (has %d steps)",
                        step_done, process_id, len(steps),
                    )

            if status:
                if status not in VALID_STATUSES:
                    logger.warning("Invalid status '%s' ignored", status)
                else:
                    proc["status"] = status
                    changed = True

            if changed:
                proc["updated"] = _now()
                self._write(processes)

            return proc

    def summary(self) -> dict:
        """Compact summary for heartbeat — counts and active process names."""
        processes = self._read()
        active = [p for p in processes if p["status"] == "active"]
        paused = [p for p in processes if p["status"] == "paused"]
        return {
            "active_count": len(active),
            "paused_count": len(paused),
            "active": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "steps_done": f"{sum(s['done'] for s in p['steps'])}/{len(p['steps'])}"
                    if p["steps"] else "open",
                    "updated": p["updated"][:10],
                }
                for p in active[:5]  # surface at most 5 in heartbeat
            ],
        }

    # ── internals ─────────────────────────────────────────────────────────────

    def _read(self) -> list:
        """Os processos gravados, ou lista vazia quando o arquivo nao serve.

        A checagem de TIPO existe porque tratar so "nao parseia" deixa passar o
        caso pior: um JSON valido do tipo errado. Medido com o arquivo contendo
        `{}`, antes desta guarda:

            ProcessTracker.create(...)  ->  AttributeError: 'dict' object has
                                            no attribute 'append'

        Os outros dois armazens JSON deste projeto ja faziam a mesma checagem --
        `jobs.py` com `isinstance(data, dict)` e `localqueue.py` com
        `isinstance(data, list)` -- e este, que guarda os 129 KB do estado que
        atravessa sessoes, era o unico sem.

        Descarta em voz alta: sobrescrever o estado de processos do usuario em
        silencio seria pior que estourar, porque pelo menos o AttributeError
        aparecia.
        """
        try:
            dados = json.loads(self.store_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error("Could not read processes store: %s", e)
            return []
        if not isinstance(dados, list):
            logger.error(
                "processes store at %s holds %s, not a list - ignoring it. The "
                "file was not deleted; move it aside if the content matters.",
                self.store_path, type(dados).__name__)
            return []
        return dados

    def _write(self, processes: list):
        tmp = self.store_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(processes, indent=2), encoding="utf-8")
            os.replace(tmp, self.store_path)  # atomic on POSIX and Windows
        except Exception as e:
            logger.error("Could not write processes store: %s", e)
            tmp.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
