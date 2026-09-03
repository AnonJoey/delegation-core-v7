"""The GPU arbiter: exactly one of BGE and llama.cpp resident at a time.

The bug these were written against: `take()` evicted the resident that was
*asking* for the card instead of the other one. That failure is silent, because
llama.cpp's layer fitting just loads fewer layers and squeezes in beside the
BGE that should have been evicted. Nothing raises, nothing logs, and the only
symptom is a slower model on a card that looks full, which is why the direction
of eviction is asserted here rather than left to a smoke test.
"""

import sys
import types

import pytest

from delegation_core import gpu


@pytest.fixture(autouse=True)
def clean_arbiter(monkeypatch):
    """Each test starts with no owners, no holder, and a stubbed device."""
    gpu._embeddings_owners.clear()
    gpu._llama_owners.clear()
    monkeypatch.setattr(gpu, "_holder", None, raising=False)
    monkeypatch.setattr(gpu, "vram_free_mib", lambda: 1000)
    yield
    gpu._embeddings_owners.clear()
    gpu._llama_owners.clear()
    gpu._holder = None


class FakeVault:
    """Stands in for VaultManager: holds the two references that pin BGE."""

    def __init__(self):
        self.ef = object()
        self.collection = object()
        self._initialized = True


class FakeEngine:
    """Stands in for DelegationEngine: holds the llama.cpp subprocess."""

    def __init__(self, running=True):
        self._proc = object() if running else None
        self.shutdown_calls = 0

    def _shutdown(self):
        self.shutdown_calls += 1
        self._proc = None


def _stub_stef(monkeypatch, models: dict):
    """Install a fake chromadb STEF whose class-level `models` dict is `models`."""
    stef = type("SentenceTransformerEmbeddingFunction", (), {"models": models})
    mod = types.ModuleType("chromadb.utils.embedding_functions")
    mod.SentenceTransformerEmbeddingFunction = stef
    monkeypatch.setitem(sys.modules, "chromadb.utils.embedding_functions", mod)
    return stef


def _cuda_model():
    return types.SimpleNamespace(device="cuda:0")


# ── the inversion this module exists to prevent ──────────────────────────────


def test_take_llama_evicts_embeddings_not_llama(monkeypatch):
    """Asking for the card on llama's behalf must drop BGE and leave llama alone."""
    stef = _stub_stef(monkeypatch, {"BAAI/bge-m3": _cuda_model()})
    vault, engine = FakeVault(), FakeEngine(running=True)
    gpu.register_embeddings_owner(vault)
    gpu.register_llama_owner(engine)

    gpu.take("llama")

    assert stef.models == {}, "BGE was left resident while llama loaded"
    assert vault.ef is None and vault.collection is None
    assert engine.shutdown_calls == 0, "took the card for llama and then killed llama"
    assert gpu.holder() == "llama"


def test_take_embeddings_evicts_llama_not_embeddings(monkeypatch):
    """And the mirror: BGE's turn stops llama.cpp, without clearing BGE itself."""
    stef = _stub_stef(monkeypatch, {"BAAI/bge-m3": _cuda_model()})
    vault, engine = FakeVault(), FakeEngine(running=True)
    gpu.register_embeddings_owner(vault)
    gpu.register_llama_owner(engine)

    gpu.take("embeddings")

    assert engine.shutdown_calls == 1
    assert stef.models != {}, "took the card for BGE and then evicted BGE"
    assert vault.ef is not None
    assert gpu.holder() == "embeddings"


def test_alternating_takes_never_leave_both_resident(monkeypatch):
    """Several handovers in a row: after each one the other side is gone."""
    stef = _stub_stef(monkeypatch, {"BAAI/bge-m3": _cuda_model()})
    vault, engine = FakeVault(), FakeEngine(running=True)
    gpu.register_embeddings_owner(vault)
    gpu.register_llama_owner(engine)

    for _ in range(3):
        gpu.take("llama")
        assert stef.models == {}
        # BGE reloads lazily on the next search, as it does in production.
        stef.models["BAAI/bge-m3"] = _cuda_model()
        vault.ef, vault.collection = object(), object()

        gpu.take("embeddings")
        assert engine._proc is None
        engine._proc = object()  # llama restarts on the next queued task


# ── the pieces the inversion test rests on ───────────────────────────────────


def test_release_embeddings_clears_every_reference(monkeypatch):
    """STEF's cache is the owning reference, but the vault's two pin it too."""
    stef = _stub_stef(monkeypatch, {"BAAI/bge-m3": _cuda_model()})
    vault = FakeVault()
    gpu.register_embeddings_owner(vault)

    gpu.release_embeddings()

    assert stef.models == {}
    assert vault.ef is None
    assert vault.collection is None
    assert vault._initialized is False, "vault would query a collection it no longer has"


def test_release_embeddings_is_a_noop_when_bge_is_on_cpu(monkeypatch):
    """A CPU-resident BGE holds no VRAM, so there is nothing to reclaim."""
    stef = _stub_stef(monkeypatch, {"BAAI/bge-m3": types.SimpleNamespace(device="cpu")})
    vault = FakeVault()
    gpu.register_embeddings_owner(vault)

    assert gpu.release_embeddings() == 0
    assert stef.models != {}, "evicted a model that was not on the card"
    assert vault.ef is not None


def test_release_llama_leaves_a_hand_started_server_alone(monkeypatch):
    """_shutdown only stops what the engine spawned; the arbiter keeps that line."""
    engine = FakeEngine(running=False)   # _proc is None: nothing this engine started
    gpu.register_llama_owner(engine)

    assert gpu.release_llama() == 0
    assert engine.shutdown_calls == 0


def test_take_is_idempotent_for_the_current_holder(monkeypatch):
    """Re-taking the card you already hold must not evict anyone."""
    _stub_stef(monkeypatch, {"BAAI/bge-m3": _cuda_model()})
    engine = FakeEngine(running=True)
    gpu.register_llama_owner(engine)

    gpu.take("embeddings")
    assert engine.shutdown_calls == 1
    engine._proc = object()

    gpu.take("embeddings")
    assert engine.shutdown_calls == 1, "handed the card to its current holder again"


def test_take_rejects_an_unknown_resident():
    with pytest.raises(ValueError):
        gpu.take("chrome")


def test_owners_are_held_weakly(monkeypatch):
    """A discarded VaultManager must not keep the arbiter calling into it."""
    _stub_stef(monkeypatch, {})
    vault = FakeVault()
    gpu.register_embeddings_owner(vault)
    assert len(gpu._embeddings_owners) == 1

    del vault
    import gc
    gc.collect()
    assert len(gpu._embeddings_owners) == 0
