"""VaultManager.search scoping: notes vs generated vs external, and per-graph.

Once a vault carries machine-generated corpora the two populations stop being
comparable: after four code graphs this vault held 978 generated notes against
179 written by hand. Without scoping, "what did I write about X" competes with
hundreds of symbol-name articles.

The first implementation filtered the result list after retrieval and was wrong
in a way worth pinning: scope='notes' returned nothing, because every one of the
top hits was generated and post-filtering can only remove, never reach further
down. Narrowing now goes into ChromaDB's `where`, which these tests assert.
"""

import pytest

from delegation_core.config import Config
from delegation_core.vault import VaultManager


class StubCollection:
    """Records the query kwargs and replays canned rows, honouring `where`."""

    def __init__(self, rows):
        self.rows = rows
        self.last_kwargs = None

    def query(self, **kwargs):
        self.last_kwargs = kwargs
        where = kwargs.get("where") or {}
        matched = [r for r in self.rows
                   if all(r["meta"].get(k) == v for k, v in where.items())]
        matched = matched[: kwargs.get("n_results", 5)]
        return {
            "documents": [[r["doc"] for r in matched]],
            "metadatas": [[r["meta"] for r in matched]],
            "distances": [[r["dist"] for r in matched]],
        }


def row(path, folder, doc="body text", dist=0.1, **extra):
    kind, graph = VaultManager.classify_path(path)
    meta = {"title": path.split("/")[-1], "path": path, "folder": folder, "kind": kind}
    if graph:
        meta["graph"] = graph
    meta.update(extra)
    return {"doc": doc, "meta": meta, "dist": dist}


@pytest.fixture
def vm(tmp_path):
    cfg = Config(vault_path=str(tmp_path / "vault"))
    manager = VaultManager(cfg)
    manager._ensure_ready = lambda: None
    manager.collection = StubCollection([
        row("Reference/graphs/alpha/Community_0.md", "Reference/graphs/alpha"),
        row("Reference/graphs/beta/Community_0.md", "Reference/graphs/beta"),
        row("Decisions/2026-01-01-real-note.md", "Decisions"),
        {"doc": "external body", "dist": 0.2,
         "meta": {"title": "README", "path": "/elsewhere/README.md",
                  "folder": "_external", "is_external": "true"}},
    ])
    return manager


# ── classification ───────────────────────────────────────────────────────────

def test_classify_path_recognises_a_generated_article():
    assert VaultManager.classify_path("Reference/graphs/alpha/Community_0.md") == ("generated", "alpha")


def test_classify_path_treats_a_normal_note_as_a_note():
    assert VaultManager.classify_path("Decisions/2026-01-01-x.md") == ("note", "")


def test_classify_path_does_not_match_a_graphs_folder_at_the_wrong_depth():
    """`graphs` must be the segment directly under a vault folder — a hand-written
    note that merely lives in a folder called graphs/ elsewhere is still a note."""
    assert VaultManager.classify_path("Reference/notes/graphs/thoughts.md") == ("note", "")
    assert VaultManager.classify_path("graphs/loose.md") == ("note", "")


def test_note_metadata_omits_graph_for_plain_notes(vm):
    meta = vm.note_metadata("Decisions/x.md", "X", "Decisions")
    assert meta["kind"] == "note" and "graph" not in meta


# ── scoping ──────────────────────────────────────────────────────────────────

def test_scope_all_does_not_filter(vm):
    hits = vm.search("q", limit=10, scope="all")
    assert vm.collection.last_kwargs.get("where") is None
    assert {h["kind"] for h in hits} == {"generated", "note", "external"}


def test_scope_notes_pushes_the_filter_into_the_query(vm):
    hits = vm.search("q", limit=10, scope="notes")
    assert vm.collection.last_kwargs["where"] == {"kind": "note"}
    assert [h["path"] for h in hits] == ["Decisions/2026-01-01-real-note.md"]


def test_scope_generated_returns_only_articles(vm):
    hits = vm.search("q", limit=10, scope="generated")
    assert vm.collection.last_kwargs["where"] == {"kind": "generated"}
    assert all(h["kind"] == "generated" for h in hits) and hits


def test_scope_external_uses_the_ingest_marker(vm):
    hits = vm.search("q", limit=10, scope="external")
    assert vm.collection.last_kwargs["where"] == {"is_external": "true"}
    assert [h["title"] for h in hits] == ["README"]


def test_graph_filter_pins_one_codebase(vm):
    hits = vm.search("q", limit=10, graph="beta")
    assert vm.collection.last_kwargs["where"] == {"graph": "beta"}
    assert [h["path"] for h in hits] == ["Reference/graphs/beta/Community_0.md"]


def test_narrowed_search_over_fetches_to_survive_the_threshold_cut(vm):
    """`where` cannot express the similarity floor, so the query asks for more
    rows than requested and truncates after filtering."""
    vm.search("q", limit=5, scope="notes")
    assert vm.collection.last_kwargs["n_results"] > 5


