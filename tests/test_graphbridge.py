"""graphbridge.py: name/folder resolution and the on-disk graphs registry.

Does NOT exercise build_graph() itself — that needs the [graph] extra's heavy
tree-sitter/networkx dependencies and a real corpus to extract, which is out of
scope for a fast unit-test pass. This covers the parts that broke in practice
2026-07-23: _resolve_folder's case-sensitivity (a vault's configured folder
name, e.g. "Reference", didn't match the lowercase "reference" this code
assumed) and the registry read/write roundtrip.

Also covers _write_vault_note/_write_artifacts_to_vault — the direct-to-vault
write path added in the v0.7.2 correction (writes + indexes generated
report/wiki content directly, bypassing the LLM synthesize() pipeline
entirely). No prior test exercised this path at all. A lightweight
FakeVaultManager stands in for the real VaultManager (no BGE/ChromaDB): it
only needs `.cfg`, `.index_note()`, and `.search()`, matching this project's
existing convention of hand-written fakes over unittest.mock (see
test_client_tracking.py's FakeMiddlewareContext).

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


class FakeVaultManager:
    """Stands in for vault.VaultManager: records index_note() calls and returns
    a canned search() result, without touching BGE/ChromaDB."""

    def __init__(self, cfg, search_hits=None, search_error=None):
        self.cfg = cfg
        self.indexed = []
        self.search_calls = []
        self._search_hits = search_hits or []
        self._search_error = search_error

    def index_note(self, content, meta):
        self.indexed.append((content, meta))

    def search(self, text, limit=5):
        self.search_calls.append((text, limit))
        if self._search_error is not None:
            raise self._search_error
        return self._search_hits


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


def test_write_vault_note_writes_expected_frontmatter_and_indexes(cfg):
    vm = FakeVaultManager(cfg)

    rel = graphbridge._write_vault_note(vm, "reference", "My Report Title", "the body text")

    dest = cfg.vault / rel
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert 'title: "My Report Title"' in content
    assert "ai_generated: false" in content
    assert "source: graph_build" in content
    assert "the body text" in content
    assert rel.startswith("reference/")
    assert rel.endswith("My Report Title.md")
    # written + indexed at least once (index_note is called again if wikilinks
    # get appended, but with no hits there should be exactly one call)
    assert len(vm.indexed) == 1
    assert vm.indexed[0][1]["path"] == rel


def test_write_vault_note_appends_related_section_and_backlinks_when_hits_score_above_threshold(cfg):
    vault = cfg.vault
    (vault / "reference").mkdir(parents=True)
    target = vault / "reference" / "2026-01-01-existing.md"
    target.write_text("---\ntitle: Existing\n---\n\nsome content", encoding="utf-8")

    hits = [{"path": "reference/2026-01-01-existing.md", "title": "Existing", "similarity": 0.95}]
    vm = FakeVaultManager(cfg, search_hits=hits)

    rel = graphbridge._write_vault_note(vm, "reference", "New Report", "body text")

    dest = cfg.vault / rel
    content = dest.read_text(encoding="utf-8")
    assert "## Related" in content
    assert "2026-01-01-existing" in content
    # index_note called 3 times: the plain write, the re-index after the
    # Related section was appended, and inject_backlinks() re-indexing the
    # target note it just added a backlink to
    assert len(vm.indexed) == 3
    # inject_backlinks wrote a backlink into the target note pointing back here
    assert dest.stem in target.read_text(encoding="utf-8")


def test_write_vault_note_ignores_hits_below_merge_threshold(cfg):
    (cfg.vault / "reference").mkdir(parents=True)
    target = cfg.vault / "reference" / "2026-01-01-existing.md"
    target.write_text("---\ntitle: Existing\n---\n\nsome content", encoding="utf-8")

    hits = [{"path": "reference/2026-01-01-existing.md", "title": "Existing", "similarity": 0.10}]
    vm = FakeVaultManager(cfg, search_hits=hits)

    rel = graphbridge._write_vault_note(vm, "reference", "New Report", "body text")

    content = (cfg.vault / rel).read_text(encoding="utf-8")
    assert "## Related" not in content
    assert len(vm.indexed) == 1


def test_write_vault_note_swallows_search_failure_during_wikilink_injection(cfg):
    """The wikilink-injection step is best-effort (module docstring/comment: `try:
    ... except Exception: logger.warning(...)`). A search() failure must not stop
    the note from being written and its rel path returned."""
    (cfg.vault / "reference").mkdir(parents=True)
    vm = FakeVaultManager(cfg, search_error=RuntimeError("chroma unavailable"))

    rel = graphbridge._write_vault_note(vm, "reference", "Resilient Title", "body")

    dest = cfg.vault / rel
    assert dest.exists()
    assert "## Related" not in dest.read_text(encoding="utf-8")
    assert len(vm.indexed) == 1


def test_write_artifacts_to_vault_writes_report_and_wiki_articles_skipping_index(cfg, tmp_path):
    vm = FakeVaultManager(cfg)
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "community-a.md").write_text("Community A content", encoding="utf-8")
    (wiki_dir / "index.md").write_text("local nav only", encoding="utf-8")

    result = graphbridge._write_artifacts_to_vault(vm, "my-graph", "# Report body", wiki_dir)

    written = result["written_paths"]
    assert len(written) == 2  # GRAPH_REPORT + community-a, index.md skipped
    contents = [(cfg.vault / p).read_text(encoding="utf-8") for p in written]
    assert any("Report body" in c for c in contents)
    assert any("Community A content" in c for c in contents)
    assert not any("local nav only" in c for c in contents)


def test_write_artifacts_to_vault_without_wiki_dir_writes_only_the_report(cfg):
    vm = FakeVaultManager(cfg)

    result = graphbridge._write_artifacts_to_vault(vm, "my-graph", "# Report body", None)

    assert len(result["written_paths"]) == 1
    content = (cfg.vault / result["written_paths"][0]).read_text(encoding="utf-8")
    assert "Report body" in content
