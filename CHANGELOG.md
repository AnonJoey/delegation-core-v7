# Changelog

All notable changes to the Delegation-Core Office project are documented in this file.
This changelog is derived comprehensively from the authoritative project history, decisions, procedures, and architectural milestones recorded in the vault and codebase since inception.

---

## v7.1.0 (2026-09-03) - Delegation-Core Office: Claude Desktop Stdio Bridge, Python Installer Unification, and Session Export Secret Redaction

### Added & Rebranded
- **Project Rebranding to Delegation-Core Office**: Official renaming of the repository and project identity to Delegation-Core Office, reflecting the full-suite local memory, knowledge graph, and task delegation platform.
- **Native Claude Desktop stdio bridge (`src/delegation_core/stdio_bridge.py`)**:
  - Implemented an in-process FastMCP stdio proxy (`mcp-stdio`) that forwards JSON-RPC communication directly to the local HTTP daemon.
  - Solves the fundamental Claude Desktop limitation where remote `type: http` entries with `url` caused Claude Desktop to overwrite `claude_desktop_config.json` and delete the entire `mcpServers` block.
  - Eliminates external dependencies on Node.js and `mcp-remote`, avoiding multi-daemon port and VRAM collisions.
- **Unified Python-based installation and update engine (`src/delegation_core/installer.py`)**:
  - Migrated 810 lines of disparate shell (`install.sh`, `uninstall.sh`) and batch (`install.bat`, `uninstall.bat`) scripts into modular, fully testable Python.
  - Shell and Batch files shrunk to thin runtime-detection stubs.
  - New CLI subcommand `delegation-core update`: performs automated package upgrades, service restarts, client config repairs, and vault health checks in a single atomic command.
  - Added robust service control commands (`stop`, `start`, `restart`, `is_up`) across systemd (Linux), launchd (macOS), and Task Scheduler (Windows).

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

---

## v7.0.0 (2026-09-02) - Autonomous Overnight Job Orchestration, GPU Arbiter, and Stamped Incremental Indexing

### Added
- **GPU Arbiter for VRAM Mutual Exclusion (`src/delegation_core/gpu.py`)**:
  - Implemented `GpuArbiter` with `threading.RLock()` to manage single-GPU contention between the 12B local LLM (`llama-server`) and `BAAI/bge-m3` embedding model.
  - Coordinated three-tier memory eviction (SentenceTransformer class cache, ChromaDB collection pointers, and PyTorch CUDA cache reclamation) to eliminate `cudaMalloc` out-of-memory errors on 16 GB GPUs.
- **Stamped Incremental Indexing (`stamp_indexed()` in `src/delegation_core/vault.py`)**:
  - Stamped timestamps on successfully indexed notes to eliminate redundant vector re-embeddings.
  - Reduced incremental reindex duration on 4,800+ note vaults from 11 minutes to under 2 seconds.
- **Modular Note Management (`src/delegation_core/notes.py`)**:
  - Extracted note authoring, frontmatter sanitization, and title alias management out of `vault.py` into a dedicated, clean domain module.
- **Systemic Test Guarding (`tests/conftest.py`)**:
  - Added global isolation fixtures to guarantee that automated test runs never mutate or overwrite the user's real Obsidian vault or configuration.

### Fixed
- **Empty Local Model Responses Treated as Success (`engine.py`, `localqueue.py`)**:
  - Fixed silent failure where empty LLM worker outputs were recorded as completed tasks rather than retried or raised.
- **Junk Filter False Positives (`src/delegation_core/junk.py`)**:
  - Corrected over-aggressive heuristic filters that discarded legitimate notes and eliminated unreachable dead-code branches.
- **Graph Rebuild Hook Deadlocks (`src/delegation_core/graph_hook_rebuild.py`)**:
  - Resolved race condition and mutual lock contention when multiple git commits triggered concurrent graph rebuilds.
- **Windows GPU Downloader Fallback (`src/delegation_core/downloader.py`)**:
  - Prevented Windows installer from incorrectly downloading CUDA GPU binaries on CPU-only machines.

---

## v6.4.0 (2026-08-31) - Workspace Management, Document Canonicalization, and Directory Normalization

### Added
- **MCP Window & Workspace Management (`src/delegation_core/windows.py`)**:
  - Introduced `workspace_save`, `workspace_apply`, `window_open`, `window_close`, and `window_list` MCP tools for dynamic switching of toolsets across multiple MCP clients.
  - Invariant protection ensuring `delegation-core` is never unmounted from `~/.claude.json`.
- **Vault Directory Normalization**:
  - Standardized root storage under `~/Documents` and unified folder resolution across Linux and Windows.
  - Resilient case-insensitivity mapping for standard vault folders (`Projects`, `Decisions`, `Fixes`, `Sessions`, `Procedures`, `Reference`, `Tools`, `Scratch`, `Infrastructure`).

---

## v6.0.0 - v6.3.0 (2026-08-25 to 2026-08-30) - Interactive Setup Wizard, Multi-Client Autoconfig, and Skills

### Added
- **Interactive Setup Wizard (`src/delegation_core/wizard.py`)**:
  - Added `delegation-core setup` with interactive Obsidian vault discovery, GGUF model downloader, engine mode selector, and autostart registration.
