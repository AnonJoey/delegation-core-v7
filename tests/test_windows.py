"""MCP window/workspace management.

This module rewrites ~/.claude.json — the file the client needs in order to start
at all. Two invariants therefore carry the weight here and are tested from several
angles: delegation-core is never unmounted (it is the channel the tools arrive on),
and entries the registry does not manage are never touched (the file belongs to the
client, and a project-scoped or hand-added server must survive a workspace switch).

Every test redirects both paths at module level, so nothing here can reach the real
config or registry.
"""

import json

import pytest

from delegation_core import windows


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Point the module at a throwaway client config and registry."""
    cfg = tmp_path / "claude.json"
    reg = tmp_path / "dc" / "mcp_workspaces.json"
    monkeypatch.setattr(windows, "CLIENT_CONFIG", cfg)
    monkeypatch.setattr(windows, "REGISTRY", reg)
    cfg.write_text(json.dumps({
        "numStartups": 7,
        "mcpServers": {
            "delegation-core": {"command": "dc", "args": ["run"]},
            "clickup": {"command": "npx", "args": ["clickup-mcp"]},
        },
    }), encoding="utf-8")
    return cfg


def mounted(cfg):
    return json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


# ── invariant 1: delegation-core is never unmounted ──────────────────────────

def test_close_refuses_self(sandbox):
    out = windows.close_window("delegation-core")
    assert "error" in out
    assert "delegation-core" in mounted(sandbox)


def test_open_refuses_self(sandbox):
    out = windows.open_window("delegation-core")
    assert "error" in out


def test_apply_workspace_keeps_self_even_when_not_a_member(sandbox):
    windows.close_window("clickup")
    windows.save_workspace("empty")
    windows.apply_workspace("empty")
    assert "delegation-core" in mounted(sandbox)


# ── invariant 2: unmanaged entries survive ───────────────────────────────────

def test_project_scoped_servers_are_never_touched(sandbox):
    """Per-project definitions live under `projects` and belong to the client's own
    project handling. A workspace switch must not read or rewrite them."""
    cfg = json.loads(sandbox.read_text(encoding="utf-8"))
    cfg["projects"] = {"/some/repo": {"mcpServers": {"scoped": {"command": "s", "args": []}}}}
    sandbox.write_text(json.dumps(cfg), encoding="utf-8")

    windows.save_workspace("base")
    windows.apply_workspace("base")

    after = json.loads(sandbox.read_text(encoding="utf-8"))
    assert after["projects"]["/some/repo"]["mcpServers"]["scoped"] == {"command": "s", "args": []}


def test_apply_unmounts_a_handmade_server_but_keeps_it_recoverable(sandbox):
    """Applying a workspace makes the set match — a server added by hand and not in
    the workspace is unmounted. It must remain reopenable, never lost."""
    windows.save_workspace("base")          # base == {clickup}
    cfg = json.loads(sandbox.read_text(encoding="utf-8"))
    cfg["mcpServers"]["handmade"] = {"command": "x", "args": ["--flag"]}
    sandbox.write_text(json.dumps(cfg), encoding="utf-8")

    out = windows.apply_workspace("base")
    assert "handmade" not in mounted(sandbox)
    assert "handmade" in out["unmounted"]

    windows.open_window("handmade")
    assert mounted(sandbox)["handmade"] == {"command": "x", "args": ["--flag"]}


def test_unrelated_top_level_keys_survive(sandbox):
    windows.close_window("clickup")
    assert json.loads(sandbox.read_text(encoding="utf-8"))["numStartups"] == 7


# ── close/open round trip ────────────────────────────────────────────────────

def test_close_then_open_restores_exact_spec(sandbox):
    original = mounted(sandbox)["clickup"]
    windows.close_window("clickup")
    assert "clickup" not in mounted(sandbox)
    windows.open_window("clickup")
    assert mounted(sandbox)["clickup"] == original


def test_close_remembers_spec_in_registry(sandbox):
    windows.close_window("clickup")
    assert windows.load_registry()["servers"]["clickup"]["command"] == "npx"


def test_open_unknown_server_errors(sandbox):
    out = windows.open_window("nope")
    assert "error" in out


def test_close_twice_is_idempotent(sandbox):
    windows.close_window("clickup")
    assert windows.close_window("clickup")["status"] == "already_closed"


def test_open_twice_is_idempotent(sandbox):
    assert windows.open_window("clickup")["status"] == "already_open"


# ── workspaces ───────────────────────────────────────────────────────────────

def test_save_workspace_excludes_self(sandbox):
    out = windows.save_workspace("soteria")
    assert out["windows"] == ["clickup"]


def test_apply_restores_a_closed_member(sandbox):
    windows.save_workspace("soteria")
    windows.close_window("clickup")
    windows.apply_workspace("soteria")
    assert "clickup" in mounted(sandbox)


def test_apply_unmounts_members_of_other_workspaces(sandbox):
    windows.save_workspace("with_clickup")
    windows.close_window("clickup")
    windows.save_workspace("bare")
    windows.apply_workspace("with_clickup")
    assert "clickup" in mounted(sandbox)
    windows.apply_workspace("bare")
    assert "clickup" not in mounted(sandbox)


def test_apply_unknown_workspace_errors(sandbox):
    assert "error" in windows.apply_workspace("ghost")


def test_apply_rejects_workspace_referencing_unknown_server(sandbox):
    reg = windows.load_registry()
    reg["workspaces"]["broken"] = ["vanished"]
    windows.save_registry(reg)
    out = windows.apply_workspace("broken")
    assert "error" in out and "vanished" in str(out)


def test_save_workspace_requires_a_name(sandbox):
    assert "error" in windows.save_workspace("   ")


# ── resilience ───────────────────────────────────────────────────────────────

def test_corrupt_registry_does_not_raise(sandbox, tmp_path):
    windows.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    windows.REGISTRY.write_text("{ not json", encoding="utf-8")
    assert windows.load_registry() == {"servers": {}, "workspaces": {}, "active": None}


def test_first_write_leaves_a_backup(sandbox):
    windows.close_window("clickup")
    assert sandbox.with_suffix(".json.dc-backup").exists()


def test_list_windows_reports_mounted_state(sandbox):
    windows.close_window("clickup")
    by_name = {w["name"]: w for w in windows.list_windows()["windows"]}
    assert by_name["clickup"]["mounted"] is False
    assert by_name["delegation-core"]["mounted"] is True
    assert by_name["delegation-core"]["managed"] is False
