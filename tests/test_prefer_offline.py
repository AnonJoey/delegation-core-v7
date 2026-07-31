"""HF offline mode must be conditional on the weights actually being cached.

Setting HF_HUB_OFFLINE unconditionally is a trap that this project walked into: on
a machine where the embedding model was never downloaded, offline mode forbids the
one download that would fix it, so every startup fails identically forever instead
of self-healing once. Observed live — the dashboard sidecar logged
"couldn't connect ... and couldn't find them in the cached files" on a loop while
the network was fine and no cache existed anywhere on disk.
"""

import pytest

from delegation_core import embeddings

MODEL = "BAAI/bge-base-en-v1.5"
SLUG = "models--BAAI--bge-base-en-v1.5"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("HF_HUB_OFFLINE", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(embeddings.Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_offline_not_enabled_when_model_is_absent(clean_env, monkeypatch):
    import os
    assert embeddings.prefer_offline(MODEL) is False
    assert "HF_HUB_OFFLINE" not in os.environ


def test_offline_enabled_when_model_is_cached(clean_env, monkeypatch):
    import os
    (clean_env / ".cache" / "huggingface" / "hub" / SLUG).mkdir(parents=True)
    assert embeddings.prefer_offline(MODEL) is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_sentence_transformers_cache_layout_also_counts(clean_env):
    (clean_env / ".cache" / "torch" / "sentence_transformers" /
     "BAAI_bge-base-en-v1.5").mkdir(parents=True)
    assert embeddings.prefer_offline(MODEL) is True


def test_hf_home_is_honoured(clean_env, monkeypatch, tmp_path):
    alt = tmp_path / "alt"
    (alt / "hub" / SLUG).mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(alt))
    assert embeddings.prefer_offline(MODEL) is True


def test_hf_hub_cache_is_honoured(clean_env, monkeypatch, tmp_path):
    alt = tmp_path / "hubcache"
    (alt / SLUG).mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(alt))
    assert embeddings.prefer_offline(MODEL) is True


def test_explicit_operator_setting_is_never_overridden(clean_env, monkeypatch):
    """An operator who set the variable by hand outranks the cache heuristic —
    in both directions."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert embeddings.prefer_offline(MODEL) is True          # no cache, still honoured

    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    (clean_env / ".cache" / "huggingface" / "hub" / SLUG).mkdir(parents=True)
    assert embeddings.prefer_offline(MODEL) is False         # cached, still honoured


def test_unusable_cache_root_does_not_raise(clean_env, monkeypatch):
    """A bad override must degrade to "not cached", never take startup down.
    (A null byte can't be used here — putenv rejects it before the code is reached;
    an over-long path is a value that really can be set and really does fail.)"""
    monkeypatch.setenv("HF_HUB_CACHE", "/" + "x" * 5000)
    assert embeddings.prefer_offline(MODEL) is False
