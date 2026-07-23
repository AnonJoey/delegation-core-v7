# delegation-core Dashboard

A native Tauri desktop app that accompanies the `delegation-core` MCP server: server
status, connected MCP clients (Claude Code, Claude Desktop, etc.), a notes browser, and
the vault rendered as an Obsidian-style force-directed wikilink graph.

## Architecture

This app has no application logic of its own — it's a thin Rust/webview shell around
delegation-core's own Python code:

- **`src-tauri/src/lib.rs`**: on startup, spawns `delegation_core.dashboard_api` (see
  `../src/delegation_core/dashboard_api.py`) as a local sidecar process from
  `~/.delegation_core/venv`, reads the port it picked from its stdout, and exposes that
  port to the frontend via the `get_api_port` Tauri command. Kills the sidecar when the
  window closes.
- **`src/`**: plain HTML/CSS/vanilla JS, no framework or bundler. Fetches everything
  from the sidecar's local JSON API (`http://127.0.0.1:<port>/api/...`). The vault graph
  is a small hand-written canvas force-directed layout (`ForceGraph` in `main.js`) — no
  external graph-rendering library, vendored or otherwise.

The dashboard never talks to the MCP server directly — that's a separate stdio
connection per client (Claude Code, Desktop, etc.), which is also why "connected
clients" works the way it does: each running `delegation-core run` instance writes its
own heartbeat file (`~/.delegation_core/sessions/<pid>.json`, via
`client_tracking.py`'s FastMCP middleware), and `/api/clients` aggregates whichever
ones are still fresh.

## Prerequisites

- delegation-core installed and configured (`delegation-core setup`) — this app spawns
  `~/.delegation_core/venv/bin/python3` directly, no separate Python install needed.
- Rust + Cargo, Node + npm (for the Tauri toolchain itself).

## Running

```bash
npm install
npm run tauri dev
```

**Linux/NVIDIA/Wayland note**: `lib.rs` sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` before
the webview is created — works around a WebKitGTK crash ("Error 71: Protocol error
dispatching to Wayland display") seen directly on an NVIDIA + KWin/Wayland dev machine.
Confirmed via A/B testing that the more commonly-suggested
`WEBKIT_DISABLE_COMPOSITING_MODE=1` also avoids the crash but breaks this app's flexbox
sidebar layout (forces a software-rendering fallback with different CSS behavior) — the
DMABUF-only fix avoids the crash without that regression. Harmless on setups that don't
need it.
