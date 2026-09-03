# Changelog

All notable changes to the Delegation-Core Office project (v0.1.0 to v0.13.0 / v13) are documented in this file.
This changelog is derived directly from the canonical versioning recorded across the codebase and vault archives.

---

## v0.13.0 / v13 (2026-09-03) - Delegation-Core Office: Claude Desktop Stdio Bridge, Python Installer Unification, GPU Arbiter, Stamped Indexing, and Secret Redaction

### Added & Rebranded
- **Project Rebranding to Delegation-Core Office**: Official renaming of the repository and project identity to Delegation-Core Office.
- **Native Claude Desktop stdio bridge (`src/delegation_core/stdio_bridge.py`)**:
  - Implemented an in-process FastMCP stdio proxy (`mcp-stdio`) that forwards JSON-RPC communication directly to the local HTTP daemon.
  - Solves the fundamental Claude Desktop limitation where remote `type: http` entries with `url` caused Claude Desktop to overwrite `claude_desktop_config.json` and delete the entire `mcpServers` block.
  - Eliminates external dependencies on Node.js and `mcp-remote`, avoiding multi-daemon port and VRAM collisions.
- **Unified Python-based installation and update engine (`src/delegation_core/installer.py`)**:
  - Migrated 810 lines of disparate shell (`install.sh`, `uninstall.sh`) and batch (`install.bat`, `uninstall.bat`) scripts into modular, fully testable Python.
  - Shell and Batch files shrunk to thin runtime-detection stubs.
  - New CLI subcommand `delegation-core update`: performs automated package upgrades, service restarts, client config repairs, and vault health checks in a single atomic command.
  - Added robust service control commands (`stop`, `start`, `restart`, `is_up`) across systemd (Linux), launchd (macOS), and Task Scheduler (Windows).
- **GPU Arbiter for VRAM Mutual Exclusion (`src/delegation_core/gpu.py`)**:
  - Implemented `GpuArbiter` with `threading.RLock()` to manage single-GPU contention between the 12B local LLM (`llama-server`) and `BAAI/bge-m3` embedding model.
  - Coordinated three-tier memory eviction (SentenceTransformer class cache, ChromaDB collection pointers, and PyTorch CUDA cache reclamation) to eliminate `cudaMalloc` out-of-memory errors on 16 GB GPUs.
- **Stamped Incremental Indexing (`stamp_indexed()` in `src/delegation_core/vault.py`)**:
  - Stamped timestamps on successfully indexed notes to eliminate redundant vector re-embeddings.
  - Reduced incremental reindex duration on 4,800+ note vaults from 11 minutes to under 2 seconds.
- **Modular Note Management (`src/delegation_core/notes.py`)**:
  - Extracted note authoring, frontmatter sanitization, and title alias management out of `vault.py` into a dedicated domain module.
- **Systemic Test Guarding (`tests/conftest.py`)**:
  - Added global isolation fixtures to guarantee that automated test runs never mutate or overwrite the user's real Obsidian vault or configuration.
  - Test suite expanded to 1,235 automated tests (100% green).

### Fixed & Hardened
- **Session Export Secret Redaction (`hooks/session_export.py`)**:
  - Ported automated credential masking to the repository hook for ClickUp personal tokens, Anthropic API keys, OpenAI keys, GitHub tokens, Slack tokens, AWS access keys, JWTs, and multi-line PEM/RSA private keys.
  - Fixed title truncation boundary defect: credentials in the first message are now redacted prior to the 60-character title slicing, preventing partial unredacted secret fragments from leaking into frontmatter.
  - Injected `segredos_removidos: <N>` metadata directly into note frontmatter.
  - Added 23 unit and mutation tests in `tests/test_session_export_redaction.py`.
- **Windows Executable Resolution in Service Manager (`src/delegation_core/service.py`)**:
  - Fixed executable resolution to discover `.exe` and `.cmd` extensions in virtual environments, ensuring reliable Task Scheduler startup.
- **Uninstall Process Hygiene**:
  - Fixed uninstaller to cleanly stop and unregister background service daemons (systemd, launchd, Task Scheduler) before deleting virtualenv and package files.
- **Claude Desktop Config Auto-Repair**:
  - `delegation-core clients --claude-desktop` cleans unsafe HTTP/token entries and replaces them with the stdio bridge while preserving all user-configured third-party MCP servers.
