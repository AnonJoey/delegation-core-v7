"""
vault.py — ChromaDB semantic search core.

Delegates embedding setup to embeddings.py (new in v0.2).
VaultManager owns: ChromaDB lifecycle, search, index, reindex, maintenance helpers.

v0.2 improvements:
  - Lazy init with double-checked lock (ABNER) + warm_up() for background pre-loading
  - doc_id parameter on index_note for chunked external ingestion (ABNER)
  - Orphan cleanup in reindex_vault: drops rows whose path no longer exists (SAAD)
  - anonymized_telemetry=False in ChromaDB client (SAAD)
  - _ensure_ready() guard on every public method

v0.3 improvements:
  - Incremental indexing: reindex_vault(force=False) skips notes whose mtime
    has not changed since the last reindex run. State stored in
    {vault}/.chroma_index.json as {rel_path: mtime}.
  - force=True bypasses mtime check (full reindex).
"""

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from .config import Config
from .embeddings import make_bge_embedding_function
from .linker import frontmatter_aliases

logger = logging.getLogger("vault")

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# v6 health de-pollution: `[[...]]` occurs in ingested content that is NOT a
# wikilink — bash `[[ -f "$x" ]]` test syntax, imported Obsidian path-links
# `[[Folder/File.pdf]]`, prose. Counting those made broken_links ~98% false
# positives. These strip code spans and keep only note-like link targets.
# One pass for fenced blocks AND inline spans: a code span opens with a run of N
# backticks and closes with a run of exactly N (CommonMark). The old two-regex
# approach (fences, then inline) desynced on notes that *document* fence syntax —
# a literal ``` inside inline code (`` ` ``` ` ``) left an unpaired fence, which
# shifted inline pairing for the rest of the file and exposed the `[[...]]`
# examples in it as "broken links". Lookarounds pin the run to its exact length so
# ``` never closes against one backtick of a longer run.
_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(?:.*?)(?<!`)\1(?!`)", re.DOTALL)
_WIKILINK_TARGET_RE = re.compile(r"\[\[([^\]\|#]+)")


def _countable_wikilinks(content: str) -> list[str]:
    """Return note-like wikilink targets from a note, excluding code spans and
    non-link `[[...]]` artifacts (shell test syntax, path/file references)."""
    body = _CODE_SPAN_RE.sub(" ", content)
    out = []
    for raw in _WIKILINK_TARGET_RE.findall(body):
        t = raw.strip()
        if not t or t[0] in "-$ ":         # shell `[[ -f`, `[[ $x`, leading space
            continue
        if any(c in t for c in '$"'):       # shell vars / quoted paths
            continue
        if "/" in t or re.search(r"\.(pdf|docx?|md|png|jpe?g|xlsx?|pptx?)$", t, re.I):
            continue                         # imported path/file reference, not a note
        out.append(t)
    return out


def resolve_in_vault(vault_root: Path, rel_path: str) -> Path | None:
    """Resolve *rel_path* under *vault_root*, or None if it escapes.

    Containment is checked with Path.relative_to, never a string prefix: a
    sibling directory whose name merely starts with the vault's own name
    (vault at .../vault, and .../vault-old exists) passes a prefix test while
    resolving outside. That exact bug was fixed twice in this codebase before —
    in relink_folder and in the dashboard's note route — so the check lives in
    one place now rather than being re-typed at each new call site.
    """
    target = (vault_root / rel_path).resolve()
    try:
        target.relative_to(vault_root.resolve())
    except ValueError:
        return None
    return target


def safe_filename(title: str, max_len: int = 50) -> str:
    """Sanitize a title into a filesystem-safe filename stem.

    Truncation cuts on a word boundary when one is available, then strips
    punctuation the cut can strand. A raw slice used to leave stems like
    "... da arquitetura (" — a dangling opening paren that reads as a broken
    filename in Obsidian and labels the note's graph node with it.
    """
    safe = _INVALID_FILENAME_CHARS.sub("_", title)
    safe = re.sub(r"_+", "_", safe).strip().rstrip(" .")
    if len(safe) > max_len:
        safe = safe[:max_len]
        # Only back up to a word boundary if that keeps the stem recognisable;
        # a title with no spaces in range (or a very late first space) keeps
        # the hard slice rather than collapsing to a stub.
        cut = safe.rfind(" ")
        if cut >= max_len // 2:
            safe = safe[:cut]
        safe = safe.rstrip(" .,;:-_([{<\"'—–")
    return safe or "untitled"


