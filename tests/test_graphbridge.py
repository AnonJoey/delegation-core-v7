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
entirely), including the filing layout corrected after the first real
multi-hundred-article build: articles land in a per-graph subfolder under their
original wiki stems, carry no BGE backlinks, and are cleared on rebuild. A lightweight
FakeVaultManager stands in for the real VaultManager (no BGE/ChromaDB): it
only needs `.cfg`, `.index_note()`, and `.search()`, matching this project's
existing convention of hand-written fakes over unittest.mock (see
test_client_tracking.py's FakeMiddlewareContext).

cfg.graphs_dir/graphs_registry_path are derived from the module-level
CONFIG_DIR constant (not per-instance), so tests monkeypatch it to tmp_path —
otherwise they'd read/write the real ~/.delegation_core/graphs/.
"""

import json
from pathlib import Path

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
        self.stamped = []
        self._search_hits = search_hits or []
        self._search_error = search_error

    def index_note(self, content, meta):
        self.indexed.append((content, meta))

    def search(self, text, limit=5):
        self.search_calls.append((text, limit))
        if self._search_error is not None:
            raise self._search_error
        return self._search_hits

    def delete_notes(self, rel_paths):
        self.deleted = getattr(self, "deleted", []) + list(rel_paths)
        return len(rel_paths)

    def stamp_indexed(self, rel_paths):
        # graph_build escrevia milhares de artigos sem carimbar nenhum, e o
        # reindex "incremental" seguinte reembutia todos eles. Este dublê tem
        # que acompanhar a interface real, senão o teste passa contra um
        # colaborador que não existe.
        self.stamped.extend(rel_paths)
        return len(rel_paths)

    def note_metadata(self, rel_path, title, folder):
        # Delegates to the real classifier so these tests also cover that graph
        # articles are written with kind='generated' + their graph name, which is
        # what search(scope=...) filters on.
        from delegation_core.vault import VaultManager
        return VaultManager.note_metadata(rel_path, title, folder)


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


def test_list_graphs_summarises_vault_paths_instead_of_listing_them(cfg):
    """The registry's one unbounded field pushed this tool past the MCP result cap.

    Measured on the real registry before this change: 181,666 characters for six
    graphs, 3,391 vault_paths between them, so the tool returned nothing a client
    could read. Counts are what every caller actually uses.
    """
    graphbridge._save_registry(cfg, {
        "big": {"node_count": 10, "vault_paths": [f"Reference/wiki/n{i}.md" for i in range(1441)]},
    })
    result = graphbridge.list_graphs(cfg)
    entry = result["graphs"]["big"]
    assert "vault_paths" not in entry
    assert entry["vault_notes_filed"] == 1441
    assert entry["node_count"] == 10
    # the whole point: the payload is bounded regardless of how much was filed
    assert len(json.dumps(result)) < 1000


def test_list_graphs_missing_vault_paths_counts_as_zero(cfg):
    graphbridge._save_registry(cfg, {"fresh": {"node_count": 3}})
    assert graphbridge.list_graphs(cfg)["graphs"]["fresh"]["vault_notes_filed"] == 0


def test_list_graphs_by_name_returns_the_full_paths_for_one_graph(cfg):
    graphbridge._save_registry(cfg, {
        "a": {"vault_paths": ["Reference/a.md"]},
        "b": {"vault_paths": ["Reference/b.md"]},
    })
    result = graphbridge.list_graphs(cfg, name="a")
    assert result["count"] == 1
    assert set(result["graphs"]) == {"a"}
    assert result["graphs"]["a"]["vault_paths"] == ["Reference/a.md"]


def test_list_graphs_by_name_slugifies_and_errors_on_unknown(cfg):
    graphbridge._save_registry(cfg, {"my-graph": {"vault_paths": []}})
    assert set(graphbridge.list_graphs(cfg, name="my graph")["graphs"]) == {"my-graph"}
    assert "error" in graphbridge.list_graphs(cfg, name="never-built")


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


def _wiki_fixture(tmp_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "Community_0.md").write_text(
        "Community A\n\nsee [Community 1](Community_1.md)", encoding="utf-8")
    (wiki_dir / "Community_1.md").write_text("Community B", encoding="utf-8")
    (wiki_dir / "index.md").write_text("wiki nav root", encoding="utf-8")
    return wiki_dir


def test_write_artifacts_to_vault_puts_wiki_in_a_per_graph_subfolder(cfg, tmp_path):
    """The report stays in the folder root; articles go under graphs/<name>/ so a
    598-article build stops burying hand-written notes in the Reference root."""
    vm = FakeVaultManager(cfg)

    result = graphbridge._write_artifacts_to_vault(vm, "my-graph", "# Report body",
                                                   _wiki_fixture(tmp_path))

    assert result["wiki_folder"] == "reference/graphs/my-graph"
    assert result["wiki_count"] == 3          # 2 communities + index.md
    assert len(result["written_paths"]) == 4  # + the report

    report_rel = result["report_path"]
    assert "/graphs/" not in report_rel
    assert "Report body" in (cfg.vault / report_rel).read_text(encoding="utf-8")

    wiki_root = cfg.vault / "reference" / "graphs" / "my-graph"
    assert {p.name for p in wiki_root.glob("*.md")} == {
        "Community_0.md", "Community_1.md", "index.md"}


def test_write_artifacts_to_vault_preserves_stems_so_wiki_internal_links_resolve(cfg, tmp_path):
    """wiki.py emits relative links between its own articles. Renaming files to
    dated safe_filename() stems broke every one of them — the whole reason the
    articles keep their original names."""
    vm = FakeVaultManager(cfg)

    graphbridge._write_artifacts_to_vault(vm, "my-graph", "# Report", _wiki_fixture(tmp_path))

    wiki_root = cfg.vault / "reference" / "graphs" / "my-graph"
    body = (wiki_root / "Community_0.md").read_text(encoding="utf-8")
    assert "[Community 1](Community_1.md)" in body
    assert (wiki_root / "Community_1.md").exists()


def test_write_artifacts_to_vault_does_not_add_related_links_to_wiki_articles(cfg, tmp_path):
    """Articles already carry exact graph-derived cross-links; BGE backlinks on top
    added ~65 near-identical entries each (every community resembles every other)
    and cost a search + rewrite + backlink pass per article."""
    hits = [{"path": "reference/other.md", "title": "Other", "similarity": 0.99}]
    vm = FakeVaultManager(cfg, search_hits=hits)

    result = graphbridge._write_artifacts_to_vault(vm, "my-graph", "# Report",
                                                   _wiki_fixture(tmp_path))

    wiki_root = cfg.vault / "reference" / "graphs" / "my-graph"
    for article in wiki_root.glob("*.md"):
        assert "## Related" not in article.read_text(encoding="utf-8")
    # the report still gets them — it is the discoverable entry point
    assert "## Related" in (cfg.vault / result["report_path"]).read_text(encoding="utf-8")
    # one search for the report, none for the three articles
    assert len(vm.search_calls) == 1


def test_write_artifacts_to_vault_clears_the_previous_filing_on_rebuild(cfg, tmp_path):
    """Community numbering is not stable across runs and filenames are dated, so
    without an explicit sweep every rebuild stacked a fresh copy on top of the last."""
    vm = FakeVaultManager(cfg)
    first = graphbridge._write_artifacts_to_vault(vm, "my-graph", "# Report", _wiki_fixture(tmp_path))

    second_wiki = tmp_path / "wiki2"
    second_wiki.mkdir()
    (second_wiki / "Community_0.md").write_text("only one this time", encoding="utf-8")

    result = graphbridge._write_artifacts_to_vault(
        vm, "my-graph", "# Report v2", second_wiki, previous_paths=first["written_paths"])

    wiki_root = cfg.vault / "reference" / "graphs" / "my-graph"
    assert {p.name for p in wiki_root.glob("*.md")} == {"Community_0.md"}
    assert result["replaced_stale"] == 4          # 3 articles + the old report
    # a same-day rebuild lands on the identical report filename, so the check is
    # that it was replaced rather than that it vanished
    assert "Report v2" in (cfg.vault / result["report_path"]).read_text(encoding="utf-8")
    # stale ChromaDB rows dropped too, not just the files
    assert set(first["written_paths"]) <= set(vm.deleted)


def test_write_artifacts_to_vault_without_wiki_dir_writes_only_the_report(cfg):
    vm = FakeVaultManager(cfg)

    result = graphbridge._write_artifacts_to_vault(vm, "my-graph", "# Report body", None)

    assert len(result["written_paths"]) == 1
    assert result["wiki_count"] == 0
    content = (cfg.vault / result["written_paths"][0]).read_text(encoding="utf-8")
    assert "Report body" in content


def test_build_graph_labels_communities_by_hub_in_every_artifact(cfg, monkeypatch, tmp_path):
    """Regression: label_communities_by_hub() existed but nothing ever called it.

    build_graph() passed a literal {} for render_report's community_labels and
    omitted the argument entirely for to_json/to_html/to_wiki, so all four fell
    back to "Community {cid}". A real 2693-community build filed 2693 vault
    notes titled "Community 0".."Community 2692" — bodies searchable, but titles
    and Obsidian graph node labels carrying no information whatsoever.

    Fakes stand in for the tree-sitter extraction stages (heavy, and not what
    this pins). The labeler itself runs for real against a real graph, so the
    assertion is on genuine hub names rather than on some dict being forwarded.
    """
    import asyncio
    from pathlib import Path

    nx = pytest.importorskip("networkx")

    G = nx.DiGraph()
    for spoke in ("a1", "a2", "a3"):
        G.add_edge("auth_handler", spoke)
    for spoke in ("b1", "b2"):
        G.add_edge("log_action", spoke)
    communities = {0: ["auth_handler", "a1", "a2", "a3"], 1: ["log_action", "b1", "b2"]}

    seen: dict[str, object] = {}

    import delegation_core.graph.analyze as analyze_mod
    import delegation_core.graph.build as build_mod
    import delegation_core.graph.callflow_html as callflow_mod
    import delegation_core.graph.cluster as cluster_mod
    import delegation_core.graph.detect as detect_mod
    import delegation_core.graph.export as export_mod
    import delegation_core.graph.extract as extract_mod
    import delegation_core.graph.report as report_mod
    import delegation_core.graph.wiki as wiki_mod

    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(detect_mod, "detect",
                        lambda root, cache_root=None, extra_excludes=None: {
                            "files": {"code": [str(src / "mod.py")]}})
    monkeypatch.setattr(extract_mod, "extract",
                        lambda files, cache_root=None, root=None: {"input_tokens": 0, "output_tokens": 0})
    monkeypatch.setattr(build_mod, "build", lambda results, directed=True, root=None: G)
    monkeypatch.setattr(cluster_mod, "cluster", lambda g: communities)
    monkeypatch.setattr(cluster_mod, "score_all", lambda g, c: {0: 0.5, 1: 0.5})
    monkeypatch.setattr(analyze_mod, "god_nodes", lambda g: [])
    monkeypatch.setattr(analyze_mod, "surprising_connections", lambda g, c: [])
    monkeypatch.setattr(analyze_mod, "suggest_questions", lambda g, c, d: [])

    def _fake_report(g, comms, cohesion, community_labels, *a, **kw):
        seen["report"] = community_labels
        return "# Report"

    def _fake_to_json(g, comms, path, *, force=False, community_labels=None, **kw):
        seen["json"] = community_labels
        Path(path).write_text("{}", encoding="utf-8")
        return True

    def _fake_to_html(g, comms, path, community_labels=None, **kw):
        seen["html"] = community_labels

    def _fake_to_wiki(g, comms, out, community_labels=None, cohesion=None, god_nodes_data=None):
        seen["wiki"] = community_labels
        return 2

    monkeypatch.setattr(report_mod, "generate", _fake_report)
    monkeypatch.setattr(export_mod, "to_json", _fake_to_json)
    monkeypatch.setattr(export_mod, "to_html", _fake_to_html)
    monkeypatch.setattr(callflow_mod, "write_callflow_html", lambda **kw: None)
    monkeypatch.setattr(wiki_mod, "to_wiki", _fake_to_wiki)

    result = asyncio.run(graphbridge.build_graph(
        cfg, FakeVaultManager(cfg), str(src), name="lbl", force=True, file_to_vault=False))

    assert result["status"] == "ok"
    expected = {0: "auth_handler", 1: "log_action"}
    # Every artifact gets the same real hub names — none may fall back to "Community N".
    assert seen["report"] == expected
    assert seen["json"] == expected
    assert seen["html"] == expected
    assert seen["wiki"] == expected


def test_build_graph_forwards_exclude_patterns_to_detect(cfg, monkeypatch, tmp_path):
    """detect() has always accepted extra_excludes; build_graph never passed it.

    Without this the only way to keep a repository's test tree out of a graph
    was to build everything and prune afterwards — one real build filed 1071
    vault articles for communities made entirely of test files, removed by hand.
    Same shape as the community_labels bug: an available parameter left unwired.
    """
    import asyncio

    nx = pytest.importorskip("networkx")

    seen = {}
    G = nx.DiGraph()
    G.add_edge("a", "b")

    import delegation_core.graph.analyze as analyze_mod
    import delegation_core.graph.build as build_mod
    import delegation_core.graph.callflow_html as callflow_mod
    import delegation_core.graph.cluster as cluster_mod
    import delegation_core.graph.detect as detect_mod
    import delegation_core.graph.export as export_mod
    import delegation_core.graph.extract as extract_mod
    import delegation_core.graph.report as report_mod
    import delegation_core.graph.wiki as wiki_mod

    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")

    def _fake_detect(root, cache_root=None, extra_excludes=None):
        seen["extra_excludes"] = extra_excludes
        return {"files": {"code": [str(src / "mod.py")]}}

    monkeypatch.setattr(detect_mod, "detect", _fake_detect)
    monkeypatch.setattr(extract_mod, "extract",
                        lambda files, cache_root=None, root=None: {"input_tokens": 0, "output_tokens": 0})
    monkeypatch.setattr(build_mod, "build", lambda results, directed=True, root=None: G)
    monkeypatch.setattr(cluster_mod, "cluster", lambda g: {0: ["a", "b"]})
    monkeypatch.setattr(cluster_mod, "score_all", lambda g, c: {0: 0.5})
    monkeypatch.setattr(analyze_mod, "god_nodes", lambda g: [])
    monkeypatch.setattr(analyze_mod, "surprising_connections", lambda g, c: [])
    monkeypatch.setattr(analyze_mod, "suggest_questions", lambda g, c, d: [])
    monkeypatch.setattr(report_mod, "generate", lambda *a, **kw: "# Report")
    monkeypatch.setattr(export_mod, "to_json",
                        lambda g, c, path, **kw: Path(path).write_text("{}", encoding="utf-8"))
    monkeypatch.setattr(export_mod, "to_html", lambda *a, **kw: None)
    monkeypatch.setattr(callflow_mod, "write_callflow_html", lambda **kw: None)
    monkeypatch.setattr(wiki_mod, "to_wiki", lambda *a, **kw: 1)

    asyncio.run(graphbridge.build_graph(
        cfg, FakeVaultManager(cfg), str(src), name="ex", force=True,
        file_to_vault=False, exclude=["tests/", "website/"]))

    assert seen["extra_excludes"] == ["tests/", "website/"]


def test_write_artifacts_stamps_every_note_it_filed(cfg, tmp_path):
    """Sem carimbo, o proximo reindex incremental reembute tudo isto de novo.

    `reindex_vault(force=False)` pula a nota cujo mtime bate com o carimbo em
    .chroma_index.json, e ate 02/09/2026 os unicos escritores desse arquivo
    eram reindex_vault e delete_notes. graph_build escreve o relatorio mais um
    artigo por comunidade direto por index_note, aos milhares, sem carimbar.
    Medido neste vault: Reference com 8.262 notas e 3.469 carimbadas, ou seja
    4.793 reembutidas a cada run.
    """
    vm = FakeVaultManager(cfg)

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    for nome in ("Community_0.md", "Community_1.md", "index.md"):
        (wiki / nome).write_text(f"# {nome}\n", encoding="utf-8")

    resultado = graphbridge._write_artifacts_to_vault(
        vm, "meu-grafo", "# relatorio\n", wiki, [])

    assert vm.stamped, "arquivou notas no vault e nao carimbou nenhuma"
    assert set(vm.stamped) == set(resultado["written_paths"]), (
        "o que foi carimbado tem que ser exatamente o que foi arquivado"
    )
    assert len(vm.stamped) == 4          # 1 relatorio + 3 artigos
