"""cli.py exit-code contract (commit 7ff7399).

Before that commit ~19 error paths printed a message and then bare-`return`ed,
leaving $? at 0 — indistinguishable from success to any script or hook checking
the exit code. These tests pin the contract for the note commands: genuine
errors exit 1, success (and legitimate empty results) stay exit 0.

Same conventions as test_cli_helpers.py: direct cmd_* function calls with
argparse.Namespace args. Config.load and VaultManager are imported inside each
cmd_* function body, so they're monkeypatched at their source modules; the fake
VaultManager never opens ChromaDB or loads BGE.
"""

import argparse

import pytest

import delegation_core.vault as vault_mod
from delegation_core.cli import cmd_note_list, cmd_note_read
from delegation_core.config import Config


class FakeVaultManager:
    """Hand-written stand-in for VaultManager — only the methods the note
    commands call. Class attributes configured per-test."""

    stem_matches: list = []
    notes: list = []

    def __init__(self, cfg):
        self.cfg = cfg

    def find_notes_by_stem(self, name):
        return list(FakeVaultManager.stem_matches)

    def list_notes(self, folder, limit=20):
        return list(FakeVaultManager.notes)


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    """Configured Config + FakeVaultManager wired into the modules cli.py
    imports from at call time."""
    cfg = Config(
        vault_path=str(tmp_path / "vault"),
        llama_binary=str(tmp_path / "llama-server"),
        llama_model=str(tmp_path / "model.gguf"),
    )
    assert cfg.is_configured()
    monkeypatch.setattr(Config, "load", lambda: cfg)
    FakeVaultManager.stem_matches = []
    FakeVaultManager.notes = []
    monkeypatch.setattr(vault_mod, "VaultManager", FakeVaultManager)
    return cfg


def test_note_read_nonexistent_exits_1(cli_env):
    FakeVaultManager.stem_matches = []  # no note matches the stem
    with pytest.raises(SystemExit) as exc_info:
        cmd_note_read(argparse.Namespace(name="no-such-note"))
    assert exc_info.value.code == 1


def test_note_read_success_prints_content_and_does_not_exit(cli_env, tmp_path, capsys):
    note = tmp_path / "2026-07-24-real-note.md"
    note.write_text("note body here", encoding="utf-8")
    FakeVaultManager.stem_matches = [note]

    # No SystemExit raised == the process would end with exit code 0.
    assert cmd_note_read(argparse.Namespace(name="real-note")) is None
    assert "note body here" in capsys.readouterr().out


def test_note_read_unconfigured_exits_1(cli_env, monkeypatch):
    monkeypatch.setattr(Config, "load", lambda: Config())  # nothing configured
    with pytest.raises(SystemExit) as exc_info:
        cmd_note_read(argparse.Namespace(name="anything"))
    assert exc_info.value.code == 1


def test_note_list_invalid_folder_exits_1(cli_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cmd_note_list(argparse.Namespace(folder="not-a-folder", limit=20))
    assert exc_info.value.code == 1
    assert "Invalid folder" in capsys.readouterr().out


def test_note_list_valid_folder_success_does_not_exit(cli_env, capsys):
    FakeVaultManager.notes = [
        {"title": "A decision", "date": "2026-07-24", "path": "decisions/a.md"}
    ]
    assert cmd_note_list(argparse.Namespace(folder="decisions", limit=20)) is None
    assert "A decision" in capsys.readouterr().out


def test_note_list_empty_folder_is_not_an_error(cli_env, capsys):
    """The commit deliberately left legitimate empty-result states at exit 0 —
    'no notes yet' must not start failing scripts."""
    FakeVaultManager.notes = []
    assert cmd_note_list(argparse.Namespace(folder="decisions", limit=20)) is None
    assert "No notes" in capsys.readouterr().out
