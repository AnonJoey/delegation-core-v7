"""search_vault confidence bands are per embedding model, not universal.

Switching this vault from bge-base-en-v1.5 to bge-m3 (for cross-lingual recall —
the English-only model scored a Portuguese query 0.622 against 0.759 for the same
question in English) moved the entire query→document similarity distribution
down, not just its centre. Measured on the reindexed vault:

    irrelevant   median 0.382 · p95 0.480 · max 0.524
    correct      0.518 – 0.677

Carrying the old bands over would have reported *every* result as "low" (nothing
reaches 0.80 any more), and the old 0.55 search_threshold cut three of four
known-good answers outright. Hard-coded cosine constants are model calibration,
so they are keyed by model.
"""

import pytest

from delegation_core.server import _confidence

M3 = "BAAI/bge-m3"
BASE = "BAAI/bge-base-en-v1.5"


@pytest.mark.parametrize("score,expected", [
    (0.70, "high"),      # above anything measured for an irrelevant hit
    (0.62, "high"),      # band edge
    (0.55, "medium"),
    (0.50, "medium"),    # band edge
    (0.49, "low"),
    (0.38, "low"),       # the irrelevant median
])
def test_m3_bands_track_the_measured_distribution(score, expected):
    assert _confidence(score, M3) == expected


@pytest.mark.parametrize("score,expected", [
    (0.85, "high"),
    (0.80, "high"),
    (0.70, "medium"),
    (0.65, "medium"),
    (0.60, "low"),
])
def test_base_model_keeps_its_original_bands(score, expected):
    assert _confidence(score, BASE) == expected


def test_a_correct_answer_that_scored_0_518_is_not_reported_as_high():
    """The weakest correct answer measured. It must still be findable — hence the
    0.45 threshold — without being oversold as a confident match."""
    assert _confidence(0.518, M3) == "medium"


def test_the_same_score_means_different_things_under_the_two_models():
    """0.65 is a strong hit for m3 and a mediocre one for bge-base; a single
    shared band would misreport one of them."""
    assert _confidence(0.65, M3) == "high"
    assert _confidence(0.65, BASE) == "medium"


def test_an_unknown_model_falls_back_to_the_conservative_bands():
    assert _confidence(0.70, "some/other-encoder") == "medium"
    assert _confidence(0.90, "") == "high"


def test_model_matching_is_case_insensitive_and_ignores_the_org_prefix():
    assert _confidence(0.62, "baai/BGE-M3") == "high"
    assert _confidence(0.62, "bge-m3") == "high"
