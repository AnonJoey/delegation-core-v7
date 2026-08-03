# Changelog

## Unreleased — 2026-08-03

Found while ingesting the `hermes-agent` repository (7.7k files, 115.756 graph
nodes) into the vault. The corpus was large enough to surface failures that a
small graph hides: every one of these produced *plausible* output rather than an
error, which is why they survived unnoticed.

### Fixed

1. **`label_communities_by_hub()` was never called — every graph artifact fell
   back to "Community {cid}".** The function existed in `graph/cluster.py`,
   correct and testable, with no caller anywhere in the repository.
   `graphbridge.build_graph()` passed a literal `{}` as `render_report`'s
   `community_labels` argument and omitted it entirely for `to_json`, `to_html`
   and `to_wiki` — all four already accepted the parameter, and all four
   silently used their fallback. A 2693-community build filed 2693 vault notes
   titled `Community 0`..`Community 2692`: bodies searchable by embedding, but
   titles and Obsidian graph node labels carrying no information at all.
   Wiring the labeler in dropped generic names from 2693 to 0.

2. **Hub selection named communities after imports.** With labels wired, the
   first real build produced communities called `Any`, `Path` and `ValueError`.
   Imported and builtin symbols accumulate high degree across a codebase and won
   an unrestricted hub search. Nodes referenced but not defined in the corpus
   carry an empty `source_file`, so hub selection is now restricted to
   locally-defined symbols, falling back to the full set for communities that
   contain none.

3. **Hub selection named communities after docstrings.** Extractors attach
   docstrings and test descriptions as node labels (`file_type == "rationale"`),
   which produced 86 wiki articles — and therefore 86 vault filenames — like
   `1000_comments_on_a_single_task_—_build_worker_context_should_....md`. Hub
   candidates now require an identifier-shaped label. A community with no such
   node (typically a single rationale node) is named after its dominant source
   file's stem, plus a bounded excerpt to keep same-file communities distinct —
   39 separate communities out of `tui_gateway/methods_session.py` had otherwise
   collapsed to `methods_session` with `_2`..`_39` suffixes.

4. **`safe_filename()` truncated mid-token.** The 50-character slice ran after
   the trailing-punctuation strip, not before, so a real `write_note` call
   produced the stem `"...dissecação da arquitetura ("` — a dangling opening
   paren in the filename and in the note's graph node label. Truncation now cuts
   on a word boundary when that keeps the stem recognisable, then strips
   punctuation the cut can strand.

5. **`VaultManager` silently indexed into the current working directory.** An
   unset `vault_path` resolves to `Path(".")` — an existing directory, so no
   existence check catches it — and `chroma_path.mkdir()` then created a
   complete ChromaDB wherever the process happened to be running, reporting
   success. `Config.load()` degrades to defaults on any read error, so a corrupt
   `config.json` reaches this state in production, not just from a hand-written
   script. `_init()` now refuses to start with an unconfigured `vault_path`.

### Added

- **`task_status()` reports pacing, not just elapsed time.** While a job runs it
  now also returns `typical_seconds` (median of the last 10 successful runs of
  that task, persisted to `~/.delegation_core/job_durations.json`) and
  `check_again_in_seconds`. Elapsed time alone cannot distinguish "20s in, 8
  minutes to go" from "nearly done", so a caller polling a 7-minute graph build
  had to either poll every 30s or abandon the tool and watch the output
  directory from a shell — which is what happened in practice. Both fields are
  absent on a task's first run, when there is no history to reason from.

- **`graph_build` / `graph_build_bg` / `graph_preview` accept `exclude`.**
  Gitignore-syntax patterns, forwarded to `detect()`'s existing
  `extra_excludes` parameter — available all along, never wired to the caller,
  the same shape as the `community_labels` bug above. Without it the only way
  to keep a repository's test tree out of a graph was to build everything and
  prune afterwards: one real build filed 1071 vault articles for communities
  made entirely of test files, removed by hand. On `hermes-agent`,
  `exclude=["tests/", "tests-js/", "website/"]` cuts the scan by 45% (7719 →
  4268 files). Passing the same patterns to `graph_preview` sizes the build
  before committing to it.

### Notes

Items 1 and 5 share a shape worth naming: a fallback that *fabricates* a
plausible value rather than degrading to empty. `test_graphbridge.py` documented
that it did not exercise `build_graph()` because the extraction stages are heavy
— and that is exactly where item 1 lived. The new seam test fakes those stages
and runs the labeler for real, in 0.1s.

## v0.6.4 — 2026-07-09

