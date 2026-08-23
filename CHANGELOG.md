# Changelog

## v0.13.0 — 2026-08-23

### Added

- **Client scoping.** A vault organised by client had no way to say "only this
  one". Probing a real deployment: a query for one client's retention metrics
  returned ten results, six of them a different client; a query about team
  structure mixed four clients plus raw audio; a query about revenue
  architecture returned the same deck five times in five formats. `client:` in a
  note's frontmatter is promoted to searchable metadata, and `search_vault`
  takes a `client` parameter that composes with `scope` through `$and` rather
  than replacing it.

  Three things this needed that a narrower fix would have missed:

  - **Normalisation on both sides.** One vault holds `Gazin` on 111 notes and
    `gazin` on 13. ChromaDB's `where` is exact equality, so normalising only on
    write leaves `client="Gazin"` matching nothing it just wrote — which reads
    as "that client has no notes". One function runs on promotion and on query.
    Names that folding cannot merge, because they are genuinely different
    strings, go in `client_aliases` — a judgement about the data, so it is
    configuration.
  - **Reach for ingested files.** They are 92.8% of rows on the deployment that
    asked for this; a filter blind to them covers a fourteenth of the corpus.
    `client_path_roots` names parent directories explicitly and the client is
    the segment directly beneath one — no root, no client, never a guess. Empty
    by default, because a wrong label is worse than none: unlabelled still
    surfaces in an unfiltered search, mislabelled is silently excluded from the
    filter that should have found it.
  - **A way to see the result.** `vault_stats()` now reports `clients` —
    distinct documents per client, as the filter sees them. That is the
    verification surface for a path-derivation rule, and it is how a caller
    learns which names are valid: guessing a slug and getting nothing back is
    indistinguishable from a client with no documents.

  Every hit carries its `client` even unfiltered, so a caller can see that six
  of ten belong to someone else — which is what tells it a filter was called
  for. Index schema bumped to 3: existing rows have no `client`, so the filter
  over an unmigrated index matches nothing and looks like an empty vault. The
  bump forces the one reindex that makes the feature real rather than present.

Tests: 694 → 714.

## v0.12.3 — 2026-08-23

### Fixed

- **The service manager killed the daemon mid-write.** The generated systemd unit
  set no `TimeoutStopSec`, so it inherited a stop ceiling far shorter than the
  daemon's normal work — a full reindex or a relink pass runs for minutes.
  Observed rather than theorised: a restart issued during a relink hit the 10s
  ceiling on the reporting machine, SIGKILLed the process with ChromaDB
  mid-operation, and the next two starts died with SIGSEGV inside
  `chromadb_rust_bindings` on a tokio-rt-worker thread. Only the third came up.
  The index survived, but that interruption is exactly what leaves HNSW segments
  without their SQLite rows — the ghost-row failure this project already had to
  defend search against. `TimeoutStopSec=600` now, and `ExitTimeOut` on the
  launchd plist, whose default 20 seconds has the same problem.

  This matters most during an upgrade, since the runbook asks for the daemon to
  be stopped before the mandatory reindex.

Tests: 692 → 694.

## v0.12.2 — 2026-08-23

### Fixed

- **A note could not be linked by the title it was written with.** `write_note`
  builds `{date}-{safe title}.md`, and nobody types the date when linking — they
  type the title, so `[[Diagnóstico — notas do vault]]` never resolved against
  `2026-08-22-Diagnóstico — notas do vault.md`. Three of the first four broken
  links audited on a real vault were this alone. Resolution now also accepts the
  stem with its leading date stripped, additively, which makes the fix retroactive
  across every note already on disk without touching a file. `safe_filename` also
  cuts at 50 characters, and no resolution rule restores characters a filename
  never held, so `compose_note` emits the untruncated title as an alias when
  truncation actually occurred. The three call sites that each built this name set
  themselves are one function now.

### Added

- **`relink_folder_bg`.** The synchronous tool embeds every note in a folder
  against every other; on a 31-note folder that ran past the MCP client's
  300-second idle timeout, aborting the call and dropping the connection while
  the server carried on with a job nobody was listening for. Both relink calls
  made while tidying a vault reported failure and had in fact succeeded. The
  vault-containment check now lives in one function both entry points call — a
  background variant that quietly omitted it would be a path-traversal hole.

Tests: 683 → 692.

