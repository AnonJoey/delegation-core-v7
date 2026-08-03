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
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["total_nodes"] == 0
    assert graph["truncated"] is False


def _note(vault, folder, name, body="body"):
    d = vault / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\ntitle: \"{name}\"\n---\n\n{body}\n", encoding="utf-8")


def _generated_note(vault, folder, name):
    d = vault / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f'---\ntitle: "{name}"\nai_generated: false\nsource: graph_build\n---\n\nbody\n',
        encoding="utf-8")


def test_graph_reports_totals_when_under_the_cap(tmp_path):
    cfg = Config(vault_path=str(tmp_path), vault_folders=["reference"])
    for i in range(3):
        _note(tmp_path, "reference", f"n{i}")
    graph = _build_vault_graph(cfg)
    assert len(graph["nodes"]) == 3
    assert graph["total_nodes"] == 3
    assert graph["truncated"] is False


def test_graph_caps_nodes_and_says_so(tmp_path):
    """Silently returning a subset is the failure mode being closed here: a
    3552-node vault answered exactly like a 3552-node payload the canvas could
    render, with nothing distinguishing the two."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["reference"])
    for i in range(12):
        _note(tmp_path, "reference", f"n{i:02d}")
    graph = _build_vault_graph(cfg, max_nodes=5)
    assert len(graph["nodes"]) == 5
    assert graph["total_nodes"] == 12
    assert graph["truncated"] is True
    assert graph["max_nodes"] == 5


def test_graph_never_emits_an_edge_to_a_dropped_node(tmp_path):
    """Edges are built after the cut; a dangling edge would crash the renderer."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["reference"])
    for i in range(10):
        _note(tmp_path, "reference", f"n{i:02d}", body="[[n00]] [[n01]] [[n09]]")
    graph = _build_vault_graph(cfg, max_nodes=3)
    ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in ids
        assert edge["target"] in ids


def test_graph_can_exclude_generated_articles(tmp_path):
    """One code-graph build filed 2711 articles into a vault of 1166 hand-written
    notes, so the unfiltered graph stopped being a picture of the user's knowledge."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["reference"])
    _note(tmp_path, "reference", "hand-written")
    for i in range(4):
        _generated_note(tmp_path, "reference", f"gen{i}")

    full = _build_vault_graph(cfg)
    assert len(full["nodes"]) == 5
    assert full["generated_excluded"] == 0

    filtered = _build_vault_graph(cfg, include_generated=False)
    assert [n["title"] for n in filtered["nodes"]] == ["hand-written"]
    assert filtered["generated_excluded"] == 4


def test_graph_nodes_carry_no_internal_sort_key(tmp_path):
    """mtime is used to pick which nodes survive the cap; it must not leak."""
    cfg = Config(vault_path=str(tmp_path), vault_folders=["reference"])
    _note(tmp_path, "reference", "n0")
    assert "mtime" not in _build_vault_graph(cfg)["nodes"][0]
