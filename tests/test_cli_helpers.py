"""cli.py's _read_content: --file / piped stdin / neither-provided error path.

The CLI's note/graph/process commands all funnel content through this one
function, so its precedence rules (file wins over stdin, missing input exits
non-zero rather than hanging on a real terminal read) are worth pinning down.
"""

import io

import pytest

from delegation_core.cli import _read_content


def test_reads_from_file_when_given(tmp_path):
    f = tmp_path / "content.txt"
    f.write_text("hello from file", encoding="utf-8")
    assert _read_content(str(f)) == "hello from file"


def test_file_takes_precedence_over_stdin(tmp_path, monkeypatch):
    f = tmp_path / "content.txt"
    f.write_text("from file", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
    assert _read_content(str(f)) == "from file"


def test_reads_from_piped_stdin_when_no_file(monkeypatch):
    fake_stdin = io.StringIO("piped content")
    fake_stdin.isatty = lambda: False
    monkeypatch.setattr("sys.stdin", fake_stdin)
    assert _read_content(None) == "piped content"


def test_exits_nonzero_when_no_file_and_stdin_is_a_tty(monkeypatch):
    fake_stdin = io.StringIO("")
    fake_stdin.isatty = lambda: True
    monkeypatch.setattr("sys.stdin", fake_stdin)
    with pytest.raises(SystemExit) as exc_info:
        _read_content(None)
    assert exc_info.value.code != 0


def test_exits_nonzero_when_stdin_piped_but_empty(monkeypatch):
    fake_stdin = io.StringIO("   \n  ")
    fake_stdin.isatty = lambda: False
    monkeypatch.setattr("sys.stdin", fake_stdin)
    with pytest.raises(SystemExit):
        _read_content(None)
