"""
embeddings.py — BGE embedding function factory and text chunking utilities.

Isolated from vault.py so the embedding backend can be swapped without
touching vault logic. normalize_embeddings=True is mandatory for BGE
cosine similarity to be correct.

New in v0.2 (previously embedded in vault.py).
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("embeddings")


#: Per-model retrieval calibration, measured on a real vault rather than guessed.
#:
#: Cosine thresholds are NOT portable between embedding models: a short query
#: against a long note scores on a different scale for each one. Switching this
#: vault from bge-base-en-v1.5 to bge-m3 moved the whole distribution down, and
#: carrying the old numbers over reported every result as "low confidence" while
#: the old 0.55 floor silently cut three of four known-good answers.
#:
#: Measured head-to-head over the same 1608 indexed rows, 9 known-answer queries:
#:   bge-base-en-v1.5 — sharper when it hits (0.75–0.82 at rank 1), but returned
#:                      nothing at all for 4 of 9, three of them Portuguese.
#:   bge-m3           — found the answer in all 9, at a compressed scale
#:                      (irrelevant p95 0.480 / max 0.524; correct 0.518–0.677).
MODEL_PROFILES: dict[str, dict] = {
    "BAAI/bge-base-en-v1.5": {
        "collection": "vault_bge",          # historical name — do not rename, it holds existing data
        "dim": 768,
        "max_seq": 512,
        "search_threshold": 0.55,
        "confidence": (0.80, 0.65),
        "languages": "inglês",
        "summary": "menor e mais rápido, ranqueia com mais contraste — mas monolíngue",
    },
    "BAAI/bge-m3": {
        "collection": "vault_bge_m3",
        "dim": 1024,
        "max_seq": 8192,
        "search_threshold": 0.45,
        "confidence": (0.62, 0.50),
        "languages": "multilíngue (100+)",
        "summary": "acha o que o base não vê, sobretudo em português; escala comprimida",
    },
}

#: Used for any model without a measured profile. Deliberately conservative:
#: an unmeasured model gets the safest floor rather than another model's numbers.
DEFAULT_PROFILE = {
    "collection": "vault_custom",
    "dim": None,
    "max_seq": None,
    "search_threshold": 0.50,
    "confidence": (0.80, 0.65),
    "languages": "desconhecido",
    "summary": "sem calibração medida — limiares são chute conservador",
}


def profile_for(model_name: str) -> dict:
    """Return the retrieval profile for an embedding model.

    Matching is exact first, then case-insensitive on the bare model name, so
    "bge-m3" and "BAAI/bge-m3" resolve to the same profile.
    """
    if model_name in MODEL_PROFILES:
        return MODEL_PROFILES[model_name]
    bare = (model_name or "").rsplit("/", 1)[-1].lower()
    for known, profile in MODEL_PROFILES.items():
        if known.rsplit("/", 1)[-1].lower() == bare:
            return profile
    return DEFAULT_PROFILE


def collection_name_for(model_name: str) -> str:
    """ChromaDB collection holding this model's vectors.

    One collection per model, because their vectors have different dimensions and
    ChromaDB rejects a mismatched insert. Keeping them separate is also what makes
    switching models cheap: both indexes can coexist, so going back is immediate
    instead of a full reindex. Unknown models are slugged rather than sharing a
    bucket, which would reintroduce the dimension clash.
    """
    profile = profile_for(model_name)
    if profile is not DEFAULT_PROFILE:
        return profile["collection"]
    slug = "".join(c if c.isalnum() else "_" for c in (model_name or "custom").lower())
    return f"vault_{slug.strip('_')[:48]}"


def _model_is_cached(model_name: str) -> bool:
    """True when the model's weights are already on disk.

    Checks the Hugging Face hub cache layout (`models--org--name`) under whichever
    root is configured, plus the sentence-transformers cache some versions use.
    """
    roots = []
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]))
    hf_home = os.environ.get("HF_HOME")
    roots.append(Path(hf_home) / "hub" if hf_home else Path.home() / ".cache" / "huggingface" / "hub")
    roots.append(Path.home() / ".cache" / "torch" / "sentence_transformers")

    slug = "models--" + model_name.replace("/", "--")
    alt = model_name.replace("/", "_")
    for root in roots:
        try:
            if (root / slug).is_dir() or (root / alt).is_dir():
                return True
        except (OSError, ValueError):
            # ValueError: a malformed value (embedded null byte) in one of the cache
            # env vars. A bad override must not take startup down with it.
            continue
    return False


def prefer_offline(model_name: str) -> bool:
    """Enable HF offline mode, but only when the weights are actually cached.

    Offline mode exists to stop a hub round-trip on every startup once the model is
    local. Setting it unconditionally is a trap: on a machine where the model was
    never downloaded it forbids the one download that would fix the situation, so
    startup fails identically forever instead of self-healing once.

    Returns True when offline mode was enabled.
    """
    if os.environ.get("HF_HUB_OFFLINE") is not None:
        return os.environ["HF_HUB_OFFLINE"] == "1"   # explicit operator choice wins
    if not model_name:
        return False        # nothing to look for — stay online rather than lock out
    if _model_is_cached(model_name):
        os.environ["HF_HUB_OFFLINE"] = "1"
        return True
    logger.info(
        "Embedding model %s is not cached locally — leaving HF online for this run "
        "so it can be fetched once. Later runs will start offline.", model_name,
    )
    return False


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