Found during the v6.3 install on Abner's Windows machine (workarounds applied manually
at the time; root-caused and fixed here).

### Fixed

1. **`download_llama_binary()` was broken on every platform, not just Windows.**
   Found while re-verifying the original avx2 fix (below) against the *live*
   llama.cpp release instead of a synthetic test — the live data exposed three
   separate, compounding bugs:
   - **Windows asset matching**: `_get_release_asset` required `"avx2"` in the
     filename, but upstream renamed the Windows CPU build (now
     `win-cpu-x64.zip`, no `avx2` substring) — match always returned `None`.
   - **Linux/macOS asset matching**: releases are packaged as `.tar.gz`, but the
     matcher only ever looked for `.zip` — `Linux`/`Darwin` candidates were
     *always* empty, unconditionally, independent of the Windows bug.
   - **Extraction only pulled the single named binary.** `llama-server`/
     `llama-server.exe` is a thin stub dynamically linked against ~10-50 sibling
     `.so`/`.dll` files in the same archive (`libllama-server-impl`, per-CPU-
     microarch `libggml-cpu-*`, etc.) — extracting just that one file produces
     something that exists on disk but can't launch (missing shared library on
     Windows, and on Linux/macOS a same-named-but-wrong-version system library
     gets picked up instead, once the SONAME symlinks are lost — see below).
   - A naive "extract everything" fix still wasn't enough: `tarfile`'s
     `member.isfile()` filter excludes symlinks, and the release tarball ships
     versioned real libraries (`libggml.so.0.15.3`) plus unversioned SONAME
     symlinks (`libggml.so.0 -> libggml.so.0.15.3`) that the dynamic linker
     actually resolves by name. Dropping them meant the extracted binary (which
     has `RUNPATH=$ORIGIN`, confirmed via `readelf -d`) fell through to
     system-wide `/usr/lib`, found a different-version system-installed
     `libggml-base.so.0` there, and segfaulted (`SIGSEGV`, exit -11) on ABI
     mismatch — confirmed by reproducing the exact segfault, then fixing it,
     with a real download+extract+`llama-server --version` execution.
   - Fixed: asset matching now accepts either `.zip` or `.tar.gz`, excludes
     GPU-backend variants (cuda/hip/vulkan/sycl/opencl/openvino/rocm) and the
     `cudart-` runtime-helper package from the fallback tier (verified this
     was needed — an unfiltered fallback alphabetically picks
     `cudart-llama-bin-win-cuda-*.zip` before the real CPU binary), and
     extraction now unpacks the *entire* archive (files + symlinks), flattening
     the release's single top-level folder. Verified end-to-end for real:
     Windows zip structure (51 files, correct flat layout, no symlinks
     present so none needed), macOS tarball structure (62 members, 18
     symlinks, same pattern as Linux), and a full live run on Linux
     (`llama-server --version` exits 0 and prints the real version string).
