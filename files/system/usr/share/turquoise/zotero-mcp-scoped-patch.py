#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp collection-scoped semantic search patch.

Why: zotero_semantic_search can scope to a library (group_id) but not to a
Zotero collection, so a single-index library can only be searched wholesale.
This patch adds query-time collection scoping: each chunk's metadata stores
its item's collection keys, and zotero_semantic_search gains a `collection`
parameter that filters DB-side (ChromaDB where clause, like group_id — never a
Python post-filter). Subcollections are included (resolved recursively from
the local DB, matching the existing corpus-level `collection_keys` filter
semantics).

Files (all in the zotero_mcp package dir passed as argv[1]):
- local_db.py          LocalZoteroReader.get_item_collections() + resolve_collection_keys()
- semantic_search.py   stamp data.collections at local item build; store in chunk
                       metadata; `collection` param + where clause in search();
                       _sync_collections_metadata() (metadata-only, no re-embed)
- tools/search.py      zotero_semantic_search tool gains `collection` param

Marker comment: "[scoped patch]". Re-applied by sjust update; see
New-RAG-Setup.md (RAG strategy decision) and Zotero-MCP.md.
Usage: zotero-mcp-scoped-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
errors: list[str] = []
changed = False

MARKER = "[scoped patch]"

LOCAL_DB_METHODS = '''    # [scoped patch] collection-scoped semantic search helpers
    def get_item_collections(self) -> dict[str, list[str]]:
        """Map item key -> list of collection keys (direct membership)."""
        conn = self._get_connection()
        # Zotero renamed the join table itemCollections -> collectionItems;
        # accept either for cross-version robustness.
        _row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('collectionItems', 'itemCollections') ORDER BY name DESC LIMIT 1"
        ).fetchone()
        _join = _row[0] if _row else "collectionItems"
        rows = conn.execute(
            f"""
            SELECT i.key, c.key
            FROM {_join} ic
            JOIN items i ON i.itemID = ic.itemID
            JOIN collections c ON c.collectionID = ic.collectionID
            """
        ).fetchall()
        out: dict[str, list[str]] = {}
        for item_key, coll_key in rows:
            out.setdefault(item_key, []).append(coll_key)
        return out

    def resolve_collection_keys(self, collection_key: str) -> list[str]:
        """Return the given collection key plus all descendant keys (recursive)."""
        conn = self._get_connection()
        out: list[str] = []
        frontier = [collection_key]
        while frontier:
            key = frontier.pop()
            row = conn.execute(
                "SELECT collectionID FROM collections WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                continue
            out.append(key)
            for sub in conn.execute(
                "SELECT key FROM collections WHERE parentCollectionID = ?", (row[0],)
            ).fetchall():
                frontier.append(sub[0])
        return out

'''

SEMANTIC_HELPERS = '''    def _resolve_collection_keys(self, collection_key: str) -> list[str]:
        """[scoped patch] Resolve a collection key + descendants from the local DB.

        Falls back to the bare key when the local DB is unavailable (web-only
        mode, desktop closed): direct members still match.
        """
        try:
            db_path = self.db_path
            if not db_path and self.config_path and os.path.exists(self.config_path):
                with open(self.config_path) as _f:
                    db_path = json.load(_f).get("semantic_search", {}).get("zotero_db_path")
            with LocalZoteroReader(db_path=db_path) as reader:
                keys = reader.resolve_collection_keys(collection_key)
                return keys or [collection_key]
        except Exception as e:
            logger.warning(f"collection resolution failed for '{collection_key}': {e}")
            return [collection_key]

    def _sync_collections_metadata(self) -> dict[str, int]:
        """[scoped patch] Stamp collection keys onto chunk metadata without re-embedding.

        Mirrors ``_backfill_group_ids`` (#396): builds item_key -> collection
        keys from the local DB and updates only documents whose stored
        membership differs, so filing/unfiling items updates scoping in
        seconds instead of a full re-embed.
        """
        stats = {"scanned": 0, "updated": 0}
        try:
            db_path = self.db_path
            if not db_path and self.config_path and os.path.exists(self.config_path):
                with open(self.config_path) as _f:
                    db_path = json.load(_f).get("semantic_search", {}).get("zotero_db_path")
            with LocalZoteroReader(db_path=db_path) as reader:
                coll_map = reader.get_item_collections()
        except Exception as e:
            logger.warning(f"collections sync: could not read local database: {e}")
            return stats

        for ids, metadatas in self.chroma_client.iter_metadatas():
            stats["scanned"] += len(ids)
            update_ids: list[str] = []
            update_metas: list[dict[str, Any]] = []
            for doc_id, meta in zip(ids, metadatas):
                item_key = meta.get("item_key", "")
                current = coll_map.get(item_key)
                if current is None:
                    continue  # unknown item - leave alone
                stored = meta.get("collections")
                if stored != current:
                    update_ids.append(doc_id)
                    m = dict(meta)
                    m["collections"] = list(current)
                    update_metas.append(m)
            if update_ids:
                self.chroma_client.update_metadatas(update_ids, update_metas)
                stats["updated"] += len(update_ids)
        return stats

'''


