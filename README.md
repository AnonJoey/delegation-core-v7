# delegation-core

A local MCP delegation server: an Obsidian vault (semantic search via BGE + ChromaDB), an
optional local LLM (llama.cpp) for summarization/synthesis, and a vendored code-graph
pipeline — all usable either as an MCP server (Claude Desktop/Code) or directly from a
terminal via the `delegation-core` CLI.

The idea: push retrieval, classification, and compression work onto a local, zero-marginal-cost
model instead of spending an LLM agent's tokens on it. See `AGENT_GUIDE.md` for the full
protocol an AI agent should follow when this MCP server is connected.

## What it does

- **Vault**: an Obsidian-compatible markdown vault, semantically searchable via BGE
  embeddings + ChromaDB. Drop files into `_inbox/` and they get classified, synthesized into
  clean notes, wikilinked, and filed — or write/read/search notes directly.
- **Code graph** (opt-in, `[graph]` extra): build a knowledge graph of a codebase — AST
  extraction across ~30 languages via tree-sitter, community detection, god-node/blast-radius
  analysis. Produces `graph.json`, an interactive `graph.html`, Mermaid architecture diagrams
  (`callflow.html`), a human-readable `GRAPH_REPORT.md`, and per-community wiki articles filed
  into the vault. Vendored and adapted from
  [Graphify](https://github.com/Graphify-Labs/graphify) — see `THIRD_PARTY_LICENSES/`.
- **Process tracking**: lightweight cross-session task tracking that survives restarts.
- **Dashboard** (`dashboard/`): a native Tauri desktop app — a top status/clients bar, a
  persistent vault-graph pane, a notes browser, and a cross-session task tracker. See
  `dashboard/README.md`.

## Install

The platform installer is the easiest path: it detects Python, creates a venv, installs
delegation-core, installs the native Tauri dashboard app (from a local build if present,
otherwise the latest GitHub release, falling back to manual build instructions if neither
is available), then launches the setup wizard automatically. Re-running it on an existing
install upgrades in place — `config.json` is preserved and the prior install is backed up,
the wizard is not re-run.

```bash
./install.sh              # Linux / macOS
install.bat                # Windows (or double-click)
```

(macOS: `install.command` is a double-clickable Finder shim to `install.sh`.)

To remove everything the installer created — venv, config, logs, hooks — run the matching
uninstaller (`uninstall.sh` / `uninstall.bat` / `uninstall.command`). It never touches your
vault or downloaded model weights.

For a manual/dev install instead (no OS packages, no dashboard app):

```bash
pip install -e .                    # core (vault + MCP server)
pip install -e ".[graph]"           # + code-graph pipeline (~27 tree-sitter language bindings)
pip install -e ".[web]"             # + web search (DuckDuckGo)
pip install -e ".[dev]"             # + pytest, for running tests/
```

Then run the interactive setup wizard once per machine:

```bash
delegation-core setup
```

This finds (or lets you create) an Obsidian vault, downloads/configures a local llama.cpp
model (or lets you skip it — see "engine modes" below), and optionally registers
delegation-core to start automatically (systemd/launchd/Task Scheduler).

## Using it as an MCP server

Register it in Claude Desktop's config or `~/.claude.json` (Claude Code). Once connected,
31 tools are available — `search_vault`, `write_note`, `graph_build`, `graph_affected`,
`process_create`, and more. Full protocol and tool reference: `AGENT_GUIDE.md`.

## Using it as a CLI

Everything the MCP server exposes is also reachable directly from a terminal:

```bash
delegation-core status                          # vault/model/llama.cpp health
delegation-core search "auth token refresh"      # BGE search, no LLM needed
delegation-core note write Reference "Some note" --file notes.md
delegation-core note read "Some note"
delegation-core graph build ~/code/my-project    # code graph -> vault
delegation-core graph affected my-project auth.py  # blast-radius query
delegation-core graph hook install ~/code/my-project  # auto-rebuild on every commit
delegation-core process create "Migrate auth" --steps "plan,implement,test"
```

Run `delegation-core --help` (and `<command> --help`) for the full tree — `setup`, `run`,
`status`, `reindex`, `maintain`, `ingest`, `relink`, `search`, `compress`, `note`, `graph`,
`process`.

## Engine modes

Set in `config.json` (`engine_mode`), chosen during `setup`:

- **`local`** — summarization/synthesis runs on a local llama.cpp model. Fully offline once
  the model is downloaded.
- **`agent`** — no local model; synthesis/compression is delegated to whichever MCP client
  is calling (e.g. Claude Code). For machines that can't spare the RAM/CPU for a local model.
- **`hybrid`** — light interactive work delegates to the calling agent; heavy/background work
  (maintenance, healing, bulk synthesis) always runs locally.

BGE embeddings + ChromaDB search always run locally in every mode.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

Tests are a starter suite (config, vault helpers, the graph registry/folder-resolution logic,
the git hook installer, process tracking) — fast, offline, no ChromaDB/BGE/llama.cpp
dependency. They don't yet cover `organizer.py`'s synthesis pipeline or `vault.py`'s
ChromaDB-backed search, which would need heavier fixtures.

## More detail

- `AGENT_GUIDE.md` — full MCP tool reference and protocol, written for the AI agent side.
- `CHANGELOG.md` — version history.
- `DEPLOYMENT_LOG.md` — per-deployment upgrade notes (this repo runs on more than one machine).
- `THIRD_PARTY_LICENSES/` — attribution for vendored code (Graphify).