- **Empty Local Model Responses Treated as Success (`engine.py`, `localqueue.py`)**:
  - Fixed silent failure where empty LLM worker outputs were recorded as completed tasks rather than retried or raised.
- **Junk Filter False Positives (`src/delegation_core/junk.py`)**:
  - Corrected over-aggressive heuristic filters that discarded legitimate notes and eliminated unreachable dead-code branches.
- **Graph Rebuild Hook Deadlocks (`src/delegation_core/graph_hook_rebuild.py`)**:
  - Resolved race condition and mutual lock contention when multiple git commits triggered concurrent graph rebuilds.

---

## v0.13.0-rc1 (2026-08-23) - Client Scoping and Code Graph Consolidation

### Added
- **Client Metadata Scoping (`search_vault(client=...)`)**:
  - Promoted `client:` frontmatter field to indexed ChromaDB metadata.
  - Added client-filtered search composed via `$and` logic with scope filters, supporting path-based client derivation for ingested files.
- **Code Graph Ingestion Engine (Graphify Integration)**:
  - Vendored and adapted Graphify AST pipeline supporting Python, JavaScript, TypeScript, Rust, Go, and C/C++.
  - Added sourcemaps extraction (`extract_source_maps`), code blast-radius analysis (`graph_affected`), and automated git commit hooks (`graph_hook_install`).

---

## v0.12.3 (2026-08-23) - Service Stop Timeouts and ChromaDB Hardening

### Fixed
- **Service Manager Daemon Stop Timeout**:
  - Set `TimeoutStopSec=600` on generated systemd user units and `ExitTimeOut` on launchd plists.
  - Prevented OS service managers from sending SIGKILL mid-write to ChromaDB and SQLite during lengthy reindexes or relinking passes, resolving HNSW segment corruption.

---

## v0.12.2 (2026-08-23) - Note Linking Resolvers and Background Relinking

### Added
- **`relink_folder_bg` Background Relinking**:
  - Asynchronous background execution for heavy folder cross-linking, preventing client timeout drops on large folders.

### Fixed
- **Date-Stripped Wikilink Resolution**:
  - Allowed `[[Title]]` to resolve against `{date}-{title}.md` without forcing users or agents to type date prefixes in links.
- **Safe Filename Truncation Aliasing**:
  - Emitted untruncated title aliases in note frontmatter when filename truncation occurred.

---

## v0.12.1 (2026-08-23) - Field Defect Resolutions and Data Safety

### Fixed
- **Legacy Collection Adoption**:
  - Gated adoption of legacy vector collections on measured embedding dimensions, preventing dimensional mismatches between bge-base (768-dim) and bge-m3 (1024-dim).
- **Incremental mtime Re-Ingestion**:
  - Fixed incremental reindex to only force re-embeds for files whose index rows are actually missing.
- **Stale Vector Tail Cleanup**:
  - Fixed `upsert` so shortened notes remove excess chunks rather than leaving stale text fragments in the vector index.
- **Dataless File Detection**:
  - Prevented cloud-evicted or zero-block ext4/btrfs files from indexing corrupt empty content.
- **Scope Defaulting**:
  - Handled default search scope dynamically based on vault composition.

---

## v0.12.0 (2026-08-20) - Note Chunking and Execution Limits

### Added
- **Vault Note Chunking**:
  - Chunked long vault notes during embedding to match the token context limits of embedding models.
- **Orphan Sweep Hardening**:
  - Verified and hardened orphan note classification.

---

## v0.11.0 to v0.11.4 (2026-08-12 to 2026-08-14) - Single HTTP Daemon Architecture & Web Dashboard

### Added
- **Single HTTP Daemon Architecture (`127.0.0.1:8787/mcp`)**:
  - Major architectural pivot from multi-process stdio to a single long-lived FastMCP HTTP daemon.
  - Eliminated VRAM duplication of `bge-m3` across multiple connected MCP clients.
  - Added Bearer token authentication on loopback.
- **Integrated Web Dashboard API (`dashboard_api.py`)**:
  - Served dashboard endpoints (`/api/status`, `/api/vault/tree`, `/api/vault/graph`, `/api/processes`, `/api/llama`) directly from the daemon on port 8788.
- **Multi-Agent Local Queue (`local_task_submit`, `local_task_status`)**:
  - Shared asynchronous task queue allowing multiple AI agents to submit background jobs to the local LLM.

---