## v0.12.1 — 2026-08-23

Nineteen defects against v0.12.0, from three independent passes: a field report
from a production install, a correctness review of the v0.12 diff, and an audit
of the work those two produced. Four of them lost or hid data.

### Fixed — data loss and silent failure

- **Legacy-collection adoption ignored the vectors it was adopting.** The rename
  of a pre-derivation collection to the model-derived name fired whenever the
  expected collection was absent, without checking what had embedded the rows.
  The common upgrade is also a model change: bge-base's 768-dim rows adopted
  under bge-m3's 1024-dim function fail every query and upsert, and the original
  index no longer sits under the name a downgrade would look for. Gated on the
  vectors' measured dimension; an unmeasured model is left alone.

- **`reindex` stopped re-ingesting external files.** v0.12's per-file mtime cache
  made everything compare unchanged while the command still reported files
  reingested — breaking precisely the recovery case it exists for. It now forces
  the sources whose rows are actually gone, and only those: forcing all of them
  costs a full re-embed on every invocation (4.8 hours for 6,637 files on the
  reporting install), which is how a recovery command stops being run at all.

- **A failed index write was recorded as a success.** `index_note` deletes a
  note's rows before writing its new ones, so a failure mid-way left the note
  with no rows — and `reindex_vault` stamped its mtime anyway, hiding it from
  every later incremental run. It returns a status now; the stamp waits on it.

- **Shortened notes kept their old tail.** `upsert` replaces and never removes,
  so a note edited from twelve chunks to three left nine holding text from the
  previous revision, answering searches with content in no file and invisible to
  the orphan sweep because the note still existed on disk.

- **Dataless detection condemned healthy filesystems.** `st_blocks == 0` is also
  true of btrfs/ext4 inline-extent files and of mounts that report no block
  count; there, every file graded as cloud-evicted and ingest indexed nothing.
  It must now also fail to read a byte.

- **Whitespace-only extraction passed the empty-text guard.** A file of blank
  lines is a truthy string, so it reached the classifier — an LLM handed blank
  text picks an arbitrary folder — and wrote an empty note into the vault.

### Fixed — wrong answers

- **`health_detail` answered from its first call for the life of the process**,
  while its docstring promised the opposite. Repair a broken link, ask again,
  and the same stale list came back.

- **`search_vault`'s scope defaulted to `notes` on every install.** Right for the
  vault it was measured on and exactly wrong for one whose authoritative corpus
  is ingested: 6,637 external files hidden from the path every agent uses. Now
  decided from the vault's composition, with `default_search_scope` to pin it.

- **`status` looked up a hardcoded collection name**, so any install not on
  bge-base reported a healthy index as uninitialised and advised a rebuild
  costing hours. Its row count also no longer calls itself a note count.

- **`compress` ignored `synthesis_lang`.** The prompt was hardcoded English while
  organizer honoured the setting, so the local model chose per call — a batch
  produced a bilingual vault by accident, reported as success every time.

- **The out-of-memory matcher accepted any message containing "out of memory".**
  Its handler moves the model to cpu permanently, so a coincidental match left
  the process an order of magnitude slower with one log line to explain it.

- **The sequence cap was set only at construction**, on a SentenceTransformer
  shared across every embedding function over that model — the last one built
  decided the sequence length for all of them.

### Fixed — cost and hygiene

- `delete_notes` was the one unbatched bulk delete, and its failure was silent.
- The over-fetch cap could return fewer results than the caller asked for.
- `vault_chunk_size` in characters overran the token window of smaller models,
  reintroducing silent truncation at smaller scale. Derived from the model now.
- `chunk_text` looped forever when overlap met or exceeded chunk size, and
  emitted a redundant single-character tail chunk.
- `ingest_forget` matched by string prefix and could delete a newer source's rows.
- `collection.get()` is paged; ghost rows with null metadata no longer break search.
- The transcript filename carried a date, so a session resumed on another day
  wrote a second partial note: 111 transcripts for 47 sessions on one install.
- The generated launchd plist let the daemon inherit `maxfiles 256`.
- `status` shows where the running code loads from, since an editable install
  serves the daemon out of a live working tree and nothing said so.

### Documentation

- `AGENT_GUIDE.md` described the old fixed search scope, and the installed copy
  had drifted from the versioned one — the copy governing behaviour was the one
  not under version control. All three copies are identical again.