def _apply(path: Path, edits: list[tuple[str, str]], name: str) -> None:
    """Apply edits to a file only when every anchor is present (all-or-nothing)."""
    global changed
    if MARKER in path.read_text(encoding="utf-8"):
        return  # already patched
    src = path.read_text(encoding="utf-8")
    work = src
    for old, new in edits:
        if old in work:
            work = work.replace(old, new, 1)
        else:
            errors.append(f"{name} anchor missing")
            return
    path.write_text(work, encoding="utf-8")
    changed = True


# --- 1. local_db.py --------------------------------------------------------
ldb = pkg / "local_db.py"
if ldb.exists():
    _apply(ldb, [
        (
            '    def __exit__(self, exc_type, exc_val, exc_tb):\n'
            '        self.close()\n'
            '\n'
            '    def get_libraries(self) -> list[dict[str, Any]]:',
            '    def __exit__(self, exc_type, exc_val, exc_tb):\n'
            '        self.close()\n'
            '\n'
            + LOCAL_DB_METHODS +
            '    def get_libraries(self) -> list[dict[str, Any]]:',
        ),
    ], "local_db.py")
else:
    errors.append("local_db.py not found")

# --- 2. semantic_search.py -------------------------------------------------
ss = pkg / "semantic_search.py"
if ss.exists():
    _apply(ss, [
        # 2a. stamp data.collections in the local item build
        (
            '                    api_items.append(api_item)\n'
            '\n'
            '                logger.info(f"Retrieved {len(api_items)} items from local database")',
            '                    api_items.append(api_item)\n'
            '\n'
            '                # [scoped patch] stamp collection membership for query-time scoping\n'
            '                try:\n'
            '                    _coll_map = reader.get_item_collections()\n'
            '                    for _api_item in api_items:\n'
            '                        _api_item["data"]["collections"] = _coll_map.get(_api_item["key"], [])\n'
            '                except Exception as _e:\n'
            '                    logger.warning(f"collections stamping failed: {_e}")\n'
            '\n'
            '                logger.info(f"Retrieved {len(api_items)} items from local database")',
        ),
        # 2b. store collections in chunk metadata
        (
            '        if (group_id := data.get("group_id")) is not None:\n'
            '            metadata["group_id"] = int(group_id)',
            '        if (group_id := data.get("group_id")) is not None:\n'
            '            metadata["group_id"] = int(group_id)\n'
            '        # [scoped patch] collection membership (list of keys) for query-time\n'
            '        # scoping. Present for local-mode items (stamped at build) and\n'
            '        # web-API items (native data.collections); omitted when unknown,\n'
            '        # so pre-patch chunks simply don\'t match a collection filter\n'
            '        # until they are re-embedded.\n'
            '        if (colls := data.get("collections")):\n'
            '            metadata["collections"] = list(colls)',
        ),
        # 2c. helpers before search()
        (
            '    def search(self,\n'
            '               query: str,\n'
            '               limit: int = 10,\n'
            '               filters: dict[str, Any] | None = None,\n'
            '               group_id: int | None = None) -> dict[str, Any]:',
            SEMANTIC_HELPERS +
            '    def search(self,\n'
            '               query: str,\n'
            '               limit: int = 10,\n'
            '               filters: dict[str, Any] | None = None,\n'
            '               group_id: int | None = None,\n'
            '               collection_key: str | None = None) -> dict[str, Any]:',
        ),
        # 2d. where clause for collection scope
        (
            '            where = filters\n'
            '            if group_id is not None:\n'
            '                group_clause = {"group_id": int(group_id)}\n'
            '                where = {"$and": [filters, group_clause]} if filters else group_clause',
            '            where = filters\n'
            '            if group_id is not None:\n'
            '                group_clause = {"group_id": int(group_id)}\n'
            '                where = {"$and": [filters, group_clause]} if filters else group_clause\n'
            '            # [scoped patch] collection scope (key + subcollections, DB-side)\n'
            '            if collection_key is not None:\n'
            '                coll_keys = self._resolve_collection_keys(str(collection_key))\n'
            '                coll_clauses = [{"collections": {"$contains": k}} for k in coll_keys]\n'
            '                coll_clause = coll_clauses[0] if len(coll_clauses) == 1 else {"$or": coll_clauses}\n'
            '                where = {"$and": [where, coll_clause]} if where else coll_clause',
        ),
        # 2e. run the metadata sync on every update (skip on rebuild: stamped fresh)
        (
            '            # Unattributed docs are excluded from library-filtered search and\n'
            '            # from deletion cleanup; keep that visible on every update, not\n'
            '            # just the one that discovered it.',
            '            # [scoped patch] collection membership sync (query-time scoping).\n'
            '            # Keeps chunk collection keys current without re-embedding\n'
            '            # (mirrors the group_id backfill: iter_metadatas + update_metadatas).\n'
            '            if not force_full_rebuild:\n'
            '                try:\n'
            '                    coll_sync = self._sync_collections_metadata()\n'
            '                    if coll_sync["updated"]:\n'
            '                        sys.stderr.write(\n'
            '                            f"Updated collection metadata on {coll_sync[\'updated\']} "\n'
            '                            "document(s).\\n"\n'
            '                        )\n'
            '                except Exception as e:\n'
            '                    logger.warning(f"collections metadata sync failed: {e}")\n'
            '\n'
            '            # Unattributed docs are excluded from library-filtered search and\n'
            '            # from deletion cleanup; keep that visible on every update, not\n'
            '            # just the one that discovered it.',
        ),
    ], "semantic_search.py")
