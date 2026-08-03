"""list_directories / list_notes_in / find_notes — the Phase 1 browse surface.

Measured on the real vault before this existed: 3661 of 3878 notes sat three
levels down and the browser listed only the 9 configured top-level folders, so
they could not be reached at all. Separately, semantic search could not answer
"open the note called X" — the exact title of a note written minutes earlier did
not appear in its own top 3, and a one-word title scored 0.57 against a 0.55
cutoff. find_notes() is the literal path that fixes recall.
"""

import pytest

from delegation_core.config import Config
from delegation_core.vault import VaultManager


@pytest.fixture
def vm(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Reference", "Decisions"])
    for rel in ["Reference/top.md",
                "Reference/graphs/repo/AIAgent.md",
                "Reference/graphs/repo/AIAgent_2.md",
                "Reference/graphs/other/thing.md",
                "Decisions/choice.md"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\ntitle: "{p.stem}"\n---\n\nbody\n', encoding="utf-8")
    return VaultManager(cfg)


def test_list_directories_reports_every_level_not_just_configured_folders(vm):
    dirs = {d["path"]: d for d in vm.list_directories()}
    assert set(dirs) == {"Reference", "Reference/graphs/repo",
                         "Reference/graphs/other", "Decisions"}
    assert dirs["Reference/graphs/repo"]["depth"] == 2
    assert dirs["Reference/graphs/repo"]["name"] == "repo"


def test_directory_counts_are_that_directory_only_not_its_subtree(vm):
    dirs = {d["path"]: d["count"] for d in vm.list_directories()}
    assert dirs["Reference"] == 1          # top.md — not the 3 below it
    assert dirs["Reference/graphs/repo"] == 2


def test_list_notes_in_is_not_recursive(vm):
    result = vm.list_notes_in("Reference")
    assert [n["title"] for n in result["notes"]] == ["top"]
    assert result["total"] == 1
    assert result["has_more"] is False


def test_list_notes_in_pages_and_reports_more(vm):
    first = vm.list_notes_in("Reference/graphs/repo", offset=0, limit=1)
    assert first["total"] == 2
    assert first["has_more"] is True
    second = vm.list_notes_in("Reference/graphs/repo", offset=1, limit=1)
    assert second["has_more"] is False
    assert first["notes"][0]["path"] != second["notes"][0]["path"]


def test_list_notes_in_refuses_a_path_outside_the_vault(vm):
    assert "error" in vm.list_notes_in("../../etc")


def test_list_notes_in_rejects_a_file(vm):
    assert "error" in vm.list_notes_in("Reference/top.md")


def test_find_notes_ranks_exact_stem_first(vm):
    hits = vm.find_notes("AIAgent")
    assert [h["match_rank"] for h in hits][:2] == [0, 1]
    assert hits[0]["path"].endswith("AIAgent.md")


def test_find_notes_matches_a_substring_of_the_stem(vm):
    assert [h["path"] for h in vm.find_notes("choice")] == ["Decisions/choice.md"]


def test_find_notes_matches_on_path_when_the_stem_does_not(vm):
    hits = vm.find_notes("graphs/other")
    assert len(hits) == 1
    assert hits[0]["match_rank"] == 3


def test_find_notes_is_case_insensitive(vm):
    assert vm.find_notes("aiagent")[0]["path"].endswith("AIAgent.md")


def test_find_notes_empty_query_returns_nothing(vm):
    assert vm.find_notes("") == []
    assert vm.find_notes("   ") == []


def test_find_notes_respects_the_limit(vm):
    assert len(vm.find_notes("a", limit=1)) == 1
