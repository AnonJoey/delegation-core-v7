"""
server.py — FastMCP tool definitions for delegation-core v0.4.
Called by run_server(); never run directly.

27 tools across six groups:
  Core (8):            search_vault, read_note, write_note, compress,
                       vault_stats, heartbeat, run_maintenance, export_session
  Maintenance (6):     vault_list_notes, vault_inbox_status,
                       vault_find_similar, vault_update_note, relink_folder,
                       search_web
  Fire-and-forget (3): run_maintenance_bg, vault_reindex_bg, task_status
  External ingestion (3): ingest_folder, ingest_folder_bg, ingest_status
  Code graph (3):      graph_build, graph_list, graph_report
  Process tracking (4):   process_create, process_list, process_update, process_get

v0.4 changes:
  - engine.invoke() is now async (httpx.AsyncClient); all run_in_executor
    wrappers for engine calls have been removed
  - run_maintenance / run_maintenance_bg use async organizer.run()
  - search_web tool added (DuckDuckGo + llama.cpp compression)
  - per-task token budgets apply in all modes (not only cpu mode)
  - write_note, vault_update_note, export_session now inject wikilinks +
    backlinks after every write via _post_write_links() (BGE-only, no llama.cpp)
  - vault_health added to heartbeat(); auto-calibration via budget_mode = "auto"
  - run_maintenance includes heal pass (sync + bg)

v0.7.0 changes:
  - graph_build/graph_list/graph_report added: a code-graph pipeline vendored
    from Graphify (github.com/Graphify-Labs/graphify — core pipeline + all
    language extractors; see delegation_core/graph/__init__.py for the
    include/exclude list). Opt-in via the [graph] extra. graph_build routes its
    GRAPH_REPORT.md through the existing inbox/organizer.run() pipeline instead
    of a separate note-writing path.
"""

import asyncio
import atexit
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP

from . import graphbridge
from . import jobs
from . import session as _session
from .config import Config
from .engine import DelegationEngine
from .ingest import IngestManager
from .linker import inject_backlinks as _inject_backlinks
from .linker import wikilinks as _wikilinks
from .organizer import heal as _heal_notes
from .organizer import relink_folder as _relink_folder
from .organizer import run as _run_maintenance
from .tracker import ProcessTracker
from .vault import VaultManager, safe_filename, yaml_quote_scalar


def _post_write_links(note_path: Path, rel_path: str, folder: str, stem: str) -> None:
    """Inject forward wikilinks + backlinks after any direct vault write.

    Uses BGE search only — no llama.cpp call, fast enough to run inline.
    Also invalidates the vault_health.json cache so heartbeat() stays current.
    """
    try:
        content = note_path.read_text(encoding="utf-8")
        hits = [h for h in _vault.search(content[:600], limit=6)
                if h.get("path") != rel_path][:5]
        links = _wikilinks(hits, _vault.cfg.merge_threshold)
        if links:
            updated = content.rstrip() + f"\n\n## Related\n{links}\n"
            note_path.write_text(updated, encoding="utf-8")
            _vault.index_note(updated, {"title": stem, "path": rel_path, "folder": folder})
            _inject_backlinks(_vault, stem,
                              [h["path"] for h in hits
                               if h.get("similarity", 0) >= _vault.cfg.merge_threshold])
    except Exception as e:
        logger.warning("post_write_links failed for %s: %s", rel_path, e)
    try:
        (Path.home() / ".delegation_core" / "vault_health.json").unlink(missing_ok=True)
    except Exception:
        pass


async def _full_maintenance_cycle(engine, vault) -> dict:
    """Inbox processing + heal pass. Used by both sync and bg maintenance tools."""
    results = await _run_maintenance(engine, vault)
    if vault.cfg.heal_per_run > 0:
        try:
            heal_result = await _heal_notes(engine, vault)
            results["healed"] = heal_result["healed"]
            results["heal_remaining"] = heal_result["remaining"]
        except Exception as e:
            logger.warning("Heal pass failed: %s", e)
    return results


logger = logging.getLogger("server")

_engine:  DelegationEngine | None = None
_vault:   VaultManager | None = None
_tracker: ProcessTracker | None = None
_ingest:  IngestManager | None = None

