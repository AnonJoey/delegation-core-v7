"""embeddings.py make_bge_embedding_function CUDA-OOM CPU fallback (commit bc45d1c).

detect_device() only checks whether an accelerator *exists*, not whether it has
free memory — a concurrently-running llama.cpp can leave VRAM exhausted, and
before bc45d1c every retry hit the same OOM forever, leaving vault search
permanently broken. The contract: construction failure on a detected
accelerator retries exactly once on cpu; failure when the device already *was*
cpu re-raises (a cpu retry would be the same call again).

SentenceTransformerEmbeddingFunction is imported inside the function from
chromadb.utils.embedding_functions, so it's monkeypatched at that source module
— no real model loading, no torch device calls, no network.
"""

import chromadb.utils.embedding_functions as ef_mod
import pytest

from delegation_core import embeddings


class FakeSTEF:
    """Stands in for SentenceTransformerEmbeddingFunction. Records every
    construction attempt; raises for devices listed in fail_on."""

    fail_on: tuple = ()
    constructions: list = []

    def __init__(self, model_name, device, normalize_embeddings):
        FakeSTEF.constructions.append(
            {"model_name": model_name, "device": device,
             "normalize_embeddings": normalize_embeddings}
        )
        if device in FakeSTEF.fail_on:
            raise RuntimeError(f"CUDA out of memory (simulated, device={device})")
        self.device = device


@pytest.fixture
def fake_stef(monkeypatch):
    FakeSTEF.fail_on = ()
    FakeSTEF.constructions = []
    monkeypatch.setattr(ef_mod, "SentenceTransformerEmbeddingFunction", FakeSTEF)
    return FakeSTEF


def test_cuda_construction_failure_falls_back_to_cpu(fake_stef, monkeypatch):
    monkeypatch.setattr(embeddings, "detect_device", lambda: "cuda")
    fake_stef.fail_on = ("cuda",)

    fn = embeddings.make_bge_embedding_function("BAAI/bge-base-en-v1.5")

    assert isinstance(fn, fake_stef)
    assert fn.device == "cpu"
    assert [c["device"] for c in fake_stef.constructions] == ["cuda", "cpu"]


def test_fallback_preserves_model_name_and_normalization(fake_stef, monkeypatch):
    """normalize_embeddings=True is mandatory for BGE cosine similarity to be
    correct — the cpu retry must not silently drop it or swap the model."""
    monkeypatch.setattr(embeddings, "detect_device", lambda: "cuda")
    fake_stef.fail_on = ("cuda",)

    embeddings.make_bge_embedding_function("BAAI/bge-base-en-v1.5")

    for attempt in fake_stef.constructions:
        assert attempt["model_name"] == "BAAI/bge-base-en-v1.5"
        assert attempt["normalize_embeddings"] is True


def test_mps_construction_failure_also_falls_back_to_cpu(fake_stef, monkeypatch):
    """The fallback keys off 'device was not already cpu', not 'device == cuda'
    — an Apple-silicon MPS failure takes the same one-shot cpu retry."""
    monkeypatch.setattr(embeddings, "detect_device", lambda: "mps")
    fake_stef.fail_on = ("mps",)

    fn = embeddings.make_bge_embedding_function("BAAI/bge-base-en-v1.5")

    assert fn.device == "cpu"
    assert [c["device"] for c in fake_stef.constructions] == ["mps", "cpu"]


def test_failure_when_device_already_cpu_reraises(fake_stef, monkeypatch):
    """cpu failure means something genuinely broken (bad model path, corrupt
    weights) — retrying on cpu again would loop the identical failure, so it
    must propagate, and exactly one construction attempt must have been made."""
    monkeypatch.setattr(embeddings, "detect_device", lambda: "cpu")
    fake_stef.fail_on = ("cpu",)

    with pytest.raises(RuntimeError, match="simulated"):
        embeddings.make_bge_embedding_function("BAAI/bge-base-en-v1.5")

    assert [c["device"] for c in fake_stef.constructions] == ["cpu"]


def test_successful_accelerator_construction_does_not_fall_back(fake_stef, monkeypatch):
    monkeypatch.setattr(embeddings, "detect_device", lambda: "cuda")

    fn = embeddings.make_bge_embedding_function("BAAI/bge-base-en-v1.5")

    assert fn.device == "cuda"
    assert [c["device"] for c in fake_stef.constructions] == ["cuda"]


def test_mps_applies_default_safety_caps_for_apple_silicon(fake_stef, monkeypatch):
    """MPS on Apple Silicon defaults to max_seq_length=1024 and batch_size=16 (DC-47)."""
    monkeypatch.setattr(embeddings, "detect_device", lambda: "mps")

    fn = embeddings.make_bge_embedding_function("BAAI/bge-m3")

    assert fn.device == "mps"
    assert fn.max_seq_length == 1024
    assert fn.batch_size == 16
