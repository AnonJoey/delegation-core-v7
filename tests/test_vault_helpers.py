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
"""

from delegation_core.vault import (
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