mcp = FastMCP("delegation-core")


# ── core ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_vault(query: str, limit: int = 5, use_local: bool = False) -> str:
    """
    CALL THIS FIRST before answering any question that could have prior context.
    Semantic search the Obsidian vault using BGE embeddings.
    In local mode the summary is written by llama.cpp; in agent/hybrid mode you
    receive the ranked 'sources' and synthesize yourself. Set use_local=true to
    force the local model to write the summary (hybrid mode).
    Cite 'sources' titles when referencing notes. Flat token cost regardless of vault size.
    """
    hits = _vault.search(query, limit=limit)
    if not hits:
        return json.dumps({"query": query, "summary": "No results above similarity threshold.", "sources": []})
    if "error" in hits[0]:
        return json.dumps(hits[0])

    top_sim    = max(h["similarity"] for h in hits)
    confidence = "high" if top_sim >= 0.80 else "medium" if top_sim >= 0.65 else "low"

    snippet_len = 300 if _engine.cfg.is_cpu_budget else 800
    combined = "\n\n".join(f"[{h['title']}]\n{h['snippet'][:snippet_len]}" for h in hits[:5])

    # Route the summarization. agent/hybrid delegate to the calling Claude
    # (return raw ranked notes); local (or hybrid + use_local) runs llama.cpp.
    route = _engine.cfg.route(task="search_summary", input_chars=len(combined), use_local=use_local)
    if route in ("agent", "offer"):
        payload = {
            "query": query, "summary": None, "sources": hits, "mode": route,
            "instruction": "No server-side summary — read the 'sources' snippets and "
                           "synthesize the answer yourself.",
            "quality": {"confidence": confidence, "top_similarity": top_sim,
                        "sources_found": len(hits), "output_empty": False},
        }
        if route == "offer":
            payload["est_tokens_if_agent"] = len(combined) // 4
            payload["instruction"] = ("Big result. Synthesize it yourself, or re-call with "
                                      "use_local=true to have the local model summarize it.")
        return json.dumps(payload)

    try:
        summary = await _engine.invoke(
            f"Summarize these vault notes for the query: {query}\n\n{combined}",
            system="Vault Analyst. Return compressed insight only — no preamble, no headers.",
            max_tokens=_engine.budget("search_summary", 800),
            temperature=0.3,
            task="search_summary",
        )
    except Exception as e:
        logger.warning("search_vault: llama.cpp summarization failed (%s) — returning raw hits", e)
        return json.dumps({
            "query": query, "summary": None, "sources": hits, "degraded": True,
            "note": "llama.cpp offline — returning raw snippets without summarization.",
            "quality": {"confidence": confidence, "top_similarity": top_sim,
                        "sources_found": len(hits), "output_empty": True},
        })

    output_empty = not summary or len(summary.strip()) < 20
    return json.dumps({
        "query": query, "summary": summary, "sources": hits,
        "quality": {"confidence": confidence, "top_similarity": top_sim,
                    "sources_found": len(hits), "output_empty": output_empty},
    })


@mcp.tool()
async def read_note(note_name: str) -> str:
    """
    Read the full content of one specific vault note by filename stem (partial, case-insensitive).
    Use only when you need the complete text of a known note.
    For discovery or topic recall, use search_vault instead.
    """
    matches = _vault.find_notes_by_stem(note_name)
    if not matches:
        return json.dumps({"error": f"Note not found: {note_name}"})
    try:
        return matches[0].read_text(encoding="utf-8")
    except OSError as e:
        return json.dumps({"error": f"Could not read note: {e}"})


