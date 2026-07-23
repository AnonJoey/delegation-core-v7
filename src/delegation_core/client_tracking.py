"""
client_tracking.py — records which MCP client(s) are currently connected to this
delegation-core process, for the Tauri dashboard's "Connected Clients" panel.

delegation-core runs over MCP stdio, so each client (Claude Code, Claude Desktop,
Codex, Antigravity, whatever else speaks MCP) that has it configured launches its
own separate `delegation-core run` process — there is no single shared connection
to introspect from one place. Instead, each running instance writes its own small
heartbeat file to ~/.delegation_core/sessions/<pid>.json, and the dashboard (via
dashboard_api.py) aggregates whichever files are still fresh. A file with a stale
last_seen (no update in SESSION_STALE_SECONDS) is treated as disconnected — this
covers unclean shutdowns/kills, not just the atexit cleanup path.

The client's identity comes from the MCP initialize handshake (clientInfo: name +
version), which the underlying MCP SDK stores on the session
(mcp/server/session.py: ServerSession._client_params.clientInfo). FastMCP's
Middleware.on_message hook fires for every message (initialize, tool calls,
everything) and exposes that session via context.fastmcp_context, so this updates
the heartbeat file on every request rather than only at session start — a session
that's actively calling tools stays "connected" even if the agent never re-calls
heartbeat().

New in v0.8.0.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger("client_tracking")

SESSIONS_DIR = Path.home() / ".delegation_core" / "sessions"
SESSION_STALE_SECONDS = 120


def _session_path(pid: int) -> Path:
    return SESSIONS_DIR / f"{pid}.json"


def cleanup_own_session_file() -> None:
    """Best-effort removal of this process's own heartbeat file. Called from the
    same atexit hook server.py already registers for engine cleanup."""
    try:
        _session_path(os.getpid()).unlink(missing_ok=True)
    except Exception:
        pass


class ClientTrackingMiddleware(Middleware):
    """Writes/updates ~/.delegation_core/sessions/<pid>.json on every message.

    Best-effort throughout: a tracking failure must never break the actual
    request it's piggybacking on.
    """

    def __init__(self):
        self._pid = os.getpid()
        self._path = _session_path(self._pid)
        self._tool_calls = 0

    async def on_message(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        try:
            self._record(context)
        except Exception as e:
            logger.debug("Client tracking skipped for this message: %s", e)
        return result

    def _record(self, context: MiddlewareContext) -> None:
        fctx = context.fastmcp_context
        if fctx is None:
            return
        session = getattr(fctx, "session", None)
        params = getattr(session, "client_params", None) if session else None
        client_info = getattr(params, "clientInfo", None) if params else None
        if client_info is None:
            return

        if context.method == "tools/call":
            self._tool_calls += 1

        now = datetime.now(timezone.utc).isoformat()
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        existing = {}
        if self._path.exists():
            try:
                existing = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        data = {
            "pid": self._pid,
            "client_name": getattr(client_info, "name", None),
            "client_version": getattr(client_info, "version", None),
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
            "tool_calls": self._tool_calls,
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._path)


def list_connected_clients() -> list[dict]:
    """Read all session heartbeat files, dropping stale ones. Used by
    dashboard_api.py's /api/clients endpoint."""
    if not SESSIONS_DIR.exists():
        return []
    now = datetime.now(timezone.utc)
    clients = []
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            last_seen = datetime.fromisoformat(data["last_seen"])
            age = (now - last_seen).total_seconds()
            if age > SESSION_STALE_SECONDS:
                continue
            data["seconds_since_active"] = round(age)
            clients.append(data)
        except Exception:
            continue
    return sorted(clients, key=lambda c: c.get("last_seen", ""), reverse=True)