## v0.10.0 (2026-08-03) - Note Renaming, Graph Hubs, and Literal Search

### Added
- **Atomic Note Renaming (`vault_rename_note` / `POST /api/vault/note/rename`)**:
  - Staged, atomic renaming of notes with automated repointing of all inbound `[[wikilinks]]`.
- **Community Hub Labeling (`graph/cluster.py`)**:
  - Named graph communities using dominant locally-defined code symbols rather than generic identifiers.
- **Paced Task Status Reporting (`task_status`)**:
  - Added execution metrics and typical run durations based on persisted job history (`job_durations.json`).
- **Literal Note Search (`vault_find_notes`)**:
  - Exact stem, prefix, and substring title matching bypassing embedding cutoffs.
- **Backlinks Panel (`vault_note_links`)**:
  - Detailed inbound and outbound link graph reporting with broken link flags.

---

## v0.9.0 (2026-07-28) - Engine Modes, Llama Controls, and Task Tracker

### Added
- **Tri-Mode Engine Architecture (`engine_mode`)**:
  - `local`: synthesis and summarization run offline on local llama.cpp.
  - `agent`: zero local LLM overhead; synthesis delegated to calling agent.
  - `hybrid`: interactive work handled by agent; maintenance and bulk synthesis handled locally.
- **Dashboard Task Tracker Panel**:
  - Added process tracking UI in dashboard and start/stop button for llama-server.
- **Opt-in Web Search**:
  - Privacy-preserving DuckDuckGo search integration via `ddgs`.

---

## v0.8.0 to v0.8.1 (2026-07-20 to 2026-07-24) - Graph Exporters and Local JSON API

### Added
- **Graph Exporters (`graph_export`)**:
  - Export code knowledge graphs to GraphML (Gephi/Cytoscape), SVG, and Cypher (Neo4j).
- **Dashboard Local Backend**:
  - Implemented client tracking and local JSON API with CORS security allowlist.

---

## v0.7.0 to v0.7.2 (2026-07-20) - Code Knowledge Graph Integration

### Added
- **Code Graph Engine**:
  - AST extraction across ~30 languages via tree-sitter, community detection, god-node, and blast-radius analysis.
- **Capability Registry**:
  - Implemented `capabilities()` tool and test enforcement to ensure all vendored pipeline functions are wired.

---

## v0.6.0 to v0.6.4 (2026-07-03 to 2026-07-10) - Multiplatform Llama Downloader and Skills

### Added
- **Multiplatform Llama.cpp Downloader (`src/delegation_core/downloader.py`)**:
  - Automated release asset discovery and extraction for Linux (`.tar.gz`), macOS (`.tar.gz`), and Windows (`.zip`), preserving dynamic library symlinks.
- **Bundled Agent Skills (`skills/`)**:
  - Integrated 17 universal Claude Code agent skills deployed into `~/.claude/skills/`.

### Fixed
- **YAML Frontmatter Colon Quoting**:
  - Sanitized and quoted frontmatter scalars containing colons and special characters.
- **Reasoning Tag Stripping**:
  - Added `_strip_think_tags()` to purge chain-of-thought `<think>` blocks from frontmatter.

---

## v0.5.0 to v0.5.3 (2026-06-29) - Recursive Folder Ingestion and Extractors

### Added
- **Recursive Folder Ingest (`ingest_folder` / `ingest_folder_bg`)**:
  - Extraction and vector indexing of PDF, DOCX, XLSX, PPTX, HTML, and MDX files.
- **Semantic Note Relinking (`relink_folder`)**:
  - Automated cross-linking of unlinked notes based on embedding similarity thresholds.

---

## v0.1.0 to v0.4.0 (2026-06-10 to 2026-06-27) - Inception: Local Memory Bridge & Vault Vector Store

### Added
- **Initial MCP Stdio Memory Server**:
  - Inception of `delegation-core` as an offline memory bridge connecting AI agents to Obsidian vaults.
  - Semantic vector search using `BAAI/bge-m3` embeddings stored in ChromaDB.
- **Core MCP Tools**:
  - `search_vault`: semantic vector query against notes.
  - `read_note` / `write_note`: curated markdown reading and atomic creation with frontmatter.
  - `compress`: automatic summarization of lengthy session notes.
  - `vault_health_detail` & `vault_stats`: orphan detection, unindexed tracking, and broken wikilink accounting.
  - `run_maintenance`: automated healing of broken links and metadata.