def unique_note_path(dest: Path) -> Path:
    """Disambiguate a note destination that already exists on disk.

    write_note/export_session/maintenance-classify all derive the filename
    from just {date}-{safe title}.md — two notes with the same title on the
    same day would otherwise silently overwrite the first note's file *and*
    its ChromaDB index row, permanently losing its content. Appends -2, -3,
    ... before the suffix until a free path is found.
    """
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = dest.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def yaml_quote_scalar(value: str) -> str:
    """Double-quote a string for safe use as a YAML frontmatter scalar value.

    An unquoted scalar containing ": " (colon-space) is ambiguous/invalid YAML
    (Obsidian and any strict frontmatter parser will choke on it) — quote
    unconditionally so titles are safe regardless of content.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def yaml_unquote_scalar(value: str) -> str:
    """Reverse yaml_quote_scalar() for frontmatter values read back with naive line parsing."""
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


_LEADING_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def compose_note(title: str, content: str, date_str: str) -> str:
    """Build note text carrying exactly one YAML frontmatter block.

    Callers routinely include a full frontmatter block at the top of `content` —
    the AGENT_GUIDE's own "Content guidelines" section shows one — while this
    function also has generated fields to add. Concatenating both produced two
    stacked blocks, and since Obsidian parses only the first, every key the
    author actually wrote (subtitle, tags, aliases) silently became literal body
    text rendered after a horizontal rule.

    The caller's block is preserved verbatim rather than re-serialized, so block
    lists (`aliases:` with `- ` items) and any YAML this module doesn't model
    survive untouched. Generated keys are appended only where the caller did not
    already supply them — so an explicit `title:` in the content wins, which is
    what lets a note carry a short display title independent of its filename.
    """
    body = content.lstrip("\n")
    generated = [
        ("title", yaml_quote_scalar(title)),
        ("date", date_str),
        ("ai_generated", "true"),
    ]

    m = _LEADING_FRONTMATTER_RE.match(body)
    if not m:
        block = "\n".join(f"{k}: {v}" for k, v in generated)
        return f"---\n{block}\n---\n\n{body}"

    supplied = m.group(1)
    # Top-level keys only: indented lines and `- ` items belong to a block list.
    have = {
        line.split(":", 1)[0].strip().lower()
        for line in supplied.splitlines()
        if ":" in line and line[:1] not in (" ", "\t", "-")
    }
    additions = [f"{k}: {v}" for k, v in generated if k not in have]
    merged = supplied + ("\n" + "\n".join(additions) if additions else "")
    return f"---\n{merged}\n---\n\n{body[m.end():].lstrip(chr(10))}"


_chroma_write_lock = threading.Lock()
"""Module-level lock guarding all ChromaDB write operations.

