"""
dashboard_api.py — local, read-only JSON HTTP API for the Tauri dashboard.

Deliberately NOT part of the MCP server: FastMCP's mcp.run() serves one transport
at a time (stdio here), so it can't also serve HTTP for a UI in the same process.
This is a separate, small process — stdlib http.server only, no new dependency —
that the Tauri app's Rust backend spawns as a sidecar and talks to over
127.0.0.1. It reuses Config/VaultManager/graphbridge/client_tracking directly;
none of this goes through the MCP protocol.

Bound to 127.0.0.1 only (never a public interface) since it has no auth — it's
meant to be reached exclusively by the local Tauri webview.

Endpoints:
  GET /api/status                  vault/binary/model/llama.cpp health
  GET /api/clients                 currently-connected MCP client surfaces
  GET /api/vault/tree               folder -> notes listing
  GET /api/vault/note?path=...      one note's raw content
  GET /api/vault/search?q=...       BGE similarity search
  GET /api/vault/graph              {nodes, edges} from [[wikilinks]] across the vault
  GET /api/graphs                   previously built code graphs (graphbridge registry)

New in v0.8.0. Also runnable standalone (without Tauri) via:
  delegation-core dashboard-api [--port N]
"""

from __future__ import annotations

import json
import logging
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("dashboard_api")

_cfg = None    # set once in run() — Config
_vault = None  # set once in run() — VaultManager, shared across every request.
               # A fresh VaultManager per request (the original version of this
               # file did this) reloads the BGE model and re-opens ChromaDB on
               # every single API call — slow, and observed directly to fail
               # ("Vault not initialized") under repeated init. One shared
               # instance, initialized once at startup, matches server.py's
               # own _vault global pattern.


