"""notewriter.py — the single path by which a note reaches the vault.

Extracted when the dashboard gained editing. Before that, every write went
through server.py's MCP tools, so the file-write / index / wikilink-injection
sequence lived there as a private helper. Giving the dashboard its own copy
would have created a second write path free to drift from the first — which is
the exact failure this codebase spent a day removing elsewhere (two wikilink
parsers disagreeing, two definitions of "generated", a labeler nothing called).

So both surfaces call these functions instead. `vault` is passed in rather than
read from a module global, because server.py and dashboard_api.py each hold
their own VaultManager.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .linker import inject_backlinks, wikilinks
from .vault import compose_note, safe_filename, unique_note_path

logger = logging.getLogger("notewriter")


def post_write_links(vault, note_path: Path, rel_path: str, folder: str, stem: str) -> None:
    """Inject forward wikilinks + backlinks after a vault write.

    BGE search only — no llama.cpp call, fast enough to run inline. Also drops
    the vault_health.json cache so heartbeat() stops reporting pre-write numbers.
    """
    try:
        content = note_path.read_text(encoding="utf-8")
        hits = [h for h in vault.search(content[:600], limit=6)
                if h.get("path") != rel_path][:5]
        links = wikilinks(hits, vault.cfg.merge_threshold)
        if links:
            updated = content.rstrip() + f"\n\n## Related\n{links}\n"
            note_path.write_text(updated, encoding="utf-8")
            vault.index_note(updated, {"title": stem, "path": rel_path, "folder": folder})
            inject_backlinks(vault, stem,
                             [h["path"] for h in hits
                              if h.get("similarity", 0) >= vault.cfg.merge_threshold])
    except Exception as e:
        logger.warning("post_write_links failed for %s: %s", rel_path, e)
    try:
        (Path.home() / ".delegation_core" / "vault_health.json").unlink(missing_ok=True)
    except Exception:
        pass


def create_note(vault, folder: str, title: str, content: str) -> dict:
    """Write a new dated note into `folder` and index it.

    Filename is `{date}-{safe title}.md`, disambiguated by unique_note_path so a
    second note with the same title on the same day cannot overwrite the first —
    that collision used to destroy both the file and its index row.
    """
    cfg = vault.cfg
    if folder not in cfg.vault_folders:
        return {"error": f"Invalid folder '{folder}'. Valid: {cfg.vault_folders}"}
    if not (title or "").strip():
        return {"error": "title is required"}

    today = datetime.now().strftime("%Y-%m-%d")
    dest = cfg.vault / folder / f"{today}-{safe_filename(title)}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = unique_note_path(dest)
    # compose_note rather than concatenation: callers routinely include their own
    # frontmatter block, which stacked a second one under the generated block and
    # silently turned every key they wrote into body text.
    full = compose_note(title, content, today)
    try:
        dest.write_text(full, encoding="utf-8")
    except OSError as e:
        return {"error": f"Write failed: {e}"}

    rel = str(dest.relative_to(cfg.vault))
    vault.index_note(full, {"title": title, "path": rel, "folder": folder})
    post_write_links(vault, dest, rel, folder, dest.stem)
    return {"status": "ok", "path": rel, "folder": folder, "name": dest.name}


def save_note(vault, rel_path: str, content: str) -> dict:
    """Overwrite an existing note's full text and reindex it.

    Unlike create_note this does not run compose_note: the caller is editing raw
    file content, frontmatter included, so re-composing would inject a second
    generated block on every save.
    """
    cfg = vault.cfg
    dest = (cfg.vault / rel_path).resolve()
    try:                                    # containment, not a string prefix
        dest.relative_to(cfg.vault.resolve())
    except ValueError:
        return {"error": f"Path outside the vault: {rel_path}"}
    if not dest.is_file():
        return {"error": f"Not a note: {rel_path}"}
    if dest.suffix != ".md":
        return {"error": "Only .md notes can be saved"}

    try:
        dest.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"error": f"Write failed: {e}"}

    rel = str(dest.relative_to(cfg.vault))
    folder = rel.split("/")[0]
    vault.index_note(content, {"title": dest.stem, "path": rel, "folder": folder})
    try:
        (Path.home() / ".delegation_core" / "vault_health.json").unlink(missing_ok=True)
    except Exception:
        pass
    # Deliberately no post_write_links here: appending a "## Related" block to
    # text the user just typed would edit their note behind them on every save.
    return {"status": "ok", "path": rel, "bytes": len(content.encode("utf-8"))}
