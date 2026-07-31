# delegation-core — Project Handoff

_Last updated: 2026-07-28, at commit `ae4189b` / tag `dashboard-v0.2.0` (core v0.9.0, dashboard v0.2.0)._

Written for whoever (human or agent) picks this project up next. Facts below were true and
verified at the timestamp above — re-verify anything load-bearing before acting on it.

## What this is

A local MCP delegation server (FastMCP, stdio transport) over an Obsidian vault
(ChromaDB + BGE embeddings), with an optional local llama.cpp LLM, a vendored
code-graph pipeline (from Graphify), a full CLI, and a cross-platform Tauri
desktop dashboard. 31 public MCP tools (32 `@mcp.tool()` registrations; one,
`_bg_maintenance_wrapper`, is an internal helper).

## Repos and remotes

- Working checkout: `/home/joey/Projects/delegation_core_v6.4` (path name is historical).
- **Active repo: `fork` → github.com/AnonJoey/delegation-core-v7** (private). Local
  `master` tracks `fork/master`. Push here.
- Frozen archive: `origin` → github.com/AnonJoey/delegation-core-v6.4. Do not push
  without an explicit ask.

## Current state (all verified, not assumed)

- **Tests: 204 passing** (`~/.delegation_core/venv/bin/python3 -m pytest tests/ -q`),
  19 test files, fast and offline (fakes/monkeypatch; no real BGE/ChromaDB/llama/network).
- **Cargo Check: clean (0.42s)** (`cargo check --manifest-path dashboard/src-tauri/Cargo.toml`).
- **CI: validated for real.** `.github/workflows/build-dashboard.yml` ran successfully on
  the `dashboard-v0.2.0` tag across ubuntu/windows/macos-latest — first-ever real run of
  the Windows/macOS legs, all green. A **draft release** exists with 7 installers attached
  (.msi + NSIS .exe, .dmg + .app.tar.gz [aarch64], .deb/.rpm/.AppImage). Not yet published.
- **Local install on this dev machine:** the Python side is an editable install in
  `~/.delegation_core/venv`; the dashboard runs from
  `dashboard/src-tauri/target/release/dashboard` via a hand-made desktop entry
  (`~/.local/share/applications/delegation-core-dashboard.desktop`) because this machine
  (CachyOS/Arch) has no dpkg/rpm for the packaged install path.
- **llama.cpp controls:** Start/stop/restart directly from the dashboard's topbar buttons or Settings overlay.

## Architecture notes that matter

- Every MCP client gets its own `delegation-core run` process (stdio, 1:1). The dashboard's
  `dashboard_api.py` is a **separate sidecar process** spawned by the Tauri Rust backend —
  no shared memory with the MCP server. Client presence is tracked via
  `~/.delegation_core/sessions/<pid>.json` heartbeat files (client_tracking.py middleware).
- The sidecar is spawned with `--parent-pid <tauri pid>`; a psutil watchdog
  (create_time-pinned, zombie-aware) self-terminates the sidecar when the parent dies.
  This closed a real orphan bug (each orphan held ~600MB GPU for its BGE model).
- Long-running MCP tools (`run_maintenance`, `graph_build`, `graph_affected`) are offloaded
  off the event loop via `asyncio.to_thread`; `_bg` variants use `jobs.submit` threads.
- `embeddings.py` falls back to CPU once if CUDA construction fails — required on this
  machine because llama.cpp routinely consumes ~11.3GB of the 15.5GB GPU, which used to
  permanently break search until fixed.
- Frontend is vanilla JS under a strict CSP (`default-src 'self'`) — no frameworks, no
  external resources. All untrusted strings route through `escapeHtml()` (fully audited).
  Graph canvas is HiDPI-aware (devicePixelRatio-scaled buffer + ResizeObserver + DPR-change
  matchMedia watcher).

## Recent history (Session Summary — 2026-07-28)

1. **Native Window Commands in Tauri (`src-tauri/src/lib.rs`)**:
   - Added native Rust IPC commands `minimize_window`, `toggle_maximize_window`, and `close_window`. Registered in `generate_handler!`.

2. **Server & Llama Power Controls**:
   - Added `#server-power-btn` (toggles polling active state) and `#server-restart-btn` (restarts llama.cpp, clears state, and re-initializes telemetry, tree, and graph).

