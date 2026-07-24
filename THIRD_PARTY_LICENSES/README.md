# Third-party code

## Graphify (`src/delegation_core/graph/`)

`src/delegation_core/graph/` is vendored from [Graphify](https://github.com/Graphify-Labs/graphify),
Copyright 2026 Safi Shamsi and the Graphify contributors.

**Licensing is not a simple "your choice" dual license.** Upstream Graphify relicensed from MIT
to Apache License 2.0 on 2026-07-22 (its `0.9.25` release, one day before this project vendored
it): contributions made *before* the relicensing remain available under the original MIT terms,
per upstream's own `NOTICE` file, while the project as a whole is now Apache-2.0. All three
upstream files are reproduced verbatim here: `Graphify-LICENSE-Apache-2.0` (current primary
license), `Graphify-LICENSE-MIT` (retained terms for pre-relicense contributions), and
`Graphify-NOTICE` (upstream's own attribution notice, reproduced as required by the Apache
License's NOTICE-passthrough clause).

**What was taken:** the core pipeline (`detect`/`extract`/`build`/`cluster`/`analyze`/`report`/
`export`), all language extractors (`extractors/`, `exporters/`), the shared infrastructure
modules (`cache.py`, `dedup.py`, `ids.py`, `paths.py`, `resolver_registry.py`,
`pascal_resolution.py`, `ruby_resolution.py`, `security.py`, `validate.py`, `_minhash.py`), the
alternate-source ingesters (`google_workspace.py`, `manifest_ingest.py`, `mcp_ingest.py`), plus
(added 2026-07-23) `callflow_html.py` (Mermaid architecture diagrams), `affected.py`
(blast-radius query), and `wiki.py` (per-community/god-node articles) — 55 of Graphify's Python
files as vendored on 2026-07-23 (upstream's own tree has grown since; this list is not
recomputed against later upstream commits). Explicitly not included: Graphify's own CLI,
installer, LLM client, MCP server (`serve.py`), watch daemon, benchmark suite, "reflect"
memory-overlay system, and alternate ingestion sources (PR/query-log/SCIP/URL/video ingestion).
See `src/delegation_core/graph/__init__.py`'s module docstring for the authoritative list.

**Not vendored, but adapted (conceptually, not as source):** `src/delegation_core/graph_hook.py`
and `graph_hook_rebuild.py` reimplement the *idea* of Graphify's `hooks.py` (git post-commit hook
that keeps the graph fresh) using entirely original code, not a copy — those two upstream files
are built around Graphify's own uv-tool/pipx PATH-detection and a cross-repo lock/queue/drain
system that don't apply to delegation-core's single-venv install. No Graphify source was copied
into either file; see graph_hook.py's module docstring for the full rationale.

**Modifications made:** internal absolute imports (`graphify.xxx`) were rewritten to
`delegation_core.graph.xxx` to fit this project's package namespace; relative imports were left
unchanged. No other logic was altered. Per the Apache License's requirement to state significant
changes to modified files, this note constitutes that statement for the whole vendored tree rather
than a per-file changelog, since the change (namespace only) is mechanical and uniform.

Upstream: https://github.com/Graphify-Labs/graphify
