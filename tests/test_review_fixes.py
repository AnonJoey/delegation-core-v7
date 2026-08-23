"""Regression tests for the v0.12 review findings.

Each test here pins a behaviour that the reviewed code got wrong. The two
marked HIGH are the ones that lost or hid data rather than merely reporting it
badly, and they are the reason this file exists.
"""

from pathlib import Path

import pytest

from delegation_core.config import Config
from delegation_core.embeddings import effective_chunk_chars
from delegation_core.extractor import is_dataless
from delegation_core.vault import VaultManager


# ── HIGH: legacy collection adoption must check the vectors, not just the name ──

class _LegacyCollection:
    def __init__(self, name, dim, rows=1):
        self.name = name
        self._dim = dim
        self._rows = rows
        self.renamed_to = None

    def get(self, limit=None, include=None):
        if not self._rows:
            return {"ids": [], "embeddings": []}
        return {"ids": ["a.md"], "embeddings": [[0.0] * self._dim]}

    def modify(self, name):
        self.renamed_to = name
        self.name = name


class _Client:
    def __init__(self, collections):
        self._cols = {c.name: c for c in collections}

    def list_collections(self):
        return list(self._cols.values())

    def get_collection(self, name):
        return self._cols[name]


def _vm(model):
    cfg = Config(vault_path="/tmp/x", bge_model=model)
    return VaultManager.__new__(VaultManager), cfg


def _adopt(model, legacy):
    vm, cfg = _vm(model)
    vm.cfg = cfg
    client = _Client([legacy])
    vm._adopt_legacy_collection(client)
    return legacy


def test_a_matching_dimension_is_adopted():
    """The case the rename exists for: an install from before the name was
    derived from the model kept bge-m3's own 1024-dim vectors under the old
    hardcoded `vault_bge`. Same model, same dimension — adopt it, or those rows
    go invisible and an empty collection is built beside them."""
    leg = _adopt("BAAI/bge-m3", _LegacyCollection("vault_bge", 1024))
    assert leg.renamed_to == "vault_bge_m3"


def test_a_mismatched_dimension_is_left_alone():
    """The upgrade that motivated this: bge-base's 768-dim rows must NOT be
    grafted under bge-m3's 1024-dim embedding function. Adopting them makes
    every query and upsert fail on a dimension mismatch, and the original index
    is no longer under the name a downgrade would look for."""
    leg = _adopt("BAAI/bge-m3", _LegacyCollection("vault_bge", 768))
    assert leg.renamed_to is None
    assert leg.name == "vault_bge", "the old index must stay reachable by its own name"


def test_an_unmeasured_model_does_not_adopt_on_faith():
    """No profile dimension means no way to verify. A wrong adoption is
    unrecoverable; a skipped one costs a reindex."""
    leg = _adopt("some-org/unknown-model", _LegacyCollection("vault_custom", 384))
    assert leg.renamed_to is None


def test_an_empty_legacy_collection_is_not_adopted():
    leg = _adopt("BAAI/bge-m3", _LegacyCollection("vault_bge", 768, rows=0))
    assert leg.renamed_to is None


def test_adoption_is_skipped_when_the_real_collection_exists():
    vm, cfg = _vm("BAAI/bge-m3")
    vm.cfg = cfg
    leg = _LegacyCollection("vault_bge", 1024)
    mine = _LegacyCollection(cfg.collection_name, 1024)
    vm._adopt_legacy_collection(_Client([leg, mine]))
    assert leg.renamed_to is None


# ── HIGH: a failed write must not be stamped as indexed ───────────────────────

class _Coll:
    """Collection whose upsert fails, after the delete has already landed."""

    def __init__(self, fail_upsert=False):
        self.fail_upsert = fail_upsert
        self.upserts = 0

    def delete(self, ids=None, where=None):
        pass

    def upsert(self, ids, documents, metadatas):
        if self.fail_upsert:
            raise RuntimeError("embedding backend exploded")
        self.upserts += 1

    def get(self, include=None, limit=None, offset=None):
        return {"ids": [], "metadatas": []}


def _vault_with(tmp_path, coll):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    vm = VaultManager.__new__(VaultManager)
    vm.cfg = cfg
    vm.collection = coll
    vm._initialized = True
    vm._disk_state = None
    vm._ensure_ready = lambda: None
    vm._read_disk_state = lambda: None
    return vm


def test_index_note_reports_failure(tmp_path):
    vm = _vault_with(tmp_path, _Coll(fail_upsert=True))
    assert vm.index_note("body", {"path": "Notes/a.md", "title": "a", "folder": "Notes"}) is False


def test_index_note_reports_success(tmp_path):
    vm = _vault_with(tmp_path, _Coll())
    assert vm.index_note("body", {"path": "Notes/a.md", "title": "a", "folder": "Notes"}) is True


def test_a_note_whose_write_failed_is_left_unstamped(tmp_path):
    """index_note deletes a note's rows before writing the new ones. If the
    write then fails the note has NO rows — stamping its mtime anyway would make
    every later incremental reindex skip it, so it would stay missing from
    search until someone forced a full rebuild."""
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "a.md").write_text("# a", encoding="utf-8")
    vm = _vault_with(tmp_path, _Coll(fail_upsert=True))
    vm.reindex_vault()

    state = vm._load_index_state()
    assert "Notes/a.md" not in state, "a failed write must stay retryable"

    # And the next run tries it again rather than skipping.
    vm.collection.fail_upsert = False
    vm.reindex_vault()
    assert vm.collection.upserts == 1
    assert "Notes/a.md" in vm._load_index_state()


