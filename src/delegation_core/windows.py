"""
windows.py — MCP window and workspace management.

delegation-core does **not** connect to other MCP servers, does not proxy their
tools, and does not route calls between them. It is an auxiliary to the LLM, and
every interaction is performed by the LLM through its own client.

What this module does is curate *which servers the client has mounted*, using the
model Obsidian uses for its panes (see `.obsidian/workspace.json`): a `leaf` is one
mounted thing, `tabs` stack several with one active, and a saved workspace restores
a whole arrangement by name.

The property worth borrowing is that a leaf in a background tab **exists but is not
rendered**. Translated to MCP: a server that is registered but not in the active
workspace is configured and dormant — its tool schemas cost no context. That, not
routing, is what makes "thirty MCP connections" manageable.

Mechanism: the client's mounted set lives in ~/.claude.json under `mcpServers`.
Opening and closing windows rewrites that key; the change takes effect when the
client reconnects. There is deliberately no runtime path — switching workspaces is
an explicit act, not something that happens underneath a running session.

Two invariants, both enforced here rather than left to callers:
  1. delegation-core is never removed from the client config. Removing it would
     sever the very channel these tools are called through.
  2. Entries this module does not manage are preserved untouched. The file belongs
     to the client, not to delegation-core — every write is surgical.
"""

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("windows")

SELF = "delegation-core"
CLIENT_CONFIG = Path.home() / ".claude.json"
REGISTRY = Path.home() / ".delegation_core" / "mcp_workspaces.json"


def _empty_registry() -> dict:
    return {"servers": {}, "workspaces": {}, "active": None}


def load_registry() -> dict:
    """Catalogue of known servers and named workspaces. Never raises on a corrupt
    file — a broken registry must not take the tools down with it."""
    if not REGISTRY.exists():
        return _empty_registry()
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("registry unreadable (%s) — starting from empty", e)
        return _empty_registry()
    base = _empty_registry()
    base.update({k: v for k, v in data.items() if k in base})
    return base