@mcp.tool()
async def write_note(folder: str, title: str, content: str) -> str:
    """
    Persist information to the vault and index it immediately with BGE embeddings.
    CALL THIS automatically after: any decision, meeting summary, research finding,
    fix, or reusable tool/prompt. Write proactively — don't wait to be asked.
    Use vault_update_note when adding to an existing topic.
    folder must be one of the configured vault_folders.
    """
    cfg = _vault.cfg
    if folder not in cfg.vault_folders:
        return json.dumps({"error": f"Invalid folder '{folder}'. Valid: {cfg.vault_folders}"})
    safe = safe_filename(title)
    dest = cfg.vault / folder / f"{datetime.now().strftime('%Y-%m-%d')}-{safe}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    full = (
        f"---\ntitle: {yaml_quote_scalar(title)}\ndate: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"ai_generated: true\n---\n\n{content}"
    )
    try:
        dest.write_text(full, encoding="utf-8")
    except OSError as e:
        return json.dumps({"error": f"Write failed: {e}"})
    rel = str(dest.relative_to(cfg.vault))
    _vault.index_note(full, {"title": title, "path": rel, "folder": folder})
    _post_write_links(dest, rel, folder, dest.stem)
    return json.dumps({"status": "ok", "path": str(dest.name), "folder": folder})


@mcp.tool()
async def compress(source: str, raw_content: str, use_local: bool = False) -> str:
    """
    CALL THIS whenever the user pastes a document, email, or content over ~500 words.
    Returns key facts, decisions, and action items.
    In agent/hybrid mode compression is delegated to the calling Claude; in local
    mode (or hybrid with use_local=true, or a big input) the local model does it.
    Max input: 6000 characters (1500 in CPU budget mode). Chunk longer content.
    """
    limit = 1500 if _engine.cfg.is_cpu_budget else 6000
    input_chars = len(raw_content)
    truncated   = input_chars > limit

    # Route: agent → hand raw text to Claude; offer → big input, surface the
    # local-model choice + cost; local → llama.cpp compresses it here.
    route = _engine.cfg.route(task="compress", input_chars=input_chars, use_local=use_local)
    if route in ("agent", "offer"):
        payload = {
            "source": source, "compressed": None, "mode": route,
            "raw_content": raw_content[:limit],
            "instruction": "Compress this raw_content yourself into key facts, "
                           "decisions, and action items — the server did not.",
            "quality": {"input_chars": input_chars, "truncated_input": truncated},
        }
        if route == "offer":
            payload["est_tokens_if_agent"] = input_chars // 4
            payload["instruction"] = (f"Big input (~{input_chars // 4} tokens). Compress it "
                                      "yourself, or re-call with use_local=true to offload it "
                                      "to the local model (slower, but zero agent tokens).")
        return json.dumps(payload)

    try:
        result = await _engine.invoke(
            f"Extract only key facts, decisions, and action items. No preamble.\n"
            f"Source: {source}\n\n{raw_content[:limit]}",
            system="Compression Engine. Be extremely concise.",
            max_tokens=_engine.budget("compress", 1200),
            temperature=0.2,
            task="compress",
        )
    except Exception as e:
        return json.dumps({"error": f"Compression failed: {e}", "source": source})

    output_chars = len(result.strip()) if result else 0
    ratio = round(output_chars / max(input_chars, 1), 2)
    return json.dumps({
        "source": source, "compressed": result,
        "quality": {
            "input_chars": input_chars, "output_chars": output_chars,
            "ratio": ratio, "truncated_input": truncated,
            "poor": output_chars < 30 or ratio > 0.85,
        },
    })


@mcp.tool()
async def vault_stats() -> str:
    """Return note counts per vault folder, ChromaDB index size, and embedding model info."""
    return json.dumps(_vault.get_stats())


