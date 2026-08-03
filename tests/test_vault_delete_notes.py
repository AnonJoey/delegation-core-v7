"""VaultManager.delete_notes: the counterpart to index_note.

Vault notes are keyed in ChromaDB by their vault-relative path, so removing a
note's file without dropping its row leaves it answering searches until the next
full reindex runs its orphan sweep. graph_build hit this directly — a rebuild
replaces a graph's whole wiki, and community numbering is not stable between
runs, so the previous run's articles have to go.

Exercises the method against a stub collection rather than a real ChromaDB
client, matching the hand-written-fake convention used elsewhere in this suite
(see test_graphbridge.FakeVaultManager).
"""

import json

import pytest

from delegation_core.config import Config
from delegation_core.vault import VaultManager


class StubCollection:
    def __init__(self, raises=None):
        self.deleted_ids = None
        self._raises = raises

    def delete(self, ids):
        if self._raises is not None:
            raise self._raises
        self.deleted_ids = list(ids)


@pytest.fixture
def vm(tmp_path):
    cfg = Config(vault_path=str(tmp_path / "vault"))
    cfg.vault.mkdir(parents=True, exist_ok=True)
    manager = VaultManager(cfg)
    manager._ensure_ready = lambda: None   # no BGE / ChromaDB boot in unit tests
    manager.collection = StubCollection()
    return manager


def test_delete_notes_removes_rows_by_relative_path(vm):
    assert vm.delete_notes(["Reference/a.md", "Reference/b.md"]) == 2
    assert vm.collection.deleted_ids == ["Reference/a.md", "Reference/b.md"]


def test_delete_notes_prunes_the_incremental_index_state(vm):
    """Leaving the mtime entry behind would make the next incremental reindex
    treat a since-recreated file as unchanged and skip re-embedding it."""
    vm._save_index_state({"Reference/a.md": 111.0, "Reference/keep.md": 222.0})

    vm.delete_notes(["Reference/a.md"])

    state = json.loads(vm._index_state_path().read_text(encoding="utf-8"))
    assert state == {"Reference/keep.md": 222.0}


def test_delete_notes_empty_list_is_a_noop(vm):
    assert vm.delete_notes([]) == 0
    assert vm.collection.deleted_ids is None


def test_delete_notes_survives_a_failing_collection(vm):
    """Filing artifacts is best-effort; a ChromaDB error must not abort the
    surrounding graph build."""
    vm.collection = StubCollection(raises=RuntimeError("chroma unavailable"))

    assert vm.delete_notes(["Reference/a.md"]) == 0


def test_delete_notes_without_a_collection_returns_zero(vm):
    vm.collection = None

    assert vm.delete_notes(["Reference/a.md"]) == 0
