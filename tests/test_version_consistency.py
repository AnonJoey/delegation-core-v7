"""__version__ and pyproject.toml agree.

This has now drifted three times: 0.2.0 vs 0.5.0 (v5.1), 0.6.3 vs 0.6.4 (v0.7.0),
and 0.9.0 vs 0.10.0 vs a docstring saying 0.10.0 (v0.11.0, found because the
dashboard header was showing 0.9.0 while pyproject said 0.10.0). Each time it
was re-synced by hand and a comment promised lockstep; the comments did not
survive the next release. This test does.

Deliberately not asserted against importlib.metadata: an editable install
freezes its metadata at install time, and the dev machine's read 0.7.0 while
the source said 0.10.0. That number tracks when someone last ran pip, not what
the code is, so it would fail this test for the wrong reason.
"""

import tomllib
from pathlib import Path

import delegation_core

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_version_matches_pyproject():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert delegation_core.__version__ == declared, (
        f"__init__.py says {delegation_core.__version__}, pyproject.toml says "
        f"{declared} — update both, or the daemon and `pip show` disagree about "
        f"what is running."
    )


def test_docstring_carries_no_version_copy():
    """The docstring used to hold a third copy, and it drifted too."""
    assert delegation_core.__doc__ is not None
    assert delegation_core.__version__ not in delegation_core.__doc__


def test_dashboard_header_does_not_hardcode_a_version():
    """index.html's brand badge was a fifth copy — the one on screen, reading
    v0.9.0 against a 0.10.0 source tree. It takes the version from /api/status
    now, so nothing in the markup should look like a hardcoded one."""
    import re

    index = (PYPROJECT.parent / "dashboard" / "src" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"v\d+\.\d+\.\d+", index), (
        "a version literal is back in the dashboard markup — it cannot be kept "
        "in sync and it is the one users see"
    )
