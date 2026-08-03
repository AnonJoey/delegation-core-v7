"""label_communities_by_hub: the LLM-free community namer.

Nothing called this function until 2026-08-03 (see test_graphbridge.py's
wiring regression) — so its behavior was never pinned. It is now the sole
source of community names in every graph artifact (report, json, html, wiki),
and a wiki article's name is also its vault filename, so a bad hub choice is
visible in Obsidian forever.

The source_file preference exists because the first real build after wiring
labeled communities `Any`, `Path` and `ValueError`: imported/builtin symbols
carry high degree across a codebase but name nothing useful.
"""

import pytest

nx = pytest.importorskip("networkx")

from delegation_core.graph.cluster import label_communities_by_hub  # noqa: E402


def _g(*edges, attrs=None):
    G = nx.DiGraph()
    G.add_edges_from(edges)
    for node, data in (attrs or {}).items():
        G.add_node(node)
        G.nodes[node].update(data)
    return G


def test_names_community_after_highest_degree_member():
    G = _g(("hub", "a"), ("hub", "b"), ("hub", "c"), ("a", "b"),
           attrs={n: {"source_file": "m.py"} for n in ("hub", "a", "b", "c")})
    assert label_communities_by_hub(G, {0: ["hub", "a", "b", "c"]}) == {0: "hub"}


def test_prefers_locally_defined_symbol_over_higher_degree_import():
    """An import with more edges must not outrank a real definition."""
    G = _g(("Any", "x1"), ("Any", "x2"), ("Any", "x3"), ("Any", "CronScheduler"),
           ("CronScheduler", "x1"), ("CronScheduler", "x2"),
           attrs={
               "Any": {"source_file": "", "label": "Any"},
               "CronScheduler": {"source_file": "scheduler.py", "label": "CronScheduler"},
               "x1": {"source_file": "scheduler.py"},
               "x2": {"source_file": "scheduler.py"},
               "x3": {"source_file": "scheduler.py"},
           })
    members = ["Any", "CronScheduler", "x1", "x2", "x3"]
    assert G.degree("Any") > G.degree("CronScheduler")
    assert label_communities_by_hub(G, {0: members}) == {0: "CronScheduler"}


def test_falls_back_to_import_when_community_has_no_defined_symbol():
    G = _g(("Any", "Path"), ("Any", "ValueError"),
           attrs={n: {"source_file": ""} for n in ("Any", "Path", "ValueError")})
    assert label_communities_by_hub(G, {0: ["Any", "Path", "ValueError"]}) == {0: "Any"}


def test_missing_source_file_attribute_is_treated_as_undefined():
    """Graphs built before source_file existed must still get a name."""
    G = _g(("a", "b"), ("a", "c"))
    assert label_communities_by_hub(G, {0: ["a", "b", "c"]}) == {0: "a"}


def test_uses_node_label_not_node_id_and_strips_call_parens():
    G = _g(("mod_log_action", "x"),
           attrs={"mod_log_action": {"label": "log_action()", "source_file": "m.py"},
                  "x": {"source_file": "m.py"}})
    assert label_communities_by_hub(G, {0: ["mod_log_action", "x"]}) == {0: "log_action"}


def test_community_with_no_members_in_graph_falls_back_to_cid():
    assert label_communities_by_hub(_g(("a", "b")), {7: ["ghost"]}) == {7: "Community 7"}


def test_ties_break_by_node_id_for_run_to_run_stability():
    G = _g(("b", "x"), ("a", "y"),
           attrs={n: {"source_file": "m.py"} for n in ("a", "b", "x", "y")})
    assert G.degree("a") == G.degree("b")
    assert label_communities_by_hub(G, {0: ["b", "a", "x", "y"]}) == {0: "a"}


