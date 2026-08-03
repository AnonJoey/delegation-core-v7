"""The guard that makes unwired capability a written decision, not an accident.

Three times in one week a working, reachable function sat unused because nothing
surfaced it: label_communities_by_hub (no caller at all — 2693 vault notes got
meaningless titles), detect's extra_excludes (never passed — 1071 articles
pruned by hand), and remap_communities_to_previous (still unwired today).

Every one was invisible because the fallback produced plausible output. Nothing
failed, so nothing pointed at them. This test is the pointer: add an
artifact-producing function to the vendored graph pipeline and it fails until
the function is classified in capabilities.GRAPH_CAPABILITIES as either wired to
an MCP tool or deliberately not exposed, with a reason.
"""

import ast
import pathlib
import re

import pytest

from delegation_core import capabilities

GRAPH_ROOT = pathlib.Path(capabilities.__file__).parent / "graph"

# Functions that produce a user-visible artifact. Byte-level writers and type
# coercions match the name shape but are plumbing, not capability.
_ARTIFACT_NAME = re.compile(r"^(to_|push_to_|write_)")
_NOT_CAPABILITIES = {"to_float", "write_text_atomic", "write_json_atomic"}


def _artifact_functions() -> dict[str, str]:
    """{function_name: module} for every artifact producer in the pipeline."""
    found = {}
    files = list(GRAPH_ROOT.glob("*.py")) + list((GRAPH_ROOT / "exporters").glob("*.py"))
    for path in files:
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken vendor file fails elsewhere
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _ARTIFACT_NAME.match(node.name) or node.name in _NOT_CAPABILITIES:
                continue
            found[node.name] = path.stem
    return found


def test_the_scan_finds_the_pipeline_it_is_meant_to_guard():
    """A guard that silently matches nothing guards nothing."""
    found = _artifact_functions()
    assert len(found) >= 10
    assert "to_wiki" in found
    assert "to_json" in found


@pytest.mark.parametrize("name", sorted(_artifact_functions()))
def test_every_artifact_producer_is_classified(name):
    assert name in capabilities.GRAPH_CAPABILITIES, (
        f"{name}() produces an artifact but is not in GRAPH_CAPABILITIES. "
        "Wire it to an MCP tool and record 'via', or set status='not-exposed' "
        "with a reason. Leaving it unclassified is how the community-label bug "
        "survived for months."
    )


def test_registry_has_no_entries_for_functions_that_no_longer_exist():
    """A stale entry is a claim the server can do something it cannot."""
    found = _artifact_functions()
    for name in capabilities.GRAPH_CAPABILITIES:
        assert name in found, f"GRAPH_CAPABILITIES lists {name}(), which no longer exists"


def test_wired_capabilities_name_the_tool_that_reaches_them():
    for name, entry in capabilities.GRAPH_CAPABILITIES.items():
        if entry["status"] == "wired":
            assert entry.get("via"), f"{name} is marked wired but names no MCP tool"


def test_unexposed_capabilities_carry_a_reason():
    for name, entry in capabilities.GRAPH_CAPABILITIES.items():
        if entry["status"] != "wired":
            assert entry.get("reason"), f"{name} is not exposed and gives no reason"


def test_status_values_are_constrained():
    for name, entry in capabilities.GRAPH_CAPABILITIES.items():
        assert entry["status"] in {"wired", "not-exposed"}, f"{name}: bad status"


def test_known_unwired_entries_point_at_functions_that_exist():
    """The unwired list is a to-do; an entry naming a deleted function is noise."""
    for dotted in capabilities.KNOWN_UNWIRED:
        module_path, _, func = dotted.rpartition(".")
        rel = module_path.replace("graph.", "", 1).replace(".", "/")
        source = (GRAPH_ROOT / f"{rel}.py").read_text(encoding="utf-8")
        assert f"def {func}(" in source, f"KNOWN_UNWIRED names {dotted}, which is absent"


def test_describe_reports_the_live_tool_list_it_is_given():
    report = capabilities.describe([{"name": "search_vault", "description": "..."}])
    assert report["tool_count"] == 1
    assert report["tools"][0]["name"] == "search_vault"
    assert "graph_export" in {e.get("via") for e in report["graph_exports"]["wired"].values()}
    assert "to_obsidian" in report["graph_exports"]["not_exposed"]
