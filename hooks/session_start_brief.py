#!/usr/bin/env python3
"""
session_start_brief.py — Claude Code SessionStart hook.

Cross-surface memory bridge (Code-side half).

Claude Desktop / Cowork sessions write curated notes to the shared vault via
the export_session() / write_note() MCP tools (see CLAUDE_SYSTEM_PROMPT.md and
AGENT_GUIDE.md standing orders). Claude Code has no direct access to those
Desktop/Cowork conversations — but it shares the same vault.

This hook prints a short "what changed since you were last here" brief at the
start of every Claude Code session: notes added/updated in the vault since the
last Code session, plus a flag if _inbox/ has files waiting. Plain stdout from
a SessionStart hook is injected directly into the model's context.

If _inbox/ has files and maintenance hasn't been triggered recently (see
MAINTENANCE_COOLDOWN), this hook also fires `delegation-core maintain` as a
detached background process — so dropped files get classified, deduped, and
filed without anyone having to remember to run run_maintenance().

If new or updated notes are found (e.g. session transcripts written by the
SessionEnd hook, or notes from Claude Desktop/Cowork) and a reindex hasn't
fired recently (see REINDEX_COOLDOWN), this hook also fires
`delegation-core reindex` as a detached background process. This is a
backstop: session_export.py already triggers a reindex after writing a
transcript, but this covers cases where that trigger didn't fire (e.g. the
venv wasn't installed yet at SessionEnd, or notes arrived from another
surface that doesn't reindex itself).

Both triggers are detached processes, which used to mean two more ChromaDB
writers against the index the daemon holds open. Since v0.11 `maintain` and
`reindex` hand their work to the running daemon and only fall back to doing it
in-process when none is listening — see src/delegation_core/daemon.py. Nothing
about this hook changes; what changed is what those commands do when they land.

Requires only stdlib — runs with system Python 3.11+, no venv needed. The
maintenance trigger shells out to the venv's delegation-core binary but does
not import anything from it, so the hook itself stays dependency-free.

Hook registration (add to ~/.claude/settings.json):
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [{ "type": "command", "command": "python3 /path/to/hooks/session_start_brief.py" }]
      }
    ]
  }
}
"""

import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / ".delegation_core" / "config.json"
STATE_PATH = Path.home() / ".delegation_core" / "last_brief.json"
_VENV_DIR = Path.home() / ".delegation_core" / "venv"
VENV_BIN = (
    _VENV_DIR / "Scripts" / "delegation-core.exe"
    if platform.system() == "Windows"
    else _VENV_DIR / "bin" / "delegation-core"
)
MAINTENANCE_LOG = Path.home() / ".delegation_core" / "maintenance.log"
MAINTENANCE_COOLDOWN = 30 * 60  # seconds — don't re-trigger if fired recently
REINDEX_LOG = Path.home() / ".delegation_core" / "reindex.log"
REINDEX_COOLDOWN = 30 * 60  # seconds — don't re-trigger if fired recently
MAX_NOTES = 8


def _detached_popen_kwargs() -> dict:
    """Platform-appropriate kwargs to fully detach a background process.

    POSIX: ``start_new_session`` (setsid) so the child survives the parent's
    exit. Windows: ``start_new_session`` is silently ignored by subprocess
    on that platform, so use DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
    instead, which achieve the equivalent (no console, own process group).
    """
    if platform.system() == "Windows":
        return {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        }
    return {"start_new_session": True}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _trigger_maintenance() -> bool:
    """Fire `delegation-core maintain` as a detached background process.

    Returns True if the process was launched (not whether it succeeded —
    output goes to MAINTENANCE_LOG for later inspection).
    """
    if not VENV_BIN.exists():
        return False
    try:
        with open(MAINTENANCE_LOG, "a", encoding="utf-8") as log_fh:
            subprocess.Popen(
                [str(VENV_BIN), "maintain"],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **_detached_popen_kwargs(),
            )
        return True
    except Exception:
        return False


