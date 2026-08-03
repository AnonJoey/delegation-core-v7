"""compose_note: exactly one frontmatter block, whatever the caller passes.

write_note used to concatenate its generated frontmatter with `content`
verbatim. Callers routinely pass their own block — the AGENT_GUIDE's "Content
guidelines" section showed one as the note body — so notes ended up with two
stacked blocks. Obsidian parses only the first, which meant every key the author
wrote (subtitle, tags, aliases) silently rendered as literal body text after a
horizontal rule. Seven notes in the live vault were found in that state.

These pin the merge contract: caller's block preserved verbatim, generated keys
filled in only where absent.
"""

from delegation_core.vault import compose_note

DATE = "2026-07-27"


def _blocks(text: str) -> int:
    """Number of leading `---` fences, i.e. frontmatter blocks stacked at the top."""
    n, rest = 0, text
    while rest.startswith("---\n"):
        close = rest.find("\n---\n", 4)
        if close == -1:
            break
        n += 1
        rest = rest[close + 5:].lstrip("\n")
    return n


def test_plain_content_gets_generated_frontmatter():
    out = compose_note("My Note", "## Summary\n\nbody", DATE)
    assert _blocks(out) == 1
    assert 'title: "My Note"' in out
    assert f"date: {DATE}" in out
    assert "ai_generated: true" in out
    assert out.rstrip().endswith("body")


def test_supplied_frontmatter_is_not_stacked():
    content = (
        "---\n"
        "title: Short Name\n"
        'subtitle: "The long descriptive one"\n'
        "---\n\n"
        "## Summary\n\nbody"
    )
    out = compose_note("A Much Longer Generated Title", content, DATE)
    assert _blocks(out) == 1


def test_supplied_keys_win_over_generated():
    """An explicit title in the content is what lets a note carry a short display
    title independent of the filename, which is derived from the title argument."""
    content = "---\ntitle: Short Name\n---\n\nbody"
    out = compose_note("A Much Longer Generated Title", content, DATE)
    assert "title: Short Name" in out
    assert "A Much Longer Generated Title" not in out


def test_missing_generated_keys_are_added_to_supplied_block():
    content = "---\nsubtitle: something\n---\n\nbody"
    out = compose_note("T", content, DATE)
    assert "subtitle: something" in out
    assert 'title: "T"' in out
    assert f"date: {DATE}" in out
    assert "ai_generated: true" in out
    assert _blocks(out) == 1


def test_block_list_aliases_survive_verbatim():
    """The caller's block is preserved rather than re-serialized, so YAML this
    module does not model (block lists) must come through untouched."""
    content = (
        "---\n"
        "aliases:\n"
        "  - First Alias\n"
        "  - Second Alias\n"
        "---\n\n"
        "body"
    )
    out = compose_note("T", content, DATE)
    assert "aliases:\n  - First Alias\n  - Second Alias" in out
    assert _blocks(out) == 1
    # a `- item` line must never be mistaken for a top-level key
    assert 'title: "T"' in out


def test_list_item_containing_colon_is_not_read_as_a_key():
    content = "---\naliases:\n  - Q3: the reckoning\n---\n\nbody"
    out = compose_note("Real Title", content, DATE)
    assert 'title: "Real Title"' in out
    assert "  - Q3: the reckoning" in out


def test_unterminated_frontmatter_is_treated_as_body():
    """Malformed input must not crash or silently swallow the note's text."""
    content = "---\ntitle: never closed\n\nbody text"
    out = compose_note("T", content, DATE)
    assert _blocks(out) == 1
    assert "body text" in out
    assert 'title: "T"' in out


def test_leading_blank_lines_before_frontmatter():
    content = "\n\n---\nsubtitle: x\n---\n\nbody"
    out = compose_note("T", content, DATE)
    assert _blocks(out) == 1
    assert "subtitle: x" in out


def test_body_without_frontmatter_starting_with_horizontal_rule():
    """A body that opens with a thematic break is not frontmatter."""
    content = "---\n\njust a rule above\n"
    out = compose_note("T", content, DATE)
    assert 'title: "T"' in out
    assert "just a rule above" in out


def test_title_with_quotes_is_escaped():
    out = compose_note('He said "hi"', "body", DATE)
    assert '\\"hi\\"' in out