else:
    errors.append("semantic_search.py not found")

# --- 3. tools/search.py ----------------------------------------------------
ts = pkg / "tools" / "search.py"
if ts.exists():
    _apply(ts, [
        # 3a. tool description
        (
            '        "library_id: optional — restrict results to one library. 0 or "\n'
            '        "\'user\' for your personal library, or a group\'s numeric groupID "\n'
            '        "(see zotero_list_libraries). Omit to search all indexed libraries. "',
            '        "library_id: optional — restrict results to one library. 0 or "\n'
            '        "\'user\' for your personal library, or a group\'s numeric groupID "\n'
            '        "(see zotero_list_libraries). Omit to search all indexed libraries. "\n'
            '        "collection: optional collection KEY to scope results to that "\n'
            '        "collection and its subcollections. Find keys with "\n'
            '        "zotero_search_collections. "',
        ),
        # 3b. signature
        (
            'def semantic_search(\n'
            '    query: str,\n'
            '    limit: int = 10,\n'
            '    filters: dict[str, str] | str | None = None,\n'
            '    library_id: int | str | None = None,\n'
            '    *,\n'
            '    ctx: Context\n'
            ') -> str:',
            'def semantic_search(\n'
            '    query: str,\n'
            '    limit: int = 10,\n'
            '    filters: dict[str, str] | str | None = None,\n'
            '    library_id: int | str | None = None,\n'
            '    collection: str | None = None,\n'
            '    *,\n'
            '    ctx: Context\n'
            ') -> str:',
        ),
        # 3c. docstring
        (
            '        library_id: Optional library scope — 0/"user" for the personal library, a\n'
            '            groupID for a group library, or None (default) to search every\n'
            '            indexed library.\n',
            '        library_id: Optional library scope — 0/"user" for the personal library, a\n'
            '            groupID for a group library, or None (default) to search every\n'
            '            indexed library.\n'
            '        collection: Optional collection key to scope results (subcollections\n'
            '            included). Find keys with zotero_search_collections.\n',
        ),
        # 3d. call site
        (
            '        results = search.search(query=query, limit=limit, filters=filters, group_id=group_id)',
            '        results = search.search(query=query, limit=limit, filters=filters, group_id=group_id, collection_key=collection)  # [scoped patch]',
        ),
    ], "tools/search.py")
else:
    errors.append("tools/search.py not found")

if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("applied" if changed else "already")
