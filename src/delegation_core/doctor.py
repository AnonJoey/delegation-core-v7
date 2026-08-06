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
* Every ``scope``-filtered search died on "Error finding id" while unfiltered
  search kept answering, so the whole hand-written slice of the vault was
  unreachable and nothing said so. doctor passed 6/6 green throughout, because
  nothing here had ever asked the index a question.

None of these surface through normal use. Each returns a status of ok | warn |
error plus a one-line fix.
"""

from __future__ import annotations

import json
import subprocess
import sys
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


#: The metadata filters search_vault puts on a query, copied from search()'s own
#: branches. A scope that cannot be queried is a scope that answers nothing,
#: however healthy the counts look. is_external is the *string* "true" — that is
#: what ingest writes and what search queries; the boolean matches no row and
#: would make this probe pass without testing anything.
_SCOPE_FILTERS = ({"kind": "note"}, {"kind": "generated"}, {"is_external": "true"})


#: Probe body, run in a child process. See check_index_integrity for why.
_PROBE_SOURCE = """
import json, sys, warnings
warnings.filterwarnings("ignore")
import chromadb

path, name, filters = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
client = chromadb.PersistentClient(
    path=path, settings=chromadb.Settings(anonymized_telemetry=False))
# No embedding_function: this must not pull BGE into memory just to probe.
collection = client.get_collection(name=name)
dimension = collection._model.dimension or 0
if not dimension:
    print(json.dumps({"empty": True}))
    raise SystemExit(0)

probe = [1.0] + [0.0] * (dimension - 1)
broken = []
for where in filters:
    try:
        collection.query(query_embeddings=[probe], n_results=1, where=where)
    except Exception as e:
        broken.append(f"{where}: {type(e).__name__}: {e}")
print(json.dumps({"broken": broken, "count": collection.count()}))
"""


def check_index_integrity(cfg) -> dict:
    """Ask the index the question that breaks, rather than inspecting its files.

    A filtered query is the only authoritative test. Comparing ids between
    chroma.sqlite3 and the vector segment looks rigorous and is not: records live
    in memory until Chroma flushes them, so a healthy server with pending writes
    is indistinguishable from a corrupt index, and ``index_metadata.pickle`` — the
    obvious place to read ids from — is a legacy artifact that current Chroma does
    not create for new collections at all.

    **The probe runs in a child process because it can take the interpreter with
    it.** Opening a PersistentClient and querying it segfaulted the whole CLI
    (SIGSEGV, exit 139) after a bulk ingest left 879 uncompacted rows in Chroma's
    ``embeddings_queue``: the Rust bindings replay that log recursively and blow
    the stack. It reproduced on a copy of the index with no other process running,
    so it is the pending write log, not contention. That is precisely when someone
    runs doctor — right after loading data — and a diagnostic that dies on the
    condition it was written to report is worse than no diagnostic. In a child, the
    same crash is an answer.
    """
    if not (cfg.chroma_path / "chroma.sqlite3").exists():
        return {"check": "index_integrity", "status": "ok", "detail": "no index built yet"}

    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE_SOURCE, str(cfg.chroma_path),
             cfg.collection_name, json.dumps(list(_SCOPE_FILTERS))],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"check": "index_integrity", "status": "warn",
                "detail": "index did not answer within 120s",
                "fix": "delegation-core reindex --force"}

    if completed.returncode < 0:
        # Measured: `reindex --force` dies the same way on this state, so do not
        # send anyone there. A running MCP server keeps serving from memory; only
        # newly opened clients crash, which makes rebuilding from a clean path the
        # remedy — and makes restarting that server the thing to avoid first.
        return {"check": "index_integrity", "status": "error",
                "detail": f"opening the index crashed the probe (signal "
                          f"{-completed.returncode}) — every new process that opens it "
                          "will crash the same way; a running server keeps working from "
                          "memory",
                "fix": "do not restart the MCP server yet — back up the chroma directory, "
                       "then rebuild the index from a clean path (reindex --force crashes "
                       "on this state too)"}
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()
        return {"check": "index_integrity", "status": "warn",
                "detail": f"index unreadable ({tail[-1] if tail else 'no output'})",
                "fix": "delegation-core reindex --force"}

    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception:
        return {"check": "index_integrity", "status": "skip",
                "detail": "probe returned nothing parseable"}

    if result.get("empty"):
        return {"check": "index_integrity", "status": "skip",
                "detail": "collection has no vectors yet"}

    if result["broken"]:
        return {"check": "index_integrity", "status": "error",
                "detail": "scope-filtered search fails — " + "; ".join(result["broken"]),
                "fix": "delegation-core reindex --force, then restart the MCP server "
                       "(it holds the old index in memory and will not re-read disk)"}

    return {"check": "index_integrity", "status": "ok",
            "detail": f"{result['count']} row(s), every search scope answers"}


def run_all(cfg) -> dict:
    """Run every check. Returns {status, counts, checks[]} with the worst status on top."""
    checks = [
        check_engine_mode(cfg),
        check_vault_folders(cfg),
        check_hook_drift(),
        check_graph_extra(),
        check_ingest_registry(),
        check_graph_registry(cfg),
        check_index_integrity(cfg),
    ]
    counts = {s: sum(1 for c in checks if c["status"] == s)
              for s in ("ok", "warn", "error", "skip")}
    overall = "error" if counts["error"] else "warn" if counts["warn"] else "ok"
    return {"status": overall, "counts": counts, "checks": checks}
