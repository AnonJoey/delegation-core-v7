"""Pure-function helpers in vault.py: safe_filename, yaml_quote_scalar/unquote roundtrip.

These are the exact functions involved in the two upstream drift bugs fixed
2026-07-23 (hooks/session_export.py writing title: unquoted) — kept covered so
a regression there fails a test instead of silently breaking Obsidian's parser.
"""

from delegation_core.vault import safe_filename, yaml_quote_scalar, yaml_unquote_scalar


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
