"""dashboard_api.py: the wikilink-graph builder — the one piece of real logic in
this module that's easily unit-testable without spinning up the HTTP server or
BGE/ChromaDB. Builds a small real vault structure under tmp_path.
"""

from delegation_core.config import Config
from delegation_core.dashboard_api import _build_vault_graph


def _write_note(vault_path, folder, stem, content, title=None):
    folder_path = vault_path / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    frontmatter = f'---\ntitle: "{title}"\n---\n\n' if title else ""
    (folder_path / f"{stem}.md").write_text(frontmatter + content, encoding="utf-8")


def test_graph_has_one_node_per_note(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "a", "no links here")
    _write_note(tmp_path, "Notes", "b", "no links here either")

    graph = _build_vault_graph(cfg)
    assert {n["id"] for n in graph["nodes"]} == {"a", "b"}


def test_graph_resolves_wikilink_to_edge(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "a", "links to [[b]]")
    _write_note(tmp_path, "Notes", "b", "no links")

    graph = _build_vault_graph(cfg)
    assert {"source": "a", "target": "b"} in graph["edges"]


def test_graph_resolves_aliased_wikilink(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "a", "links to [[b|Display Text]]")
    _write_note(tmp_path, "Notes", "b", "no links")

    graph = _build_vault_graph(cfg)
    assert {"source": "a", "target": "b"} in graph["edges"]


def test_graph_skips_dangling_links(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "a", "links to [[nonexistent]]")

    graph = _build_vault_graph(cfg)
    assert graph["edges"] == []


def test_graph_skips_self_links(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "a", "links to itself: [[a]]")

    graph = _build_vault_graph(cfg)
    assert graph["edges"] == []


def test_graph_does_not_duplicate_edges_for_repeated_links(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "a", "[[b]] and again [[b]] and once more [[b]]")
    _write_note(tmp_path, "Notes", "b", "")

    graph = _build_vault_graph(cfg)
    assert graph["edges"].count({"source": "a", "target": "b"}) == 1


def test_graph_node_uses_frontmatter_title_when_present(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "a", "content", title="A Nicer Title")

    graph = _build_vault_graph(cfg)
    assert graph["nodes"][0]["title"] == "A Nicer Title"


def test_graph_node_falls_back_to_filename_stem_without_frontmatter(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes", "plain-file", "no frontmatter here")

    graph = _build_vault_graph(cfg)
    assert graph["nodes"][0]["title"] == "plain-file"


def test_graph_recurses_into_subfolders(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["Notes"])
    _write_note(tmp_path, "Notes/Sub", "nested", "content")

    graph = _build_vault_graph(cfg)
    assert graph["nodes"][0]["path"] == "Notes/Sub/nested.md"


def test_graph_ignores_nonexistent_folder(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["DoesNotExist"])
    graph = _build_vault_graph(cfg)
    assert graph == {"nodes": [], "edges": []}
