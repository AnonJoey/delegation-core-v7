"""Pure-function helpers in vault.py: safe_filename, yaml_quote_scalar/unquote
roundtrip, and unique_note_path collision disambiguation.

safe_filename/yaml_*: the exact functions involved in the two upstream drift
bugs fixed 2026-07-23 (hooks/session_export.py writing title: unquoted) — kept
covered so a regression there fails a test instead of silently breaking
Obsidian's parser.

unique_note_path (commit 6856ebe): previously only exercised by a throwaway
smoke script quoted in the commit message, never by the suite. It guards
against write_note/export_session/maintenance silently overwriting an existing
note's file AND its ChromaDB index row when two same-titled notes land on the
same day — data loss, so the -2/-3 suffixing contract is pinned here.

VaultManager._init's vault_path guard (2026-08-03): an unset vault_path
resolves to Path(".") — an existing directory — so the index silently
materialised under the process's cwd instead of failing. Config.load() degrades
to defaults on any read error, so this is reachable in production.
"""

import pytest

from delegation_core.config import Config
from delegation_core.vault import (
    VaultManager,
    safe_filename,
    unique_note_path,
    yaml_quote_scalar,
    yaml_unquote_scalar,
)


def test_safe_filename_replaces_invalid_chars():
    assert safe_filename("a/b:c*d?e") == "a_b_c_d_e"


def test_safe_filename_collapses_repeated_underscores():
    assert safe_filename("a///b") == "a_b"


def test_safe_filename_truncates_to_max_len():
    assert len(safe_filename("x" * 200, max_len=10)) == 10


def test_safe_filename_empty_input_falls_back():
    assert safe_filename("") == "untitled"
    assert safe_filename("   ") == "untitled"


def test_safe_filename_truncation_does_not_strand_open_bracket():
    """Regression: a real write_note call produced the stem

        "2026-08-02-Hermes Agent v0.19.1 — dissecação da arquitetura ("

    — the 50-char slice landed just past an opening paren, so Obsidian showed
    a note (and a graph node) labelled with a dangling bracket.
    """
    stem = safe_filename("Hermes Agent v0.19.1 — dissecação da arquitetura (NousResearch)")
    assert not stem.endswith("(")
    assert stem == "Hermes Agent v0.19.1 — dissecação da arquitetura"


def test_safe_filename_truncation_cuts_on_word_boundary():
    stem = safe_filename("the quick brown fox jumps over the lazy dog", max_len=20)
    assert stem == "the quick brown fox"
    assert len(stem) <= 20


def test_safe_filename_truncation_keeps_hard_slice_without_word_boundary():
    # No space in range → backing up would leave a stub, so the slice stands.
    assert safe_filename("supercalifragilistic" + "x" * 40, max_len=10) == "supercalif"


def test_safe_filename_truncation_never_returns_empty():
    assert safe_filename("((((((((((((((((((((", max_len=10) == "untitled"


def test_yaml_quote_scalar_wraps_in_quotes():
    assert yaml_quote_scalar("hello") == '"hello"'


def test_yaml_quote_scalar_escapes_embedded_quotes_and_backslashes():
    assert yaml_quote_scalar('He said "hi"') == '"He said \\"hi\\""'
    assert yaml_quote_scalar(r"C:\path") == '"C:\\\\path"'


def test_yaml_quote_handles_colon_which_is_the_actual_bug_class():
    # Unquoted `title: Fix: the bug` is ambiguous YAML — this is exactly the
    # class of value that broke Obsidian's frontmatter parser before the fix.
    quoted = yaml_quote_scalar("Fix: the bug")
    assert quoted == '"Fix: the bug"'


def test_yaml_quote_unquote_roundtrip():
    for value in ["simple", "with: colon", 'with "quotes"', r"with\backslash", ""]:
        assert yaml_unquote_scalar(yaml_quote_scalar(value)) == value


def test_yaml_unquote_passes_through_unquoted_values():
    assert yaml_unquote_scalar("plain value") == "plain value"


def test_unique_note_path_returns_nonexisting_path_unchanged(tmp_path):
    dest = tmp_path / "2026-07-24-fresh-note.md"
    assert unique_note_path(dest) is dest


def test_unique_note_path_appends_dash_two_on_collision(tmp_path):
    dest = tmp_path / "2026-07-24-note.md"
    dest.write_text("first note", encoding="utf-8")
    assert unique_note_path(dest) == tmp_path / "2026-07-24-note-2.md"


def test_unique_note_path_appends_dash_three_on_double_collision(tmp_path):
    dest = tmp_path / "2026-07-24-note.md"
    dest.write_text("first", encoding="utf-8")
    (tmp_path / "2026-07-24-note-2.md").write_text("second", encoding="utf-8")
    assert unique_note_path(dest) == tmp_path / "2026-07-24-note-3.md"


def test_unique_note_path_keeps_suffix_after_counter(tmp_path):
    # "-2" must land BEFORE .md ("note-2.md"), never after ("note.md-2") —
    # a trailing counter would make the file invisible to Obsidian and to
    # every *.md glob in the vault pipeline.
    dest = tmp_path / "note.md"
    dest.write_text("x", encoding="utf-8")
    result = unique_note_path(dest)
    assert result.suffix == ".md"
    assert result.name == "note-2.md"


def test_vault_manager_refuses_to_index_into_cwd_when_vault_path_unset(tmp_path, monkeypatch):
    """Regression: Config() with no vault_path yields Path(".") — an existing
    directory — so chroma_path.mkdir() silently created a full ChromaDB under
    the process's cwd and 2709 notes were "filed" nowhere near the vault.

    Config.load() degrades to cls() on any read error, so an unreadable or
    corrupt config.json reaches this same state in production.
    """
    monkeypatch.chdir(tmp_path)
    vm = VaultManager(Config())

    with pytest.raises(ValueError, match="vault_path is not configured"):
        vm._init()

    assert not (tmp_path / ".chroma_bge").exists()


def test_resolve_in_vault_accepts_a_path_inside(tmp_path):
    from delegation_core.vault import resolve_in_vault
    (tmp_path / "Reference").mkdir()
    assert resolve_in_vault(tmp_path, "Reference/a.md") == tmp_path / "Reference" / "a.md"


def test_resolve_in_vault_rejects_traversal(tmp_path):
    from delegation_core.vault import resolve_in_vault
    assert resolve_in_vault(tmp_path, "../../etc/passwd") is None


def test_resolve_in_vault_rejects_a_sibling_sharing_the_name_prefix(tmp_path):
    """The bug this centralises: `str(target).startswith(str(root))` passes for
    .../vault-old when the root is .../vault. It was fixed twice independently,
    in relink_folder and in the dashboard note route, before being one function.
    """
    from delegation_core.vault import resolve_in_vault
    root = tmp_path / "vault"
    root.mkdir()
    (tmp_path / "vault-old").mkdir()
    # The naive check passes here — that is the bug being guarded against.
    assert str(tmp_path / "vault-old" / "x.md").startswith(str(root)) is True
    assert resolve_in_vault(root, "../vault-old/x.md") is None
