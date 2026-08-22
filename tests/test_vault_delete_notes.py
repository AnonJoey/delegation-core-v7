"""VaultManager.delete_notes: the counterpart to index_note.

Removing a note's file without dropping its rows leaves them answering searches
until the next full reindex runs its orphan sweep. graph_build hit this directly
— a rebuild replaces a graph's whole wiki, and community numbering is not stable
between runs, so the previous run's articles have to go.

Since v0.12 a note is many ``<path>::chunk_N`` rows, and only the note itself
knows how many, so deletion goes through ``where={"path": {"$in": [...]}}``
rather than a list of ids: ``delete(ids=["Reference/a.md"])`` now matches no row
at all. The metadata path is the one thing every row of a note shares —
including a pre-v0.12 whole-note row, which the same call reaps.

Exercises the method against a stub collection rather than a real ChromaDB
client, matching the hand-written-fake convention used elsewhere in this suite
(see test_graphbridge.FakeVaultManager). test_vault_chunking.py covers the same
method against a real collection holding real chunk rows.
"""

import json

import pytest

from delegation_core.config import Config
from delegation_core.vault import VaultManager


class StubCollection:
    """Records how it was asked to delete.

    ``delete(self, ids)`` — the pre-v0.12 signature — made this stub reject the
    ``where=`` call with a TypeError, which delete_notes catches and reports as a
    failed delete, so the state-pruning assertion below passed for the wrong
    reason. Chroma's own signature accepts both, and so must the double.
    """

    def __init__(self, raises=None):
        self.deleted_ids = None
        self.deleted_where = None
        self.calls = []
        self._raises = raises

    def delete(self, ids=None, where=None):
        if self._raises is not None:
            raise self._raises
        self.calls.append({"ids": ids, "where": where})
        if ids is not None:
            self.deleted_ids = list(ids)
        if where is not None:
            self.deleted_where = where


@pytest.fixture
def vm(tmp_path):
    cfg = Config(vault_path=str(tmp_path / "vault"))
    cfg.vault.mkdir(parents=True, exist_ok=True)
    manager = VaultManager(cfg)
    manager._ensure_ready = lambda: None   # no BGE / ChromaDB boot in unit tests
    manager.collection = StubCollection()
    return manager


def test_delete_notes_removes_rows_by_the_metadata_path(vm):
    """Was an id-based delete. That silently stopped removing anything the moment
    notes were chunked: a note's rows are keyed "<path>::chunk_N" and the bare
    path is no longer any row's id."""
    assert vm.delete_notes(["Reference/a.md", "Reference/b.md"]) == 2

    assert vm.collection.deleted_where == {
        "path": {"$in": ["Reference/a.md", "Reference/b.md"]}
    }
    assert vm.collection.deleted_ids is None, "ids would match no chunk row"


def test_the_whole_batch_goes_in_one_round_trip(vm):
    """graph_build hands over the entire stale wiki of a graph — hundreds of
    paths. One `$in` covers them; a delete per path would not."""
    vm.delete_notes([f"Reference/graphs/alpha/Community_{i}.md" for i in range(200)])

    assert len(vm.collection.calls) == 1
    assert len(vm.collection.deleted_where["path"]["$in"]) == 200


def test_delete_notes_prunes_the_incremental_index_state(vm):
    """Leaving the mtime entry behind would make the next incremental reindex
    treat a since-recreated file as unchanged and skip re-embedding it."""
    vm._save_index_state({"Reference/a.md": 111.0, "Reference/keep.md": 222.0})

    vm.delete_notes(["Reference/a.md"])

    state = json.loads(vm._index_state_path().read_text(encoding="utf-8"))
    assert state == {"Reference/keep.md": 222.0}


def test_delete_notes_empty_list_is_a_noop(vm):
    assert vm.delete_notes([]) == 0
    assert vm.collection.calls == []


def test_delete_notes_survives_a_failing_collection(vm):
    """Filing artifacts is best-effort; a ChromaDB error must not abort the
    surrounding graph build."""
    vm.collection = StubCollection(raises=RuntimeError("chroma unavailable"))

    assert vm.delete_notes(["Reference/a.md"]) == 0


def test_delete_notes_without_a_collection_returns_zero(vm):
    vm.collection = None

    assert vm.delete_notes(["Reference/a.md"]) == 0
