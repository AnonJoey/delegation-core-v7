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


# ── note_links: the backlinks relation ───────────────────────────────────────

@pytest.fixture
def linked(tmp_path):
    """hub is referenced by two notes; it points at one real and one dead target."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Reference"])
    notes = {
        "hub": "See [[leaf]] and [[ghost]].",
        "a": "Points at [[hub]].",
        "b": "Also points at [[hub]].",
        "leaf": "No links here.",
        "shell": "Bash idiom: `[[ -f x ]]` and [[hub]] in prose.",
    }
    for stem, body in notes.items():
        p = tmp_path / "Reference" / f"{stem}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'---\ntitle: "{stem}"\n---\n\n{body}\n', encoding="utf-8")
    return VaultManager(cfg)


def test_note_links_lists_inbound_references(linked):
    result = linked.note_links("Reference/hub.md")
    assert {n["title"] for n in result["inbound"]} == {"a", "b", "shell"}
    assert result["inbound_count"] == 3


def test_note_links_marks_a_dead_target_broken_instead_of_dropping_it(linked):
    """63 broken links exist in the real vault; omitting them would make a note
    look better connected than it is."""
    outbound = {o["target"]: o for o in linked.note_links("Reference/hub.md")["outbound"]}
    assert outbound["leaf"]["broken"] is False
    assert outbound["leaf"]["path"] == "Reference/leaf.md"
    assert outbound["ghost"]["broken"] is True
    assert outbound["ghost"]["path"] is None
    assert linked.note_links("Reference/hub.md")["broken_count"] == 1


def test_note_links_ignores_shell_syntax_inside_code_spans(linked):
    """Uses _countable_wikilinks, the same filter vault_health uses — not
    linker.existing_targets, whose bare regex counts `[[ -f x ]]` as a link.
    On the real vault that difference is 606/176 versus 527/63."""
    outbound = linked.note_links("Reference/shell.md")["outbound"]
    assert [o["target"] for o in outbound] == ["hub"]


def test_note_links_excludes_self_reference(linked):
    assert all(n["title"] != "hub" for n in linked.note_links("Reference/hub.md")["inbound"])


def test_note_links_note_with_no_links_is_empty_not_an_error(linked):
    result = linked.note_links("Reference/leaf.md")
    assert result["outbound"] == []
    assert result["inbound_count"] == 1     # hub points at it


def test_note_links_rejects_a_path_outside_the_vault(linked):
    assert "error" in linked.note_links("../../etc/passwd")


def test_note_links_rejects_a_directory(linked):
    assert "error" in linked.note_links("Reference")


def test_note_links_resolves_frontmatter_aliases(tmp_path):
    """A link addressing a note by its declared alias is not broken.

    note_links originally resolved only against stems of notes inside
    vault_folders, while vault_health had always also honoured aliases and notes
    outside those folders. On the real vault that gap labelled 9 live targets as
    missing — the exact misreport the panel exists to prevent.
    """
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Reference"])
    ref = tmp_path / "Reference"
    ref.mkdir(parents=True)
    (ref / "command-center.md").write_text(
        '---\ntitle: "cc"\naliases:\n  - Command Center\n---\n\nhub\n', encoding="utf-8")
    (ref / "user.md").write_text(
        '---\ntitle: "user"\n---\n\nSee [[Command Center]].\n', encoding="utf-8")

    vm = VaultManager(cfg)
    assert vm.note_links("Reference/user.md")["broken_count"] == 0
    inbound = vm.note_links("Reference/command-center.md")["inbound"]
    assert [n["path"] for n in inbound] == ["Reference/user.md"]


def test_note_links_resolves_notes_outside_the_managed_folders(tmp_path):
    """MEMORY.md at the vault root resolves for a reader; only grading is scoped."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Reference"])
    (tmp_path / "Reference").mkdir(parents=True)
    (tmp_path / "MEMORY.md").write_text("root note\n", encoding="utf-8")
    (tmp_path / "Reference" / "a.md").write_text("Points at [[MEMORY]].\n", encoding="utf-8")

    assert VaultManager(cfg).note_links("Reference/a.md")["broken_count"] == 0


def test_note_links_lists_an_aliased_referrer_once(tmp_path):
    """The alias index maps several names to one file; iterating its keys listed
    that file once per alias."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Reference"])
    ref = tmp_path / "Reference"
    ref.mkdir(parents=True)
    (ref / "target.md").write_text("plain\n", encoding="utf-8")
    (ref / "many-names.md").write_text(
        '---\naliases:\n  - First\n  - Second\n  - Third\n---\n\nSee [[target]].\n',
        encoding="utf-8")

    inbound = VaultManager(cfg).note_links("Reference/target.md")["inbound"]
    assert [n["path"] for n in inbound] == ["Reference/many-names.md"]


# ── classify_path: graph reports are generated too ───────────────────────────

@pytest.mark.parametrize("rel,expected", [
    ("Reference/graphs/hermes-agent/AIAgent.md", ("generated", "hermes-agent")),
    ("Reference/2026-08-03-Code Graph Report — hermes-agent.md", ("generated", "hermes-agent")),
    ("Decisions/2026-01-01-Code Graph Report — repo.md", ("generated", "repo")),
    ("Reference/2026-08-03-An ordinary note.md", ("note", "")),
    ("Reference/Code Graph Report — no date.md", ("note", "")),
    ("Reference/sub/2026-08-03-Code Graph Report — x.md", ("note", "")),
])
def test_classify_path_recognises_graph_reports(rel, expected):
    """graph_build files its report at the top of the folder on purpose — the
    "discoverable entry point" — so it is not under graphs/ and was graded as a
    hand-written note. That leaked 5 reports into the dashboard's knowledge graph
    and counted their code-derived link artifacts as the user's broken links."""
    from delegation_core.vault import VaultManager
    assert VaultManager.classify_path(rel) == expected