@mcp.tool()
async def heartbeat() -> str:
    """
    CALL THIS at the start of every session before using any other tool.
    Returns llama.cpp status, vault stats, active background jobs, and configuration summary.
    If status is 'degraded', warn the user before proceeding.
    """
    cfg = _engine.cfg
    # In agent mode there is no local model, so llama being "offline" is the
    # expected healthy state. In hybrid mode llama is started on demand for
    # big/bulk tasks, so "not running yet" is also healthy — only pure local
    # mode treats an unreachable llama as degraded.
    if cfg.is_agent_mode:
        status, llama_state = "healthy", "delegated-to-agent"
    elif cfg.is_hybrid_mode:
        llama_ok = await _engine.check_health()
        status = "healthy"
        llama_state = "online" if llama_ok else "on-demand (local for big/bulk tasks)"
    else:
        llama_ok = await _engine.check_health()
        status, llama_state = ("healthy" if llama_ok else "degraded",
                               "online" if llama_ok else "offline")
    return json.dumps({
        "status":      status,
        "timestamp":   datetime.now().isoformat(),
        "engine_mode": cfg.engine_mode,
        "llama_cpp":   llama_state,
        "llama_url":   cfg.llama_url,
        "vault":       _vault.get_stats(),
        "vault_health": _vault.get_health_summary(),
        "background_jobs": jobs.running_count(),
        "processes":   _tracker.summary(),
        "config": {
            "synthesis_enabled":  cfg.synthesis_enabled,
            "synthesis_lang":     cfg.synthesis_lang,
            "budget_mode":        cfg.budget_mode,
            "tok_sec":            cfg.tok_sec,
            "mcp_timeout_sec":    cfg.mcp_timeout_sec,
            "quality_threshold":  cfg.quality_threshold,
            "heal_per_run":       cfg.heal_per_run,
            "split_min_chars":    cfg.split_min_chars,
            "split_max_notes":    cfg.split_max_notes,
            "web_search_enabled": cfg.web_search_enabled,
            "hybrid_local_min_chars": cfg.hybrid_local_min_chars,
        },
    })


@mcp.tool()
async def export_session(title: str, summary: str, key_decisions: str = "") -> str:
    """
    Save a curated summary of this conversation to the vault's sessions/ folder.
    CALL THIS when the user signals they are ending the session — any variation of
    goodbye, thanks, we're done, wrapping up, see you tomorrow, etc.
    Do not wait to be asked. Fire proactively the moment you detect session-ending intent.
    """
    result = _session.export(_vault, title, summary, key_decisions)
    if result.get("status") == "ok" and result.get("path") and result.get("folder"):
        rel = f"{result['folder']}/{result['path']}"
        note_path = _vault.cfg.vault / rel
        if note_path.exists():
            _post_write_links(note_path, rel, result["folder"], note_path.stem)
    return json.dumps(result)


@mcp.tool()
async def run_maintenance() -> str:
    """
    Run vault maintenance synchronously. Classify inbox notes, merge near-duplicates,
    add wikilinks, write weekly summary, then heal low-quality notes.
    Use run_maintenance_bg for large inboxes.
    """
    results = await _full_maintenance_cycle(_engine, _vault)
    return json.dumps(results)


# ── maintenance ───────────────────────────────────────────────────────────────

@mcp.tool()
async def vault_list_notes(folder: str, limit: int = 20) -> str:
    """List notes in a vault folder sorted newest-first. Returns title, date, path, size."""
    if folder not in _vault.cfg.vault_folders:
        return json.dumps({"error": f"Invalid folder '{folder}'. Valid: {_vault.cfg.vault_folders}"})
    notes = _vault.list_notes(folder, limit=limit)
    return json.dumps({"folder": folder, "count": len(notes), "notes": notes})


@mcp.tool()
async def vault_inbox_status() -> str:
    """Check what files are waiting in _inbox. Call BEFORE run_maintenance."""
    return json.dumps(_vault.inbox_status())


@mcp.tool()
async def vault_find_similar(note_name: str, threshold: float = 0.80, limit: int = 5) -> str:
    """Find notes semantically similar to the given note. Useful before merging."""
    results = _vault.find_similar(note_name, threshold=threshold, limit=limit)
    return json.dumps({"source_note": note_name, "threshold": threshold, "similar": results})


@mcp.tool()
async def vault_update_note(note_name: str, append_content: str) -> str:
    """Append content to an existing note and re-index. Prefer over write_note for follow-ups."""
    result = _vault.update_note(note_name, append_content)
    if "error" not in result:
        matches = _vault.find_notes_by_stem(note_name)
        if matches:
            f = matches[0]
            rel = str(f.relative_to(_vault.cfg.vault))
            _post_write_links(f, rel, f.parent.name, f.stem)
    return json.dumps(result)


