# Third-party code

## Graphify (`src/delegation_core/graph/`)

`src/delegation_core/graph/` is vendored from [Graphify](https://github.com/Graphify-Labs/graphify),
Copyright 2026 Safi Shamsi and the Graphify contributors, dual-licensed under your choice of the
Apache License 2.0 (`Graphify-LICENSE-Apache-2.0`) or the MIT License (`Graphify-LICENSE-MIT`) —
both included in this directory verbatim from the upstream repository.

**What was taken:** the core pipeline (`detect`/`extract`/`build`/`cluster`/`analyze`/`report`/`export`)
and all language extractors (`extractors/`, `exporters/`) — roughly 40 of Graphify's 90 files.
Explicitly not included: Graphify's own CLI, installer, LLM client, MCP server (`serve.py`), watch
daemon, benchmark suite, wiki generator, "reflect" memory-overlay system, and alternate ingestion
sources (PR/query-log/SCIP/URL/video ingestion). See `src/delegation_core/graph/__init__.py`'s
module docstring for the authoritative list.

**Modifications made:** internal absolute imports (`graphify.xxx`) were rewritten to
`delegation_core.graph.xxx` to fit this project's package namespace; relative imports were left
unchanged. No other logic was altered. Per the Apache License's requirement to state significant
changes to modified files, this note constitutes that statement for the whole vendored tree rather
than a per-file changelog, since the change (namespace only) is mechanical and uniform.

Upstream: https://github.com/Graphify-Labs/graphify
