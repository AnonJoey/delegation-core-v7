"""
graphbridge.py — Orchestrates the vendored code-graph pipeline (delegation_core.graph,
adapted from Graphify: github.com/Graphify-Labs/graphify — see that package's
docstring for exactly what was and wasn't vendored) end-to-end, and files its
output into the vault directly instead of Graphify's own Obsidian writer
(to_obsidian()).

AST-only: only the code-file extraction path is used. Graphify's semantic
(doc/paper/image) extraction pass requires an LLM backend, which was deliberately
not vendored — see delegation_core/graph/__init__.py for the include/exclude list.

New in v0.7.0. v0.7.1 added callflow.html (Mermaid architecture diagrams),
wiki/ articles (one vault note per community/god-node instead of one big
report), and graph_affected (blast-radius query).

v0.7.2 correction: the wiki/report artifacts are now written to the vault
DIRECTLY (write + index_note + BGE-only wikilinks), not routed through
_inbox/ + organizer.run(). The original design queued every wiki article
(often 100+) through the FULL classify/synthesize pipeline meant for messy
raw documents — since each synthesize() call is a real LLM round-trip, one
graph_build() turned into 100+ sequential llama.cpp calls (tens of minutes,
observed directly). Wiki articles and GRAPH_REPORT.md are already clean,
structured markdown (report.generate()/wiki.py's own templates) — they don't
need extraction or rewriting, only filing, indexing, and linking. This also
means graph_build no longer needs a DelegationEngine at all.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("graphbridge")


def _load_registry(cfg) -> dict:
    path = cfg.graphs_registry_path
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception as e:
        logger.warning("Could not load graphs registry: %s", e)
        return {}


def _save_registry(cfg, registry: dict) -> None:
    cfg.graphs_dir.mkdir(parents=True, exist_ok=True)
    cfg.graphs_registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def _slugify(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-")
    return safe or "graph"


def _resolve_folder(cfg, preferred: str = "reference") -> str:
    """Case-insensitive match against cfg.vault_folders (vaults configure their
    own casing — e.g. "Reference" vs the config.py default "reference" — and
    writing to the wrong case silently creates a stray duplicate folder instead
    of erroring, so this has to be resolved explicitly rather than assumed).
    Falls back to the first configured folder if nothing matches "reference".
    """
    for f in cfg.vault_folders:
        if f.lower() == preferred:
            return f
    return cfg.vault_folders[0] if cfg.vault_folders else preferred


def _write_vault_note(vault_manager, folder: str, title: str, content: str) -> str:
    """Direct write: file + index_note + best-effort BGE-only wikilinks. No LLM
    call — for content that's already well-formed (a generated report/article),
    not raw material that needs extraction or rewriting. Mirrors write_note()'s
    shape in server.py, minus the "ai_generated: true" framing since nothing
    here was LLM-authored, just LLM-analyzed.
    """
    from .linker import inject_backlinks, wikilinks
    from .vault import safe_filename, yaml_quote_scalar

    cfg = vault_manager.cfg
    safe = safe_filename(title)
    dest = cfg.vault / folder / f"{datetime.now().strftime('%Y-%m-%d')}-{safe}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    full = (
        f"---\ntitle: {yaml_quote_scalar(title)}\ndate: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"ai_generated: false\nsource: graph_build\n---\n\n{content}"
    )
    dest.write_text(full, encoding="utf-8")
    rel = str(dest.relative_to(cfg.vault))
    vault_manager.index_note(full, {"title": title, "path": rel, "folder": folder})

    try:
        hits = [h for h in vault_manager.search(full[:600], limit=6) if h.get("path") != rel][:5]
        links = wikilinks(hits, cfg.merge_threshold)
        if links:
            updated = full.rstrip() + f"\n\n## Related\n{links}\n"
            dest.write_text(updated, encoding="utf-8")
            vault_manager.index_note(updated, {"title": title, "path": rel, "folder": folder})
            inject_backlinks(vault_manager, dest.stem,
                              [h["path"] for h in hits if h.get("similarity", 0) >= cfg.merge_threshold])
    except Exception as e:
        logger.warning("wikilink injection skipped for %s: %s", rel, e)

    return rel


def _write_artifacts_to_vault(vault_manager, graph_name: str, report_md: str, wiki_dir: Path | None) -> dict:
    """File GRAPH_REPORT.md (+ any per-community/god-node wiki articles) into the
    vault as siblings: GRAPH_REPORT.md is the single-note summary (good for a
    quick search hit), the wiki articles are individually-searchable per-topic
    notes (good for "what does the X community do" style questions). Neither
    replaces the other.
    """
    folder = _resolve_folder(vault_manager.cfg, "reference")
    written = [_write_vault_note(vault_manager, folder, f"Code Graph Report — {graph_name}", report_md)]

    if wiki_dir is not None and wiki_dir.is_dir():
        for article in sorted(wiki_dir.glob("*.md")):
            if article.stem.lower() == "index":
                continue  # local nav aid only, nothing worth searching for in it
            content = article.read_text(encoding="utf-8")
            title = f"{graph_name}: {article.stem}"
            written.append(_write_vault_note(vault_manager, folder, title, content))

    return {"written_paths": written}


async def build_graph(cfg, vault_manager, source_path: str,
                       name: str | None = None, force: bool = False,
                       file_to_vault: bool = True) -> dict:
    """Build a code knowledge graph for source_path using the vendored pipeline.

    Writes graph.json / graph.html / callflow.html / GRAPH_REPORT.md / wiki/*.md
    to cfg.graphs_dir/<name>/. When file_to_vault=True (default — the MCP tool's
    behavior) also files GRAPH_REPORT.md + the wiki articles directly into the
    vault (see _write_artifacts_to_vault). file_to_vault=False is for the git
    post-commit hook path (graph_hook.py): keep the on-disk artifacts fresh on
    every commit without touching the vault each time — vault filing stays a
    deliberate action.

    async only because it's called from server.py's async MCP tools; nothing
    inside actually awaits (no engine/LLM involved — see module docstring).
    """
    from delegation_core.graph.detect import detect
    from delegation_core.graph.extract import extract as ast_extract
    from delegation_core.graph.build import build as build_graph_fn
    from delegation_core.graph.cluster import cluster as cluster_fn, score_all
    from delegation_core.graph.analyze import god_nodes, surprising_connections, suggest_questions
    from delegation_core.graph.report import generate as render_report
    from delegation_core.graph.export import to_json, to_html
    from delegation_core.graph.callflow_html import write_callflow_html
    from delegation_core.graph.wiki import to_wiki

    root = Path(source_path).expanduser().resolve()
    if not root.exists():
        return {"error": f"Path not found: {source_path}"}
    if not root.is_dir():
        return {"error": f"Not a directory: {source_path}"}

    graph_name = _slugify(name or root.name)
    out_dir = cfg.graphs_dir / graph_name

    if out_dir.exists() and not force and (out_dir / "graph.json").exists():
        return {
            "status": "exists",
            "name": graph_name,
            "message": f"Graph '{graph_name}' already built at {out_dir}. Pass force=true to rebuild.",
            "graph_json": str(out_dir / "graph.json"),
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    cache_root = out_dir / "cache"

    try:
        detection = detect(root, cache_root=cache_root)
    except Exception as e:
        return {"error": f"detect() failed: {e}"}

    code_files = [Path(p) for p in detection.get("files", {}).get("code", [])]
    if not code_files:
        return {
            "status": "empty",
            "name": graph_name,
            "message": f"No recognized code files found under {root}.",
        }

    try:
        ast_result = ast_extract(code_files, cache_root=cache_root, root=root)
        G = build_graph_fn([ast_result], directed=True, root=root)
        communities = cluster_fn(G)
        cohesion = score_all(G, communities)
        gods = god_nodes(G)
        surprises = surprising_connections(G, communities)
        questions = suggest_questions(G, communities, {})

        report_md = render_report(
            G, communities, cohesion, {}, gods, surprises,
            detection,
            {"input_tokens": ast_result.get("input_tokens", 0),
             "output_tokens": ast_result.get("output_tokens", 0)},
            str(root), suggested_questions=questions,
        )
        (out_dir / "GRAPH_REPORT.md").write_text(report_md, encoding="utf-8")
        to_json(G, communities, str(out_dir / "graph.json"), force=True)

        try:
            to_html(G, communities, str(out_dir / "graph.html"))
        except Exception as e:
            logger.warning("graph.html export skipped for %s: %s", graph_name, e)

        callflow_path = out_dir / "callflow.html"
        try:
            write_callflow_html(
                graph=str(out_dir / "graph.json"),
                report=str(out_dir / "GRAPH_REPORT.md"),
                output=str(callflow_path),
            )
        except Exception as e:
            logger.warning("callflow.html export skipped for %s: %s", graph_name, e)
            callflow_path = None

        wiki_dir = out_dir / "wiki"
        wiki_count = 0
        try:
            wiki_count = to_wiki(G, communities, wiki_dir, cohesion=cohesion, god_nodes_data=gods)
        except Exception as e:
            logger.warning("wiki export skipped for %s: %s", graph_name, e)
            wiki_dir = None
    except Exception as e:
        logger.error("Graph build failed for %s: %s", root, e)
        return {"error": f"Graph build failed: {e}"}

    registry = _load_registry(cfg)
    registry[graph_name] = {
        "source_path": str(root),
        "built_at": datetime.now().isoformat(),
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "community_count": len(communities),
        "out_dir": str(out_dir),
    }
    _save_registry(cfg, registry)

    result = {
        "status": "ok",
        "name": graph_name,
        "source_path": str(root),
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "community_count": len(communities),
        "god_nodes": gods[:5],
        "graph_json": str(out_dir / "graph.json"),
        "graph_html": str(out_dir / "graph.html"),
        "callflow_html": str(callflow_path) if callflow_path else None,
        "wiki_articles": wiki_count,
    }

    if not file_to_vault:
        result["filed_to_vault"] = None
        return result

    try:
        result["filed_to_vault"] = _write_artifacts_to_vault(vault_manager, graph_name, report_md, wiki_dir)
    except Exception as e:
        logger.warning("Filing artifacts to vault failed for %s: %s", graph_name, e)
        result["filed_to_vault"] = {"error": str(e)}

    return result


def list_graphs(cfg) -> dict:
    """Return the registry of previously built graphs: name, source path, counts, timestamps."""
    registry = _load_registry(cfg)
    return {"count": len(registry), "graphs": registry}


def get_report(cfg, name: str) -> dict:
    """Return the full GRAPH_REPORT.md text for a previously built graph by name."""
    graph_name = _slugify(name)
    report_path = cfg.graphs_dir / graph_name / "GRAPH_REPORT.md"
    if not report_path.exists():
        return {"error": f"No report found for graph '{graph_name}'. Call graph_build first."}
    return {"name": graph_name, "report": report_path.read_text(encoding="utf-8")}


def get_affected(cfg, name: str, query: str, depth: int = 2, relations: list[str] | None = None) -> dict:
    """Blast-radius query: what else is affected if `query` (a file path or symbol
    label) changes? Walks calls/indirect_call/references/imports edges backward
    from the matched node up to `depth` hops. Requires graph_build to have run
    for this name first.
    """
    from delegation_core.graph.affected import (
        DEFAULT_AFFECTED_RELATIONS, affected_nodes, format_affected, load_graph, resolve_seed,
    )

    graph_name = _slugify(name)
    graph_path = cfg.graphs_dir / graph_name / "graph.json"
    if not graph_path.exists():
        return {"error": f"No graph found for '{graph_name}'. Call graph_build first."}

    try:
        G = load_graph(graph_path)
    except Exception as e:
        return {"error": f"Could not load graph: {e}"}

    relation_list = tuple(relations) if relations else DEFAULT_AFFECTED_RELATIONS
    seed = resolve_seed(G, query)
    if seed is None:
        return {"error": f"No unique node match for {query!r} in graph '{graph_name}'."}

    hits = affected_nodes(G, seed, relations=relation_list, depth=depth)
    text = format_affected(G, query, relations=relation_list, depth=depth)
    return {
        "name": graph_name,
        "query": query,
        "seed": seed,
        "depth": depth,
        "relations": list(relation_list),
        "hit_count": len(hits),
        "report": text,
    }