2. **Unquoted `title:` in frontmatter broke YAML on titles containing `:`** — four
   write sites (`write_note` MCP tool, session-export hook, synthesizer LLM output,
   and the synthesizer's failure fallback) wrote `title: <value>` unquoted. A title
   like "Standup: Q3 planning" produces invalid YAML frontmatter that Obsidian's
   parser chokes on. All four sites now write through `vault.yaml_quote_scalar()`;
   the synthesizer additionally force-quotes whatever title the LLM produced as a
   safety net (prompt compliance isn't reliable) via a new `_quote_frontmatter_fields()`
   step in `sanitize_note()`. The three places that read title back out of
   frontmatter (`vault.list_notes`, `vault._parse_frontmatter`, `organizer.py`'s
   heal loop) now unquote via `vault.yaml_unquote_scalar()` so titles display
   without literal quote characters.
3. **Same unquoted-colon risk on `client:`** — found while auditing older vault notes
   for this exact failure class. `client` is free text sourced from the ingest
   sidecar (client/project name), so it can contain `: ` just like title. Folded into
   `_quote_frontmatter_fields()` (now covers both `title` and `client`) and into the
   synthesizer's failure-fallback write.
4. **No `<think>...</think>` stripping in `sanitize_note()`** — also found via the
   vault audit: 20 older notes had raw reasoning-model chain-of-thought leaked
   directly into a frontmatter field (schema predates this codebase, so not
   reproducible by the current write paths, but nothing here would have caught it
   either). Added `_strip_think_tags()`, run first in `sanitize_note()`, before any
   other cleanup — strips closed `<think>...</think>` blocks and, if the model's
   output got truncated mid-reasoning, drops a dangling unclosed `<think>` and
   everything after it.

## v0.6.3 — 2026-07-03

Skills bundle. The distributable now also deploys a set of Claude skills to the
machine's universal Claude Code layer, so the same skill set travels with the package.

### Added

1. **Bundled Claude skills** (`skills/`) — 17 skills from `anthropics/skills`
   (algorithmic-art, brand-guidelines, canvas-design, claude-api, doc-coauthoring,
   docx, frontend-design, internal-comms, mcp-builder, pdf, pptx, skill-creator,
   slack-gif-creator, theme-factory, web-artifacts-builder, webapp-testing, xlsx)
   plus `document-format-skills` (KaguraNanaga). Installed to `~/.claude/skills/`,
   which Claude Code loads in every session on the machine, independent of any
   plugin configuration — so they are available universally, not just where a
   plugin is wired up.
2. **Installer skill deployment** (`install.sh`, `install.bat`) — copies each
   bundled skill to `~/.claude/skills/<name>`, **never clobbering** a skill the
   user already has by that name (kept-yours guard). Skills take effect on the
   next Claude Code session start.

## v0.6.2 — 2026-07-03

Critical index-integrity fix. Follows v0.6.1's read-only health-metric fix.

### Fixed

1. **`reindex_vault` deleted subfolder notes from the search index** —
   `vault.py::reindex_vault` scanned each content folder with a **non-recursive**
   `glob("*.md")` (a v6.0 regression — 0.5.0 correctly used `rglob`). Because the
   orphan sweep deletes any indexed note whose path is not in the freshly-scanned
   `on_disk` set, every note living in a subfolder was treated as an orphan and
   **removed from ChromaDB on each reindex** — silently collapsing search. On the
   first field vault a post-upgrade reindex dropped the index from 411 → 69 notes.
   Restored to `rglob`. Unlike the health metric (read-only), this bug lost index
   rows, so **after upgrading run `delegation-core reindex` once** (or
   `vault_reindex_bg`) to rebuild; the corrected sweep no longer prunes subfolders.

## v0.6.1 — 2026-07-03

Portability + a second health-metric false-positive fix. Generalizes the v6
linking redesign so the package can be dropped onto *any* deployment (fresh or
existing) without hand-holding.

### Fixed

1. **Health metric ignored notes in subfolders** — `vault.py::get_health_summary`
   scanned each content folder with a **non-recursive** `glob("*.md")`. Vaults
   that nest notes (`research/<Client>/…`, `meetings/…/2024-2025/…`,
   `reference/<project>/…`) had those notes invisible to the link resolver, so
   every `[[link]]` pointing at them counted as broken. Changed to
   `rglob("*.md")` — mirrors how Obsidian resolves by basename and how ChromaDB
   already indexes recursively. On the first field vault this took
   broken_links 128 → 6 and corrected total_notes 38 → 411 (now equals
   `indexed_notes`). No note bodies are edited — this is purely a measurement fix,
   consistent with v0.6.0's finding.

### Changed

2. **Installer is now upgrade-safe / idempotent** — `install.sh`, `install.bat`
   - Backs up the existing package to `~/.delegation_core/backups_pre_upgrade_<ts>/`
     before reinstalling (reversible).
   - **Never clobbers customized agent docs.** If `AGENT_GUIDE.md` /
     `CLAUDE_SYSTEM_PROMPT.md` already exist (e.g. a translated copy), the user's
     version is kept and the shipped one is written as `<name>.dist.md`.
   - **Skips the setup wizard when `config.json` exists**, so an upgrade never
     re-prompts or overwrites a working configuration; prints restart guidance
     instead. Fresh installs still run the wizard.
   - Invalidates the cached `vault_health.json` so the corrected metric
     recomputes on next start.

## v0.6.0 — 2026-07-03

Linking redesign. Originated in the SAAD deployment (see `DEPLOYMENT_LOG.md`),
which found that the vault's "259 broken links / 260 orphans" health metric was
~98% false positives and that `relink_folder` would *manufacture* broken links if
run. Root causes were two code defects, now fixed.

### Fixed

1. **`relink_folder` linked by title, not stem** — `linker.py`
   `wikilinks()` (note-creation path) emitted `[[stem]]` (resolves), but
   `relink_folder()` emitted `[[title]]` (does not — files are named
   `YYYY-MM-DD-slug.md`). On a real folder this produced ~84% broken links.
   Both paths now share one vocabulary: **target = filename stem, always.**

2. **Health metric counted non-links** — `vault.py::get_health_summary`
   Every `[[...]]` in a note counted as a wikilink, including bash `[[ -f "$x" ]]`
   test syntax in ingested scripts and imported Obsidian path-links
   `[[Folder/File.pdf]]`. New `_countable_wikilinks()` strips fenced/inline code
   and keeps only note-like targets. Resolution now checks **stems ∪ frontmatter
   aliases** (mirrors Obsidian). `orphans` redefined from "note has no ## Related
   heading" to a **true graph orphan** (nothing links to it), sessions excluded.

### Added

3. **Aliased wikilinks `[[stem|Display]]`** — `linker.py`, `splitter.py`
   New shared `format_link()` / `clean_display()` used by every generator
   (`wikilinks`, `relink_folder`, sibling links, backlinks). Target resolves
   deterministically; display is a readable title (date-prefix and staging-
   truncation stripped). Correct *and* legible in the Obsidian graph.

4. **Obsidian `aliases:` frontmatter** — `linker.py`, `organizer.py`
   New `ensure_aliases()` / `frontmatter_aliases()`. New notes register their
   readable title as an alias at synthesis time, so a human-written `[[Title]]`
   resolves even though the file is a dated slug. Additive, idempotent.

### Not changed

- **Filenames are not renamed.** Too risky (breaks ChromaDB paths + existing
  links); readability comes from the alias display, not renaming.

### Upgrade note

Drop-in over v5.1: `pip install -e .` then restart the MCP server. The linking
fixes are code-only; existing notes are migrated by the deployment's own vault
pass (SAAD ran a full 442-note migration — see `DEPLOYMENT_LOG.md`).

## v0.5.1 — 2026-07-03

Patch release over v5 (0.5.0). Four fixes, no behavior/feature changes to the
tool surface. Each fix carries an inline comment at the call site explaining the
reasoning; this file is the summary.

### Fixed

1. **Version string inconsistency** — `src/delegation_core/__init__.py`
   The module docstring read `v0.2.0` while `pyproject.toml` declared `0.5.0`,
   so `import delegation_core; delegation_core.__doc__` disagreed with
   `pip show delegation-core`. Docstring corrected to `v0.5.1` and an explicit
   `__version__ = "0.5.1"` added so there is now a single machine-readable
   source of truth. `pyproject.toml` bumped to `0.5.1`. The server's startup
   log banner (`server.py`) also updated from `v0.5` to `v0.5.1`.

2. **`asyncio.get_event_loop()` in the atexit cleanup handler** — `server.py`
   `_cleanup()` is registered with `atexit`, i.e. it runs at interpreter
   shutdown in a synchronous context with no running event loop. Under Python
   3.12, `asyncio.get_event_loop()` with no current loop emits a
   `DeprecationWarning` (and is slated to raise in a future release), so the
   engine's async shutdown (`_engine.aclose()`, which closes the httpx client to
   llama.cpp) could be silently skipped. Rewritten to probe
   `get_running_loop()` (guarding the normal "no loop" case) and otherwise use
   `asyncio.run()` to spin up a fresh loop and flush the coroutine. This was one
   of three `get_event_loop()` sites flagged on 2026-06-11; the other two were
   already migrated in v5 — this was the straggler.

3. **Ambiguous process-ID matching** — `tracker.py`
   `_find_process()` matched on `startswith(id) OR endswith(id)`. Process IDs are
   `"proc_" + random hex`, so every ID ends in hex and a bare hex fragment could
   match unrelated processes — the exact collision the 2026-06-11 fix removed and
   v5 reintroduced. Reverted to prefix-only matching (exact match still wins
   first), which is what abbreviated IDs like `proc_a1b2` actually need.

4. **Installer clobbers a torch-compatible setuptools** — `install.sh`, `install.bat`
   Both installers ran `pip install --upgrade pip setuptools wheel`, pulling
   setuptools 82.x. `torch` (pulled transitively by `sentence-transformers`)
   requires `setuptools<82`, so a fresh install could leave the embedding stack
   unimportable. Pinned to `"setuptools<82"`; `pip` and `wheel` stay unpinned.

5. **Web search is now opt-in** — `pyproject.toml`, `config.py`, `server.py`
   The v5 `search_web` tool reaches the public internet (DuckDuckGo), which sits
   outside delegation-core's local-only design. In v5.1 it is opt-in on two
   levels: (a) the `duckduckgo-search` dependency moved out of the base
   requirements into a `[web]` extra — `pip install "delegation-core[web]"` — so
   a default install never pulls it; (b) a new `web_search_enabled` config flag
   (default `False`) gates the tool at call time. The tool still registers, but
   returns a clear "disabled / opt-in" message until both the flag is set and
   the extra is installed. Exposed in the `status` tool output for visibility.
   The extra uses `ddgs` (the maintained rename of `duckduckgo-search`; the old
   name now warns and returns 0 results). `search_web` imports `ddgs` first and
   falls back to the legacy module name for older installs — result fields
   (`title`/`href`/`body`) are unchanged.

### Added

6. **Engine mode: `local` vs `agent`** — `config.py`, `engine.py`, `server.py`, `wizard.py`
   New `engine_mode` config (default `"local"`, unchanged behavior). Set to
   `"agent"` for machines that can't run a local model alongside other apps:
   - **No local model.** The engine never launches llama.cpp; `check_health`/
     `ensure_running` short-circuit.
   - **Generation is delegated to the calling Claude.** Interactive tools
     (`search_vault`, `compress`, `search_web`) return the raw retrieved
     material plus an `instruction`/`mode:"agent"` field instead of a
     locally-generated summary — the agent synthesizes. `search_web` still
     fetches locally via `ddgs`; only the summarization is delegated.
   - **Background maintenance never hangs.** `engine.invoke()` returns a
     deterministic extractive fallback in agent mode (no agent is in the loop
     during fire-and-forget jobs), so classify/synthesize/heal keep moving with
     zero local compute.
   - **Embeddings + search stay local** in both modes (BGE + ChromaDB).
   - **Installer** (`wizard.py`) asks local-vs-agent up front; agent mode skips
     the ~2 GB model + llama binary download entirely.
   - **Visibility:** `heartbeat` reports `engine_mode` and treats agent mode as
     `healthy` (llama offline is expected), with `llama_cpp:"delegated-to-agent"`.

7. **Engine mode: `hybrid`** — `config.py`, `server.py`, `wizard.py`
   A third `engine_mode` that combines the other two: **interactive/light work is
   delegated to Claude** (fast, no local load) while **big/slow/bulk generation
   uses the local model**. Routing is deliberate, not silent:
   - `Config.route(task, input_chars, use_local)` decides `local` / `agent` /
     `offer`, considering **task type + input size + explicit opt-in**.
   - **Background/bulk pipelines** (`synthesize`, `heal`, ingestion) route to the
     local model automatically — no agent is in the loop there to delegate to.
   - **Interactive tools** (`search_vault`, `compress`, `search_web`) gained a
     `use_local` param. Below the size threshold they delegate to Claude; at/above
     `hybrid_local_min_chars` (default 8000) they return `mode:"offer"` with an
     `est_tokens_if_agent` cost estimate and an explicit invitation to re-call
     with `use_local=true` — so the local-model option is **surfaced with its cost,
     never silently taken**. This is the "evaluate the token cost, then choose"
     behavior: manual (Claude) vs. explicit local offload.
   - `heartbeat` reports `engine_mode` + `hybrid_local_min_chars`; hybrid is
     `healthy` even when llama isn't running yet (`on-demand` — started only when
     a big/bulk task actually routes local).
   - Installer offers local / agent / **hybrid**; hybrid downloads the model
     (needed on-hand for big tasks), agent still skips the download.

### Known / not changed

- `AGENT_GUIDE.md` still says "It is not a web search tool." With web search now
  opt-in and off by default, this statement is accurate for a default install,
  so the guide is left unchanged.

### Upgrade note

This is a drop-in replacement for v5. If already on v5, upgrade in place:
`~/.delegation_core/venv/bin/pip install /path/to/delegation_core_v5.1`
then restart the MCP server. No venv rebuild or model re-download needed.

> **Correction (multi-implementation reality — see `DEPLOYMENT_LOG.md`).**
> The "drop-in over v5" framing above assumes a single linear lineage. In
> practice v5.1 lands on top of **several divergent field implementations** (SAAD,
> MAURICIO, …) that never shared a common v5 install — some are still on
> pre-refactor branches (e.g. `0.1.0`). For those, this release is a **major
> refactor migration, not a patch**: the monolith split into 11 modules and the
> per-implementation local hardening was upstreamed unevenly (see junk.py "SAAD",
> sidecar.py/synthesizer.py "MAURICIO"). Do not assume a v5 baseline. Per-install
> findings, downgrades, and ported-forward fixes are logged in `DEPLOYMENT_LOG.md`;
> upgraders should read their own deployment's entry before running the command
> above.