@mcp.tool()
async def relink_folder(
    folder: str,
    days: int | None = None,
    min_similarity: float | None = None,
    max_links_per_note: int = 8,
) -> str:
    """
    Additively add wikilinks under `## Related` for notes in a vault subfolder.
    Use after bulk ingestion or when a topic cluster should cross-link.
    Strictly additive — never removes existing wikilinks.
    folder: vault-relative subpath (e.g. 'meetings/Client/2026' or 'meetings')
    days: restrict to notes modified within last N days (None = all)
    """
    vault_root = _vault.cfg.vault.resolve()
    target = (vault_root / folder).resolve()
    if not str(target).startswith(str(vault_root)):
        return json.dumps({"error": f"Invalid folder path: {folder}"})
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(
            None,
            lambda: _relink_folder(_vault, folder, days=days, min_similarity=min_similarity,
                                   max_links_per_note=max_links_per_note),
        )
    except Exception as e:
        return json.dumps({"error": f"relink_folder failed: {e}"})
    return json.dumps(results)


@mcp.tool()
async def search_web(query: str, num_results: int = 5, use_local: bool = False) -> str:
    """
    Search the web via DuckDuckGo. The fetch always runs locally; the summary is
    written by llama.cpp in local mode, or delegated to the calling Claude in
    agent/hybrid mode (pass use_local=true to force the local model).
    Returns a JSON summary with sources.

    Opt-in feature (v5.1): disabled unless web_search_enabled=true in config.json
    AND the [web] extra is installed (pip install "delegation-core[web]").
    """
    # v5.1: gate on the config flag first so a default install advertises the
    # tool but refuses to reach the internet until the user explicitly opts in.
    if not _engine.cfg.web_search_enabled:
        return json.dumps({"error": "web search is disabled. Set web_search_enabled=true in "
                                    "~/.delegation_core/config.json and restart to enable."})
    # v5.1: the `duckduckgo-search` package was renamed to `ddgs` and the old
    # name now emits a deprecation warning and returns 0 results. Prefer `ddgs`,
    # fall back to the legacy import so older installs keep importing.
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return json.dumps({"error": 'web search backend not installed. Run: pip install "delegation-core[web]"'})
    try:
        loop = asyncio.get_running_loop()
        raw_results = await loop.run_in_executor(
            None, lambda: list(DDGS().text(query, max_results=num_results))
        )
        if not raw_results:
            return json.dumps({"query": query, "summary": "No results found.", "sources": []})
        sources = [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")[:200]}
            for r in raw_results
        ]
        # Fetch stays local (DuckDuckGo). Route the summary: agent/hybrid hand
        # results to the calling Claude; local (or use_local) uses llama.cpp.
        snippets_len = sum(len(r.get("body", "")) for r in raw_results)
        route = _engine.cfg.route(task="compress", input_chars=snippets_len, use_local=use_local)
        if route in ("agent", "offer"):
            return json.dumps({
                "query": query, "summary": None, "sources": sources, "mode": route,
                "instruction": "Summarize these 'sources' yourself"
                               + (" (or re-call with use_local=true for the local model)."
                                  if route == "offer" else "."),
            })
        snippets = "\n\n".join(
            f"[{i+1}] {r.get('title', '')}\n{r.get('body', '')}"
            for i, r in enumerate(raw_results)
        )[:5000]
        summary = await _engine.invoke(
            f"Compress these search results into key facts for: {query}\n\n{snippets}",
            system="Research Compressor. Be extremely concise.",
            max_tokens=_engine.budget("compress", 400),
            temperature=0.2,
            task="compress",
        )
        return json.dumps({"query": query, "summary": summary, "sources": sources})
    except Exception as e:
        logger.error("search_web failed: %s", e)
        return json.dumps({"error": str(e)})


# ── fire-and-forget ───────────────────────────────────────────────────────────

@mcp.tool()
async def _bg_maintenance_wrapper() -> dict:
    """Runs _full_maintenance_cycle with a fresh DelegationEngine for the background thread.

    asyncio.run() in jobs.submit creates a new event loop. httpx.AsyncClient transports
    are bound to the loop that created them — sharing _engine._http across loops raises
    RuntimeError on the first pooled connection. Creating bg_engine here ensures the
    client is born in the background loop and never touches the main loop's transports.
    """
    bg_engine = DelegationEngine(_engine.cfg)
    try:
        return await _full_maintenance_cycle(bg_engine, _vault)
    finally:
        await bg_engine.aclose()


