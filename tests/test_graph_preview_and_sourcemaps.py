"""graph_preview and source-map reconstruction.

**preview** exists because the first real build filed 598 wiki articles into the
vault before anyone could see it coming, and undoing that took a migration
script. detect() already computes the inventory in seconds; not surfacing it was
the whole problem. It deliberately invents no community estimate — previously
built graphs are returned as `scale_reference` so the caller interpolates from
real measurements taken on their own machine.

**source maps** came out of a live discovery: `paperclipai` ships one esbuild
bundle whose 3.3 MB .js.map carried all 303 original TypeScript files, none of
which existed on disk in any other form. Graphing the bundle would have produced
a single meaningless module; graphing the reconstruction produced 3682 nodes over
the real package structure.

The skip list here is deliberately NOT the graph pipeline's _SKIP_DIRS — see the
test that pins why dist/ must stay searchable.
"""

import json

import pytest

from delegation_core import graphbridge
from delegation_core.config import Config


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    import delegation_core.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return Config(vault_path=str(tmp_path / "vault"), vault_folders=["Reference"])


def write_map(path, sources, contents=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 3, "sources": sources,
        "sourcesContent": contents if contents is not None else [f"// {s}" for s in sources],
        "mappings": "", "names": [],
    }), encoding="utf-8")


# ── source-map discovery ─────────────────────────────────────────────────────

def test_finds_a_bundle_map_with_reconstructable_sources(tmp_path):
    write_map(tmp_path / "dist" / "index.js.map",
              ["../../packages/shared/src/a.ts", "../src/b.ts"])

    found = graphbridge.find_source_maps(tmp_path)

    assert len(found) == 1
    assert found[0]["reconstructable_sources"] == 2
    assert found[0]["by_extension"] == {".ts": 2}


def test_dist_is_searched_because_that_is_where_bundles_live(tmp_path):
    """Reusing the graph pipeline's _SKIP_DIRS would exclude dist/, build/ and
    out/ — exactly the directories a bundle and its map are emitted into. An
    earlier revision did that and stopped finding paperclipai's map entirely."""
    for d in ("dist", "build", "out"):
        write_map(tmp_path / d / "bundle.js.map", [f"../src/{d}.ts"])

    assert len(graphbridge.find_source_maps(tmp_path)) == 3


def test_dependency_maps_under_node_modules_are_ignored(tmp_path):
    """Pointing at a project must not surface every dependency's map — voicebox
    otherwise reported lucide-react's 1417 sources as if they were its own."""
    write_map(tmp_path / "dist" / "app.js.map", ["../src/app.ts"])
    write_map(tmp_path / "node_modules" / "dep" / "dist" / "dep.js.map",
              [f"../src/{i}.ts" for i in range(50)])

    found = graphbridge.find_source_maps(tmp_path)

    assert [f["reconstructable_sources"] for f in found] == [1]


def test_a_globally_installed_package_is_still_found_when_it_is_the_root(tmp_path):
    """The skip is judged relative to root, so targeting a package that itself
    lives under node_modules — where global npm installs are — still works."""
    pkg = tmp_path / "node_modules" / "paperclipai"
    write_map(pkg / "dist" / "index.js.map", ["../../packages/shared/src/x.ts"])

    assert len(graphbridge.find_source_maps(pkg)) == 1


def test_a_map_without_sources_content_is_not_reported(tmp_path):
    write_map(tmp_path / "dist" / "x.js.map", ["../src/a.ts"], contents=[None])

    assert graphbridge.find_source_maps(tmp_path) == []


# ── extraction ───────────────────────────────────────────────────────────────

def test_extraction_normalises_bundler_relative_paths_into_a_clean_tree(tmp_path):
    write_map(tmp_path / "dist" / "index.js.map",
              ["../../packages/shared/src/a.ts", "../src/commands/b.ts"])
    out = tmp_path / "reconstructed"

    result = graphbridge.extract_source_maps(str(tmp_path), str(out))

    assert result["files_written"] == 2
    assert (out / "packages" / "shared" / "src" / "a.ts").read_text() .startswith("//")
    assert (out / "src" / "commands" / "b.ts").exists()
    assert result["top_level"] == {"packages": 1, "src": 1}


def test_extraction_refuses_a_non_empty_output_directory(tmp_path):
    write_map(tmp_path / "dist" / "index.js.map", ["../src/a.ts"])
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("existing work", encoding="utf-8")

    result = graphbridge.extract_source_maps(str(tmp_path), str(out))

    assert "error" in result
    assert (out / "keep.txt").read_text() == "existing work"


def test_extraction_reports_empty_when_there_is_nothing_to_reconstruct(tmp_path):
    result = graphbridge.extract_source_maps(str(tmp_path), str(tmp_path / "out"))

    assert result["status"] == "empty"


# ── preview ──────────────────────────────────────────────────────────────────

def test_preview_reports_where_it_would_write_without_writing(cfg, tmp_path):
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("def f(): pass\n", encoding="utf-8")

    result = graphbridge.preview_graph(cfg, str(src))

    assert result["would_write_to"] == "Reference/graphs/proj"
    assert result["code_by_extension"].get(".py") == 1
    assert result["already_built"] is False
    assert not (cfg.vault / "Reference").exists()   # nothing filed


def test_preview_surfaces_a_previous_build_and_what_a_rebuild_would_replace(cfg, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n", encoding="utf-8")
    graphbridge._save_registry(cfg, {"proj": {
        "source_path": str(src), "built_at": "2026-07-31T10:00:00",
        "node_count": 100, "edge_count": 200, "community_count": 12,
        "vault_paths": ["Reference/graphs/proj/Community_0.md"],
    }})

    result = graphbridge.preview_graph(cfg, str(src))

    assert result["already_built"] is True
    assert result["previous_build"]["vault_notes_filed"] == 1
    assert result["scale_reference"][0]["community_count"] == 12


def test_preview_flags_a_bundle_whose_sources_can_be_reconstructed(cfg, tmp_path):
    src = tmp_path / "tool"
    (src / "dist").mkdir(parents=True)
    (src / "a.js").write_text("export const a = 1;\n", encoding="utf-8")
    write_map(src / "dist" / "index.js.map", [f"../src/{i}.ts" for i in range(7)])

    result = graphbridge.preview_graph(cfg, str(src))

    assert result["source_maps"][0]["reconstructable_sources"] == 7
    assert "extract_source_maps" in result["source_map_hint"]


def test_preview_reports_empty_for_a_directory_with_no_code(cfg, tmp_path):
    src = tmp_path / "docs-only"
    src.mkdir()
    (src / "README.md").write_text("# hi\n", encoding="utf-8")

    result = graphbridge.preview_graph(cfg, str(src))

    assert result["status"] == "empty"


def test_preview_errors_on_a_missing_path(cfg, tmp_path):
    assert "error" in graphbridge.preview_graph(cfg, str(tmp_path / "nope"))
