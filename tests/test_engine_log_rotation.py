"""engine.py llama_cpp.log startup rotation (commit b291471).

The rotation lives inline in DelegationEngine._start() just before the
subprocess spawn, so it isn't separately callable — these tests drive _start()
itself far enough to hit rotation without ever spawning anything real:
subprocess.Popen is monkeypatched to raise, which stops _start() at the exact
point after the log file has been rotated and reopened (and before any health
polling loop, so there are no sleeps). A fake config object points every path
at tmp_path; the >10MB pre-existing log is created sparsely (seek + one write)
so no test actually writes 10MB.

DelegationEngine.__init__ registers an atexit shutdown hook — each test
unregisters it so interpreter exit never runs engine teardown against fakes.
"""

import atexit
import subprocess

import pytest

from delegation_core.engine import DelegationEngine

TEN_MB = 10 * 1024 * 1024


class FakeCfg:
    """Just the attributes _start()/_shutdown() touch — llama_log_path is a
    read-only property on the real Config (hardwired to ~/.delegation_core),
    so a real Config can't be pointed at tmp_path without patching the class."""

    def __init__(self, tmp_path):
        self.llama_binary = str(tmp_path / "llama-server")
        self.llama_model = str(tmp_path / "model.gguf")
        self.llama_port = 8181
        self.llama_ctx = 4096
        self.llama_ngl = 999
        self.llama_log_path = tmp_path / "llama_cpp.log"


@pytest.fixture
def engine(tmp_path):
    cfg = FakeCfg(tmp_path)
    # Dummy binary/model files so _start()'s early existence checks pass.
    (tmp_path / "llama-server").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "model.gguf").write_text("not a real model", encoding="utf-8")
    eng = DelegationEngine(cfg)
    atexit.unregister(eng._shutdown)
    yield eng, cfg
    if eng._log_fh:
        eng._log_fh.close()
        eng._log_fh = None


@pytest.fixture
def popen_blocked(monkeypatch):
    """Popen that raises immediately: _start() reaches the point right after
    rotation + log reopen, then returns False without spawning or sleeping.
    Records calls so tests can prove the spawn point was actually reached."""
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("spawn blocked by test")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


def _write_oversized_log(log_path, marker: bytes):
    """Create a >10MB file cheaply: marker at the front, sparse hole, one byte
    past the threshold."""
    with open(log_path, "wb") as fh:
        fh.write(marker)
        fh.seek(TEN_MB)
        fh.write(b"x")
    assert log_path.stat().st_size > TEN_MB


def test_oversized_log_is_rotated_to_dot_one_and_fresh_log_opened(engine, popen_blocked):
    eng, cfg = engine
    _write_oversized_log(cfg.llama_log_path, b"OLD-LOG-CONTENT")

    assert eng._start() is False  # Popen raised — nothing was spawned

    rotated = cfg.llama_log_path.with_suffix(cfg.llama_log_path.suffix + ".1")
    assert rotated.exists()
    with open(rotated, "rb") as fh:
        assert fh.read(15) == b"OLD-LOG-CONTENT"
    # A fresh, empty log was opened in place of the rotated one.
    assert cfg.llama_log_path.exists()
    assert cfg.llama_log_path.stat().st_size == 0
    # Rotation must happen BEFORE the spawn attempt — Popen was reached after.
    assert len(popen_blocked) == 1


def test_rotation_replaces_stale_previous_dot_one(engine, popen_blocked):
    """rename() over an existing target fails on Windows and is what the
    unlink(missing_ok=True) guards — the old .1 must be replaced, not kept."""
    eng, cfg = engine
    rotated = cfg.llama_log_path.with_suffix(cfg.llama_log_path.suffix + ".1")
    rotated.write_bytes(b"ANCIENT-ROTATION")
    _write_oversized_log(cfg.llama_log_path, b"NEWER-LOG")

    eng._start()

    with open(rotated, "rb") as fh:
        assert fh.read(9) == b"NEWER-LOG"


def test_small_log_is_not_rotated_and_content_is_preserved(engine, popen_blocked):
    """Under-threshold logs must keep accumulating in place ('a' append mode) —
    rotating on every start would destroy the crash context the log exists for."""
    eng, cfg = engine
    cfg.llama_log_path.write_bytes(b"keep me")

    eng._start()

    rotated = cfg.llama_log_path.with_suffix(cfg.llama_log_path.suffix + ".1")
    assert not rotated.exists()
    assert cfg.llama_log_path.read_bytes() == b"keep me"
    assert len(popen_blocked) == 1


def test_missing_log_file_is_simply_created(engine, popen_blocked):
    eng, cfg = engine
    assert not cfg.llama_log_path.exists()

    eng._start()

    assert cfg.llama_log_path.exists()
    rotated = cfg.llama_log_path.with_suffix(cfg.llama_log_path.suffix + ".1")
    assert not rotated.exists()


def test_missing_binary_short_circuits_before_touching_the_log(engine, popen_blocked, tmp_path):
    """The binary/model existence checks come first — a misconfigured engine
    must not rotate (or even open) the log of whatever llama.cpp instance the
    path happens to point at, and must never reach Popen."""
    eng, cfg = engine
    (tmp_path / "llama-server").unlink()
    _write_oversized_log(cfg.llama_log_path, b"UNTOUCHED")

    assert eng._start() is False

    rotated = cfg.llama_log_path.with_suffix(cfg.llama_log_path.suffix + ".1")
    assert not rotated.exists()
    assert cfg.llama_log_path.stat().st_size > TEN_MB
    assert popen_blocked == []