def test_rejects_docstring_labels_that_would_become_filenames():
    """A real build named communities after test docstrings, producing files like
    "1000_comments_on_a_single_task_—_build_worker_context_should_...md"."""
    prose = "1000 comments on a single task — build worker context should still be reasonable"
    spokes = ("a", "b", "c", "d")
    G = _g(*[("doc", s) for s in spokes], ("doc", "e"),
           *[("kanban_db", s) for s in spokes],
           attrs={
               "doc": {"label": prose, "source_file": "test_kanban.py"},
               "kanban_db": {"label": "kanban_db", "source_file": "kanban_db.py"},
               **{s: {"source_file": "kanban_db.py"} for s in spokes + ("e",)},
           })
    # The docstring node genuinely outranks the module it documents.
    assert G.degree("doc") > G.degree("kanban_db") > G.degree("a")
    members = ["doc", "kanban_db", *spokes, "e"]
    assert label_communities_by_hub(G, {0: members}) == {0: "kanban_db"}


def test_rejects_rationale_nodes_as_hubs():
    spokes = ("x", "y", "z")
    G = _g(*[("r", s) for s in spokes], ("r", "w"),
           *[("real", s) for s in spokes],
           attrs={
               "r": {"label": "module_summary", "file_type": "rationale", "source_file": "m.py"},
               "real": {"label": "do_work", "source_file": "m.py"},
               **{s: {"source_file": "m.py"} for s in spokes + ("w",)},
           })
    # An identifier-shaped label is not enough — rationale nodes are prose too.
    assert G.degree("r") > G.degree("real") > G.degree("x")
    assert label_communities_by_hub(G, {0: ["r", "real", *spokes, "w"]}) == {0: "do_work"}


def test_names_prose_only_community_after_its_source_file():
    """The real build produced a 1-member community holding a single rationale
    node from tests/stress/test_atypical_scenarios.py, and named the article
    after the docstring's first 80 characters."""
    prose = "1000 comments on a single task — build worker context should still be reasonable"
    G = nx.DiGraph()
    G.add_node("doc", label=prose, file_type="rationale",
               source_file="tests/stress/test_atypical_scenarios.py")
    name = label_communities_by_hub(G, {0: ["doc"]})[0]
    assert name.startswith("test_atypical_scenarios")
    assert len(name) < len(prose)


def test_same_file_prose_communities_get_distinct_names():
    """39 prose-only communities from tui_gateway/methods_session.py all became
    "methods_session", separated on disk only by a _2.._39 suffix."""
    G = nx.DiGraph()
    G.add_node("a", label="Handle session list request from the client",
               file_type="rationale", source_file="tui_gateway/methods_session.py")
    G.add_node("b", label="Persist session transcript to disk on close",
               file_type="rationale", source_file="tui_gateway/methods_session.py")
    labels = label_communities_by_hub(G, {0: ["a"], 1: ["b"]})
    assert labels[0] != labels[1]
    assert all(n.startswith("methods_session") for n in labels.values())
    # bounded — the 80-char sentence filenames must not come back
    assert all(len(n) <= 60 for n in labels.values())


def test_source_file_fallback_picks_the_dominant_file():
    G = _g(("a", "b"), ("a", "c"),
           attrs={"a": {"label": "one sentence here", "source_file": "pkg/core.py"},
                  "b": {"label": "another sentence", "source_file": "pkg/core.py"},
                  "c": {"label": "third sentence too", "source_file": "pkg/other.py"}})
    # "core" (2 nodes) beats "other" (1); the discriminator follows the stem.
    assert label_communities_by_hub(G, {0: ["a", "b", "c"]})[0].startswith("core — ")


def test_falls_back_to_prose_when_no_identifier_and_no_source_file():
    prose = "this whole community is documentation and nothing else at all"
    G = _g(("doc", "more"),
           attrs={"doc": {"label": prose, "source_file": ""},
                  "more": {"label": "another sentence entirely here", "source_file": ""}})
    assert label_communities_by_hub(G, {0: ["doc", "more"]}) == {0: prose}