3. **Obsidian-Inspired Settings Overlay Modal & Config API**:
   - Extended `dashboard_api.py` with `GET /api/config` and `POST /api/config/update`.
   - Added GGUF model auto-detection (`~/.delegation_core/models/*.gguf`).
   - Built a 4-section Obsidian-style settings modal in `index.html`, `styles.css`, and `main.js`:
     - ⚙️ **Work Mode & Engine**: `LOCAL`, `AGENT`, `HYBRID` cards, Hardware Budget Mode (`normal`, `cpu`, `auto`), Synthesis toggle/language (`en`/`pt`), DuckDuckGo web search.
     - 🧠 **Local LLM Model**: GGUF auto-detector dropdown + custom model path input, llama-server binary path, context size (`llama_ctx`), and GPU layers (`llama_ngl`).
     - 🔍 **Embeddings & Vector Search**: BGE model input, real-time interactive search threshold slider (`0.55`), merge threshold slider (`0.88`), max tokens (`2048`).
     - 📁 **Vault & Storage**: Vault location path and folder chips.

4. **Process Sanitation & Orphan Purger**:
   - Updated `client_tracking.py`'s `list_connected_clients()` to verify `psutil.pid_exists(pid)` and delete dead session `.json` files immediately.
   - Added `POST /api/system/purge_orphans` to `dashboard_api.py` to kill orphaned `dashboard_api` processes whose parent PID is dead/1 and remove stale session files.
   - Added a **Purge Orphans** button in the Fleet Inspector panel.

5. **Responsive Window & Canvas Resizing**:
   - Added `ResizeObserver` and `matchMedia("(resolution: ...dppx)")` watcher to `ForceGraph` in `main.js` so graph canvas re-buffers without distortion or blurriness on window/DPR resize.
   - Added interactive drag-to-resize splitter bar (`#graph-resizer`) between graph pane and main workspace (`setupPaneResizer()`).
   - Updated flexbox bounds in `styles.css` (`min-width: 0`, topbar whitespace/scrolling) for clean re-flowing across display sizes.

6. **Test Suite & Isolation Fixes**:
   - Updated `tests/test_dashboard_api_routes.py` fixture to monkeypatch `CONFIG_DIR`/`CONFIG_FILE` to `tmp_path`, preventing test runs from overwriting real user `~/.delegation_core/config.json`.
   - All 204 unit tests passing cleanly in ~30s.

## Hard rules / invariants

- **Never touch the user's vault** (path in `~/.delegation_core/config.json` →
  `/home/joey/Documents/Projects_Archive/Claude Vault`) or `~/.delegation_core/models/`
  from installers/uninstallers/tests. The uninstallers hard-abort if the vault resolves
  under `~/.delegation_core`.
- Tests must stay offline/fast; no test may touch the real process table for termination,
  the real vault, or real model loading.
- Dashboard identifier `com.delegationcore.dashboard` must not change (orphans installs).
- When testing the app on this machine, close the window cleanly or SIGKILL is fine now
  (watchdog reaps the sidecar) — but always verify no `dashboard_api` processes linger.

## Known limitations / open items

- **Draft release unpublished** — awaiting review/publish decision.
- **macOS build is aarch64-only** (`macos-latest` = ARM runners). Add `macos-13` or an
  `x86_64-apple-darwin` target for Intel Macs if wanted.
- **Unsigned builds** — SmartScreen/Gatekeeper warn on first launch. Signing needs paid certs.
- **AppImage cannot build on this dev machine** (sandboxed linuxdeploy/FUSE) — builds fine
  in CI. Not a code bug.
- **No real dpkg/rpm install test yet** — needs an actual Debian/Fedora box or VM.
- **No auto-update** (Tauri updater plugin) — deliberate deferral.

## How to do things

```bash
# Tests
~/.delegation_core/venv/bin/python3 -m pytest tests/ -q

# Dashboard dev / build
cd dashboard && npm run tauri dev      # dev (WEBKIT_DISABLE_DMABUF_RENDERER=1 is set in lib.rs — needed on NVIDIA/Wayland)
cd dashboard && npm run tauri build    # .deb/.rpm locally; AppImage only in CI

# Release pipeline
git tag dashboard-vX.Y.Z && git push fork dashboard-vX.Y.Z   # builds 3 platforms, attaches to a draft release

# Sanity check the machine
nvidia-smi                             # llama.cpp eats ~11.3GB when on; BGE falls back to CPU if full
ps aux | grep dashboard_api            # should be empty when no dashboard window is open
```
