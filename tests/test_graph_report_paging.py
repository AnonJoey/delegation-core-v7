"""graph_report pages a long GRAPH_REPORT.md instead of returning nothing.

A report is a whole markdown document, and the ones worth reading are the big
ones. Measured on this machine's built graphs:

    openclaw       335,321 chars   hermes-agent   319,254
    graphify       164,546         paperclip-src   41,075
    delegation-core 36,874         paperclip        1,747

The two largest are past the MCP tool-result cap, so the tool returned nothing
usable for exactly the graphs a caller most wants a report on — the same defect
graph_list had, one field wider.

Paging rather than summarising is the difference from graph_list: vault_paths
there was bookkeeping no caller read, while every line of a report is content
the caller asked for. So nothing is dropped, only deferred — the whole document
is still reachable by following next_offset, and these tests pin that it
reassembles byte-identically.

Only the MCP tool pages. graphbridge.get_report() is untouched and still returns
the document whole, because the dashboard renders it into a <pre> and the CLI
prints it — neither has a token cap, and truncating there would have been a
regression in two places to fix a problem in a third.
"""

from delegation_core.server import GRAPH_REPORT_PAGE_CHARS, _page_report


def _result(report: str) -> dict:
    return {"name": "g", "report": report}


def test_a_short_report_is_returned_whole_and_unflagged():
    body = "# Small graph\n\nnothing much here."
    page = _page_report(_result(body), 0)
    assert page["report"] == body
    assert page["total_chars"] == len(body)
    assert "next_offset" not in page
    assert "truncated" not in page


def test_a_long_report_is_cut_to_one_page_and_says_so():
    body = "x" * (GRAPH_REPORT_PAGE_CHARS * 3)
    page = _page_report(_result(body), 0)
    assert len(page["report"]) == GRAPH_REPORT_PAGE_CHARS
    assert page["truncated"] is True
    assert page["offset"] == 0
    assert page["next_offset"] == GRAPH_REPORT_PAGE_CHARS
    assert page["total_chars"] == len(body)


def test_following_next_offset_reassembles_the_document_exactly():
    """The point of paging: deferred, not dropped. 12 pages of the real 335,321
    char openclaw report reassembled identically when this was measured."""
    body = "".join(f"line {i}\n" for i in range(20_000))
    out, offset, pages = "", 0, 0
    while True:
        page = _page_report(_result(body), offset)
        out += page["report"]
        pages += 1
        if not page.get("truncated"):
            break
        offset = page["next_offset"]
    assert out == body
    assert pages > 1


def test_the_last_page_is_not_flagged_truncated():
    body = "y" * (GRAPH_REPORT_PAGE_CHARS + 10)
    last = _page_report(_result(body), GRAPH_REPORT_PAGE_CHARS)
    assert last["report"] == "y" * 10
    assert "truncated" not in last
    assert "next_offset" not in last


def test_an_offset_past_the_end_yields_an_empty_final_page_not_an_error():
    body = "z" * 100
    page = _page_report(_result(body), 5_000)
    assert page["report"] == ""
    assert page["offset"] == 100
    assert "truncated" not in page


def test_a_negative_offset_is_clamped_to_the_start():
    body = "abcdef"
    assert _page_report(_result(body), -50)["report"] == body


def test_an_error_result_passes_through_untouched():
    """get_report returns {"error": ...} with no report key for an unbuilt graph;
    paging it must not invent offset/total_chars fields on a failure."""
    err = {"error": "No report found for graph 'nope'. Call graph_build first."}
    assert _page_report(dict(err), 0) == err
