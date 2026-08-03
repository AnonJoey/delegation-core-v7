"""
doctor.py — Installation drift and vault hygiene checks.

Every check here exists because the condition it looks for went unnoticed on a
live machine, sometimes for weeks:

* The SessionEnd hook installed in ~/.delegation_core/hooks/ had fallen behind
  the repo copy, so a fix shipped in the repo was simply not running.
* That stale hook wrote transcripts to a hardcoded lowercase ``sessions/`` beside
  the vault's configured ``Sessions/``. Indexing, search and health accounting
  all iterate ``vault_folders``, so 29 transcripts spanning seven weeks were
  invisible — no error anywhere, just absence.
* The [graph] extra was never installed in the live venv, so graph_build failed
  on a missing import rather than on anything to do with graphs.
* ``ingest_folder`` registry entries outlive the folders they point at, leaving
  rows that answer searches with paths that no longer resolve.

None of these surface through normal use. Each returns a status of ok | warn |
error plus a one-line fix.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".delegation_core"
INSTALLED_HOOKS = CONFIG_DIR / "hooks"

#: Folders delegation-core creates itself; a case-variant sibling of a
#: configured folder is a bug, but these are not.
_VAULT_INTERNAL = {"_inbox", "_processed", "_failed", "_archive"}


def _repo_hooks_dir() -> Path | None:
    """Locate the packaged hooks/ directory, if this is an editable/source install."""
    candidate = Path(__file__).resolve().parents[2] / "hooks"
    return candidate if candidate.is_dir() else None


def check_hook_drift() -> dict:
    repo = _repo_hooks_dir()
    if repo is None:
        return {"check": "hook_drift", "status": "skip",
                "detail": "packaged hooks/ not found (installed from a wheel, not a source tree)"}
    if not INSTALLED_HOOKS.is_dir():
        return {"check": "hook_drift", "status": "warn",
                "detail": f"no hooks installed at {INSTALLED_HOOKS}",
                "fix": "run install.sh (or copy hooks/ there) to enable session export/brief"}

    stale, missing = [], []
    for src in sorted(repo.glob("*.py")):
        dst = INSTALLED_HOOKS / src.name
        if not dst.exists():
            missing.append(src.name)
        elif src.read_bytes() != dst.read_bytes():
            stale.append(src.name)

    if not stale and not missing:
        return {"check": "hook_drift", "status": "ok",
                "detail": f"{len(list(repo.glob('*.py')))} hook(s) match the source tree"}
    return {
        "check": "hook_drift", "status": "warn",
        "detail": f"stale: {stale or '—'} · missing: {missing or '—'}",
        "fix": f"cp {repo}/*.py {INSTALLED_HOOKS}/",
    }


def check_vault_folders(cfg) -> dict:
    """Configured folders must exist, and no case-variant sibling may shadow one."""
    vault = cfg.vault
    if not vault.is_dir():
        return {"check": "vault_folders", "status": "error",
                "detail": f"vault path does not exist: {vault}",
                "fix": "delegation-core setup"}

    configured = {f: (vault / f) for f in cfg.vault_folders}
    missing = [f for f, p in configured.items() if not p.is_dir()]

    lowered = {f.lower(): f for f in cfg.vault_folders}
    shadows = []
    for child in sorted(p for p in vault.iterdir() if p.is_dir()):
        if child.name.startswith(".") or child.name in _VAULT_INTERNAL:
            continue
        canonical = lowered.get(child.name.lower())
        if canonical and child.name != canonical:
            n = len(list(child.rglob("*.md")))
            shadows.append(f"{child.name}/ shadows {canonical}/ ({n} note(s) invisible to search)")

    if shadows:
        return {"check": "vault_folders", "status": "error",
                "detail": "; ".join(shadows),
                "fix": "move the notes into the configured folder and remove the stray directory"}
    if missing:
        return {"check": "vault_folders", "status": "warn",
                "detail": f"configured but absent: {missing}",
                "fix": "they are created on first write; remove them from vault_folders if unwanted"}
    return {"check": "vault_folders", "status": "ok",
            "detail": f"{len(configured)} folder(s) present, no case-variant shadows"}


def check_graph_extra() -> dict:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except Exception as e:
        return {"check": "graph_extra", "status": "warn",
                "detail": f"code-graph pipeline unavailable ({type(e).__name__}: {e})",
                "fix": 'pip install "delegation-core[graph]"'}
    return {"check": "graph_extra", "status": "ok", "detail": "tree-sitter parsers importable"}


def check_engine_mode(cfg) -> dict:
    if cfg.is_agent_mode:
        return {"check": "engine_mode", "status": "ok",
                "detail": "agent — no local model is loaded; generation is delegated"}
    missing = []
    if not Path(cfg.llama_binary).exists():
        missing.append(f"llama_binary {cfg.llama_binary}")
    if not Path(cfg.llama_model).exists():
        missing.append(f"llama_model {cfg.llama_model}")
    if missing:
        return {"check": "engine_mode", "status": "error",
                "detail": f"engine_mode={cfg.engine_mode} but missing: {', '.join(missing)}",
                "fix": 'set engine_mode to "agent", or fix the paths in config.json'}
    return {"check": "engine_mode", "status": "ok",
            "detail": f"{cfg.engine_mode} — binary and model present"}


def check_ingest_registry() -> dict:
    reg_path = CONFIG_DIR / "ingested_sources.json"
    if not reg_path.exists():
        return {"check": "ingest_registry", "status": "ok", "detail": "no external folders ingested"}
    try:
        registry = json.loads(reg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"check": "ingest_registry", "status": "warn",
                "detail": f"registry unreadable: {e}", "fix": f"delete {reg_path}"}
    gone = [p for p in registry if not Path(p).exists()]
    if gone:
        return {"check": "ingest_registry", "status": "warn",
                "detail": f"{len(gone)} ingested path(s) no longer exist: {gone[:3]}",
                "fix": "ingest_forget(<path>) to drop their rows from the index"}
    return {"check": "ingest_registry", "status": "ok",
            "detail": f"{len(registry)} ingested path(s), all still present"}


def check_graph_registry(cfg) -> dict:
    reg = cfg.graphs_registry_path
    if not reg.exists():
        return {"check": "graph_registry", "status": "ok", "detail": "no graphs built"}
    try:
        registry = json.loads(reg.read_text(encoding="utf-8"))
    except Exception as e:
        return {"check": "graph_registry", "status": "warn",
                "detail": f"registry unreadable: {e}", "fix": f"delete {reg}"}
    gone = [n for n, e in registry.items() if not Path(e.get("source_path", "")).exists()]
    untracked = [n for n, e in registry.items() if e.get("node_count") and "vault_paths" not in e]
    if gone or untracked:
        bits = []
        if gone:
            bits.append(f"source gone: {gone}")
        if untracked:
            bits.append(f"built before vault_paths tracking (a rebuild cannot clean them): {untracked}")
        return {"check": "graph_registry", "status": "warn", "detail": "; ".join(bits),
                "fix": "graph_build(..., force=true) to refile, or delete the stale entry"}
    return {"check": "graph_registry", "status": "ok",
            "detail": f"{len(registry)} graph(s), all sources present and tracked"}


def run_all(cfg) -> dict:
    """Run every check. Returns {status, counts, checks[]} with the worst status on top."""
    checks = [
        check_engine_mode(cfg),
        check_vault_folders(cfg),
        check_hook_drift(),
        check_graph_extra(),
        check_ingest_registry(),
        check_graph_registry(cfg),
    ]
    counts = {s: sum(1 for c in checks if c["status"] == s)
              for s in ("ok", "warn", "error", "skip")}
    overall = "error" if counts["error"] else "warn" if counts["warn"] else "ok"
    return {"status": overall, "counts": counts, "checks": checks}
