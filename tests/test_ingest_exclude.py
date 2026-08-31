"""ingest(exclude=...) : leaving a subtree out without moving files around.

Without this, the only control over what enters the index is which directory you
point at. Measured on a real vault: a `Logs/` folder of MD5 manifests, 188
thousand lines of hashes and paths, took 19.5 minutes to embed and answered no
question anybody would ask. The caller's only escape was to ingest each useful
subfolder one at a time, which is a workaround, not a control.

These call the real matcher, not a copy of it, which is why it lives at module
level in ingest.py rather than inside the method.
"""

import inspect
from pathlib import Path

from delegation_core.ingest import IngestManager, is_excluded


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Logs").mkdir()
    (tmp_path / "Docs" / "spec.md").write_text("real content", encoding="utf-8")
    (tmp_path / "Logs" / "build.log").write_text("noise", encoding="utf-8")
    (tmp_path / "Logs" / "manifest.txt").write_text("hashes", encoding="utf-8")
    (tmp_path / "notes.md").write_text("top level", encoding="utf-8")
    return tmp_path


def test_a_directory_name_excludes_the_whole_subtree(tmp_path):
    src = _tree(tmp_path)
    assert is_excluded(src / "Logs" / "build.log", src, ["Logs"]) is True
    assert is_excluded(src / "Logs" / "manifest.txt", src, ["Logs"]) is True
    assert is_excluded(src / "Docs" / "spec.md", src, ["Logs"]) is False
    assert is_excluded(src / "notes.md", src, ["Logs"]) is False


def test_a_path_pattern_works(tmp_path):
    src = _tree(tmp_path)
    assert is_excluded(src / "Logs" / "build.log", src, ["Logs/*"]) is True
    assert is_excluded(src / "Docs" / "spec.md", src, ["Logs/*"]) is False


def test_a_name_pattern_works(tmp_path):
    src = _tree(tmp_path)
    assert is_excluded(src / "Logs" / "build.log", src, ["*.log"]) is True
    assert is_excluded(src / "Logs" / "manifest.txt", src, ["*.log"]) is False


def test_several_patterns_are_or_ed(tmp_path):
    src = _tree(tmp_path)
    pats = ["*.log", "manifest.*"]
    assert is_excluded(src / "Logs" / "build.log", src, pats) is True
    assert is_excluded(src / "Logs" / "manifest.txt", src, pats) is True
    assert is_excluded(src / "Docs" / "spec.md", src, pats) is False


def test_no_patterns_excludes_nothing(tmp_path):
    """Default is unchanged, so every existing caller keeps its behaviour."""
    src = _tree(tmp_path)
    assert is_excluded(src / "Logs" / "build.log", src, []) is False


def test_a_path_outside_source_falls_back_to_the_name(tmp_path):
    """relative_to raises when the file is not under source; the name still matches."""
    src = _tree(tmp_path)
    outside = tmp_path.parent / "stray.log"
    assert is_excluded(outside, src, ["*.log"]) is True


def test_ingest_accepts_the_parameter():
    assert "exclude" in inspect.signature(IngestManager.ingest).parameters
