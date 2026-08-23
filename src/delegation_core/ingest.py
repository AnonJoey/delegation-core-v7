"""
ingest.py — External folder ingestion (ABNER).

Index files from any path without moving or modifying them.
Uses embeddings.chunk_text for long documents and persists an ingestion registry
so re-runs are safe (upsert semantics — no duplicates).

New in v0.2.
"""

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import CONFIG_DIR
from .embeddings import chunk_text

logger = logging.getLogger("ingest")

_REGISTRY_FILE = CONFIG_DIR / "ingested_sources.json"

#: Extensions that mean "this was a codebase, not a document folder" — used only
#: to phrase the hint, not to decide what gets indexed.
_CODE_HINT_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala",
    ".lua", ".zig", ".ex", ".exs", ".dart", ".vue", ".svelte", ".sh", ".sql",
})


def _load_registry() -> dict:
    try:
        return json.loads(_REGISTRY_FILE.read_text(encoding="utf-8")) if _REGISTRY_FILE.exists() else {}
    except Exception:
        return {}


def _save_registry(registry: dict):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _REGISTRY_FILE.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not save ingest registry: %s", e)


def _paged_get(collection, limit: int = 5000, **kwargs) -> dict:
    """Safely get rows from ChromaDB in batches to prevent SQLite 'too many SQL variables'."""
    offset = 0
    all_ids = []
    all_metas = []
    try:
        while True:
            chunk = collection.get(limit=limit, offset=offset, **kwargs)
            ids = chunk.get("ids") or []
            if not ids:
                break
            all_ids.extend(ids)
            if "metadatas" in kwargs.get("include", []):
                all_metas.extend(chunk.get("metadatas") or [])
            if len(ids) < limit:
                break
            offset += len(ids)
        return {"ids": all_ids, "metadatas": all_metas}
    except TypeError:
        # Fallback for test mocks or collection wrappers that don't support limit/offset
        return collection.get(**kwargs)


