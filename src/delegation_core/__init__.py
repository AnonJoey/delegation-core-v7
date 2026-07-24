"""delegation-core v0.8.1 — local MCP delegation server."""

# v5.1 patch: this docstring previously read "v0.2.0" while pyproject.toml
# declared 0.5.0 — a stale copy-paste. Kept in lockstep with pyproject going
# forward so `python -c "import delegation_core; print(delegation_core.__doc__)"`
# and `pip show delegation-core` never disagree.
#
# v0.7.0: this drifted again (docstring/__version__ said 0.6.3 while
# pyproject.toml said 0.6.4, then 0.7.0) — the exact regression the 2026-07-22
# upgrade session on the other machine caught and patched. Re-synced here.
__version__ = "0.8.1"
