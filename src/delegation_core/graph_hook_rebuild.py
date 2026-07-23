"""
graph_hook_rebuild.py — entry point invoked by the git post-commit hook
installed by graph_hook.py: `python -m delegation_core.graph_hook_rebuild <repo_root> [name]`.

Runs detached in the background (see graph_hook.py's hook script). Rebuilds the
on-disk graph artifacts only (file_to_vault=False) — no LLM, no vault writes,
no maintenance pass. A simple non-blocking lock file skips a rebuild if one is
already running for this graph rather than piling up (Graphify's watch.py has
a much more elaborate queue/drain mechanism for the same problem; skipping is
an acceptable simplification here since the *next* commit's rebuild covers
everything anyway).
"""

from __future__ import annotations

import asyncio
import os
import sys


def _apply_resource_limits() -> None:
    """Best-effort nice + memory cap so a background rebuild doesn't starve
    whatever else is running (e.g. an editor, the MCP server itself).
    Silently skips whatever the platform doesn't support.
    """
    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass
    mb = os.environ.get("DELEGATION_CORE_GRAPH_REBUILD_MEMORY_LIMIT_MB", "").strip()
    if not mb:
        return
    try:
        limit = int(mb) * 1024 * 1024
    except ValueError:
        return
    try:
        import resource
        which = resource.RLIMIT_DATA if sys.platform == "darwin" else resource.RLIMIT_AS
        soft, hard = resource.getrlimit(which)
        new_hard = hard if hard != resource.RLIM_INFINITY and hard < limit else limit
        resource.setrlimit(which, (limit, new_hard))
    except (ImportError, ValueError, OSError):
        pass


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: python -m delegation_core.graph_hook_rebuild <repo_root> [name]\n")
        return 2

    repo_root = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None

    _apply_resource_limits()

    from .config import Config
    from . import graphbridge

    cfg = Config.load()
    graph_name = graphbridge._slugify(name or __import__("pathlib").Path(repo_root).resolve().name)
    lock_path = cfg.graphs_dir / graph_name / ".rebuild.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        sys.stderr.write(f"graph_hook_rebuild: rebuild already in progress for '{graph_name}' — skipping\n")
        return 0

    try:
        os.write(lock_fd, str(os.getpid()).encode())
        os.close(lock_fd)
        result = asyncio.run(
            graphbridge.build_graph(cfg, None, None, repo_root, name=name, force=True, file_to_vault=False)
        )
        if "error" in result:
            sys.stderr.write(f"graph_hook_rebuild: {result['error']}\n")
            return 1
        sys.stderr.write(
            f"graph_hook_rebuild: rebuilt '{result['name']}' — "
            f"{result['node_count']} nodes, {result['edge_count']} edges\n"
        )
        return 0
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
