"""
graphbridge.py — Orchestrates the vendored code-graph pipeline (delegation_core.graph,
adapted from Graphify: github.com/Graphify-Labs/graphify — see that package's
docstring for exactly what was and wasn't vendored) end-to-end, and routes its
output through delegation-core's EXISTING vault ingestion pipeline (organizer.run)
instead of Graphify's own Obsidian writer (to_obsidian()).

AST-only: only the code-file extraction path is used. Graphify's semantic
(doc/paper/image) extraction pass requires an LLM backend, which was deliberately
not vendored — see delegation_core/graph/__init__.py for the include/exclude list.

New in v0.7.0.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from . import organizer

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


def _file_report_to_vault(cfg, vault_manager, graph_name: str, report_md: str) -> dict:
    """Drop GRAPH_REPORT.md + a folder_hint sidecar into the vault inbox — reuses
    the exact same extractor/classifier/synthesizer/merge path any other dropped
    document goes through, rather than writing a bespoke graph-specific note format.
    """
    inbox = cfg.vault / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    stem = f"graph-report-{graph_name}"
    report_path = inbox / f"{stem}.md"
    sidecar_path = inbox / f"{stem}.meta.yaml"
    report_path.write_text(report_md, encoding="utf-8")
    # no_merge: a fresh report replaces the prior one in the registry/graphs_dir
    # artifact anyway; merging into an older report note would blur the two.
    sidecar_path.write_text("folder_hint: reference\nno_merge: true\n", encoding="utf-8")
    return {"queued_path": str(report_path)}


async def build_graph(cfg, engine, vault_manager, source_path: str,
                       name: str | None = None, force: bool = False) -> dict:
    """Build a code knowledge graph for source_path using the vendored pipeline.

    Writes graph.json / graph.html / GRAPH_REPORT.md to cfg.graphs_dir/<name>/,
    then files GRAPH_REPORT.md into the vault by queuing it in _inbox/ and running
    the normal maintenance pass (organizer.run) — same code path run_maintenance()
    uses, so it gets classified/synthesized/filed like any other document.
    """
    from delegation_core.graph.detect import detect
    from delegation_core.graph.extract import extract as ast_extract
    from delegation_core.graph.build import build as build_graph_fn
    from delegation_core.graph.cluster import cluster as cluster_fn, score_all
    from delegation_core.graph.analyze import god_nodes, surprising_connections, suggest_questions
    from delegation_core.graph.report import generate as render_report
    from delegation_core.graph.export import to_json, to_html

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

    queued = _file_report_to_vault(cfg, vault_manager, graph_name, report_md)
    try:
        maintenance_result = await organizer.run(engine, vault_manager)
        filed = {**queued, "maintenance": {
            "classified": maintenance_result.get("classified", []),
            "errors": maintenance_result.get("errors", []),
        }}
    except Exception as e:
        logger.warning("Maintenance pass after graph_build failed: %s", e)
        filed = {**queued, "note": f"Report queued but maintenance pass failed ({e}); "
                                    "call run_maintenance() to retry filing it."}

    return {
        "status": "ok",
        "name": graph_name,
        "source_path": str(root),
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "community_count": len(communities),
        "god_nodes": gods[:5],
        "graph_json": str(out_dir / "graph.json"),
        "graph_html": str(out_dir / "graph.html"),
        "filed_to_vault": filed,
    }


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