def _trigger_reindex() -> bool:
    """Fire `delegation-core reindex` as a detached background process.

    Backstop for new/updated notes that may not be in ChromaDB yet — e.g. a
    session transcript written by session_export.py before its own reindex
    trigger could run, or notes written by a surface that doesn't reindex
    itself. Returns True if the process was launched.
    """
    if not VENV_BIN.exists():
        return False
    try:
        with open(REINDEX_LOG, "a", encoding="utf-8") as log_fh:
            subprocess.Popen(
                [str(VENV_BIN), "reindex"],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **_detached_popen_kwargs(),
            )
        return True
    except Exception:
        return False


def main():
    config = _load_json(CONFIG_PATH)
    vault_path = config.get("vault_path", "")
    if not vault_path:
        return  # not configured — say nothing

    vault = Path(vault_path).expanduser()
    if not vault.exists():
        return

    state = _load_json(STATE_PATH)
    last_check = state.get("last_check", 0)
    last_maintenance = state.get("last_maintenance_trigger", 0)
    last_reindex = state.get("last_reindex_trigger", 0)
    now = datetime.now().timestamp()

    # First run: establish a baseline, don't dump the whole vault into context.
    first_run = "last_check" not in state

    # _inbox status (cheap, stdlib only)
    inbox = vault / "_inbox"
    inbox_count = sum(1 for f in inbox.iterdir() if f.is_file()) if inbox.exists() else 0

    # Auto-trigger maintenance for a non-empty inbox, rate-limited so a burst
    # of session starts doesn't spawn a pile of overlapping jobs.
    maintenance_triggered = False
    if inbox_count and (now - last_maintenance) > MAINTENANCE_COOLDOWN:
        maintenance_triggered = _trigger_maintenance()
        if maintenance_triggered:
            state["last_maintenance_trigger"] = now

    # Notes added/updated since the last Code session
    folders = config.get("vault_folders", [])
    new_notes = []
    if not first_run:
        for folder in folders:
            folder_path = vault / folder
            if not folder_path.exists():
                continue
            for f in folder_path.rglob("*.md"):
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if mtime > last_check:
                    new_notes.append((mtime, f))

    # Backstop reindex: new/updated notes (e.g. session transcripts) may not be
    # in ChromaDB yet if the writing surface's own reindex trigger didn't fire.
    reindex_triggered = False
    if new_notes and (now - last_reindex) > REINDEX_COOLDOWN:
        reindex_triggered = _trigger_reindex()
        if reindex_triggered:
            state["last_reindex_trigger"] = now

    state["last_check"] = now
    try:
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass

    if first_run:
        if inbox_count:
            if maintenance_triggered:
                print(
                    f"`_inbox/` has {inbox_count} file(s) — "
                    f"maintenance started automatically in the background."
                )
            else:
                print(
                    f"`_inbox/` has {inbox_count} file(s) waiting — "
                    f"consider `vault_inbox_status()` then `run_maintenance()`."
                )
        return

    if not new_notes and not inbox_count:
        return

    new_notes.sort(reverse=True)
    lines = ["## Vault activity since your last Claude Code session"]

    if new_notes:
        lines.append("")
        lines.append(
            "New or updated notes (may be from Claude Desktop/Cowork or other sessions "
            "via export_session/write_note):"
        )
        for mtime, f in new_notes[:MAX_NOTES]:
            rel = f.relative_to(vault)
            when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"- `{rel}` ({when})")
        if len(new_notes) > MAX_NOTES:
            lines.append(f"- ...and {len(new_notes) - MAX_NOTES} more")
        if reindex_triggered:
            lines.append("")
            lines.append("*(search index updating in background)*")

    if inbox_count:
        lines.append("")
        if maintenance_triggered:
            lines.append(
                f"`_inbox/` has {inbox_count} file(s) — maintenance started "
                f"automatically in the background (see `~/.delegation_core/maintenance.log` "
                f"or the weekly summary in `Sessions/`)."
            )
        else:
            lines.append(
                f"`_inbox/` has {inbox_count} file(s) waiting — maintenance ran "
                f"recently and is on cooldown, or `delegation-core maintain` is "
                f"unavailable. Use `vault_inbox_status()` then `run_maintenance()` "
                f"if it still needs attention."
            )

    print("\n".join(lines))


if __name__ == "__main__":
    main()