def test_unscoped_search_does_not_over_fetch(vm):
    vm.search("q", limit=5)
    assert vm.collection.last_kwargs["n_results"] == 5


# ── legacy rows / context budget ─────────────────────────────────────────────

def test_rows_indexed_before_kind_existed_are_classified_by_path(tmp_path):
    """Until `reindex --force` backfills, old rows carry no kind field."""
    cfg = Config(vault_path=str(tmp_path / "v"))
    vm = VaultManager(cfg)
    vm._ensure_ready = lambda: None
    vm.collection = StubCollection([
        {"doc": "d", "dist": 0.1,
         "meta": {"title": "old", "path": "Reference/graphs/alpha/Community_1.md",
                  "folder": "Reference"}},
    ])
    assert vm.search("q")[0]["kind"] == "generated"


# ── kind is stamped at write time, not only by reindex ───────────────────────

class RecordingCollection:
    """Captures what index_note actually upserts."""

    def __init__(self):
        self.upserts = []

    def upsert(self, ids, documents, metadatas):
        self.upserts.append((ids[0], metadatas[0]))


@pytest.fixture
def writer(tmp_path):
    vm = VaultManager(Config(vault_path=str(tmp_path / "v")))
    vm._ensure_ready = lambda: None
    vm.collection = RecordingCollection()
    return vm


def test_a_note_written_without_kind_is_still_reachable_by_scope(writer):
    """The live defect: write_note, vault_update_note, export_session, inbox
    classification and merges all passed a bare {title, path, folder}. The row
    landed with no `kind`, and scope='notes' filters on kind == "note" inside
    ChromaDB — so a note just written could not be found under the default scope
    until a full reindex backfilled it."""
    writer.index_note("body", {"title": "t", "path": "Decisions/a.md",
                               "folder": "Decisions"})

    _, meta = writer.collection.upserts[0]
    assert meta["kind"] == "note"


def test_a_generated_article_written_without_kind_gets_its_graph_too(writer):
    writer.index_note("body", {"title": "Community_0",
                               "path": "Reference/graphs/alpha/Community_0.md",
                               "folder": "Reference/graphs/alpha"})

    _, meta = writer.collection.upserts[0]
    assert meta["kind"] == "generated"
    assert meta["graph"] == "alpha"


def test_an_explicit_kind_from_the_caller_wins(writer):
    writer.index_note("body", {"title": "t", "path": "Decisions/a.md",
                               "folder": "Decisions", "kind": "generated"})

    assert writer.collection.upserts[0][1]["kind"] == "generated"


def test_external_chunks_are_left_unscoped(writer):
    """They scope on is_external and their path is absolute, not vault-relative —
    classify_path would grade them as hand-written notes."""
    writer.index_note("chunk", {"title": "README", "path": "/elsewhere/README.md",
                                "folder": "_external", "is_external": "true"},
                      doc_id="/elsewhere/README.md::chunk_0")

    assert "kind" not in writer.collection.upserts[0][1]


def test_an_absolute_path_is_never_graded_as_a_hand_written_note(writer):
    """inject_backlinks re-indexes with a bare dict, dropping the is_external it
    was given — 12 such rows exist in the live vault. Stamping them would file
    ingested source files under scope='notes'; leaving them unscoped is correct."""
    writer.index_note("body", {"title": "SKILL",
                               "path": "/home/joey/Projects/hermes-agent/skills/SKILL.md",
                               "folder": "/home/joey/Projects/hermes-agent/skills"})

    assert "kind" not in writer.collection.upserts[0][1]


def test_a_windows_absolute_path_is_also_left_alone(writer):
    writer.index_note("body", {"title": "SKILL", "path": "C:\\repo\\skills\\SKILL.md",
                               "folder": "skills"})

    assert "kind" not in writer.collection.upserts[0][1]


def test_a_row_with_no_path_is_left_unscoped(writer):
    """index_note falls back to a timestamp doc_id when there is no path;
    classify_path("") would grade that row as hand-written."""
    writer.index_note("body", {"title": "t", "folder": "Decisions"})

    assert "kind" not in writer.collection.upserts[0][1]


def test_the_caller_s_metadata_dict_is_not_mutated(writer):
    """Callers reuse these dicts; stamping in place would leak across writes."""
    supplied = {"title": "t", "path": "Decisions/a.md", "folder": "Decisions"}

    writer.index_note("body", supplied)

    assert "kind" not in supplied


def test_snippet_chars_caps_what_each_hit_costs(vm):
    long_doc = "x" * 5000
    vm.collection.rows = [row("Decisions/a.md", "Decisions", doc=long_doc)]
    assert len(vm.search("q", snippet_chars=50)[0]["snippet"]) == 50
    assert len(vm.search("q")[0]["snippet"]) == 800