def _pick_port(preferred: int = 0) -> int:
    """Bind-test a port; 0 lets the OS assign a free one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", preferred))
        return s.getsockname()[1]


def _build_vault_graph(cfg) -> dict:
    """Parse every vault note for [[wikilinks]] and return {nodes, edges}.

    Dependency-free on purpose (no networkx) — the vault graph view shouldn't
    require the [graph] extra, which is for the separate *code* graph pipeline.
    Reuses linker.py's own wikilink regex (existing_targets) rather than
    re-deriving the [[stem|Display]]/[[stem#section]] parsing rules.
    """
    from .linker import existing_targets
    from .vault import yaml_unquote_scalar

    vault = cfg.vault
    notes: dict[str, dict] = {}   # lowercase stem -> {id, title, folder, path}
    contents: dict[str, str] = {}  # lowercase stem -> raw content (for link extraction)

    for folder in cfg.vault_folders:
        folder_path = vault / folder
        if not folder_path.exists():
            continue
        for f in folder_path.rglob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue
            title = f.stem
            if content.startswith("---\n"):
                close = content.find("\n---\n", 4)
                if close != -1:
                    for line in content[4:close].splitlines():
                        if line.startswith("title:"):
                            title = yaml_unquote_scalar(line.split(":", 1)[1])
                            break
            rel = str(f.relative_to(vault))
            key = f.stem.lower()
            notes[key] = {"id": key, "title": title, "folder": folder, "path": rel}
            contents[key] = content

    edges = []
    seen_edges = set()
    for key, content in contents.items():
        for target in existing_targets(content):
            target_key = target.strip().lower()
            if target_key == key or target_key not in notes:
                continue  # skip self-links and dangling links (not built this pass)
            edge = (key, target_key)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            edges.append({"source": key, "target": target_key})

    return {"nodes": list(notes.values()), "edges": edges}


def _status(cfg, vault) -> dict:
    """Mirrors cli.py's cmd_status checks.

    Reads the note count off the already-open shared `vault` (VaultManager.
    get_stats()) rather than opening a second chromadb.PersistentClient at the
    same path — two clients contending for the same ChromaDB/SQLite path is
    exactly what silently broke search/vault-tree before dashboard_api.py was
    switched to one shared VaultManager instance; opening yet another one here
    just for a count would reintroduce the same class of problem.
    """
    import requests

    vault_ok = Path(cfg.vault_path).exists() if cfg.vault_path else False
    binary_ok = Path(cfg.llama_binary).exists() if cfg.llama_binary else False
    model_ok = Path(cfg.llama_model).exists() if cfg.llama_model else False

    try:
        r = requests.get(f"{cfg.llama_url}/health", timeout=3)
        llama_state = "online" if r.status_code == 200 else "unhealthy"
    except Exception:
        llama_state = "offline"

    chroma_count = vault.get_stats().get("indexed_notes") if vault else None

    return {
        "configured": cfg.is_configured(),
        "vault_path": cfg.vault_path,
        "vault_ok": vault_ok,
        "llama_binary": cfg.llama_binary,
        "binary_ok": binary_ok,
        "llama_model": cfg.llama_model,
        "model_ok": model_ok,
        "vault_folders": cfg.vault_folders,
        "llama_url": cfg.llama_url,
        "llama_state": llama_state,
        "chroma_indexed_notes": chroma_count,
        "budget_mode": cfg.budget_mode,
        "engine_mode": cfg.engine_mode,
        "synthesis_enabled": cfg.synthesis_enabled,
    }


class _Handler(BaseHTTPRequestHandler):
    # Quiets the default per-request stderr access log; dashboard_api is meant
    # to run silently as a sidecar. Uncomment for debugging.
    def log_message(self, format, *args):
        pass

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        try:
            if parsed.path == "/api/status":
                self._send_json(_status(_cfg, _vault))
            elif parsed.path == "/api/clients":
                from .client_tracking import list_connected_clients
                self._send_json({"clients": list_connected_clients()})
            elif parsed.path == "/api/vault/tree":
                self._handle_vault_tree()
            elif parsed.path == "/api/vault/note":
                self._handle_vault_note(query)
            elif parsed.path == "/api/vault/search":
                self._handle_vault_search(query)
            elif parsed.path == "/api/vault/graph":
                self._send_json(_build_vault_graph(_cfg))
            elif parsed.path == "/api/graphs":
                from . import graphbridge
                self._send_json(graphbridge.list_graphs(_cfg))
            else:
                self._send_json({"error": f"not found: {parsed.path}"}, status=404)
        except Exception as e:
            logger.exception("Request failed: %s", parsed.path)
            self._send_json({"error": str(e)}, status=500)

    def _handle_vault_tree(self) -> None:
        tree = {folder: _vault.list_notes(folder, limit=1000) for folder in _cfg.vault_folders}
        self._send_json({"folders": tree})

    def _handle_vault_note(self, query) -> None:
        rel_path = (query.get("path") or [""])[0]
        if not rel_path:
            self._send_json({"error": "missing path parameter"}, status=400)
            return
        vault_root = _cfg.vault.resolve()
        target = (vault_root / rel_path).resolve()
        if not str(target).startswith(str(vault_root)):
            self._send_json({"error": "path escapes vault root"}, status=400)
            return
        if not target.exists():
            self._send_json({"error": f"not found: {rel_path}"}, status=404)
            return
        self._send_json({"path": rel_path, "content": target.read_text(encoding="utf-8")})

    def _handle_vault_search(self, query) -> None:
        q = (query.get("q") or [""])[0]
        limit = int((query.get("limit") or ["5"])[0])
        if not q:
            self._send_json({"error": "missing q parameter"}, status=400)
            return
        self._send_json({"query": q, "results": _vault.search(q, limit=limit)})


def run(port: int = 0, host: str = "127.0.0.1") -> None:
    import os
    from .config import Config

    # Matches cli.py's cmd_run(): use cached model weights only, no live HF Hub
    # version-check chatter on every startup (the model is already on disk).
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stderr)

    global _cfg, _vault
    _cfg = Config.load()
    if not _cfg.is_configured():
        sys.stderr.write("delegation-core is not configured.\nRun: delegation-core setup\n")
        sys.exit(1)

    from .vault import VaultManager
    _vault = VaultManager(_cfg)
    _vault._init()  # blocking — BGE + ChromaDB ready before serving, once, not per-request

    actual_port = _pick_port(port)
    server = ThreadingHTTPServer((host, actual_port), _Handler)
    # Printed as the first line so a spawning parent (the Tauri sidecar) can
    # read it off stdout to learn which port got assigned when port=0.
    print(f"dashboard_api listening on http://{host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="delegation-core dashboard API (local sidecar)")
    parser.add_argument("--port", type=int, default=0, help="Port to bind (0 = pick a free one)")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    run(port=args.port, host=args.host)
