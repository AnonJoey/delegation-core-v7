"""rename_note: the one operation that can corrupt the link graph silently.

A stem is a note's link identity, so renaming without repointing every
`[[stem]]` aimed at it breaks them all — and nothing reports the break until
someone clicks. It happened during this project's own work: a note renamed by
hand left two links dangling and they were only found days later by an audit.
The most-referenced note in the real vault has 33 inbound links, so the blast
radius is real but bounded.

71 links in that vault carry a `#section` or `|display` part, which is why the
rewriter matches the target alone rather than replacing whole links.
"""

import pytest

from delegation_core import notewriter
from delegation_core.config import Config
from delegation_core.vault import safe_filename


class FakeVault:
    def __init__(self, cfg):
        self.cfg = cfg
        self.indexed = []
        self.deleted = []

    def index_note(self, content, meta):
        self.indexed.append(meta["path"])

    def delete_notes(self, rel_paths):
        self.deleted.extend(rel_paths)
        return len(rel_paths)

    def search(self, text, limit=5):
        return []


@pytest.fixture
def vault(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Reference", "Decisions"])
    ref = tmp_path / "Reference"
    ref.mkdir(parents=True)
    (tmp_path / "Decisions").mkdir()
    (ref / "2026-08-01-old name.md").write_text(
        '---\ntitle: "old name"\n---\n\nbody\n', encoding="utf-8")
    (ref / "plain.md").write_text("Points at [[2026-08-01-old name]].\n", encoding="utf-8")
    (ref / "fancy.md").write_text(
        "See [[2026-08-01-old name|the old one]] and "
        "[[2026-08-01-old name#Summary]].\n", encoding="utf-8")
    (tmp_path / "Decisions" / "unrelated.md").write_text(
        "Links [[plain]] only.\n", encoding="utf-8")
    return FakeVault(cfg)


def _read(vault, rel):
    return (vault.cfg.vault / rel).read_text(encoding="utf-8")


def test_rename_moves_the_file_and_keeps_the_date_prefix(vault):
    result = notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")
    assert result["status"] == "ok"
    assert result["path"] == f"Reference/2026-08-01-{safe_filename('new name')}.md"
    assert not (vault.cfg.vault / "Reference/2026-08-01-old name.md").exists()


def test_rename_repoints_a_plain_link(vault):
    notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")
    assert "[[2026-08-01-new name]]" in _read(vault, "Reference/plain.md")


def test_rename_preserves_display_text_and_section_anchors(vault):
    """Replacing whole links would discard these; 71 links in the real vault
    carry one."""
    notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")
    fancy = _read(vault, "Reference/fancy.md")
    assert "[[2026-08-01-new name|the old one]]" in fancy
    assert "[[2026-08-01-new name#Summary]]" in fancy


def test_rename_leaves_unrelated_links_alone(vault):
    notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")
    assert _read(vault, "Decisions/unrelated.md") == "Links [[plain]] only.\n"


def test_rename_updates_the_frontmatter_title(vault):
    """The displayed name must not disagree with the filename."""
    result = notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")
    assert 'title: "new name"' in _read(vault, result["path"])


def test_rename_reindexes_the_new_path_and_drops_the_old(vault):
    result = notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")
    assert vault.deleted == ["Reference/2026-08-01-old name.md"]
    assert result["path"] in vault.indexed
    # referrers are reindexed too, or search keeps serving their old bodies
    assert "Reference/plain.md" in vault.indexed
    assert "Reference/fancy.md" in vault.indexed


def test_rename_reports_what_it_touched(vault):
    result = notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")
    assert result["links_rewritten"] == 2
    assert set(result["referrers"]) == {"Reference/plain.md", "Reference/fancy.md"}


def test_rename_refuses_to_overwrite_an_existing_note(vault):
    (vault.cfg.vault / "Reference" / "2026-08-01-taken.md").write_text("x", encoding="utf-8")
    result = notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "taken")
    assert "error" in result
    assert (vault.cfg.vault / "Reference/2026-08-01-old name.md").exists()


def test_rename_refuses_a_no_op(vault):
    assert "error" in notewriter.rename_note(
        vault, "Reference/2026-08-01-old name.md", "old name")


def test_rename_requires_a_title(vault):
    assert "error" in notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "  ")


def test_rename_refuses_a_path_outside_the_vault(vault):
    assert "error" in notewriter.rename_note(vault, "../../etc/passwd", "x")


def test_rename_rolls_back_when_a_write_fails(vault, monkeypatch):
    """A half-renamed vault must not be a reachable state."""
    from pathlib import Path

    real_write = Path.write_text
    calls = {"n": 0}

    def flaky(self, data, **kw):
        calls["n"] += 1
        if calls["n"] == 2:                 # first referrer
            raise OSError("disk full")
        return real_write(self, data, **kw)

    monkeypatch.setattr(Path, "write_text", flaky)
    result = notewriter.rename_note(vault, "Reference/2026-08-01-old name.md", "new name")

    assert "error" in result
    monkeypatch.undo()
    # original file still in place, and the note it touched first is restored
    assert (vault.cfg.vault / "Reference/2026-08-01-old name.md").exists()
    assert 'title: "old name"' in _read(vault, "Reference/2026-08-01-old name.md")
