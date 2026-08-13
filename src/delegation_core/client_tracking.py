"""
client_tracking.py — records which MCP client(s) are currently connected to this
delegation-core process, for the Tauri dashboard's "Connected Clients" panel.

Since v0.11 delegation-core is a single HTTP daemon and every client (Claude Code,
Claude Desktop, Codex, Antigravity, whatever else speaks MCP) connects to the same
process. Before that it ran over stdio, so each client spawned its own
`delegation-core run` and the heartbeat file was keyed by **pid** — one file per
process was one file per client, and the two were the same thing.

That identity no longer holds: one pid now serves N clients, and pid-keyed files
would have every client overwriting the previous one's name, leaving
list_mcp_clients() reporting whoever called most recently as the only client
connected. Files are therefore keyed by **MCP session id**
(~/.delegation_core/sessions/<session>.json), which is one per connected client
for exactly as long as that client stays connected.

The pid is still recorded inside the file, because it is still what tells a
*dead* daemon's leftovers apart from a live one's sessions. A file with a stale
last_seen (no update in SESSION_STALE_SECONDS) is treated as disconnected — that
covers clients that vanish without closing, which over HTTP is the common case.

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
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger("client_tracking")

SESSIONS_DIR = Path.home() / ".delegation_core" / "sessions"
SESSION_STALE_SECONDS = 120


#: Session ids arrive from the wire, so they are sanitised before becoming a
#: filename — an id containing "../" would otherwise write outside SESSIONS_DIR.
_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_key(session_id: str) -> str:
    return _SAFE_KEY_RE.sub("_", session_id)[:120] or "unknown"


def _session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{_safe_key(session_id)}.json"


def cleanup_own_session_file() -> None:
    """Best-effort removal of every heartbeat file this process owns.

    Called from the same atexit hook server.py already registers for engine
    cleanup. Under stdio this deleted one file, because the process *was* the
    session. The daemon owns one file per connected client, so it clears all of
    the ones carrying its pid and leaves other daemons' files alone.
    """
    try:
        my_pid = os.getpid()
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("pid") == my_pid:
                    f.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass


class ClientTrackingMiddleware(Middleware):
    """Writes/updates ~/.delegation_core/sessions/<session>.json on every message.

    One middleware instance serves every client on the daemon, so all per-client
    state is keyed by session id rather than held as a scalar on self.

    Best-effort throughout: a tracking failure must never break the actual
    request it's piggybacking on.
    """

    def __init__(self):
        self._pid = os.getpid()
        # session id -> tool call count. Guarded because the HTTP transport can
        # dispatch concurrent requests from different sessions, which the stdio
        # transport never did.
        self._tool_calls: dict[str, int] = {}
        self._lock = threading.Lock()

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

        session_id = getattr(fctx, "session_id", None)
        if not session_id:
            # Nothing sane to key on. Recording under a shared fallback would
            # merge unrelated clients into one row, which is the failure this
            # module was rewritten to avoid — so skip instead.
            logger.debug("No session id on this message; not recording a client row")
            return

        with self._lock:
            if context.method == "tools/call":
                self._tool_calls[session_id] = self._tool_calls.get(session_id, 0) + 1
            calls = self._tool_calls.get(session_id, 0)

        now = datetime.now(timezone.utc).isoformat()
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = _session_path(session_id)

        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        data = {
            "pid": self._pid,
            "session_id": session_id,
            "client_name": getattr(client_info, "name", None),
            "client_version": getattr(client_info, "version", None),
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
            "tool_calls": calls,
        }
        # Unique temp name: two sessions writing concurrently would otherwise
        # race on a single "<key>.tmp" and one could rename the other's partial
        # file into place.
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)


def list_connected_clients() -> list[dict]:
    """Read all session heartbeat files, dropping stale ones and deleting files
    whose PIDs no longer exist in the system process table. Used by
    dashboard_api.py's /api/clients endpoint.
    """
    import psutil
    if not SESSIONS_DIR.exists():
        return []
    now = datetime.now(timezone.utc)
    clients = []
    is_real_dir = SESSIONS_DIR == (Path.home() / ".delegation_core" / "sessions")

    for f in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid = data.get("pid")

            # In real production sessions directory, delete files for dead processes
            if is_real_dir and pid and not psutil.pid_exists(pid):
                f.unlink(missing_ok=True)
                continue

            last_seen = datetime.fromisoformat(data["last_seen"])
            age = (now - last_seen).total_seconds()
            if age > SESSION_STALE_SECONDS:
                # Deleted, not just skipped. The pid check above only clears
                # files left by a *dead* daemon; a stale session belonging to
                # the live one used to sit here forever. That was invisible
                # while clients were long-lived editors, but since v0.11 the
                # CLI connects for each reindex/maintain/ingest — including
                # every hook-triggered one — so a skipped-but-kept file is a
                # file per invocation, several a day, accumulating with no
                # reader. A stale row is by definition not a connected client.
                if is_real_dir:
                    f.unlink(missing_ok=True)
                continue
            data["seconds_since_active"] = round(age)
            clients.append(data)
        except Exception:
            continue
    return sorted(clients, key=lambda c: c.get("last_seen", ""), reverse=True)


def current_client_name(default: str = "unknown") -> str:
    """Name of the client whose call is being handled, from inside a tool.

    The middleware above records this per session for the dashboard; a tool that
    wants to *attribute* something needs the same fact at call time. With one
    client connected this was not worth asking — with several agents sharing one
    task line, "who queued this" is the first question anyone asks about a task.

    Best-effort by design: no client info (a direct call, a test, a handshake
    that omitted clientInfo) returns the default rather than raising, because
    failing to name the submitter is not a reason to refuse the work.
    """
    try:
        from fastmcp.server.dependencies import get_context

        ctx = get_context()
        session = getattr(ctx, "session", None)
        params = getattr(session, "client_params", None) if session else None
        info = getattr(params, "clientInfo", None) if params else None
        name = getattr(info, "name", None)
        return name or default
    except Exception:
        return default