- The chunk collapse in `search()` applies to external rows, changing behaviour
  that predates chunking. Recorded as a decision rather than left as a side
  effect.

Tests: 612 → 683.

## Unreleased

### Fixed

- **`graph_list` and `graph_report` were both too large to return.** Measured on
  this machine's registry, `graph_list` answered with 181,666 characters, past
  the MCP tool-result cap — the tool was unusable through the one interface it
  exists for. One field caused it: `vault_paths` is the only unbounded entry in
  a registry record, holding a path per filed vault article, and the six graphs
  here carry 3,391 between them (openclaw 1,441, hermes-agent 1,053). Nothing
  read the array — the CLI prints counts, the dashboard reads `node_count`, and
  `graph_preview` had already settled on `vault_notes_filed` for this same field.
  It now returns that count; measured after, 1,580 characters, a 99.1%
  reduction. The paths stay reachable through `graph_list(name=...)`, which
  returns one graph's record whole.

  `graph_report` had the same problem and no summary available, since every line
  of a report is content the caller asked for: openclaw's is 335,321 characters
  and hermes-agent's 319,254, both past the cap, so the largest graphs were
  exactly the ones that returned nothing. It is paged instead — 30,000 characters
  a page with `next_offset`/`total_chars`/`truncated`, and openclaw's twelve
  pages reassemble byte-identical to the document. Reports under a page come back
  whole and unflagged. Only the MCP path pages: `graphbridge.get_report()` still
  returns the full text for the dashboard and CLI, which render it locally with
  no cap.

### Changed

- **`task_status` says whether a vanished job was lost or never existed.** A
  missing `job_id` answered `{"error": "Job 'x' not found."}` and nothing more.
  Under stdio that was almost always a typo; under the daemon it is almost always
  a restart, and the two call for opposite responses — re-run the tool, or go
  check whether the work already finished. The response now carries
  `job_store_started`, the one fact that separates them, with the reasoning
  spelled out for the agent reading it. It deliberately gained no `status` key:
  `daemon.next_poll_wait()` identifies not-found by that key's *absence*, and any
  value there reads as neither done nor error, which would leave the CLI polling
  a nonexistent job until its timeout. Confirmed by calling `next_poll_wait` on
  such a payload — it returns a wait instead of raising — and pinned by a test.

- **The vault graph drew 13 edges instead of 2962.** `/api/vault/graph` defaulted
  to *including* graph_build articles, while `_build_vault_graph`'s own
  signature, its docstring and the frontend all assumed the opposite — and since
  the frontend passes no `generated` parameter, that one line decided every
  graph the dashboard ever drew. Nodes are capped at the 1500 most recent, and
  3427 of this vault's 3629 notes are generated articles, so the cap filled with
  articles that carry no wikilinks between them and crowded out the hand-written
  notes holding all of them. Measured on the live vault: 238 nodes / 2962 edges
  excluded, against 1500 nodes / 13 edges included. On screen that was a canvas
  with six dots and no lines, which is how it was found. The frontend's own
  "N code-graph articles are under Code" caption had never once been shown.

- **The version was wrong in five places at once.** `pyproject.toml` said
  0.10.0, `__init__.py`'s docstring said 0.10.0, its `__version__` said 0.9.0,
  the installed editable metadata said 0.7.0, and `index.html` hardcoded
  "Orchestrator v0.9.0" into the header users actually look at. This is the
  third recorded drift, each previously re-synced by hand under a comment
  promising lockstep. Now: one literal in `__init__.py`, no copy in the
  docstring, the header served from `/api/status`, and
  `test_version_consistency.py` failing the suite when it drifts again.
  Deriving it from `importlib.metadata` was tried and rejected — an editable
  install freezes metadata at install time, which is why it read 0.7.0.

- **A client hanging up mid-response logged two tracebacks.** The disconnect
  raised out of `_send_json`, was logged at ERROR with a traceback, and then the
  handler tried to send a 500 down the same dead socket — raising again, out of
  the handler entirely and into socketserver's "Exception occurred during
  processing of request". Cheap under a sidecar whose stderr nobody read; these
  handlers now run in the daemon, where it lands in the journal beside real
  faults. A disconnect is logged at debug and not answered.

### Changed

