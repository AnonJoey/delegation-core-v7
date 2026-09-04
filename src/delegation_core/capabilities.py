"""capabilities.py — what this server can actually do, derived from the code.

Written after a class of bug that hit three times in one week: a capability
existed, worked, and was reachable, but nothing surfaced it to the caller, so it
sat unused for months while the fallback produced plausible output.

  - ``label_communities_by_hub()`` had no caller at all; every graph artifact
    silently fell back to "Community {cid}" and 2693 vault notes were filed with
    titles carrying no information.
  - ``detect(extra_excludes=...)`` was never wired to ``graph_build``, so the
    only way to keep a repository's tests out of a graph was to build everything
    and prune 1071 articles by hand.
  - ``remap_communities_to_previous()`` still has no caller, while graphbridge
    works around the exact instability it was written to fix.

Prose does not prevent this. The hand-written guide for a comparable project was
wrong on four of four numeric constants when checked against its own source, and
this project's own AGENT_GUIDE drifts the same way. So the registry below is
paired with ``tests/test_capability_registry.py``, which fails when an
artifact-producing function exists in the vendored graph pipeline and is not
classified here. Adding one without wiring it is then a deliberate, written-down
decision rather than a silent omission.

``describe()`` is what a connecting LLM reads: the live MCP tool list (asked of
the server itself, so it cannot drift) plus this table.
"""

from __future__ import annotations

# Every artifact-producing function in the vendored graph pipeline.
# status:
#   "wired"        — reachable through the named MCP tool
#   "not-exposed"  — reachable in code, deliberately not surfaced; reason given
# Helpers that merely write bytes (write_text_atomic, write_json_atomic) and
# type coercions (to_float) are not capabilities and are excluded by the guard
# test's own filter, not by omission here.
GRAPH_CAPABILITIES: dict[str, dict] = {
    "to_json": {
        "module": "graph.export",
        "produces": "graph.json — the full node/edge graph",
        "status": "wired",
        "via": "graph_build",
    },
    "to_html": {
        "module": "graph.exporters.html",
        "produces": "graph.html — interactive vis.js explorer",
        "status": "wired",
        "via": "graph_build",
        "note": "Refuses above 5000 nodes; large graphs get an aggregated community view.",
    },
    "to_wiki": {
        "module": "graph.wiki",
        "produces": "wiki/*.md — one article per community and god node",
        "status": "wired",
        "via": "graph_build",
    },
    "write_callflow_html": {
        "module": "graph.callflow_html",
        "produces": "callflow.html — Mermaid architecture diagrams",
        "status": "wired",
        "via": "graph_build",
    },
    "to_graphml": {
        "module": "graph.export",
        "produces": "GraphML — opens in Gephi, yEd, Cytoscape",
        "status": "wired",
        "via": "graph_export",
    },
    "to_svg": {
        "module": "graph.export",
        "produces": "SVG — static layout via matplotlib",
        "status": "wired",
        "via": "graph_export",
    },
    "to_cypher": {
        "module": "graph.export",
        "produces": "Cypher script — replays the graph into Neo4j",
        "status": "wired",
        "via": "graph_export",
    },
    "to_obsidian": {
        "module": "graph.export",
        "produces": "an Obsidian vault — one .md per node with [[wikilinks]]",
        "status": "not-exposed",
        "reason": "Obsidian is no longer the target reader; the dashboard is. "
                  "Kept vendored so the decision can be revisited without a re-vendor.",
    },
    "to_canvas": {
        "module": "graph.export",
        "produces": "an Obsidian Canvas file — communities as groups",
        "status": "not-exposed",
        "reason": "Obsidian-specific format; same decision as to_obsidian.",
    },
    "push_to_neo4j": {
        "module": "graph.exporters.graphdb",
        "produces": "writes the graph into a running Neo4j instance",
        "status": "not-exposed",
        "reason": "Needs a live server plus credentials. to_cypher covers the same "
                  "ground through graph_export without holding a connection.",
    },
    "push_to_falkordb": {
        "module": "graph.exporters.graphdb",
        "produces": "writes the graph into a running FalkorDB instance",
        "status": "not-exposed",
        "reason": "Same as push_to_neo4j — live connection, no credential path here.",
    },
}

