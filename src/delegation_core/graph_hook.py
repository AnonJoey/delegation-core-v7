"""
graph_hook.py — git post-commit hook install/uninstall for auto-refreshing a
code graph after every commit.

Adapted from (not vendored verbatim) Graphify's hooks.py + watch.py. Those two
files together are ~2,200 lines built around Graphify's own uv-tool/pipx
distribution model: a shell script that probes PATH across several possible
install methods to find a working "graphify" interpreter, plus a hardened
rebuild function with cross-repo file-locking and a changed-paths queue/drain
loop. delegation-core doesn't have that distribution problem — it always runs
from one known venv (~/.delegation_core/venv, same as hooks/session_export.py
already assumes), so the hook script here just calls that interpreter directly
instead of porting the PATH-detection dance. The lock/queue/drain sophistication
in watch.py is also not ported: this uses a single non-blocking lock file (skip
a rebuild if one is already running) rather than queuing and merging changed
paths across overlapping commits — simpler, and sufficient for a per-commit
refresh where the next commit's rebuild will just pick up everything anyway.

The hook it installs never touches the vault (file_to_vault=False in
graphbridge.build_graph) — it only keeps cfg.graphs_dir/<name>/'s on-disk
artifacts fresh. Filing a report into the vault stays a deliberate graph_build()
MCP tool call.

New in v0.7.1.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

_HOOK_MARKER = "# delegation-core-graph-hook-start"
_HOOK_MARKER_END = "# delegation-core-graph-hook-end"

_VENV_DIR = Path.home() / ".delegation_core" / "venv"
_VENV_PYTHON = (
    _VENV_DIR / "Scripts" / "python.exe"
    if platform.system() == "Windows"
    else _VENV_DIR / "bin" / "python3"
)


def _git_root(path: Path) -> Path | None:
    """Walk up to find the repo containing .git (dir or file, for worktrees)."""
    current = path.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _hooks_dir(root: Path) -> Path:
    """Return the git hooks directory, respecting core.hooksPath if set (e.g. Husky).

    Asks git itself via `rev-parse --git-path hooks` rather than parsing
    .git/config directly — git allows duplicate keys/sections that a strict
    parser would reject, and this also correctly resolves linked worktrees
    (where .git is a file, not a directory).
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            hooks_path = Path(res.stdout.strip())
            return hooks_path if hooks_path.is_absolute() else root / hooks_path
    except OSError:
        pass
    return root / ".git" / "hooks"


def _hook_script(repo_root: Path, graph_name: str | None) -> str:
    name_arg = f' "{graph_name}"' if graph_name else ""
    log_path = Path.home() / ".delegation_core" / "graph_rebuild.log"
    python = str(_VENV_PYTHON)
    return (
        f"{_HOOK_MARKER}\n"
        f'if [ -x "{python}" ]; then\n'
        f'  ( "{python}" -m delegation_core.graph_hook_rebuild "{repo_root}"{name_arg} '
        f'>> "{log_path}" 2>&1 & ) >/dev/null 2>&1 &\n'
        f"fi\n"
        f"{_HOOK_MARKER_END}\n"
    )


def install(path: str | Path = ".", name: str | None = None) -> dict:
    """Install a post-commit hook that rebuilds the code graph (in the
    background, code-only, no LLM, no vault filing) after every commit.
    """
    root = _git_root(Path(path))
    if root is None:
        return {"error": f"No git repository found at or above {Path(path).resolve()}"}
    if not _VENV_PYTHON.exists():
        return {"error": f"delegation-core venv python not found at {_VENV_PYTHON}. "
                          "Run the [graph] extra install first."}

    hooks_dir = _hooks_dir(root)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"
    script = _hook_script(root, name)

    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8")
        if _HOOK_MARKER in content:
            return {"status": "already installed", "path": str(hook_path)}
        hook_path.write_text(content.rstrip() + "\n\n" + script, encoding="utf-8", newline="\n")
        return {"status": "appended to existing post-commit hook", "path": str(hook_path)}

    hook_path.write_text("#!/bin/sh\n" + script, encoding="utf-8", newline="\n")
    hook_path.chmod(0o755)
    return {"status": "installed", "path": str(hook_path)}


def uninstall(path: str | Path = ".") -> dict:
    """Remove the delegation-core graph post-commit hook (leaves any other
    content in post-commit untouched)."""
    root = _git_root(Path(path))
    if root is None:
        return {"error": f"No git repository found at or above {Path(path).resolve()}"}

    hook_path = _hooks_dir(root) / "post-commit"
    if not hook_path.exists():
        return {"status": "not installed"}

    content = hook_path.read_text(encoding="utf-8")
    if _HOOK_MARKER not in content:
        return {"status": "not installed"}

    start = content.index(_HOOK_MARKER)
    end = content.index(_HOOK_MARKER_END) + len(_HOOK_MARKER_END)
    remaining = (content[:start] + content[end:]).strip("\n")

    if remaining.strip() in ("", "#!/bin/sh"):
        hook_path.unlink()
        return {"status": "removed (hook file deleted — no other content)"}

    hook_path.write_text(remaining + "\n", encoding="utf-8", newline="\n")
    return {"status": "removed (other hook content preserved)", "path": str(hook_path)}


def status(path: str | Path = ".") -> dict:
    root = _git_root(Path(path))
    if root is None:
        return {"error": f"No git repository found at or above {Path(path).resolve()}"}
    hook_path = _hooks_dir(root) / "post-commit"
    if not hook_path.exists():
        return {"installed": False}
    return {"installed": _HOOK_MARKER in hook_path.read_text(encoding="utf-8"), "path": str(hook_path)}