- **The daemon serves the dashboard's JSON API; the Tauri app stops spawning a
  sidecar.** The sidecar was correct under stdio: `mcp.run()` serves one
  transport at a time, so a dashboard could not attach to the MCP server and
  needed a process of its own. It paid for that separation with a second
  `VaultManager` — measured here, opening the dashboard took GPU use from 3826
  to 6055 MiB, the extra 2314 MiB being a second resident copy of BGE-m3, plus a
  second ChromaDB opener on the index the daemon already had open. That is the
  duplication the move to a daemon exists to remove, still in place on the one
  client the transport rearchitecture was originally *for*.

  The daemon holds a warm `VaultManager`, so it now serves those routes itself
  on `dashboard_port` (8788, loopback; 0 disables). No handler changed: they
  read `_cfg`/`_vault`/`_tracker` as module globals, and `serve_in_process()`
  points those at the daemon's instances instead of building new ones. The Tauri
  app probes that port with a real `GET /api/status` — a bare TCP connect would
  accept any process squatting the port — and only spawns the sidecar when
  nothing answers, so a machine running no daemon keeps working unchanged.
  Verified against the live daemon: one process, one 2314 MiB BGE copy, both
  8787 and 8788 served, `/api/vault/search` answering off the real 3629-note
  vault.

  Note the exposure this changes: the dashboard API has no auth of its own (it
  is loopback-bound behind the CORS allowlist added in v0.8.1), and it now
  listens whenever the daemon runs rather than only while the dashboard app is
  open. Any local process can read the vault through it. The MCP transport's
  bearer token does not cover these routes.

- **`reindex`, `maintain` and `ingest` hand their work to the running daemon.**
  Moving the server to a single HTTP daemon gave one owner of the index and one
  resident copy of BGE — for *clients*. The CLI kept building its own
  `VaultManager`, so the common path through the product was still the one that
  reintroduced the second writer: the hooks fire exactly these three commands as
  detached processes (`session_export.py` after writing a transcript,
  `session_start_brief.py` for maintenance and a backstop reindex). The daemon's
  journal shows the consequence minutes into any session — *"Index changed on
  disk by another process — reopening"*.

  Measured on this vault (3627 notes, incremental, nothing to do): the old path
  took 7.9s wall and pushed GPU use from 4060 to 6383 MiB — a second 2.3 GiB
  copy of BGE-m3 loaded to index nothing. Routed, the same command takes 0.99s
  and GPU use does not move. `--local` forces the old in-process path, and a
  machine with no daemon listening still falls back to it automatically, so an
  install without the service keeps working.

  A daemon call that *fails* deliberately does not fall back: retrying locally
  would start the concurrent writer this removes, against a daemon that is
  already unwell. A daemon that *disappears* mid-call does fall back — that is a
  restart, not a failure. `ingest` resolves its path before sending it, since
  the daemon's working directory is not the shell's.

  Two bugs surfaced in the first real run rather than in review. Polling read
  `"error" in status`, but `jobs.submit()` seeds every job with `error=None`, so
  a *finished* reindex was reported as a lost job while the daemon's log showed
  it completing. And obeying `check_again_in_seconds` — floored at 30s, written
  for an agent that spends a turn per poll — turned 70ms of work into 10.6s of
  waiting; the interval now starts at 250ms and grows. Both are pinned by tests.

### Fixed

- **Stale session files were skipped forever instead of deleted.**
  `list_connected_clients()` only unlinked files whose pid was gone, so a stale
  session belonging to the *live* daemon sat in `~/.delegation_core/sessions/`
  permanently. Harmless while every client was a long-lived editor; since the
  CLI now connects once per routed command — several times a day via the hooks —
  it was a file per invocation with no reader.

### Added

- **`vault_health_detail()` — the findings behind the health counts.** The
  summary returned numbers and nothing else, so acting on "broken_links: 26"
  meant writing a throwaway script to enumerate them, and a throwaway script
  re-implements the definitions. Three such scripts over two days reported 248,
  63 and 5 against true values of 31, 31 and 0: one used a bare regex instead of
  `_countable_wikilinks`, one built a narrower resolvable set than the health
  pass uses, one compared an unstripped stem against a stripped link target.
  Every time the correct function already existed.

  The lists are collected during the same pass that produces the counts, so
  `len(broken_link_items) == broken_links` holds by construction rather than by
  care. `folder_marker_items` are reported separately — `[[reference]]`-style
  category markers, deliberately uncounted — because listing them is what stops
  the next reader from "fixing" a link that names a folder.