# Capabilities that exist in the pipeline but are not artifact producers, and so
# are invisible to the guard test's pattern. Listed because they are exactly the
# kind of thing that goes unused: each one is real, working, and unreachable.
#: Capacidades que existem no codigo e nao tem chamador nenhum. Cada entrada e
#: uma DECISAO registrada, e nao um inventario que envelhece: `find_import_cycles`
#: esteve aqui como "Never surfaced" enquanto era chamada por
#: `graph/report.py:199`, com o resultado indo para a secao "## Import Cycles"
#: de todo GRAPH_REPORT.md gerado.
#:
#: Isso importa mais aqui do que em qualquer outro lugar do projeto: o contrato
#: deste modulo manda preferir este relatorio "over any prose description of this
#: server, including AGENT_GUIDE.md, which has no such guard" -- e esta lista
#: era prosa escrita a mao vestindo a autoridade do relatorio gerado.
#:
#: `tests/test_capability_registry.py` agora varre o AST atras de chamadores de
#: cada nome daqui e falha em quem ganhou um.
KNOWN_UNWIRED: dict[str, str] = {
    "graph.cluster.remap_communities_to_previous":
        "Keeps community IDs stable across rebuilds. Still has no caller; "
        "graphbridge instead deletes and re-files every vault article on each "
        "rebuild. Wiring it changes rebuild behaviour, so it needs its own test.",
    "graph.cluster.community_member_sigs":
        "Membership fingerprints that let a later pass tell which communities "
        "actually changed. Unused for the same reason as remap_communities_to_previous.",
    "graph.analyze.graph_diff":
        "Structural diff between two builds. Never surfaced; would answer "
        "'what changed in this codebase since the last graph'.",
    "graph.validate.assert_valid":
        "Post-extraction validation. Never called by the build pipeline.",
    "graph.detect.detect_incremental":
        "Detect only files changed since the last run. graph_build always does "
        "a full detect; this is what an incremental rebuild would use.",
}


def describe(mcp_tools: list[dict], default_scope: dict | None = None) -> dict:
    """Assemble the capability report handed to a connecting client.

    ``mcp_tools`` comes from the live server registry rather than a hand-kept
    list, so the tool surface reported here cannot drift from the tool surface
    actually served.

    ``default_scope`` e o escopo que a busca sem argumento REALMENTE usaria nesta
    maquina, mais de onde ele veio. Antes disso o relatorio dizia
    `"all": "everything (default)"`, uma palavra escrita a mao, enquanto
    `server._default_scope()` decide por vault: 'notes' quando artigo gerado
    passa de metade, 'all' quando nao passa, e uma chave em config.json vence os
    dois.

    Medido neste vault: 8.246 gerados de 8.593, entao o padrao resolve para
    'notes' e toda busca sem escopo respondeu `"scope": "notes"`. Um agente que
    lesse o relatorio acreditaria estar cobrindo tudo enquanto cobria 347 notas
    e ignorava 8.246 artigos, em silencio.

    E o mesmo defeito do `known_unwired`: o relatorio GERADO carregando
    afirmacao escrita a mao, sob um contrato que manda preferi-lo a qualquer
    prosa. A correcao e a mesma: parar de afirmar e passar a calcular.
    """
    wired = {k: v for k, v in GRAPH_CAPABILITIES.items() if v["status"] == "wired"}
    unexposed = {k: v for k, v in GRAPH_CAPABILITIES.items() if v["status"] != "wired"}
    return {
        "tools": mcp_tools,
        "tool_count": len(mcp_tools),
        "graph_exports": {
            "wired": wired,
            "not_exposed": unexposed,
        },
        "known_unwired": KNOWN_UNWIRED,
        "search_scopes": {
            "notes": "hand-written vault notes",
            "generated": "graph_build wiki articles (filter by graph= to pin one codebase)",
            "external": "ingest_folder'd files, never moved from their source",
            "all": "everything",
        },
        "default_scope": default_scope or {
            "resolved": "notes",
            "source": "fallback",
            "detail": "the report was built without asking the server; "
                      "'notes' is the documented fallback",
        },
        "contract": (
            "This report is generated: 'tools' is asked of the running server and "
            "'graph_exports' is guarded by tests/test_capability_registry.py, which "
            "fails when a new artifact-producing function appears unclassified. "
            "Prefer it over any prose description of this server, including "
            "AGENT_GUIDE.md, which has no such guard."
        ),
    }