def save_registry(reg: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(REGISTRY, json.dumps(reg, indent=2, ensure_ascii=False))


def _atomic_write(path: Path, text: str) -> None:
    """Write via a sibling temp file then rename. A partial write to ~/.claude.json
    would leave the client unable to start, so this is not optional."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_client_config() -> dict:
    if not CLIENT_CONFIG.exists():
        return {}
    return json.loads(CLIENT_CONFIG.read_text(encoding="utf-8"))


def write_client_config(cfg: dict) -> None:
    """Persist the client config, keeping a one-shot backup the first time."""
    backup = CLIENT_CONFIG.with_suffix(".json.dc-backup")
    if CLIENT_CONFIG.exists() and not backup.exists():
        shutil.copy2(CLIENT_CONFIG, backup)
    _atomic_write(CLIENT_CONFIG, json.dumps(cfg, indent=2, ensure_ascii=False))


def _mounted(cfg: dict) -> dict:
    return cfg.get("mcpServers") or {}


def sync_registry_from_client() -> dict:
    """Catalogue whatever the client currently has mounted.

    Without this, closing a window would lose whatever the client needs to reopen
    it. Called before every mutation so a server configured by hand is never
    dropped.

    The spec is stored and restored verbatim, without inspecting its shape — so
    stdio entries ({command, args}) and HTTP entries ({type, url, headers}) both
    round-trip. That matters since v0.11, where delegation-core itself moved to
    HTTP and other servers in a user's config may be either kind.
    """
    reg = load_registry()
    for name, spec in _mounted(read_client_config()).items():
        if name != SELF:
            reg["servers"][name] = spec
    save_registry(reg)
    return reg


def list_windows() -> dict:
    """Registered servers, which are currently mounted, and the active workspace."""
    reg = sync_registry_from_client()
    mounted = set(_mounted(read_client_config()))
    known = set(reg["servers"]) | mounted
    windows = [
        {"name": n, "mounted": n in mounted, "managed": n != SELF}
        for n in sorted(known)
    ]
    return {
        "windows": windows,
        "active_workspace": reg.get("active"),
        "workspaces": sorted(reg["workspaces"]),
        "note": "Changes to mounted windows take effect when the client reconnects.",
    }


def open_window(name: str) -> dict:
    """Mount a registered server into the client config."""
    reg = sync_registry_from_client()
    if name == SELF:
        return {"error": f"'{SELF}' is always mounted and is not managed as a window."}
    spec = reg["servers"].get(name)
    if not spec:
        return {"error": f"Unknown server '{name}'. Known: {sorted(reg['servers'])}"}

    cfg = read_client_config()
    servers = dict(_mounted(cfg))
    if name in servers:
        return {"status": "already_open", "window": name}
    servers[name] = spec
    cfg["mcpServers"] = servers
    write_client_config(cfg)
    return {"status": "opened", "window": name, "reconnect_required": True}


def close_window(name: str) -> dict:
    """Unmount a server, keeping its definition in the registry so it can return."""
    if name == SELF:
        return {"error": f"Refusing to close '{SELF}' — it is the channel these tools arrive on."}
    reg = sync_registry_from_client()

    cfg = read_client_config()
    servers = dict(_mounted(cfg))
    if name not in servers:
        return {"status": "already_closed", "window": name}
    reg["servers"][name] = servers.pop(name)   # remember before removing
    save_registry(reg)
    cfg["mcpServers"] = servers
    write_client_config(cfg)
    return {"status": "closed", "window": name, "reconnect_required": True}


def list_workspaces() -> dict:
    reg = load_registry()
    return {
        "workspaces": {n: sorted(m) for n, m in reg["workspaces"].items()},
        "active": reg.get("active"),
    }


def save_workspace(name: str) -> dict:
    """Snapshot the currently mounted set under a name."""
    if not name or not name.strip():
        return {"error": "Workspace name is required."}
    name = name.strip()
    reg = sync_registry_from_client()
    members = sorted(n for n in _mounted(read_client_config()) if n != SELF)
    reg["workspaces"][name] = members
    reg["active"] = name
    save_registry(reg)
    return {"status": "saved", "workspace": name, "windows": members}


def apply_workspace(name: str) -> dict:
    """Make the client's top-level mounted set match a named workspace.

    Servers outside the workspace are unmounted, exactly as applying a saved layout
    in Obsidian closes the panes that layout does not contain. Nothing is lost:
    every mounted server is catalogued before the rewrite, so an unmounted one keeps
    its command/args and can be reopened by name.

    Scope is deliberately narrow. Only the **top-level** `mcpServers` key is
    rewritten — per-project server definitions under `projects` belong to the
    client's own project handling and are never read or modified here.
    """
    reg = sync_registry_from_client()   # catalogue first, so nothing becomes unrecoverable
    if name not in reg["workspaces"]:
        return {"error": f"Unknown workspace '{name}'. Known: {sorted(reg['workspaces'])}"}

    wanted = set(reg["workspaces"][name])
    missing = sorted(w for w in wanted if w not in reg["servers"])
    if missing:
        return {"error": f"Workspace '{name}' references unknown servers: {missing}"}

    cfg = read_client_config()
    current = dict(_mounted(cfg))

    new = {SELF: current[SELF]} if SELF in current else {}
    for n in sorted(wanted):
        new[n] = reg["servers"][n]

    unmounted = sorted(n for n in current if n != SELF and n not in new)

    cfg["mcpServers"] = new
    write_client_config(cfg)
    reg["active"] = name
    save_registry(reg)

    return {
        "status": "applied",
        "workspace": name,
        "mounted": sorted(new),
        "unmounted": unmounted,
        "note": "Unmounted servers keep their definition and can be reopened by name.",
        "reconnect_required": True,
    }
