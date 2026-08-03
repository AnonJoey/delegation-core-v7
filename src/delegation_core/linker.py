"""
linker.py — Wikilink generation and additive relinking.

Two responsibilities:
  1. wikilinks(): generate `## Related` links from a set of search hits
     (used inline during organizer.run() when filing a new note).
  2. relink_folder(): walk an existing vault subfolder and additively add
     Related wikilinks to notes that don't already have them — useful after
     bulk ingestion or when a topic cluster grows over time.

relink_folder() is strictly additive: it never removes existing wikilinks
or rewrites note bodies, only appends new entries into `## Related`.

Introduced in the MAURICIO deployment.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("linker")

_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")

# v6 linking redesign — one vocabulary for every generator.
# A link's TARGET is always the filename stem (resolves deterministically, matches
# Obsidian's basename resolution); its DISPLAY is a clean human title (readable in
# the graph/preview). Historically wikilinks() linked by stem while relink_folder()
# linked by title — the two disagreed and produced ~84% broken links on relink.
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
# Drop the ` _ <tail>` staging-truncation artifact (space-underscore-space, added
# when a long source title was cut at the 50-char filename cap). Requires spaces
# around the underscore so plain `_` word separators (Sara_Saad_-_2024) are kept.
_TRUNC_TAIL_RE  = re.compile(r"\s+_\s+\S.*$")


def clean_display(stem: str, title: str | None = None) -> str:
    """Human-readable label for a note: prefer a real frontmatter title, else derive
    one from the filename stem (strip the YYYY-MM-DD- prefix and the ` _ …` tail).
    Sanitizes characters that would break the alias segment of a wikilink."""
    label = (title or "").strip()
    if not label or label.lower() == stem.lower():
        label = _TRUNC_TAIL_RE.sub("", _DATE_PREFIX_RE.sub("", stem)).strip() or stem
    # ] | # would terminate/confuse the [[target|display]] syntax
    return label.replace("]", ")").replace("|", "/").replace("#", "").strip()


def _to_stem(path_or_stem: str) -> str:
    """Filename stem WITHOUT mangling dots inside the name. `Path(...).stem` would
    strip a trailing dotted segment (e.g. `Gathering_Data_pt.02` → `..._pt`), so we
    only drop the directory and a literal `.md` extension."""
    name = Path(path_or_stem).name
    return name[:-3] if name.endswith(".md") else name


def format_link(path_or_stem: str, title: str | None = None) -> str:
    """Build an aliased wikilink `[[stem|Display]]` (or bare `[[stem]]` when the
    display equals the stem). `path_or_stem` may be a vault-relative path or a stem."""
    stem = _to_stem(path_or_stem)
    disp = clean_display(stem, title)
    return f"[[{stem}]]" if disp == stem else f"[[{stem}|{disp}]]"


def wikilinks(hits: list, threshold: float) -> str:
    """Return a `- [[stem|Display]]` block for hits above the similarity threshold."""
    lines = []
    for h in hits:
        if h.get("similarity", 0) >= threshold:
            if h.get("path"):
                lines.append(f"- {format_link(h['path'], h.get('title'))}")
            elif h.get("title"):
                lines.append(f"- {format_link(h['title'], h.get('title'))}")
    return "\n".join(lines)


def strip_frontmatter(content: str) -> str:
    """Return the body of a note with YAML frontmatter removed."""
    if not content.startswith("---"):
        return content
    m = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
    return content[m.end():] if m else content


def frontmatter_aliases(content: str) -> set:
    """Return the set of Obsidian `aliases:` declared in a note's frontmatter.
    Supports both block-list and inline `[a, b]` forms. Empty set if none."""
    if not content.startswith("---\n"):
        return set()
    close = content.find("\n---\n", 4)
    if close == -1:
        return set()
    fm = content[4:close]
    # [^\S\n]* = horizontal whitespace only, so it never crosses the newline into
    # the first block-list item (the bug that swallowed `- item` into group 1).
    m = re.search(r"^aliases:[^\S\n]*(.*)$", fm, re.MULTILINE)
    if not m:
        return set()
    out: set = set()
    inline = m.group(1).strip()
    if inline.startswith("["):                       # aliases: [a, b]
        out |= {x.strip().strip('"').strip("'") for x in inline[1:-1].split(",")}
    else:                                            # block list under aliases:
        for line in fm[m.end():].splitlines():
            lm = re.match(r"[^\S\n]*-\s+(.*\S)", line)
            if lm:
                out.add(lm.group(1).strip().strip('"').strip("'"))
            elif line.strip() and not line[:1].isspace():
                break                                # next top-level key → stop
    return {a for a in out if a}


def _alias_block(aliases: list) -> str:
    """Render an `aliases:` YAML block list, quoting values that need it."""
    body = "\n".join(f'  - "{a}"' if any(c in a for c in ':#[]') else f"  - {a}"
                     for a in aliases)
    return f"aliases:\n{body}"


def ensure_aliases(content: str, new_aliases: list) -> str:
    """Merge entries into the note's `aliases:` frontmatter (creating frontmatter
    if absent). So `[[Human Title]]` resolves in Obsidian even though the file is
    named `YYYY-MM-DD-slug.md`. Never drops existing aliases; idempotent."""
    have = frontmatter_aliases(content)
    add: list = []
    for a in new_aliases:                            # dedup vs existing AND within add
        a = a.strip()
        if a and a not in have and a not in add:
            add.append(a)
    if not add:
        return content
    merged = list(have) + add                        # existing first, then new

    if content.startswith("---\n") and (close := content.find("\n---\n", 4)) != -1:
        # strip any existing aliases: line + its block, then re-emit merged block
        lines = content[4:close].splitlines()
        kept, i = [], 0
        while i < len(lines):
            if re.match(r"^aliases:", lines[i]):
                i += 1
                while i < len(lines) and re.match(r"[^\S\n]*-\s+", lines[i]):
                    i += 1
                continue
            kept.append(lines[i]); i += 1
        fm_clean = "\n".join(kept).rstrip()
        new_fm = (fm_clean + "\n" if fm_clean else "") + _alias_block(merged)
        return f"---\n{new_fm}\n---\n{content[close + 5:]}"
    return f"---\n{_alias_block(merged)}\n---\n\n{content}"


def existing_targets(content: str) -> set:
    """Return the set of [[target]] titles already linked in a note."""
    return {m.strip() for m in _WIKILINK_RE.findall(content)}


def add_related_links(content: str, new_links: list) -> str:
    """Append new_links into the `## Related` section, creating it if absent.
    Never removes or reorders existing wikilinks."""
    if not new_links:
        return content
    block = "\n".join(new_links)
    match = re.search(r"\n##+\s*Related\s*\n", content)
    if match:
        insert_pos = match.end()
        next_section = re.search(r"\n##+\s+", content[insert_pos:])
        if next_section:
            cut = insert_pos + next_section.start()
            return content[:cut].rstrip() + "\n" + block + "\n" + content[cut:]
        return content.rstrip() + "\n" + block + "\n"
    return content.rstrip() + "\n\n## Related\n" + block + "\n"


def inject_backlinks(vault_manager, source_stem: str, target_paths: list,
                     source_title: str | None = None) -> int:
    """For each target note, inject a [[source_stem|Title]] backlink if not present.

    Called after a new note is written so the notes it links to also link back.
    Strictly additive — only appends, never removes existing links.
    """
    cfg = vault_manager.cfg
    updated = 0
    for rel_path in target_paths:
        try:
            f = cfg.vault / rel_path
            if not f.exists():
                continue
            content = f.read_text(encoding="utf-8")
            if source_stem in existing_targets(content):
                continue
            updated_content = add_related_links(content, [f"- {format_link(source_stem, source_title)}"])
            if updated_content == content:
                continue
            f.write_text(updated_content, encoding="utf-8")
            vault_manager.index_note(
                updated_content,
                {"title": f.stem, "path": rel_path,
                 "folder": str(Path(rel_path).parent)},
            )
            updated += 1
        except Exception as e:
            logger.warning("inject_backlinks failed for %s: %s", rel_path, e)
    return updated


def relink_folder(
    vault_manager,
    folder: str,
    days: int | None = None,
    min_similarity: float | None = None,
    max_links_per_note: int = 8,
) -> dict:
    """Additively add wikilinks under `## Related` for notes in a vault subfolder.

    For each .md note:
      - Search semantically for related notes above min_similarity
      - Skip self and already-linked targets
      - Append new [[wikilinks]] under ## Related
      - Re-index the updated note

    folder: vault-relative subpath (e.g. 'meetings/Gazin/2026' or 'meetings')
    days: restrict to notes modified within last N days (None = all)
    min_similarity: link threshold; defaults to cfg.search_threshold
    max_links_per_note: cap on new links added per note in this pass
    """
    cfg = vault_manager.cfg
    target = cfg.vault / folder
    if not target.exists():
        return {"error": f"Folder not found in vault: {folder}"}

    threshold = min_similarity if min_similarity is not None else cfg.search_threshold
    cutoff = (datetime.now().timestamp() - days * 86400) if days else None

    # Generated articles are excluded, not merely deprioritised: graph_build
    # rewrites its wiki folder wholesale on every rebuild, so a "## Related"
    # block injected here is discarded the next time the graph is built. It is
    # also the bulk of the work — Reference/ holds 3656 generated articles
    # against 59 written by hand, so an unfiltered pass spends its entire run on
    # notes that will not keep the result.
    from .vault import VaultManager

    md_files = []
    for f in target.rglob("*.md"):
        if cutoff is not None and f.stat().st_mtime < cutoff:
            continue
        rel = str(f.relative_to(cfg.vault))
        if VaultManager.classify_path(rel)[0] == "generated":
            continue
        md_files.append(f)

    # Resolved once: every path the vault can legitimately link to.
    resolvable_notes = set(vault_manager.resolvable_link_targets().values())

    results = {
        "folder": folder,
        "processed": 0,
        "updated": 0,
        "links_added": 0,
        "errors": [],
        "skipped": [],
    }

    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8")
            results["processed"] += 1

            body = strip_frontmatter(content).strip()
            if not body:
                results["skipped"].append(f"{f.name}: empty body")
                continue

            self_path = str(f.relative_to(cfg.vault))
            already_linked = existing_targets(content)

            # scope='notes': this relinks the user's own writing to itself. An
            # unscoped search on a vault holding 3692 generated code-graph
            # articles against 187 hand-written notes links a note about GPU
            # memory to TestVisionCpuBurstCap and hermes-agent: managed_uv.py —
            # observed directly on the first real run, which added 24 such links
            # to 9 notes before they were removed again.
            hits = vault_manager.search(body[:800], limit=max_links_per_note + 3,
                                        scope="notes")
            new_links = []
            new_link_paths = []
            for h in hits:
                path = h.get("path")
                if not path or path == self_path:
                    continue
                # A hit must be a note this vault can actually resolve. Externally
                # ingested files are indexed by absolute source path and carry no
                # kind marker, so they can survive a scoped search and then be
                # written as [[SKILL]] or [[Start here]] — a link to a file that
                # is not in the vault and never resolves. 23 such links were
                # added on a real pass before this guard existed.
                if (cfg.vault / path) not in resolvable_notes:
                    continue
                if h.get("similarity", 0) < threshold:
                    continue
                stem = Path(path).stem      # dedup + link by stem, not title (v6)
                if stem in already_linked:
                    continue
                new_links.append(f"- {format_link(path, h.get('title'))} _(sim: {h['similarity']:.2f})_")
                new_link_paths.append(path)
                already_linked.add(stem)
                if len(new_links) >= max_links_per_note:
                    break

            if not new_links:
                continue

            updated = add_related_links(content, new_links)
            if updated == content:
                continue

            f.write_text(updated, encoding="utf-8")
            vault_manager.index_note(updated, {
                "title": f.stem,
                "path": self_path,
                "folder": str(f.parent.relative_to(cfg.vault)),
            })
            results["updated"] += 1
            results["links_added"] += len(new_links)

            # Bidirectional: inject backlinks into each note we just linked to
            inject_backlinks(vault_manager, f.stem, new_link_paths)

        except Exception as e:
            logger.warning("relink failed for %s: %s", f.name, e)
            results["errors"].append(f"{f.name}: {e}")

    return results
