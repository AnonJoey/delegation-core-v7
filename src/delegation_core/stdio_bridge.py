"""
stdio_bridge.py — a stdio MCP server that forwards to the HTTP daemon.

Claude Desktop's `claude_desktop_config.json` validates stdio servers only: an
entry carrying `url` is not a valid server configuration to it. Worse than being
ignored, an entry with `url` makes Desktop rewrite that file on startup and drop
the WHOLE `mcpServers` section plus some `preferences` keys, silently. So a
config written for Claude Code, which does accept `type: http`, does not merely
fail to work in Desktop: it can take every other MCP server the user configured
down with it.

The two answers in circulation are registering a Custom Connector by hand, or
bridging with `npx mcp-remote`, which needs Node and spawns a process per
session. This module is the same bridge without either cost: the venv that is
already installed speaks MCP on both sides, so the hop is one Python process
that holds no state.

    Claude Desktop  --stdio-->  this bridge  --HTTP-->  the one daemon

What it is NOT is a second server. `delegation-core run` would start another
FastMCP with its own ChromaDB handle and its own BGE on the same GPU, which is
exactly the contention v0.11 removed by moving to a single daemon. The bridge
opens no index, loads no model, and owns no port: every call is forwarded.

If the daemon is not running there is nothing to forward to, and that is
reported as such rather than by starting one — a stdio client launching a
background daemon behind the user's back is how the two-servers problem starts.
"""

from __future__ import annotations

import logging
import sys

from .config import Config

logger = logging.getLogger("stdio_bridge")


def build_proxy(cfg: Config):
    """A FastMCP proxy whose backend is this machine's daemon.

    The Authorization header is the daemon's own token, read from config. It
    never reaches the client: Desktop talks stdio to this process and this
    process talks HTTP to loopback, so the token stays on the machine and out of
    `claude_desktop_config.json`, which is the other reason not to write an HTTP
    entry there.
    """
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.server import create_proxy

    transporte = StreamableHttpTransport(
        url=cfg.server_url,
        headers={"Authorization": f"Bearer {cfg.server_token}"},
    )
    return create_proxy(Client(transporte))


def run(cfg: Config | None = None) -> int:
    """Serve MCP on stdio, forwarding everything to the daemon.

    Returns non-zero without serving when the daemon is not answering. A bridge
    that starts up against nothing would present Desktop with a server that
    connects and then fails every call, which reads to the user as "the tools
    are broken" rather than "the service is down".
    """
    cfg = cfg or Config.load()

    from . import service
    if not service.is_up():
        sys.stderr.write(
            "delegation-core: the daemon is not answering on "
            f"{cfg.server_url}.\n"
            "  Start it with:  delegation-core service install\n"
            "  Or check it:    delegation-core status\n"
        )
        return 1

    logger.info("stdio bridge forwarding to %s", cfg.server_url)
    # show_banner=False: on a stdio server every byte of stdout is protocol.
    # Verified that FastMCP writes its banner to stderr, so the handshake is not
    # actually corrupted by it, but Desktop captures that stream into its own
    # log and an ASCII-art panel per launch is noise in the one place someone
    # looks when a connection misbehaves.
    build_proxy(cfg).run(show_banner=False)
    return 0