class IngestManager:
    """Index external files into the vault's ChromaDB without touching them on disk.

    External results are tagged folder='_external' so search_vault can distinguish
    them from vault notes. Each file's absolute path is the ChromaDB document ID,
    so re-indexing the same path is safe.
    """

    def __init__(self, vault_manager):
        self._vault = vault_manager
        self._cfg = vault_manager.cfg

    def ingest(self, source_path: str, recursive: bool = True, force: bool = False) -> dict:
        """Index all supported files under source_path.

        source_path: absolute path to a file or directory.
        recursive: walk subdirectories (default True).
        force: re-index even if file mtime and size are unchanged (default False).
        """
        from .extractor import DatalessFileError, SUPPORTED, UnreadableFileError, extract

        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            return {"error": f"Path not found: {source_path}"}

        candidates: list[Path]
        unsupported: Counter[str] = Counter()

        def _keep(f: Path) -> bool:
            if f.suffix.lower() in SUPPORTED:
                return True
            unsupported[f.suffix.lower() or "(sem extensão)"] += 1
            return False

        if source.is_file():
            candidates = [source] if _keep(source) else []
        else:
            pattern = "**/*" if recursive else "*"
            candidates = [f for f in source.glob(pattern) if f.is_file() and _keep(f)]

        indexed: list[str] = []
        skipped_empty: list[str] = []
        skipped_unreadable: list[str] = []
        skipped_dataless: list[str] = []
        skipped_unchanged: list[str] = []
        errors: list[str] = []
        now = datetime.now().isoformat()

        max_chars = self._cfg.ingest_chunk_size
        overlap   = self._cfg.ingest_chunk_overlap

        registry = _load_registry()
        source_key = str(source)
        source_meta = registry.get(source_key, {})
        cached_files = source_meta.get("files", {})
        new_cached_files = {}

        for f in candidates:
            f_str = str(f)
            try:
                st = f.stat()
                f_mtime = st.st_mtime
                f_size = st.st_size
            except OSError as e:
                logger.warning("Stat failed for %s: %s", f.name, e)
                skipped_unreadable.append(f.name)
                errors.append(f"{f.name}: {e}")
                continue

            # Incremental skip: check if file has already been indexed and is unmodified
            if not force and f_str in cached_files:
                c_info = cached_files[f_str]
                if isinstance(c_info, (list, tuple)) and len(c_info) >= 2:
                    if c_info[0] == f_mtime and c_info[1] == f_size:
                        skipped_unchanged.append(f_str)
                        indexed.append(f_str)
                        new_cached_files[f_str] = [f_mtime, f_size]
                        continue

            try:
                content = extract(f)
                if not content or not content.strip():
                    skipped_empty.append(f.name)
                    continue

                chunks = chunk_text(content, max_chars=max_chars, overlap=overlap)
                for i, chunk in enumerate(chunks):
                    chunk_id = f"{f}::chunk_{i}" if len(chunks) > 1 else str(f)
                    self._vault.index_note(
                        chunk,
                        {
                            "title":         f.stem,
                            "path":          f_str,
                            "folder":        "_external",
                            "source_folder": source_key,
                            "ingested_at":   now,
                            "is_external":   "true",
                            "chunk":         str(i),
                            "total_chunks":  str(len(chunks)),
                        },
                        doc_id=chunk_id,
                    )
                indexed.append(f_str)
                new_cached_files[f_str] = [f_mtime, f_size]
            except DatalessFileError as e:
                logger.warning("Dataless file %s: %s", f.name, e)
                skipped_dataless.append(f.name)
                errors.append(f"{f.name}: {e}")
            except UnreadableFileError as e:
                logger.warning("Unreadable file %s: %s", f.name, e)
                skipped_unreadable.append(f.name)
                errors.append(f"{f.name}: {e}")
            except Exception as e:
                logger.warning("Ingest error %s: %s", f.name, e)
                skipped_unreadable.append(f.name)
                errors.append(f"{f.name}: {e}")

        registry[source_key] = {
            "last_indexed":  now,
            "indexed_count": len(indexed),
            "error_count":   len(errors),
            "recursive":     recursive,
            "files":         new_cached_files,
        }
        _save_registry(registry)

        total_skipped = len(skipped_empty) + len(skipped_unreadable) + len(skipped_dataless)
        result = {
            "source":             source_key,
            "indexed":            len(indexed),
            "skipped":            total_skipped,
            "skipped_empty":      len(skipped_empty),
            "skipped_unreadable": len(skipped_unreadable),
            "skipped_dataless":   len(skipped_dataless),
            "skipped_unchanged":  len(skipped_unchanged),
            "errors":             errors,
        }
        if skipped_dataless:
            result["dataless_files"] = skipped_dataless
        if unsupported:
            result["unsupported"] = dict(unsupported.most_common(12))
            result["unsupported_total"] = sum(unsupported.values())
            code_like = sum(n for ext, n in unsupported.items() if ext in _CODE_HINT_EXTS)
            if code_like:
                result["hint"] = (
                    f"{code_like} code file(s) were not indexed — ingest_folder handles "
                    "documents only. Use graph_build() to make a codebase searchable."
                )
        return result

    def forget(self, source_path: str) -> dict:
        """Drop everything previously ingested from source_path.

        ingest is upsert-by-absolute-path, which is safe for re-runs but leaves
        rows behind forever once the source moves or is deleted — they keep
        answering searches with paths that no longer resolve. Matches the source
        folder strictly or any file contained beneath it.
        """
        source_p = Path(source_path).expanduser().resolve()
        source_str = str(source_p)
        collection = getattr(self._vault, "collection", None)
        if collection is None:
            self._vault._ensure_ready()
            collection = getattr(self._vault, "collection", None)
        if collection is None:
            return {"error": "Vault not initialized"}

        removed = 0
        try:
            rows = _paged_get(collection, where={"is_external": "true"}, include=["metadatas"])
            ids = []
            for doc_id, meta in zip(rows.get("ids") or [], rows.get("metadatas") or []):
                meta = meta or {}
                if meta.get("source_folder") == source_str:
                    ids.append(doc_id)
                    continue
                p_str = meta.get("path", "")
                if p_str:
                    try:
                        p = Path(p_str)
                        # Exact match or parent directory containment (not loose string startswith)
                        if p == source_p or source_p in p.parents:
                            ids.append(doc_id)
                    except Exception:
                        pass
            if ids:
                # Delete in batches to avoid SQLite variable limits
                batch_size = 5000
                for i in range(0, len(ids), batch_size):
                    collection.delete(ids=ids[i:i + batch_size])
                removed = len(ids)
        except Exception as e:
            logger.warning("Ingest forget failed for %s: %s", source_str, e)
            return {"error": str(e)}

        registry = _load_registry()
        had_entry = registry.pop(source_str, None) is not None
        _save_registry(registry)
        return {"source": source_str, "removed_chunks": removed, "registry_entry_removed": had_entry}


    def status(self) -> dict:
        """Return the ingestion registry: which paths have been indexed and when."""
        registry = _load_registry()
        return {"sources": registry, "count": len(registry)}