# ── subkind must not flip depending on which write path touched the note ─────

def test_reindex_passes_content_so_subkind_survives(tmp_path):
    """A transcript whose nature is only visible in its frontmatter is graded
    `transcript` when written. Reindex must read the content too, or the same
    note comes back `curated` and ranks differently."""
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "s.md").write_text(
        "---\ntype: session-transcript\n---\n\nbody", encoding="utf-8")

    seen = {}

    class _Capture(_Coll):
        def upsert(self, ids, documents, metadatas):
            seen.update(metadatas[0])

    vm = _vault_with(tmp_path, _Capture())
    vm.reindex_vault()
    assert seen.get("subkind") == "transcript"


# ── bulk delete must be batched like every other delete in this codebase ─────

def test_delete_notes_batches_large_path_lists(tmp_path):
    calls = []

    class _Counting(_Coll):
        def delete(self, ids=None, where=None):
            calls.append(where["path"]["$in"])

    vm = _vault_with(tmp_path, _Counting())
    paths = [f"Notes/n{i}.md" for i in range(12000)]
    assert vm.delete_notes(paths) == 12000
    assert len(calls) == 3, "12000 paths must not go out as one $in"
    assert sum(len(c) for c in calls) == 12000, "and nothing may be dropped"


# ── chunk size must respect the model's token window ────────────────────────

def test_chunk_chars_are_clamped_to_a_small_window():
    """4000 chars is ~1000 English tokens. Against bge-base's 512-token window
    every chunk would be cut in half — the truncation bug chunking exists to
    fix, returning at a smaller scale."""
    assert effective_chunk_chars("BAAI/bge-base-en-v1.5", 4000, 2048) < 4000


def test_chunk_chars_pass_through_when_the_window_is_wide():
    assert effective_chunk_chars("BAAI/bge-m3", 4000, 2048) == 4000


def test_chunk_chars_untouched_when_no_cap_is_configured():
    assert effective_chunk_chars("BAAI/bge-base-en-v1.5", 4000, 0) == 4000


# ── dataless detection must not condemn a readable file ─────────────────────

def test_a_readable_file_is_never_dataless(tmp_path):
    """btrfs/ext4 store small files inline and FUSE mounts often report no block
    count. Grading those dataless made ingest skip an entire healthy tree."""
    f = tmp_path / "small.md"
    f.write_text("inline content", encoding="utf-8")
    assert is_dataless(f) is False


def test_a_missing_file_is_not_dataless(tmp_path):
    assert is_dataless(tmp_path / "nope.md") is False


# ── health_detail must answer from the vault, not from the first call ────────

def test_health_detail_reflects_a_change_made_between_calls(tmp_path):
    """It promised "Always recomputed" while reusing _last_health_detail for the
    life of the process. Fix a broken link, ask again, and the same stale list
    came back — which reads as "the fix did not work"."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    (tmp_path / "Notes").mkdir()
    src = tmp_path / "Notes" / "src.md"
    src.write_text("see [[ghost]]", encoding="utf-8")

    vm = VaultManager.__new__(VaultManager)
    vm.cfg = cfg
    vm.collection = None
    vm._initialized = True
    vm._ensure_ready = lambda: None

    first = vm.health_detail()
    assert first["broken_links"] == 1

    # Give the link something to resolve to, exactly as a real fix would.
    (tmp_path / "Notes" / "ghost.md").write_text("# ghost", encoding="utf-8")

    second = vm.health_detail()
    assert second["broken_links"] == 0, "a second call must re-read the vault"


# ── the OOM matcher must not condemn the process on a coincidence ────────────

def test_a_generic_out_of_memory_is_not_an_accelerator_oom():
    """The response to a match is moving the model to cpu permanently, so a
    false positive leaves the process ~20x slower with one log line to explain
    it. A bare "out of memory" from anywhere in the stack used to be enough."""
    from delegation_core.embeddings import _is_out_of_memory
    assert _is_out_of_memory(RuntimeError("sqlite: out of memory")) is False
    assert _is_out_of_memory(RuntimeError("corrupt weights")) is False


def test_a_real_cuda_oom_is_matched_by_message():
    from delegation_core.embeddings import _is_out_of_memory
    assert _is_out_of_memory(
        RuntimeError("CUDA out of memory. Tried to allocate 32.00 GiB")) is True


def test_a_real_oom_is_matched_by_class_name():
    from delegation_core.embeddings import _is_out_of_memory

    class OutOfMemoryError(Exception):
        pass

    assert _is_out_of_memory(OutOfMemoryError("whatever")) is True


# ── the sequence cap must not be decided by whoever constructed last ────────

def test_the_cap_is_reasserted_per_encode_not_only_at_construction():
    """STEF caches one SentenceTransformer per model name, so every EF over a
    model shares the instance. Setting the cap only in __init__ let the last EF
    built silently decide the sequence length for all of them."""
    source = (Path(__file__).resolve().parent.parent
              / "src" / "delegation_core" / "embeddings.py").read_text(encoding="utf-8")
    body = source[source.index("def _encode(self, documents"):][:1400]
    assert "self._model.max_seq_length = self.max_seq_length" in body, (
        "the cap must be re-applied on the encode path, not just at construction"
    )
