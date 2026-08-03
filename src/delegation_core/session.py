"""
session.py — Session export: write a curated digest to the vault sessions/ folder.

Called by the export_session MCP tool when the user signals end-of-session.
"""

import logging
from datetime import datetime

logger = logging.getLogger("session")


def export(vault, title: str, summary: str, key_decisions: str = "") -> dict:
    """Write a formatted session note to vault sessions/ and index it immediately.

    vault: VaultManager instance
    title: short descriptive name for the session
    summary: 2-4 sentences covering what was discussed, decided, or built
    key_decisions: comma-separated decisions, artifacts created, or next steps
    """
    cfg = vault.cfg
    # Case-insensitive: a vault with a "Sessions" folder must not fall through to
    # vault_folders[0], which silently filed every session digest under Projects/.
    from .config import resolve_folder
    folder = resolve_folder("sessions", cfg.vault_folders) or cfg.vault_folders[0]

    from .vault import safe_filename, unique_note_path, yaml_quote_scalar
    safe = safe_filename(title)
    date_str = datetime.now().strftime("%Y-%m-%d")
    dest = cfg.vault / folder / f"{date_str}-{safe}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = unique_note_path(dest)

    decisions_list = [d.strip() for d in key_decisions.split(",") if d.strip()] if key_decisions else []

    lines = [
        "---",
        f"title: {yaml_quote_scalar(title)}",
        f"date: {date_str}",
        "type: session",
        "ai_generated: true",
        "---",
        "",
        f"# {title}",
        f"*{datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "## Summary",
        "",
        summary.strip(),
        "",
    ]
    if decisions_list:
        lines += ["## Key decisions / artifacts", ""]
        for d in decisions_list:
            lines.append(f"- {d}")
        lines.append("")

    full = "\n".join(lines)
    dest.write_text(full, encoding="utf-8")
    vault.index_note(
        full,
        {"title": title, "path": str(dest.relative_to(cfg.vault)), "folder": folder, "type": "session"},
    )
    logger.info("Session exported: %s", dest.name)
    return {"status": "ok", "path": str(dest.name), "folder": folder}