- **`doctor` asks the index a question.** Every `scope`-filtered search was
  dying on ChromaDB's "Error finding id" while unfiltered search kept answering,
  so the entire hand-written slice of a 6723-row vault was unreachable — and
  `doctor` passed 6/6 green throughout, because nothing in it had ever queried
  the index. `check_index_integrity` now issues the same shape of query
  `search_vault` issues, once per scope, carrying a constant vector so no
  embedding model loads.

  Comparing ids between `chroma.sqlite3` and the vector segment was the first
  attempt and it does not work: records live in memory until Chroma flushes, so
  a healthy server with pending writes is indistinguishable from a corrupt
  index — it reported an error against a note written seconds earlier. The
  obvious place to read ids from, `index_metadata.pickle`, is worse: current
  Chroma does not create it for new collections at all, so the check would have
  been silently inert on any fresh install. The probed filters are asserted
  against the ones `search()` actually sends; `is_external` is the string
  `"true"`, and probing the boolean matches no row and passes without testing
  anything.

### Fixed

- **A running server never saw another process's writes.** Concurrent writers
  are by design — the SessionEnd hook fires a detached `reindex`, SessionStart
  fires `maintain`, and any CLI use writes the same path while the server runs —
  but `PersistentClient` loads the vector index once and never re-reads it, and
  the module write lock is a `threading.Lock` that knows nothing about another
  process. After a CLI ingest, a running server answered `scope='all'` with
  pre-write content and failed every scope-filtered query with "Error finding
  id", while a freshly opened client read the same index perfectly. Only a
  restart cleared it, which made the documented "the transcript is searchable
  right after the session" path the thing that broke search.

  `_ensure_ready` now compares a one-stat fingerprint of `chroma.sqlite3` and
  reopens when it moved. Constructing a new `PersistentClient` is not enough on
  its own: chromadb caches one System per path for the process lifetime, so the
  "new" client shares the stale segment state and keeps failing filtered queries
  while reporting the new row count — `clear_system_cache()` is what makes the
  reopen equivalent to the fresh process. The embedding function is reused
  across reopens, because rebuilding it reloads BGE onto a GPU that is routinely
  full here.

- **A rejected oversized body arrived as a connection reset.** The dashboard
  API's 413 path answered without reading the request body, and closing with
  unread bytes in the socket makes the client see ECONNRESET instead of the
  status line. It also explains a flake: the test for it passed alone and failed
  about one full-suite run in three, when load tips the timing. The body is now
  drained before answering, bounded at 8 MB.

- **`.mdx` was never an ingest candidate.** `ingest` globs on `SUPPORTED` before
  extracting, so three real deploy guides in a docs tree were reported as
  "764 indexed, 0 skipped" while being invisible. MDX is markdown with JSX
  interleaved; the prose reads as text and the components degrade to inert tags,
  so it goes through `_text` with no extraction logic of its own.

- **Notes were unreachable through the default search scope from the moment
  they were written.** `search(scope='notes')` filters on `kind == "note"`
  inside ChromaDB, and of the fifteen `index_note` call sites only
  `graphbridge`'s and `reindex_vault`'s passed `note_metadata()`. Every other
  write path — `write_note`, `vault_update_note`, `export_session`, inbox
  classification, merges, relinking — handed over a bare
  `{title, path, folder}`. The row landed without `kind` and stayed invisible
  until the next full reindex backfilled it. Since `scope='notes'` is the
  default, the symptom was: write a note, then fail to find it by its own
  near-exact title.

  `search()`'s docstring treated a missing `kind` as a legacy condition that
  `reindex --force` cures; these paths kept creating it. It is now derived in
  `index_note` rather than trusted from the caller, so all fifteen call sites
  are covered at one choke point. External chunks keep scoping on
  `is_external`, and an absolute path is never stamped — `classify_path` grades
  what it cannot recognise as hand-written, which would file ingested source
  files under `scope='notes'`.

## v0.10.0 — 2026-08-03

### Added