- **Client Configuration CLI (`delegation-core clients`)**:
  - Automated injection of MCP server configurations into Claude Code, Claude Desktop, Cursor, and custom client JSON configs.
- **Bundled Agent Skills (`skills/`)**:
  - Integrated 17 universal Claude Code agent skills deployed into `~/.claude/skills/`.

---

## v0.13.0 (2026-08-23) - Production Consolidation, Graphify Integration, and Client Scoping

### Added
- **Client Metadata Scoping (`search_vault(client=...)`)**:
  - Promoted `client:` frontmatter field to indexed ChromaDB metadata.
  - Added client-filtered search composed via `$and` logic with scope filters, supporting path-based client derivation for ingested files.
- **Code Graph Ingestion Engine (Graphify Integration)**:
  - Vendored and adapted Graphify AST pipeline supporting Python, JavaScript, TypeScript, Rust, Go, and C/C++.
  - Added sourcemaps extraction (`extract_source_maps`), code blast-radius analysis (`graph_affected`), and automated git commit hooks (`graph_hook_install`).

---

## v0.12.0 - v0.12.3 (2026-08-20 to 2026-08-23) - ChromaDB Recovery, Service Timeouts, and Background Relinking

### Added
- **Background Relinking (`relink_folder_bg`)**:
  - Added asynchronous background task for heavy folder cross-linking, preventing client timeout drops on large folders.
- **Systemd Stop Timeout Hardening**:
  - Configured `TimeoutStopSec=600` and launchd `ExitTimeOut` to prevent service managers from sending SIGKILL mid-write to ChromaDB and SQLite.

### Fixed
- **Filename Date-Stripped Wikilink Resolvers**:
  - Allowed `[[Title]]` to resolve against `{date}-{title}.md` without forcing users to type timestamps in links.
- **Stale Vector Tail Cleanup**:
  - Fixed `upsert` so shortened notes remove excess chunks rather than leaving stale text fragments in the vector index.
- **Dataless File Detection**:
  - Prevented cloud-evicted or zero-block ext4/btrfs files from indexing corrupt empty content.

---

## v0.11.0 - v0.11.4 (2026-08-12 to 2026-08-14) - Single HTTP Daemon Architecture & Web Dashboard

### Added
- **Single HTTP Daemon Migration (`127.0.0.1:8787/mcp`)**:
  - Major architectural pivot from multi-process stdio to a single long-lived FastMCP HTTP daemon.
  - Eliminated VRAM duplication of `bge-m3` across multiple connected MCP clients.
  - Added Bearer token authentication on loopback.
- **Integrated Web Dashboard API (`dashboard_api.py`)**:
  - Served dashboard endpoints (`/api/status`, `/api/vault/tree`, `/api/vault/graph`, `/api/processes`, `/api/llama`) directly from the daemon on port 8788.
- **Multi-Agent Local Queue (`local_task_submit`, `local_task_status`)**:
  - Shared asynchronous task queue allowing multiple AI agents to submit background jobs to the local LLM.

---

## v0.10.0 (2026-08-03) - Note Renaming, Graph Community Hubs, and Literal Search

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

## v0.7.0 - v0.9.31 (2026-07-20 to 2026-08-01) - Engine Modes, Web Search, and Graph Exporters

### Added
- **Tri-Mode Engine Architecture (`engine_mode`)**:
  - `local`: synthesis and summarization run offline on local llama.cpp.
  - `agent`: zero local LLM overhead; synthesis delegated to calling agent.
  - `hybrid`: interactive work handled by agent; maintenance and bulk synthesis handled locally.
- **Graph Exporters (`graph_export`)**:
  - Export code knowledge graphs to GraphML (Gephi/Cytoscape), SVG, and Cypher (Neo4j).
- **Opt-in Web Search**:
  - Privacy-preserving DuckDuckGo search integration via `ddgs`.

---

## v0.5.0 - v0.6.4 (2026-06-29 to 2026-07-10) - Folder Ingestion, Semantic Relinking, and Multiplatform Downloader

### Added
- **Recursive Folder Ingest (`ingest_folder` / `ingest_folder_bg`)**:
  - Extraction and vector indexing of PDF, DOCX, XLSX, PPTX, HTML, and MDX files.
- **Semantic Note Relinking (`relink_folder`)**:
  - Automated cross-linking of unlinked notes based on embedding similarity thresholds.
- **Multiplatform Llama.cpp Downloader (`src/delegation_core/downloader.py`)**:
  - Automated release asset discovery and extraction for Linux (`.tar.gz`), macOS (`.tar.gz`), and Windows (`.zip`).

### Fixed
- **YAML Frontmatter Colon Quoting**:
  - Sanitized and quoted frontmatter scalars containing colons and special characters.
- **Reasoning Tag Stripping**:
  - Added `_strip_think_tags()` to purge chain-of-thought `<think>` blocks from frontmatter.

---

## v0.1.0 - v0.4.0 (2026-06-10 to 2026-06-27) - Inception: Local Memory Bridge & Vault Vector Store

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