@mcp.tool()
async def run_maintenance_bg() -> str:
    """Start vault maintenance (inbox + heal pass) in the background. Returns a job_id immediately."""
    job_id = jobs.submit("run_maintenance", asyncio.run, _bg_maintenance_wrapper())
    return json.dumps({"job_id": job_id, "status": "running",
                       "message": "Maintenance + heal pass started. Call task_status(job_id) to check progress."})


@mcp.tool()
async def vault_reindex_bg(force: bool = False) -> str:
    """Rebuild the ChromaDB index in the background. Returns a job_id immediately.
    force=False (default): incremental — only reindexes notes changed since last run.
    force=True: full reindex of every note.
    """
    import functools
    fn = functools.partial(_vault.reindex_vault, force=force)
    job_id = jobs.submit("vault_reindex", fn)
    mode = "full" if force else "incremental"
    return json.dumps({"job_id": job_id, "status": "running", "mode": mode,
                       "message": f"{mode.capitalize()} reindex started. Call task_status(job_id) to check progress."})


@mcp.tool()
async def task_status(job_id: str) -> str:
    """Check the status of a background job. Returns status, elapsed time, and result."""
    job = jobs.get(job_id)
    if not job:
        return json.dumps({"error": f"Job '{job_id}' not found."})
    if job["status"] == "running":
        from datetime import datetime as dt
        job["elapsed_seconds"] = (dt.now() - dt.fromisoformat(job["started"])).seconds
    return json.dumps(job)


# ── external ingestion ────────────────────────────────────────────────────────