- **`vault_rename_note` / `POST /api/vault/note/rename`.** Renaming a note
  repoints every `[[wikilink]]` aimed at it. Without this, renaming is silent
  corruption — a stem *is* a note's link identity — and it bit this project
  directly: a note renamed by hand left two links dangling, found days later by
  an audit. Section anchors and display text (`[[stem#Summary|label]]`) survive,
  because 71 links in this vault carry one and a whole-link replacement would
  discard them. Writes are staged and rolled back on failure, so a half-renamed
  vault is not reachable. Exercised against a copy of the real vault: the
  most-referenced note renamed cleanly, 35 notes repointed, 0 broken links, all
  33 inbound references preserved.

### Changed

- **`search_vault` defaults to `scope='notes'`.** This vault holds 3692
  generated articles against 187 hand-written notes, and under `scope='all'` a
  search for the exact title of a note written minutes earlier returned two
  unrelated code-graph articles instead of it. Questions about a codebase now
  need `graph='<name>'` or `scope='generated'` — deliberately, since that is the
  narrower intent. Every response names the scope it used, so a caller can tell
  a scoped answer from an exhaustive one. `VaultManager.search()` keeps
  `scope='all'` so internal callers (wikilink suggestion, health) are unchanged.

### Removed

- **The `vault_bge` ChromaDB collection (1608 rows).** Left behind by the switch
  to `bge-m3`; the active collection is `vault_bge_m3`. Regenerable with
  `delegation-core embed-model <model> --reindex`.
- **`graphs/hermes-agent/pruned_from_vault/` (1071 files, 4.2 MB).** The
  pre-rebuild `Community_N` articles, superseded when the graph was rebuilt with
  semantic names. Regenerable with `graph_build`.

Vault orphans were reviewed and deliberately **not** touched: the 113 notes with
no inbound link are curated writing — project docs, decisions, archived sessions
— and deleting them would destroy content, not tidy it. `relink_folder` is the
additive fix if they should be connected.

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

- **Every listing surface now reports what it is hiding.** The same shape as
  items 1 and 5, on the read side: `vault_list_notes` truncated with a bare
  slice and `/api/vault/tree` capped each folder at 1000, both returning a short
  list that looked exactly like a short folder. With `Reference` holding 3715
  notes the dashboard's browser showed its newest 1000 as if that were all of
  them. `VaultManager.count_notes()` is new; the MCP tool now returns
  `total`/`truncated` and the route returns per-folder `counts`.

- **`/api/vault/graph` is the knowledge graph again, and is bounded.** Code
  graphs were already a separate thing — their own artifacts under
  `~/.delegation_core/graphs/<name>/`, their own `/api/graphs` endpoints, their
  own pane behind the dashboard's Vault/Code toggle, and their own
  `search_vault(scope="generated")` filter via `VaultManager.classify_path`.
  They leaked into the vault view only because graph_build files their articles
  into a vault folder to make them searchable, and this endpoint re-derived
  membership from the filesystem instead of using that existing classification.
  On this vault the Vault pane was 3552 nodes of which 216 were hand-written —
  94% of the "knowledge graph" was one codebase. Generated articles are now
  excluded by default (`?generated=1` opts back in), using `classify_path`
  rather than a second definition of what counts as generated. The result is
  221 nodes / 421 edges / 82 KB instead of 3552 nodes / 600 KB. Nodes are also
  capped at 1500 (newest first, edges built after the cut so none can dangle)
  with `total_nodes`/`truncated`/`max_nodes` reported, and the pane states its
  own scope under the legend.

- **`capabilities()` — a connecting client can now ask what this server does.**
  Returns the live tool list (asked of the running server via `mcp.list_tools()`,
  so it cannot drift from what is served), every graph exporter with the tool
  that reaches it, the ones deliberately unexposed with reasons, capabilities
  that exist but are still unwired, and the search scopes. `AGENT_GUIDE.md` now
  says outright that it is not authoritative and this report is — the guide's
  numeric claims are copies with no test comparing them to source, which is how
  a comparable project's guide ended up wrong on four of four constants.

- **`tests/test_capability_registry.py` makes unwired capability a written
  decision.** It scans the vendored pipeline for artifact-producing functions
  and fails when one is not classified in `capabilities.GRAPH_CAPABILITIES` as
  either wired to a named tool or deliberately unexposed with a reason. Verified
  by adding a `to_parquet()` stub: the suite fails until it is classified. This
  is the structural answer to the three bugs above — each was a working,
  reachable function that nothing pointed at.

