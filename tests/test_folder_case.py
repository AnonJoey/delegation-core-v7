"""Folder-name resolution against a Capitalized vault (regression, 2026-07-27).

The shipped defaults are lowercase ("decisions", "research", ...), but a vault
configured through the wizard or edited by hand commonly uses Capitalized names
("Decisions", "Reference"). Every site that hardcoded a lowercase name and tested
`"sessions" in folders` silently misbehaved on such a vault:

  - session.py    export_session fell through to vault_folders[0], filing every
                  session digest under Projects/ instead of Sessions/
  - classifier.py the session fast-path never fired; FOLDER_HINTS never matched,
                  so the prompt shipped with no folder descriptions at all; the
                  fallback became folders[0] rather than the neutral reference
                  folder; and because the model's answer is lowercased before the
                  membership test, `candidate in folders` could never be true —
                  so EVERY classified file fell back to folders[0]
  - vault.py      orphan accounting skipped nothing, counting session notes as
                  orphans and inflating the reported figure

These pin the case-insensitive behaviour so the whole class stays fixed.
"""

import asyncio

from delegation_core.classifier import FOLDER_HINTS, classify
from delegation_core.config import resolve_folder

CAPS = ["Projects", "Decisions", "Fixes", "Sessions", "Procedures",
        "Reference", "Tools", "Scratch", "Infrastructure"]
LOWER = ["decisions", "research", "tools", "fixes", "reference", "sessions"]


# ── resolve_folder ───────────────────────────────────────────────────────────

def test_resolve_folder_returns_canonical_casing():
    assert resolve_folder("sessions", CAPS) == "Sessions"
    assert resolve_folder("reference", CAPS) == "Reference"


def test_resolve_folder_is_identity_on_lowercase_vault():
    assert resolve_folder("sessions", LOWER) == "sessions"


def test_resolve_folder_returns_none_when_absent():
    assert resolve_folder("research", CAPS) is None
    assert resolve_folder("nope", LOWER) is None


def test_resolve_folder_handles_empty_and_whitespace():
    assert resolve_folder("", CAPS) is None
    assert resolve_folder("  Sessions  ", CAPS) == "Sessions"


def test_resolve_folder_matches_regardless_of_input_case():
    for probe in ("FIXES", "Fixes", "fIxEs"):
        assert resolve_folder(probe, CAPS) == "Fixes"


# ── classifier ───────────────────────────────────────────────────────────────

class _StubEngine:
    """Engine whose invoke() returns a fixed model reply."""

    def __init__(self, reply):
        self.reply = reply
        self.prompt = None

    def budget(self, _task, default):
        return default

    async def invoke(self, prompt, **_kw):
        self.prompt = prompt
        return self.reply


def test_session_fastpath_returns_capitalized_folder():
    engine = _StubEngine("Decisions")
    got = asyncio.run(classify(engine, CAPS, "session-2026-07-27.md", "notes"))
    assert got == "Sessions"          # not "sessions", not the fallback
    assert engine.prompt is None      # fast-path short-circuits before the model


def test_model_answer_resolves_to_capitalized_folder():
    """The regression: the reply is lowercased before the membership test, so a
    Capitalized folder list could never match and everything hit the fallback."""
    engine = _StubEngine("Fixes")
    got = asyncio.run(classify(engine, CAPS, "bug.md", "a crash and its workaround"))
    assert got == "Fixes"


def test_fallback_is_reference_not_first_folder():
    engine = _StubEngine("not-a-folder")
    got = asyncio.run(classify(engine, CAPS, "x.md", "body"))
    assert got == "Reference"         # neutral, never Projects (folders[0])


def test_fallback_on_engine_error_is_reference():
    class Boom(_StubEngine):
        async def invoke(self, prompt, **_kw):
            raise RuntimeError("model down")

    got = asyncio.run(classify(Boom(""), CAPS, "x.md", "body"))
    assert got == "Reference"


def test_prompt_includes_folder_hints_for_capitalized_vault():
    """hint_lines was empty on a Capitalized vault, stripping every folder
    description out of the classification prompt."""
    engine = _StubEngine("Reference")
    asyncio.run(classify(engine, CAPS, "x.md", "body"))
    assert FOLDER_HINTS["decisions"] in engine.prompt
    assert "Decisions:" in engine.prompt


def test_folder_hints_keys_are_lowercase():
    """The lookup is FOLDER_HINTS[f.lower()], so every key must be lowercase."""
    assert all(k == k.lower() for k in FOLDER_HINTS)
