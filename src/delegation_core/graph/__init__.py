"""delegation_core.graph - extract · build · cluster · analyze · report.

Vendored from Graphify (github.com/Graphify-Labs/graphify) — core pipeline,
all language extractors, plus callflow_html.py (Mermaid architecture diagrams),
affected.py (blast-radius query), and wiki.py (per-community/god-node articles).
Graphify's own CLI/installer/LLM client/MCP server (serve.py)/watch daemon/
"reflect" memory-overlay were intentionally left out; see the 2026-07-23 vault
decision note for the full include/exclude list.

Graphify is Copyright 2026 Safi Shamsi and the Graphify contributors, dual-
licensed under Apache-2.0 / MIT — see /THIRD_PARTY_LICENSES/ at the repo root.
"""


def __getattr__(name):
    # Lazy imports so importing this package doesn't eagerly pull in every
    # submodule's (heavy, tree-sitter-backed) dependencies.
    _map = {
        "extract": ("delegation_core.graph.extract", "extract"),
        "collect_files": ("delegation_core.graph.extract", "collect_files"),
        "build_from_json": ("delegation_core.graph.build", "build_from_json"),
        "cluster": ("delegation_core.graph.cluster", "cluster"),
        "score_all": ("delegation_core.graph.cluster", "score_all"),
        "cohesion_score": ("delegation_core.graph.cluster", "cohesion_score"),
        "god_nodes": ("delegation_core.graph.analyze", "god_nodes"),
        "surprising_connections": ("delegation_core.graph.analyze", "surprising_connections"),
        "suggest_questions": ("delegation_core.graph.analyze", "suggest_questions"),
        "generate": ("delegation_core.graph.report", "generate"),
        "to_json": ("delegation_core.graph.export", "to_json"),
        "to_html": ("delegation_core.graph.export", "to_html"),
        "to_svg": ("delegation_core.graph.export", "to_svg"),
        "to_canvas": ("delegation_core.graph.export", "to_canvas"),
    }
    if name in _map:
        import importlib
        mod_name, attr = _map[name]
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    raise AttributeError(f"module 'delegation_core.graph' has no attribute {name!r}")
