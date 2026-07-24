"""
embeddings.py — BGE embedding function factory and text chunking utilities.

Isolated from vault.py so the embedding backend can be swapped without
touching vault logic. normalize_embeddings=True is mandatory for BGE
cosine similarity to be correct.

New in v0.2 (previously embedded in vault.py).
"""

import logging

logger = logging.getLogger("embeddings")


def detect_device() -> str:
    """Return 'cuda', 'mps', or 'cpu' depending on available hardware accelerators."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    except Exception:
        return "cpu"


def make_bge_embedding_function(model_name: str):
    """Build a chromadb-compatible BGE embedding function.

    Uses SentenceTransformerEmbeddingFunction with normalize_embeddings=True,
    which is required for BGE models to produce correct cosine similarities.
    Automatically selects CUDA when available.
    """
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    device = detect_device()
    logger.info("Loading BGE model: %s (device=%s)", model_name, device)
    try:
        return SentenceTransformerEmbeddingFunction(
            model_name=model_name,
            device=device,
            normalize_embeddings=True,
        )
    except Exception as e:
        # detect_device() only checks whether CUDA/MPS *exists*, not whether
        # there's free memory for it right now — a concurrently-running
        # llama.cpp server (this project's own local-LLM mode) can leave next
        # to nothing free, and every retry would hit the exact same OOM
        # forever since _ensure_ready() just calls this again unchanged. Fall
        # back to CPU once rather than leaving search permanently broken.
        if device == "cpu":
            raise
        logger.warning("BGE load failed on %s (%s) — falling back to cpu", device, e)
        return SentenceTransformerEmbeddingFunction(
            model_name=model_name,
            device="cpu",
            normalize_embeddings=True,
        )


def chunk_text(text: str, max_chars: int = 4000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for better embedding coverage of long documents.

    Used by IngestManager for external files that exceed the effective embedding window.
    Short texts (≤ max_chars) are returned as a single-element list unchanged.
    """
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + max_chars])
        start += max_chars - overlap
    return chunks