@mcp.tool()
async def ingest_folder(source_path: str, recursive: bool = True) -> str:
    """
    Index all supported files from an external folder into vault search.
    Original files are NEVER moved or modified. Results are tagged folder='_external'.
    Re-running is safe — files are upserted, not duplicated.
    For large directories, prefer ingest_folder_bg().
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: _ingest.ingest(source_path, recursive=recursive))
    return json.dumps(result)


@mcp.tool()
async def ingest_folder_bg(source_path: str, recursive: bool = True) -> str:
    """Index files from an external folder in the background. Returns a job_id immediately."""
    job_id = jobs.submit("ingest_folder", _ingest.ingest, source_path, recursive)
    return json.dumps({"job_id": job_id, "source": source_path, "status": "running",
                       "message": "Ingestion started. Call task_status(job_id) to check progress."})


@mcp.tool()
async def ingest_status() -> str:
    """Return the ingestion registry: which external paths have been indexed and when."""
    return json.dumps(_ingest.status())


# ── code graph ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def graph_build(path: str, name: str = "", force: bool = False) -> str:
    """
    Build a code knowledge graph for a local directory: detect -> AST extract
    (tree-sitter, code files only) -> build -> cluster -> analyze -> report -> export.
    Writes graph.json/graph.html/GRAPH_REPORT.md under ~/.delegation_core/graphs/<name>/
    and files GRAPH_REPORT.md into the vault (folder_hint=reference) via the normal
    maintenance pipeline, so it becomes searchable through search_vault.
    name defaults to the directory's basename. Re-running the same name is a no-op
    unless force=true (rebuilds and overwrites).
    Requires the [graph] extra: pip install "delegation-core[graph]".
    """
    try:
        result = await graphbridge.build_graph(
            _vault.cfg, _engine, _vault, path, name=name or None, force=force,
        )
    except ModuleNotFoundError as e:
        return json.dumps({"error": f"code-graph pipeline not installed: {e}. "
                                    'Run: pip install "delegation-core[graph]"'})
    return json.dumps(result)


@mcp.tool()
async def graph_list() -> str:
    """List previously built code graphs: name, source path, node/edge/community counts, last built."""
    return json.dumps(graphbridge.list_graphs(_vault.cfg))


@mcp.tool()
async def graph_report(name: str) -> str:
    """Return the full GRAPH_REPORT.md for a previously built graph by name (see graph_list)."""
    return json.dumps(graphbridge.get_report(_vault.cfg, name))


# ── process tracking ──────────────────────────────────────────────────────────

@mcp.tool()
async def process_create(name: str, description: str = "", steps: str = "") -> str:
    """
    Track a new ongoing process that persists across sessions and server restarts.
    Use whenever a task spans multiple conversations or requires follow-up.
    steps: comma-separated list of steps (optional).
    """
    step_list = [s.strip() for s in steps.split(",") if s.strip()] if steps else []
    return json.dumps(_tracker.create(name=name, description=description, steps=step_list))


@mcp.tool()
async def process_list(status: str = "active", query: str = "") -> str:
    """List tracked processes. status: active|paused|done|cancelled|all."""
    processes = _tracker.list_processes(status=status, query=query)
    return json.dumps({
        "status_filter": status, "query": query, "count": len(processes),
        "processes": [
            {
                "id":          p["id"],
                "name":        p["name"],
                "status":      p["status"],
                "description": p["description"],
                "steps_done":  f"{sum(s['done'] for s in p['steps'])}/{len(p['steps'])}" if p["steps"] else "open",
                "last_note":   p["notes"][-1]["text"] if p["notes"] else "",
                "updated":     p["updated"],
            }
            for p in processes
        ],
    })


@mcp.tool()
async def process_update(process_id: str, note: str = "", step_done: int = -1, status: str = "") -> str:
    """Update a tracked process. All parameters optional — only set what changed."""
    _VALID_STATUSES = {"", "active", "paused", "done", "cancelled"}
    if status not in _VALID_STATUSES:
        return json.dumps({"error": f"Invalid status '{status}'. Valid: active, paused, done, cancelled"})
    proc = _tracker.update(process_id=process_id, note=note, step_done=step_done, status=status)
    if proc is None:
        return json.dumps({"error": f"Process not found: {process_id}"})
    return json.dumps(proc)


@mcp.tool()
async def process_get(process_id: str) -> str:
    """Get full details of a tracked process including all steps, notes, and history."""
    proc = _tracker.get(process_id)
    if proc is None:
        return json.dumps({"error": f"Process not found: {process_id}"})
    return json.dumps(proc)


# ── entry point ───────────────────────────────────────────────────────────────

def run_server(cfg: Config):
    global _engine, _vault, _tracker, _ingest

    if not cfg.vault.exists():
        sys.stderr.write(f"FATAL: Vault not found at {cfg.vault}\n")
        sys.stderr.write("Run 'delegation-core setup' to configure.\n")
        sys.exit(1)

    _engine  = DelegationEngine(cfg)
    _vault   = VaultManager(cfg)
    _tracker = ProcessTracker(cfg.processes_path)
    _ingest  = IngestManager(_vault)

    def _cleanup():
        # Runs at interpreter shutdown via atexit — a *synchronous* context.
        # v5.1 patch: the previous asyncio.get_event_loop() is deprecated in
        # Python 3.12 when there is no current loop (and will raise in a future
        # release), which made this handler silently no-op on the Deprecation
        # warning path. atexit never has a running loop, so we simply spin up a
        # fresh one with asyncio.run() to flush _engine.aclose() (closes the
        # httpx.AsyncClient to llama.cpp). get_running_loop() is checked first
        # only to defend against the theoretical case of atexit firing from
        # inside a live loop, where a fresh asyncio.run() would raise.
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.create_task(_engine.aclose())
            else:
                asyncio.run(_engine.aclose())
        except Exception:
            pass
    atexit.register(_cleanup)

    _vault._init()   # blocking — BGE + ChromaDB must be ready before tools serve

    if not _vault.collection:
        sys.stderr.write("FATAL: Could not initialize ChromaDB/BGE.\n")
        sys.stderr.write("Check sentence-transformers install and vault path.\n")
        sys.exit(1)

    from . import __version__ as _version
    logger.info(
        "delegation-core v%s ready — vault: %s | llama: %s | budget: %s "
        "| synthesis: %s (%s) | split: %d chars / %d notes max | tools: 27",
        _version, cfg.vault, cfg.llama_url, cfg.budget_mode,
        "on" if cfg.synthesis_enabled else "off", cfg.synthesis_lang,
        cfg.split_min_chars, cfg.split_max_notes,
    )
    mcp.run()
