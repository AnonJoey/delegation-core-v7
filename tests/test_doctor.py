"""doctor: the checks that would have caught conditions which went unnoticed.

Each check here is retrospective. On a live machine:

* the installed SessionEnd hook had drifted behind the repo, so a shipped fix
  simply was not running;
* that hook wrote transcripts into a lowercase ``sessions/`` beside the vault's
  configured ``Sessions/`` — 29 files over seven weeks, invisible to indexing,
  search and health accounting, with no error anywhere;
* the [graph] extra had never been installed, so graph_build failed on an import;
* ``graphify`` had been built before vault_paths tracking, so a rebuild could not
  clean up its 599 filed notes — doctor found this on its very first run.

None of them raise; each returns ok | warn | error plus a one-line fix.
"""

import json

import pytest

from delegation_core import doctor
from delegation_core.config import Config


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "cfgdir")
    monkeypatch.setattr(doctor, "CONFIG_DIR", tmp_path / "cfgdir")
    monkeypatch.setattr(doctor, "INSTALLED_HOOKS", tmp_path / "cfgdir" / "hooks")
    (tmp_path / "cfgdir").mkdir()
    c = Config(vault_path=str(tmp_path / "vault"),
               vault_folders=["Reference", "Sessions", "Decisions"])
    for f in c.vault_folders:
        (c.vault / f).mkdir(parents=True)
    return c


# ── vault folders ────────────────────────────────────────────────────────────

def test_case_variant_folder_is_an_error_with_the_invisible_note_count(cfg):
    """The exact live bug: a lowercase sessions/ beside the configured Sessions/,
    holding notes that no indexed path ever reaches."""
    stray = cfg.vault / "sessions"
    stray.mkdir()
    for i in range(3):
        (stray / f"t{i}.md").write_text("x", encoding="utf-8")

    result = doctor.check_vault_folders(cfg)

    assert result["status"] == "error"
    assert "sessions/ shadows Sessions/" in result["detail"]
    assert "3 note(s) invisible" in result["detail"]


def test_internal_underscore_folders_are_not_treated_as_shadows(cfg):
    for name in ("_inbox", "_processed", "_failed"):
        (cfg.vault / name).mkdir()

    assert doctor.check_vault_folders(cfg)["status"] == "ok"


def test_unrelated_folder_is_not_a_shadow(cfg):
    (cfg.vault / "Attachments").mkdir()

    assert doctor.check_vault_folders(cfg)["status"] == "ok"


def test_missing_configured_folder_is_only_a_warning(cfg):
    (cfg.vault / "Decisions").rmdir()

    result = doctor.check_vault_folders(cfg)

    assert result["status"] == "warn" and "Decisions" in result["detail"]


def test_absent_vault_is_an_error(cfg, tmp_path):
    cfg.vault_path = str(tmp_path / "nowhere")

    assert doctor.check_vault_folders(cfg)["status"] == "error"


# ── hook drift ───────────────────────────────────────────────────────────────

def test_hook_drift_detects_a_stale_installed_copy(cfg, tmp_path, monkeypatch):
    repo = tmp_path / "repo_hooks"
    repo.mkdir()
    (repo / "session_export.py").write_text("new version\n", encoding="utf-8")
    installed = tmp_path / "cfgdir" / "hooks"
    installed.mkdir()
    (installed / "session_export.py").write_text("old version\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_repo_hooks_dir", lambda: repo)

    result = doctor.check_hook_drift()

    assert result["status"] == "warn"
    assert "session_export.py" in result["detail"]
    assert result["fix"].startswith("cp ")


def test_hook_drift_reports_ok_when_bytes_match(cfg, tmp_path, monkeypatch):
    repo = tmp_path / "repo_hooks"
    repo.mkdir()
    (repo / "h.py").write_text("same\n", encoding="utf-8")
    installed = tmp_path / "cfgdir" / "hooks"
    installed.mkdir()
    (installed / "h.py").write_text("same\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_repo_hooks_dir", lambda: repo)

    assert doctor.check_hook_drift()["status"] == "ok"


def test_hook_drift_skips_cleanly_on_a_wheel_install(cfg, monkeypatch):
    monkeypatch.setattr(doctor, "_repo_hooks_dir", lambda: None)

    assert doctor.check_hook_drift()["status"] == "skip"


# ── registries ───────────────────────────────────────────────────────────────

def test_ingest_registry_flags_paths_that_no_longer_exist(cfg, tmp_path):
    (tmp_path / "cfgdir" / "ingested_sources.json").write_text(
        json.dumps({"/gone/forever": {"indexed_count": 3}}), encoding="utf-8")

    result = doctor.check_ingest_registry()

    assert result["status"] == "warn" and "ingest_forget" in result["fix"]


def test_graph_registry_flags_a_graph_built_before_vault_path_tracking(cfg, tmp_path):
    """This is what the first real doctor run found, on `graphify`."""
    cfg.graphs_dir.mkdir(parents=True, exist_ok=True)
    cfg.graphs_registry_path.write_text(json.dumps({
        "graphify": {"source_path": str(tmp_path), "node_count": 9594},
    }), encoding="utf-8")

    result = doctor.check_graph_registry(cfg)

    assert result["status"] == "warn"
    assert "graphify" in result["detail"]


def test_graph_registry_is_ok_when_sources_exist_and_paths_are_tracked(cfg, tmp_path):
    cfg.graphs_dir.mkdir(parents=True, exist_ok=True)
    cfg.graphs_registry_path.write_text(json.dumps({
        "ok-graph": {"source_path": str(tmp_path), "node_count": 10, "vault_paths": ["a.md"]},
    }), encoding="utf-8")

    assert doctor.check_graph_registry(cfg)["status"] == "ok"


# ── engine mode ──────────────────────────────────────────────────────────────

def test_agent_mode_never_complains_about_a_missing_model(cfg):
    cfg.engine_mode = "agent"
    cfg.llama_binary = "/nonexistent/llama-server"
    cfg.llama_model = "/nonexistent/model.gguf"

    assert doctor.check_engine_mode(cfg)["status"] == "ok"


def test_local_mode_with_a_missing_model_is_an_error(cfg):
    cfg.engine_mode = "local"
    cfg.llama_model = "/nonexistent/model.gguf"

    result = doctor.check_engine_mode(cfg)

    assert result["status"] == "error" and "llama_model" in result["detail"]


# ── aggregate ────────────────────────────────────────────────────────────────

def test_run_all_surfaces_the_worst_status(cfg):
    stray = cfg.vault / "sessions"
    stray.mkdir()
    (stray / "a.md").write_text("x", encoding="utf-8")

    result = doctor.run_all(cfg)

    assert result["status"] == "error"
    assert result["counts"]["error"] >= 1
    assert {c["check"] for c in result["checks"]} >= {"engine_mode", "vault_folders", "hook_drift"}
