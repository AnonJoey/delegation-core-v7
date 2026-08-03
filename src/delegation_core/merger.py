"""
merger.py — Near-duplicate note merging.

Extracted from organizer.py in v0.2 so the merge policy is testable and
reusable independently of the main inbox pipeline.

Merge guards (SAAD):
  _MAX_INCOMING  — documents larger than this are standalone artifacts
  _MAX_TARGET    — notes larger than this refuse incoming merges

Never-merge folders (MAURICIO):
  sessions/ and meetings/ are chronological records; merging unrelated entries
  into them caused multiple sessions to pile up into one shared note.
"""

import logging

logger = logging.getLogger("merger")

_MAX_INCOMING = 32 * 1024    # characters of extracted text
_MAX_TARGET   = 150 * 1024   # bytes of existing note on disk

_NEVER_MERGE_FOLDERS = frozenset({"sessions", "meetings"})


def try_merge(
    vault_manager,
    hits: list[dict],
    raw_text: str,
    note_content: str,
    folder: str,
    filename: str,
    today: str,
) -> tuple[bool, str]:
    """Attempt to merge note_content into an existing near-duplicate.

    Returns (merged: bool, target_path: str).
    target_path is the vault-relative path merged into, or "" when not merged.

    Merge is skipped when:
      - the root folder is in _NEVER_MERGE_FOLDERS
      - raw_text exceeds _MAX_INCOMING (this is a standalone artifact)
      - the best matching existing note exceeds _MAX_TARGET
    """
    root_folder = folder.split("/", 1)[0]
    if root_folder in _NEVER_MERGE_FOLDERS:
        return False, ""

    if len(raw_text) > _MAX_INCOMING:
        return False, ""

    cfg = vault_manager.cfg
    for hit in hits:
        if hit.get("similarity", 0) < cfg.merge_threshold:
            continue
        existing_path = cfg.vault / hit["path"]
        if not existing_path.exists():
            continue
        if existing_path.stat().st_size > _MAX_TARGET:
            logger.debug("Skipping merge into %s — target too large", hit["path"])
            continue

        existing_text = existing_path.read_text(encoding="utf-8")
        merged = (
            existing_text
            + f"\n\n---\n*Merged from `{filename}` — {today}*\n\n"
            + note_content
        )
        existing_path.write_text(merged, encoding="utf-8")
        vault_manager.index_note(
            merged,
            {"title": hit["title"], "path": hit["path"], "folder": hit.get("folder", folder)},
        )
        logger.info("Merged %s → %s", filename, hit["path"])
        return True, hit["path"]

    return False, ""
