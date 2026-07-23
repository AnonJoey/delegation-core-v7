"""graphbridge.py: name/folder resolution and the on-disk graphs registry.

Does NOT exercise build_graph() itself — that needs the [graph] extra's heavy
tree-sitter/networkx dependencies and a real corpus to extract, which is out of
scope for a fast unit-test pass. This covers the parts that broke in practice
2026-07-23: _resolve_folder's case-sensitivity (a vault's configured folder
name, e.g. "Reference", didn't match the lowercase "reference" this code
assumed) and the registry read/write roundtrip.

cfg.graphs_dir/graphs_registry_path are derived from the module-level
CONFIG_DIR constant (not per-instance), so tests monkeypatch it to tmp_path —
otherwise they'd read/write the real ~/.delegation_core/graphs/.
"""

import json

import pytest

from delegation_core import graphbridge
from delegation_core.config import Config


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    import delegation_core.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return Config(vault_path=str(tmp_path / "vault"))


def test_slugify_replaces_unsafe_characters():
    assert graphbridge._slugify("My Project v2!") == "My-Project-v2"


def test_slugify_strips_leading_trailing_dashes():
    assert graphbridge._slugify("  /weird/path/  ") == "weird-path"


def test_slugify_empty_input_falls_back_to_graph():
    assert graphbridge._slugify("") == "graph"
    assert graphbridge._slugify("///") == "graph"


def test_resolve_folder_matches_case_insensitively():
    cfg = Config(vault_folders=["Projects", "Reference", "Decisions"])
    assert graphbridge._resolve_folder(cfg, "reference") == "Reference"


def test_resolve_folder_matches_exact_lowercase_default():
    cfg = Config(vault_folders=["decisions", "research", "reference", "sessions"])
    assert graphbridge._resolve_folder(cfg, "reference") == "reference"


def test_resolve_folder_falls_back_to_first_when_no_match():
    cfg = Config(vault_folders=["Notes", "Archive"])
    assert graphbridge._resolve_folder(cfg, "reference") == "Notes"


def test_registry_roundtrip(cfg):
    registry = {"my-graph": {"source_path": "/x", "node_count": 5}}
    graphbridge._save_registry(cfg, registry)
    assert graphbridge._load_registry(cfg) == registry


def test_load_registry_missing_file_returns_empty_dict(cfg):
    assert graphbridge._load_registry(cfg) == {}


def test_load_registry_corrupt_file_returns_empty_dict_not_raises(cfg):
    cfg.graphs_dir.mkdir(parents=True, exist_ok=True)
    cfg.graphs_registry_path.write_text("{not valid json", encoding="utf-8")
    assert graphbridge._load_registry(cfg) == {}


def test_list_graphs_reports_count(cfg):
    graphbridge._save_registry(cfg, {"a": {}, "b": {}})
    result = graphbridge.list_graphs(cfg)
    assert result["count"] == 2
    assert set(result["graphs"]) == {"a", "b"}


def test_get_report_missing_graph_returns_error(cfg):
    result = graphbridge.get_report(cfg, "never-built")
    assert "error" in result


def test_get_report_reads_written_file(cfg):
    out_dir = cfg.graphs_dir / "my-graph"
    out_dir.mkdir(parents=True)
    (out_dir / "GRAPH_REPORT.md").write_text("# Report\n\nhello", encoding="utf-8")
    result = graphbridge.get_report(cfg, "my-graph")
    assert result["name"] == "my-graph"
    assert "hello" in result["report"]


def test_get_affected_missing_graph_returns_error(cfg):
    result = graphbridge.get_affected(cfg, "never-built", "some_file.py")
    assert "error" in result