run_maintenance_bg() runs asyncio.run() inside a daemon thread, sharing the
same VaultManager and ChromaDB collection as the main event loop. ChromaDB's
embedded client is not thread-safe for concurrent writes. This lock serialises
index_note() calls across both paths.
"""


class VaultManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.collection = None
        self.ef = None
        self._initialized = False
        self._init_lock = threading.Lock()

    # ── init ─────────────────────────────────────────────────────────────────

    def _init(self):
        with self._init_lock:
            if self._initialized:
                return
            # An unset vault_path resolves to Path(".") — an existing directory —
            # so no existence check catches it and the whole index materialises
            # under whatever the process's cwd happens to be. Config.load()
            # falls back to defaults on any read error, so this is reachable in
            # production, not just from a hand-written script.
            if not str(self.cfg.vault_path).strip():
                raise ValueError(
                    "vault_path is not configured — refusing to create the index "
                    "in the current working directory. Run the setup wizard, or "
                    "build the Config with Config.load()."
                )
            try:
                import chromadb
                self.cfg.chroma_path.mkdir(parents=True, exist_ok=True)
                self.ef = make_bge_embedding_function(self.cfg.bge_model)
                client = chromadb.PersistentClient(
                    path=str(self.cfg.chroma_path),
                    settings=chromadb.Settings(anonymized_telemetry=False),
                )
                self.collection = client.get_or_create_collection(
                    name=self.cfg.collection_name,
                    embedding_function=self.ef,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info("ChromaDB ready — %d notes indexed in %s",
                            self.collection.count(), self.cfg.collection_name)
            except Exception as e:
                logger.error("ChromaDB/BGE init failed: %s — vault will retry on next call", e)
                return  # do NOT set _initialized; leave it False so _ensure_ready() retries
            self._initialized = True  # only reached on successful init

    def _ensure_ready(self):
        if not self._initialized:
            self._init()

    def warm_up(self):
        """Start BGE model loading in a background thread before the first tool call."""
        threading.Thread(target=self._init, daemon=True, name="vault-warmup").start()

    # ── search ───────────────────────────────────────────────────────────────

    #: Vault-relative path segment under which graph_build files its wiki articles.
    GENERATED_SEGMENT = "graphs"
    # "<date>-Code Graph Report — <graph>.md", written by graphbridge.
    _REPORT_STEM_RE = re.compile(
        r"^\d{4}-\d{2}-\d{2}-Code Graph Report — (?P<graph>.+)\.md$")

    @classmethod
    def classify_path(cls, rel_path: str) -> tuple[str, str]:
        """Return (kind, graph_name) for a vault-relative note path.

        A generated article lives at ``<folder>/graphs/<graph>/<stem>.md``; the
        graph name is its own metadata field so a search can be pinned to one
        codebase. Anything else is a hand-written note.
        """
        parts = [p for p in str(rel_path).replace("\\", "/").split("/") if p]
        if len(parts) >= 3 and parts[1] == cls.GENERATED_SEGMENT:
            return "generated", parts[2]
        # graph_build's report is machine-written too, but it is filed at the top
        # of its folder on purpose — graphbridge calls it the "discoverable entry
        # point" — so it does not sit under graphs/ and was being graded as a
        # hand-written note. That put 5 reports into the dashboard's knowledge
        # graph and counted their code-derived [[Foo alloc]]-style artifacts as
        # the user's broken links. Recognised by name rather than moved, so the
        # entry point stays where it was designed to be.
        if len(parts) == 2:
            m = cls._REPORT_STEM_RE.match(parts[1])
            if m:
                return "generated", m.group("graph")
        return "note", ""

    @classmethod
    def note_metadata(cls, rel_path: str, title: str, folder: str) -> dict:
        """Build the metadata row for a vault note, including its search scope.

        Classmethod because it derives everything from the path — callers that
        only stand in for a VaultManager (write paths, tests) can use it without
        a live ChromaDB collection behind them.
        """
        kind, graph = cls.classify_path(rel_path)
        meta = {"title": title, "path": rel_path, "folder": folder, "kind": kind}
        if graph:
            meta["graph"] = graph
        return meta

    def search(self, query: str, limit: int = 5, scope: str = "all",
               graph: str = "", snippet_chars: int = 800) -> list[dict]:
        """Semantic search, optionally narrowed to one kind of indexed content.

        scope:
          all       — everything (default, previous behaviour)
          notes     — hand-written vault notes only
          generated — graph_build wiki articles only
          external  — ingest_folder'd files only
        graph: restrict to one built graph by name (implies scope='generated').

        Scoping matters once a vault carries machine-generated corpora: after four
        code graphs this vault held 978 generated notes against 179 written by
        hand. Narrowing is pushed into ChromaDB's `where` rather than applied to
        the result list — a first attempt filtered afterwards and returned nothing
        for scope='notes', because all of the top hits were generated and none
        survived. Post-filtering can only ever remove, never reach further down.

        Rows indexed before `kind` existed carry no marker; `reindex(force=True)`
        backfills them. Until then they behave as unscoped and are matched by a
        path fallback.
        """
        self._ensure_ready()
        if not self.collection:
            return [{"error": "Vault not initialized"}]

        scope = (scope or "all").lower()
        if graph:
            scope = "generated"

        where: dict | None = None
        if graph:
            where = {"graph": graph}
        elif scope == "external":
            where = {"is_external": "true"}
        elif scope in ("notes", "generated"):
            where = {"kind": scope[:-1] if scope == "notes" else scope}

        want = max(limit, 1)
        # Over-fetch only to absorb the similarity-threshold cut, which `where`
        # cannot express.
        n_results = min(want * 3, 60) if where else want

        try:
            kwargs = {"query_texts": [query], "n_results": n_results}
            if where:
                kwargs["where"] = where
            res = self.collection.query(**kwargs)
            hits = []
            docs  = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                sim = round(1 - dist, 3)
                if sim < self.cfg.search_threshold:
                    continue
                kind = meta.get("kind") or (
                    "external" if str(meta.get("is_external", "")).lower() == "true"
                    else self.classify_path(meta.get("path", ""))[0])
                hits.append({
                    "title":      meta.get("title", "Untitled"),
                    "path":       meta.get("path", ""),
                    "folder":     meta.get("folder", ""),
                    "kind":       kind,
                    "snippet":    doc[:max(snippet_chars, 0)],
                    "similarity": sim,
                })
                if len(hits) >= want:
                    break
            return hits
        except Exception as e:
            return [{"error": str(e)}]

    # ── write / index ────────────────────────────────────────────────────────

    def index_note(self, content: str, metadata: dict, doc_id: str = ""):
        """Upsert content into ChromaDB.

        doc_id: explicit ID for chunked external files (IngestManager).
        Defaults to metadata['path'] so vault notes are keyed by their vault-relative path.

        `kind` is derived here rather than trusted from the caller. Of the fifteen
        call sites only graphbridge's and reindex_vault's passed note_metadata();
        every other write path — write_note, vault_update_note, export_session,
        inbox classification, merges, relinking — handed over a bare
        {title, path, folder}. Rows written that way carry no `kind`, and
        search(scope='notes') filters on `kind == "note"` inside ChromaDB, so a
        note was unreachable through the default scope from the moment it was
        written until the next full reindex backfilled it. search()'s docstring
        treats missing `kind` as a legacy condition; these paths kept creating it.
        """
        self._ensure_ready()
        if not self.collection:
            return
        doc_id = doc_id or metadata.get("path", str(datetime.now().timestamp()))
        # Only vault-relative paths can be classified. External content scopes on
        # is_external, and an absolute path must never be stamped: classify_path
        # grades anything it cannot recognise as hand-written, which would file
        # ingested source files under scope='notes'. Rows that reach here absolute
        # and unmarked stay unscoped — see inject_backlinks, which re-indexes with
        # a bare metadata dict and drops the is_external marker it was given.
        raw_path = str(metadata.get("path", ""))
        # Both flavours: PurePosixPath misses "C:\...", PureWindowsPath misses "/...".
        absolute = (PurePosixPath(raw_path).is_absolute()
                    or PureWindowsPath(raw_path).is_absolute())
        classifiable = (
            "kind" not in metadata
            and bool(raw_path)  # classify_path("") grades as "note"
            and str(metadata.get("is_external", "")).lower() != "true"
            and not absolute
        )
        if classifiable:
            kind, graph = self.classify_path(metadata.get("path", ""))
            metadata = {**metadata, "kind": kind}
            if graph:
                metadata["graph"] = graph
        try:
            with _chroma_write_lock:
                self.collection.upsert(ids=[doc_id], documents=[content], metadatas=[metadata])
        except Exception as e:
            logger.warning("Index error: %s", e)

    def delete_notes(self, rel_paths: list[str]) -> int:
        """Drop notes from ChromaDB and the incremental index state by vault-relative path.

        Counterpart to index_note: doc IDs for vault notes are the relative path,
        so removing a note's file without this leaves a stale row that keeps
        surfacing in search until the next full reindex runs its orphan sweep.
        Returns the number of IDs submitted for deletion.
        """
        self._ensure_ready()
        if not self.collection or not rel_paths:
            return 0
        try:
            with _chroma_write_lock:
                self.collection.delete(ids=list(rel_paths))
        except Exception as e:
            logger.warning("Delete error: %s", e)
            return 0
        state = self._load_index_state()
        for p in rel_paths:
            state.pop(p, None)
        self._save_index_state(state)
        return len(rel_paths)

    # ── incremental index state ───────────────────────────────────────────────

    def _index_state_path(self) -> Path:
        return self.cfg.vault / ".chroma_index.json"

    def _load_index_state(self) -> dict[str, float]:
        p = self._index_state_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_index_state(self, state: dict[str, float]):
        try:
            self._index_state_path().write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Could not save index state: %s", e)

    # ── reindex ───────────────────────────────────────────────────────────────

    def reindex_vault(self, force: bool = False) -> int:
        """Reindex markdown notes in configured vault folders.

        force=False (default): skips notes whose mtime matches the last recorded
        value in {vault}/.chroma_index.json — incremental, fast on large vaults.
        force=True: re-indexes every note regardless of mtime.

        Also removes orphan ChromaDB rows whose vault path no longer exists on
        disk. External chunk IDs (containing '::') are never touched.
        """
        self._ensure_ready()
        if not self.collection:
            return 0

        state = {} if force else self._load_index_state()
        count = 0
        skipped = 0
        on_disk: set[str] = set()

        for folder in self.cfg.vault_folders:
            folder_path = self.cfg.vault / folder
            if not folder_path.exists():
                continue
            # rglob (not glob): index notes in subfolders too. With a
            # non-recursive glob, `on_disk` omits subfolder notes, so the orphan
            # sweep below deletes every already-indexed subfolder note from
            # ChromaDB — silently collapsing the search index on each reindex.
            # (0.5.0 used rglob here; v6.0/6.1 regressed it to glob.)
            for f in folder_path.rglob("*.md"):
                rel = str(f.relative_to(self.cfg.vault))
                on_disk.add(rel)
                mtime = f.stat().st_mtime
                if not force and abs(state.get(rel, 0) - mtime) < 0.001:
                    skipped += 1
                    continue
                try:
                    content = f.read_text(encoding="utf-8")
                    fm = self._parse_frontmatter(content)
                    title = fm.get("title") or f.name[:-3]
                    self.index_note(content, self.note_metadata(rel, title, folder))
                    state[rel] = mtime
                    count += 1
                except Exception as e:
                    logger.warning("Could not index %s: %s", f.name, e)

        try:
            with _chroma_write_lock:
                # get() and delete() share the lock — prevents newly-indexed notes from
                # being identified as orphans between the get() and delete() calls.
                # The `on_disk` snapshot itself is stale by the time we get here: it was
                # built by the folder walk above, which can take a while, and a
                # concurrent write_note()/vault_update_note() on the main thread can
                # index a brand-new note in that window. Re-check the filesystem for
                # each orphan candidate rather than trusting on_disk alone, or a note
                # written mid-reindex gets its just-created ChromaDB row deleted here
                # (file survives, but silently drops out of search_vault).
                existing_ids = self.collection.get(include=[]).get("ids") or []
                orphans = [i for i in existing_ids if "::" not in i and i not in on_disk
                          and not (self.cfg.vault / i).exists()]
                if orphans:
                    self.collection.delete(ids=orphans)
                # Remove orphan entries from saved state
                for o in orphans:
                    state.pop(o, None)
                logger.info("Reindex dropped %d orphan rows", len(orphans))
        except Exception as e:
            logger.warning("Orphan cleanup failed: %s", e)

        self._save_index_state(state)
        if skipped:
            logger.info("Reindex: %d indexed, %d unchanged (skipped)", count, skipped)
        return count

    # ── maintenance helpers ───────────────────────────────────────────────────

    def count_notes(self, folder: str) -> int:
        """Total notes in a folder, ignoring any list limit.

        list_notes() truncates with a bare slice, so its caller cannot tell a
        folder of 20 notes from a folder of 3715 showing its newest 20. Every
        surface that lists notes reports this alongside the truncated list.
        """
        folder_path = self.cfg.vault / folder
        if not folder_path.exists():
            return 0
        return sum(1 for _ in folder_path.rglob("*.md"))

    def list_notes(self, folder: str, limit: int = 20) -> list[dict]:
        """List notes in a folder (including subfolders) sorted newest-first by mtime.

        rglob, not glob: reindex_vault() already made this exact fix (see its own
        comment) because a non-recursive glob silently omits subfolder notes. This
        method had the same bug independently — found while building the
        dashboard's notes-browser endpoint, which showed 0 notes for a folder that
        actually had its notes nested one level down.
        """
        folder_path = self.cfg.vault / folder
        if not folder_path.exists():
            return []
        files = sorted(folder_path.rglob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        return [self._note_row(f) for f in files[:limit]]

    def _note_row(self, f: Path) -> dict:
        """Title/date/path/size for one note, reading only its frontmatter head."""
        title, date = f.name[:-3], ""
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        try:
            for line in f.read_text(encoding="utf-8").splitlines()[:8]:
                if line.startswith("title:"):
                    title = yaml_unquote_scalar(line.split(":", 1)[1])
                elif line.startswith("date:"):
                    date = line.split(":", 1)[1].strip()
        except Exception as e:
            logger.warning("Could not read frontmatter from %s: %s", f.name, e)
        return {"title": title, "date": date,
                "path": str(f.relative_to(self.cfg.vault)), "size_bytes": size}

    def list_directories(self) -> list[dict]:
        """Every directory under a configured folder that holds notes.

        The browser used to list only the configured top-level folders, so a
        vault where 3661 of 3878 notes sit three levels down showed them as if
        they were loose in their top folder, with no way to reach the subtree.
        Returning the real shape is cheap here: this vault has 25 such
        directories.
        """
        out = []
        for folder in self.cfg.vault_folders:
            root = self.cfg.vault / folder
            if not root.exists():
                continue
            seen: dict[str, int] = {}
            for f in root.rglob("*.md"):
                rel = str(f.parent.relative_to(self.cfg.vault))
                seen[rel] = seen.get(rel, 0) + 1
            for rel in sorted(seen):
                out.append({"path": rel,
                            "name": rel.split("/")[-1],
                            "depth": rel.count("/"),
                            "count": seen[rel]})
        return out

    def list_notes_in(self, dir_rel: str, offset: int = 0, limit: int = 200) -> dict:
        """Notes directly inside one directory — not its subdirectories.

        Paginated because a single directory here holds 2711 notes; the caller
        gets `total` and `has_more` so a page is never mistaken for the lot.
        """
        target = resolve_in_vault(self.cfg.vault, dir_rel)
        if target is None:
            return {"error": f"Path outside the vault: {dir_rel}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {dir_rel}"}

        files = sorted(target.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        page = files[offset:offset + limit]
        return {"dir": dir_rel, "total": len(files), "offset": offset,
                "limit": limit, "has_more": offset + len(page) < len(files),
                "notes": [self._note_row(f) for f in page]}

    def find_notes(self, query: str, limit: int = 30) -> list[dict]:
        """Literal title/path search — no embeddings, no similarity threshold.

        search() is semantic and answers "what is about X". It cannot reliably
        answer "open the note called X": searching this vault for the exact
        title of a note written minutes earlier did not return it in the top 3,
        and a one-word title matched at 0.57 against a 0.55 cutoff. Exact recall
        needs literal matching, so this walks filenames instead.

        Ranked: exact stem, then prefix, then substring in the stem, then
        anywhere in the path.
        """
        q = (query or "").strip().lower()
        if not q:
            return []
        scored = []
        for folder in self.cfg.vault_folders:
            root = self.cfg.vault / folder
            if not root.exists():
                continue
            for f in root.rglob("*.md"):
                stem = f.stem.lower()
                rel = str(f.relative_to(self.cfg.vault))
                if stem == q:
                    rank = 0
                elif stem.startswith(q):
                    rank = 1
                elif q in stem:
                    rank = 2
                elif q in rel.lower():
                    rank = 3
                else:
                    continue
                scored.append((rank, len(stem), rel, f))
        scored.sort(key=lambda t: (t[0], t[1], t[2]))
        return [dict(self._note_row(f), match_rank=rank)
                for rank, _, _, f in scored[:limit]]

    def resolvable_link_targets(self) -> dict[str, Path]:
        """Every name a `[[wikilink]]` in this vault can legitimately resolve to.

        Wider than "stems of notes in vault_folders", in two ways that both
        produce false "broken link" reports when missed:

        - **Frontmatter aliases.** A note declaring `aliases: [Command Center]`
          is reachable by that name.
        - **Notes outside the managed folders.** MEMORY.md at the vault root and
          anything staged in _processed/ resolve fine for a reader; only grading
          is scoped to vault_folders.

        vault_health() has always resolved this way. note_links() did not when it
        was written, and would have labelled 9 live targets in this vault as
        missing — the exact misreport the backlinks panel exists to avoid. One
        method now, so the panel and the health count cannot disagree.
        """
        targets: dict[str, Path] = {}
        for f in self.cfg.vault.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(self.cfg.vault).parts):
                continue
            targets.setdefault(f.name[:-3].strip().lower(), f)
        for folder in self.cfg.vault_folders:
            root = self.cfg.vault / folder
            if not root.exists():
                continue
            for f in root.rglob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                except OSError:
                    continue
                for alias in frontmatter_aliases(content):
                    targets.setdefault(alias.lower(), f)
        return targets

    def note_links(self, rel_path: str) -> dict:
        """Inbound and outbound wikilinks for one note.

        The server has always computed backlinks — linker.inject_backlinks writes
        them into the note body on every write — but nothing exposed the relation
        itself, so a reader could only see whatever text happened to be there.

        Uses `_countable_wikilinks`, the same filter vault_health's broken-link
        count uses, rather than linker.existing_targets. The distinction is not
        cosmetic: existing_targets is a bare `[[...]]` regex, so it counts shell
        test syntax and imported path references as links. Measured on this
        vault, the naive reading gives 606 links / 176 broken; the filtered one
        gives 527 / 63.

        Broken outbound links are returned rather than dropped — 63 of them exist
        and a panel that silently omitted them would misreport the note.
        """
        target = resolve_in_vault(self.cfg.vault, rel_path)
        if target is None:
            return {"error": f"Path outside the vault: {rel_path}"}
        if not target.is_file():
            return {"error": f"Not a note: {rel_path}"}

        by_stem = self.resolvable_link_targets()
        own_stem = target.stem.lower()
        own_text = target.read_text(encoding="utf-8", errors="ignore")
        outbound = []
        for link in _countable_wikilinks(own_text):
            hit = by_stem.get(link.strip().lower())
            outbound.append({"target": link.strip(),
                             "path": str(hit.relative_to(self.cfg.vault)) if hit else None,
                             "broken": hit is None})

        # Iterate distinct files, not by_stem keys: an aliased note appears under
        # every one of its names, which listed it once per alias.
        # A link may address this note by stem or by any alias it declares.
        own_names = {own_stem} | {a.lower() for a in frontmatter_aliases(own_text)}

        # Iterate distinct files, not by_stem keys: an aliased note appears under
        # every one of its names, which listed it once per alias.
        inbound = []
        for f in dict.fromkeys(by_stem.values()):
            if f == target:
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(t.strip().lower() in own_names for t in _countable_wikilinks(content)):
                inbound.append(self._note_row(f))

        inbound.sort(key=lambda n: n["title"].lower())
        return {"path": rel_path, "inbound": inbound, "outbound": outbound,
                "inbound_count": len(inbound),
                "broken_count": sum(1 for o in outbound if o["broken"])}

    def inbox_status(self) -> dict:
        """Return what is waiting in the vault _inbox folder."""
        from .extractor import SUPPORTED, format_label
        inbox = self.cfg.vault / "_inbox"
        if not inbox.exists():
            return {"count": 0, "files": [], "unsupported": [], "inbox_path": str(inbox)}

        supported, unsupported = [], []
        for f in sorted(inbox.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if not f.is_file():
                continue
            entry = {
                "name": f.name,
                "format": format_label(f),
                "size_bytes": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
            (supported if f.suffix.lower() in SUPPORTED else unsupported).append(entry)

        return {"count": len(supported), "files": supported,
                "unsupported": unsupported, "inbox_path": str(inbox)}

    def find_notes_by_stem(self, note_name: str) -> list[Path]:
        """Find notes whose filename stem contains note_name (case-insensitive).

        rglob, not glob: same subfolder-visibility fix as reindex_vault/list_notes
        (see their comments) — a non-recursive glob here made read_note,
        vault_update_note, and vault_find_similar all silently miss any note
        nested one level down (e.g. research/Client/...).
        """
        matches: list[Path] = []
        for folder in self.cfg.vault_folders:
            folder_path = self.cfg.vault / folder
            if not folder_path.exists():
                continue
            for f in folder_path.rglob("*.md"):
                if note_name.lower() in f.name[:-3].lower():
                    matches.append(f)
        if len(matches) > 1:
            logger.warning(
                "Ambiguous note name '%s' matches %d notes — using %s",
                note_name, len(matches), matches[0].relative_to(self.cfg.vault),
            )
        return matches

    def find_similar(self, note_name: str, threshold: float = 0.80, limit: int = 5) -> list[dict]:
        """Find notes semantically similar to the given note."""
        self._ensure_ready()
        if not self.collection:
            return [{"error": "Vault not initialized"}]
        matches = self.find_notes_by_stem(note_name)
        if not matches:
            return [{"error": f"Note not found: {note_name}"}]
        f = matches[0]
        try:
            source_content = f.read_text(encoding="utf-8")
        except Exception as e:
            return [{"error": f"Could not read note: {note_name} — {e}"}]
        source_path = str(f.relative_to(self.cfg.vault))
        hits = self.search(source_content[:1000], limit=limit + 1)
        return [h for h in hits if h.get("path") != source_path and h.get("similarity", 0) >= threshold]

    def update_note(self, note_name: str, append_content: str) -> dict:
        """Append content to an existing note and re-index it."""
        matches = self.find_notes_by_stem(note_name)
        if not matches:
            return {"error": f"Note not found: {note_name}"}
        f = matches[0]
        folder = f.parent.name
        try:
            existing = f.read_text(encoding="utf-8")
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            updated = existing + f"\n\n---\n*Updated {stamp}*\n\n{append_content}"
            f.write_text(updated, encoding="utf-8")
            rel = str(f.relative_to(self.cfg.vault))
            fm = self._parse_frontmatter(updated)
            title = fm.get("title") or f.name[:-3]
            self.index_note(updated, {"title": title, "path": rel, "folder": folder})
            return {"status": "ok", "path": rel, "appended_chars": len(append_content)}
        except Exception as e:
            return {"error": str(e)}

    # ── quality health ────────────────────────────────────────────────────────

    def _parse_frontmatter(self, content: str) -> dict:
        """Extract key:value pairs from the first YAML frontmatter block."""
        fm = {}
        if content.startswith("---\n"):
            close = content.find("\n---\n", 4)
            if close != -1:
                for line in content[4:close].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)   # split(1) preserves colons in values
                        fm[k.strip()] = yaml_unquote_scalar(v)
        return fm

    def get_health_summary(self) -> dict:
        """Scan vault frontmatter for quality issues. Cached 5 min in ~/.delegation_core/vault_health.json."""
        cache_path = Path.home() / ".delegation_core" / "vault_health.json"
        now = datetime.now().timestamp()

        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                # Keyed by vault: the cache path is fixed, so without this a
                # second vault — another profile, or a dashboard sidecar pointed
                # elsewhere — is served the first one's numbers for five minutes.
                # Found when two tests over different temp vaults returned
                # identical health: the second never ran.
                same_vault = cached.get("vault_path") == str(self.cfg.vault)
                if same_vault and now - cached.get("computed_at_ts", 0) < 300:
                    cached.pop("computed_at_ts", None)
                    cached.pop("vault_path", None)
                    return cached
            except Exception:
                pass

        threshold = getattr(self.cfg, "quality_threshold", 0.50)
        # Compared case-insensitively below: a vault whose folder is "Sessions"
        # would otherwise skip nothing and count every session note as an orphan.
        skip_orphan = {"sessions"}

        # ── Pass 1: read every note once; collect resolution keys + per-note data ──
        # A link resolves against the union of filename stems AND Obsidian
        # frontmatter aliases (v6) — mirroring how Obsidian itself resolves.
        resolvable: set[str] = set()      # lowercased stems + aliases
        notes: list[dict] = []
        for folder in self.cfg.vault_folders:
            fp = self.cfg.vault / folder
            if not fp.exists():
                continue
            # rglob (not glob): notes live in subfolders too (e.g.
            # research/Client/…, meetings/…/2024-2025/…). Obsidian resolves links
            # by basename across the whole vault, so a non-recursive scan misses
            # subfolder notes and falsely counts every link to them as broken.
            # Matches ChromaDB's recursive index (total_notes ⇒ indexed_notes).
            for f in fp.rglob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                # strip only ".md"; dots in the name are part of the note identity.
                # .strip() trailing whitespace so resolution matches _countable_wikilinks,
                # which strips link targets (a few ingested files have trailing-space names).
                note_stem = f.name[:-3].strip()
                resolvable.add(note_stem.lower())
                resolvable.update(a.lower() for a in frontmatter_aliases(content))
                notes.append({"stem": note_stem, "folder": folder, "content": content})

        # Obsidian resolves a wikilink against every note in the vault, not just
        # the folders delegation-core manages. Notes do live outside them —
        # MEMORY.md and Vault_Master_Index.md at the root, anything staged in
        # _processed/ — and links to those were being reported broken when they
        # open fine in Obsidian. Widen resolution only; grading above stays scoped
        # to vault_folders, which is what this vault actually curates.
        for f in self.cfg.vault.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(self.cfg.vault).parts):
                continue
            stem = f.name[:-3].strip().lower()
            if stem not in resolvable:
                resolvable.add(stem)

        # Folder names (and the vault's own name) used as markers, lowercased.
        folder_markers = {f.lower() for f in self.cfg.vault_folders}
        folder_markers.add(self.cfg.vault.name.lower())
        total = needs_repair = truncated = orphans = broken_links = 0
        # Collected alongside the counts, not by a second pass: the whole point
        # is that the detail and the summary cannot disagree.
        broken: list[dict] = []
        markers: list[dict] = []
        orphan_notes: list[dict] = []
        repair_notes: list[dict] = []
        truncated_notes: list[dict] = []
        linked_to: set[str] = set()       # stems that are the target of a resolvable link

        # ── Pass 2: grade quality + resolve only note-like, non-code wikilinks ──
        for n in notes:
            total += 1
            content = n["content"]
            try:
                fm = self._parse_frontmatter(content)
                nr = fm.get("needs_review", "").lower() == "true"
                q_str = fm.get("quality_score")
                try:
                    q = float(q_str) if q_str is not None else None
                except (ValueError, TypeError):
                    logger.debug("Unparseable quality_score %r in %s — treating as needs_repair", q_str, n["stem"])
                    q = 0.0
                if nr or (q is not None and q < threshold):
                    needs_repair += 1
                    repair_notes.append({"stem": n["stem"], "folder": n["folder"],
                                         "reason": "needs_review" if nr else f"quality {q}"})
                if fm.get("truncated", "").lower() == "true":
                    truncated += 1
                    truncated_notes.append({"stem": n["stem"], "folder": n["folder"]})
                # Verbatim machine records — graph_build's wiki articles and the
                # SessionEnd hook's raw transcripts — are a different kind of
                # object from a curated note, and both distort these metrics.
                #
                # Deliberately NOT classify_path(): that answers "which graph
                # does this belong to", for search scoping, and knows nothing
                # about session transcripts. This answers "is this a verbatim
                # machine record", which is a wider question. Two definitions
                # because there are two questions — not an oversight to merge.
                n["generated"] = (fm.get("source") == "graph_build"
                                  or fm.get("type") == "session-transcript")

                # Orphans: articles cross-link each other with relative markdown
                # links rather than [[wikilinks]], so the pass below saw every one
                # as unreferenced — one mid-sized repo took orphans from 63 to 662.
                #
                # Broken links: both kinds quote text verbatim, so a `[[...]]`
                # inside is a sample and not a link anyone meant to follow. The
                # graphify report contributed a phantom `[[Foo alloc]]`; a
                # transcript of a conversation about linker code contributed
                # `[[stem]]`, `[[target]]`, `[[source_stem]]`, `[[new-note-stem]]`.
                # Neither kind emits real wikilinks, so nothing is lost by
                # skipping them here.
                if n["generated"]:
                    continue

                for link in _countable_wikilinks(content):
                    key = link.lower()
                    if key in resolvable:
                        linked_to.add(key)
                    elif key in folder_markers:
                        # This vault ends notes with `[[reference]] #digest #pdf`
                        # — a categorisation marker naming a folder, not a link
                        # to a note. 12 of the 26 "broken links" were these, and
                        # no note will ever exist to satisfy them.
                        markers.append({"source": n["stem"], "target": link})
                    else:
                        broken_links += 1
                        broken.append({"source": n["stem"], "folder": n["folder"],
                                       "target": link})
            except Exception:
                pass

        # Orphan = hand-written note nothing links to (a true graph orphan);
        # sessions and generated artifacts excluded.
        generated = 0
        for n in notes:
            if n.get("generated"):
                generated += 1
                continue
            if n["folder"].lower() in skip_orphan:
                continue
            if n["stem"].lower() not in linked_to:
                orphans += 1
                orphan_notes.append({"stem": n["stem"], "folder": n["folder"]})

        result = {
            "total_notes": total,
            "needs_repair": needs_repair,
            "truncated": truncated,
            "orphans": orphans,
            "generated_notes": generated,
            "broken_links": broken_links,
            "computed_at": datetime.now().isoformat(),
        }
        self._last_health_detail = {
            "_vault": str(self.cfg.vault),
            **result,
            "broken_link_items": broken,
            "orphan_items": orphan_notes,
            "needs_repair_items": repair_notes,
            "truncated_items": truncated_notes,
            "folder_marker_items": markers,
        }
        try:
            cache_path.write_text(
                json.dumps({**result, "computed_at_ts": now,
                        "vault_path": str(self.cfg.vault)}, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        return result

    def health_detail(self, limit: int = 50) -> dict:
        """The findings behind get_health_summary()'s counts, itemised.

        Exists because the summary returns numbers and nothing else, so acting
        on "broken_links: 26" meant writing a throwaway script to enumerate
        them — and a throwaway script re-implements the definitions. Three such
        scripts over two days reported 248, 63 and 5 against true values of 31,
        31 and 0: one used a bare regex instead of `_countable_wikilinks`, one
        built a narrower resolvable set than the health pass uses, one compared
        an unstripped stem against a stripped link target.

        The lists are collected during the same pass that produces the counts,
        not by a second traversal, so the detail cannot drift from the summary.
        `folder_marker_items` are reported separately: they are `[[reference]]`
        style categorisation markers, deliberately not counted as broken, and
        showing them is what stops the next reader from "fixing" them.

        Always recomputed — the summary's five-minute cache holds counts only.
        """
        detail = getattr(self, "_last_health_detail", None)
        if detail is None or detail.get("_vault") != str(self.cfg.vault):
            self._force_health_recompute()
            detail = self._last_health_detail
        capped = {"limit": limit}
        for key in ("broken_link_items", "orphan_items", "needs_repair_items",
                    "truncated_items", "folder_marker_items"):
            items = detail.get(key, [])
            capped[key] = items[:limit]
            capped[f"{key}_total"] = len(items)
        for key in ("total_notes", "needs_repair", "truncated", "orphans",
                    "generated_notes", "broken_links", "computed_at"):
            capped[key] = detail.get(key)
        return capped

    def _force_health_recompute(self) -> None:
        """Run the health pass ignoring the summary cache, to populate detail."""
        cache = Path.home() / ".delegation_core" / "vault_health.json"
        backup = None
        try:
            if cache.exists():
                backup = cache.read_text(encoding="utf-8")
                cache.unlink()
            self.get_health_summary()
        finally:
            if backup is not None and not cache.exists():
                try:
                    cache.write_text(backup, encoding="utf-8")
                except OSError:
                    pass

    def get_notes_needing_repair(self, threshold: float | None = None) -> list[dict]:
        """Return [{path, content, quality_score}] sorted by score ascending (worst first).

        Excludes never_merge_folders (sessions by default) since those are chronological
        records, not synthesis artifacts, and should not be re-synthesized.
        """
        th = threshold if threshold is not None else getattr(self.cfg, "quality_threshold", 0.50)
        # Lowercased on both sides: a vault whose folder is "Sessions" would
        # otherwise match nothing here and re-synthesize chronological records
        # the docstring above promises to leave alone.
        never_merge = {f.lower() for f in
                       getattr(self.cfg, "never_merge_folders", ["sessions"])}
        results = []

        for folder in self.cfg.vault_folders:
            if folder.lower() in never_merge:
                continue
            fp = self.cfg.vault / folder
            if not fp.exists():
                continue
            # rglob, not glob: same subfolder-visibility fix as reindex_vault/
            # list_notes/get_health_summary — otherwise a note nested one folder
            # down never surfaces for healing even when its quality_score is low.
            for f in fp.rglob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                    fm = self._parse_frontmatter(content)
                    nr = fm.get("needs_review", "").lower() == "true"
                    q_str = fm.get("quality_score")
                    try:
                        q = float(q_str) if q_str is not None else None
                    except (ValueError, TypeError):
                        logger.debug("Unparseable quality_score %r in %s — treating as needs_repair", q_str, f.name)
                        q = 0.0
                    if nr or (q is not None and q < th):
                        results.append({
                            "path": str(f.relative_to(self.cfg.vault)),
                            "content": content,
                            "quality_score": q if q is not None else 0.0,
                        })
                except Exception as e:
                    logger.debug("Skipped %s in repair scan: %s", f.name, e)

        results.sort(key=lambda x: x["quality_score"])
        return results

    # ── stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        self._ensure_ready()
        folder_counts = {}
        for folder in self.cfg.vault_folders:
            p = self.cfg.vault / folder
            # rglob, not glob: indexed_notes below counts recursively (reindex_vault
            # uses rglob), so a non-recursive count here made folder_counts
            # undercount vs indexed_notes for any folder with subfolder notes.
            folder_counts[folder] = len(list(p.rglob("*.md"))) if p.exists() else 0
        return {
            "indexed_notes": self.collection.count() if self.collection else 0,
            "vault_path": str(self.cfg.vault),
            "embed_model": self.cfg.bge_model,
            "folder_counts": folder_counts,
        }