- **`graph_export(name, format)` exposes three exporters that had no caller.**
  `graphml` (Gephi, yEd, Cytoscape), `svg`, and `cypher` (Neo4j replay script).
  They read the existing `graph.json` rather than re-extracting, so they cost
  seconds. `to_obsidian`/`to_canvas` stay unexposed by decision, now recorded:
  Obsidian is no longer the target reader.

- **Phase 1 of replacing Obsidian: the note browser has a real tree and a
  literal search.** Measured first: 3661 of 3878 notes sit three levels down,
  the browser listed only the 9 configured top-level folders, and the largest
  single directory holds 2711 notes. `/api/vault/tree` now returns directory
  shape (25 entries here, with depth and per-directory counts) and
  `/api/vault/notes?dir=&offset=` pages one directory at a time, so a 2711-note
  folder is browsable instead of capped. `graphs/` subtrees start folded.

  `/api/vault/find?q=` and the `vault_find_notes` MCP tool do literal
  title/path matching with no embeddings and no similarity cutoff, ranked exact
  stem → prefix → substring → path. This is not a nicety: searching the vault
  semantically for the exact title of a note written minutes earlier did not
  return it in the top 3, and the one-word title "AIAgent" matched at 0.57
  against a 0.55 threshold. The filter box now hits this endpoint, so it can
  see the whole vault rather than whichever page was already loaded.

- **Phase 2: a backlinks panel, and a corrected broken-link count.** The
  relation was always computed — `linker.inject_backlinks` writes it into note
  bodies on every write — but nothing exposed it, so a reader saw only whatever
  text happened to be present. `VaultManager.note_links()`,
  `/api/vault/backlinks?path=` and the `vault_note_links` MCP tool return
  inbound references and outbound targets, with dead targets marked
  `broken: true` rather than dropped. Half the hand-written notes here have
  inbound links, so this is not a corner case: the most-referenced note has 33.
  Costs 0.14s on a 3878-note vault.

  While implementing it, the earlier "248 broken links / 31%" figure was found
  to be wrong. It came from an ad-hoc naive regex; the codebase has two wikilink
  parsers and the strict one (`_countable_wikilinks`, which strips code spans
  and shell `[[ -f ]]` syntax) is what `vault_health` uses. Measured properly:
  527 links, **63 broken across 42 notes**, not 248. The panel uses the strict
  parser so it cannot disagree with the health count. Note that `heartbeat()`
  still reports 31, since `vault_health.json` is a cache.

- **Phase 3: the dashboard can create and edit notes, through the server.**
  `notewriter.py` is new and is now the only path a note takes into the vault:
  `create_note` (dated filename, collision-safe, single frontmatter block,
  wikilinks injected) and `save_note` (verbatim overwrite plus reindex).
  `server.py`'s `write_note` delegates to it, and `POST /api/vault/note/create`
  and `/save` call the same functions.

  Writing straight to disk from Tauri would have been faster and would have left
  the note unindexed until something else noticed — a second write path free to
  drift from the first, which is the failure this release spent its length
  removing. `save_note` deliberately does *not* inject a `## Related` block:
  create does, but doing it on save would edit the user's text behind them every
  time they hit save.

- **`delegation-core embed-model` did not exist.** `cmd_embed_model` was
  written, complete, and never registered on the argument parser — while
  `cmd_status` printed "Rode: `delegation-core embed-model <modelo>`", so the
  CLI instructed users to run a command that answered "invalid choice". Now
  registered, with `tests/test_cli_commands.py` asserting that every top-level
  subcommand is reachable and that every registered one still has a handler.
  Running it revealed 1608 rows still indexed under the previous embedding
  model's collection, which is worth reviewing separately.

- **Path containment is one function.** `resolve_in_vault()` replaces three
  copies of the resolve-then-`relative_to` dance. The check exists because a
  string-prefix test passes for `.../vault-old` when the root is `.../vault`;
  that bug was found and fixed twice independently in this codebase before it
  had a single home.

- **graph_build's report was graded as a hand-written note.** It is filed at
  the top of its folder on purpose — graphbridge calls it the "discoverable
  entry point" — so it does not live under `graphs/<name>/` and `classify_path`
  returned `("note", "")` for it. Five reports were therefore rendered in the
  dashboard's *knowledge* graph, immediately after that view was separated from
  code graphs. `classify_path` now recognises the report by its generated
  filename rather than moving it, so the entry point stays where it was designed
  to be. Reports in the knowledge graph: 5 → 0.

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
