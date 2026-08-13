"""
clients.py — point MCP clients at the HTTP daemon.

v0.11 replaced stdio with a single HTTP daemon, and that is a breaking change for
every client already configured: a `{"command": ..., "args": ["run"]}` entry now
spawns a *second* server that will fight the daemon for the port, the ChromaDB
index, and the GPU. There is no version of "it keeps working by accident", so the
migration is explicit and this module is what performs it.

Two clients are handled directly because they are the two on this machine.
Everything else is covered by `entry_summary()`, which just describes the URL and
token so a user can paste them wherever their client wants them.

Claude Code (~/.claude.json, JSON):

    "delegation-core": {
      "type": "http",
      "url": "http://127.0.0.1:8787/mcp",
      "headers": {"Authorization": "Bearer <token>"}
    }

Codex (~/.codex/config.toml, TOML):

    [mcp_servers.delegation-core]
    url = "http://127.0.0.1:8787/mcp"
    bearer_token_env_var = "DELEGATION_CORE_TOKEN"

Codex reads the secret from an environment variable rather than the config file,
so migrating it also means telling the user to export DELEGATION_CORE_TOKEN. That
is Codex's design, not a choice made here.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .config import Config
from .windows import SELF, read_client_config, write_client_config

logger = logging.getLogger("clients")

CODEX_CONFIG = Path.home() / ".codex" / "config.toml"

#: Codex looks the bearer token up in the environment under this name.
CODEX_TOKEN_ENV_VAR = "DELEGATION_CORE_TOKEN"


def claude_code_entry(cfg: Config) -> dict:
    """The ~/.claude.json mcpServers value pointing at the daemon."""
    return {
        "type": "http",
        "url": cfg.server_url,
        "headers": {"Authorization": f"Bearer {cfg.server_token}"},
    }


def codex_block(cfg: Config) -> str:
    """The ~/.codex/config.toml table pointing at the daemon."""
    return (
        f"\n[mcp_servers.{SELF}]\n"
        f'url = "{cfg.server_url}"\n'
        f'bearer_token_env_var = "{CODEX_TOKEN_ENV_VAR}"\n'
        f"startup_timeout_sec = 30\n"
        f"tool_timeout_sec = 120\n"
    )


def install_claude_code(cfg: Config) -> dict:
    """Rewrite delegation-core's entry in ~/.claude.json to the HTTP form.

    Goes through windows.write_client_config so the invariants that module
    already guarantees still hold: one-shot backup before the first edit, atomic
    replace (a half-written ~/.claude.json leaves the client unable to start),
    and the `projects` key untouched.
    """
    client_cfg = read_client_config()
    servers = dict(client_cfg.get("mcpServers") or {})
    previous = servers.get(SELF)
    servers[SELF] = claude_code_entry(cfg)
    client_cfg["mcpServers"] = servers
    write_client_config(client_cfg)
    return {
        "client": "claude-code",
        "path": str(Path.home() / ".claude.json"),
        "replaced": previous,
        "status": "updated",
        "reconnect_required": True,
    }


def install_codex(cfg: Config) -> dict:
    """Append (or report) delegation-core's table in ~/.codex/config.toml.

    Deliberately append-only and refuses to edit an existing table. There is no
    TOML writer in the stdlib, so rewriting a table in place would mean
    hand-editing text around a parser that only reads — the failure mode is a
    corrupted config for a tool the user relies on. Reporting the block and
    letting them replace it is the honest option.
    """
    block = codex_block(cfg)
    if not CODEX_CONFIG.exists():
        CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CODEX_CONFIG.write_text(block.lstrip("\n"), encoding="utf-8")
        return {"client": "codex", "path": str(CODEX_CONFIG),
                "status": "created", "block": block,
                "env_var": CODEX_TOKEN_ENV_VAR}

    existing = CODEX_CONFIG.read_text(encoding="utf-8")
    if f"[mcp_servers.{SELF}]" in existing:
        return {"client": "codex", "path": str(CODEX_CONFIG),
                "status": "already_present",
                "block": block, "env_var": CODEX_TOKEN_ENV_VAR,
                "note": ("A [mcp_servers.delegation-core] table already exists. "
                         "Replace it by hand with the block above — this command "
                         "will not rewrite TOML it did not write.")}

    backup = CODEX_CONFIG.with_suffix(".toml.dc-backup")
    if not backup.exists():
        shutil.copy2(CODEX_CONFIG, backup)
    CODEX_CONFIG.write_text(existing.rstrip("\n") + "\n" + block, encoding="utf-8")
    return {"client": "codex", "path": str(CODEX_CONFIG), "status": "appended",
            "block": block, "env_var": CODEX_TOKEN_ENV_VAR}


def entry_summary(cfg: Config) -> dict:
    """Everything a client needs, for surfaces this module does not write."""
    return {
        "url": cfg.server_url,
        "authorization_header": f"Bearer {cfg.server_token}",
        "token_env_var": CODEX_TOKEN_ENV_VAR,
    }
