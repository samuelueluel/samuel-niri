#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp collection-scoped semantic search patch.

Why: zotero_semantic_search can scope to a library (group_id) but not to a
Zotero collection, so a single-index library can only be searched wholesale.
This patch adds query-time collection scoping:
- Queries local SQLite in real-time to resolve the collection key (or name) and its
  subcollections to the live list of `item_key`s currently in that collection.
- Filters ChromaDB and BM25 by `item_key IN [...]` DB-side at search time.
- Moving items between collections in Zotero GUI works instantly in RAG with ZERO
  manual metadata sync or re-embedding required.

Files (all in the zotero_mcp package dir passed as argv[1]):
- local_db.py          LocalZoteroReader.resolve_collection_item_keys()
- semantic_search.py   _resolve_collection_item_keys(); `collection` param + item_key filter in search()
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

    def resolve_collection_item_keys(self, collection_identifier: str) -> list[str]:
        """Return all item keys belonging to collection_identifier (key or name) and subcollections."""
        conn = self._get_connection()
        target_key = collection_identifier
        row = conn.execute(
            "SELECT key FROM collections WHERE key = ?", (collection_identifier,)
        ).fetchone()
        if not row:
            name_row = conn.execute(
                "SELECT key FROM collections WHERE collectionName = ? COLLATE NOCASE", (collection_identifier,)
            ).fetchone()
            if name_row:
                target_key = name_row[0]
            else:
                return []

        coll_keys = self.resolve_collection_keys(target_key)
        if not coll_keys:
            return []

        placeholders = ",".join("?" * len(coll_keys))
        _row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('collectionItems', 'itemCollections') ORDER BY name DESC LIMIT 1"
        ).fetchone()
        _join = _row[0] if _row else "collectionItems"
        rows = conn.execute(
            f"""
            SELECT DISTINCT i.key
            FROM {_join} ic
            JOIN items i ON i.itemID = ic.itemID
            JOIN collections c ON c.collectionID = ic.collectionID
            WHERE c.key IN ({placeholders})
            """,
            coll_keys
        ).fetchall()
        return [r[0] for r in rows]

'''

SEMANTIC_HELPERS = '''    def _resolve_collection_item_keys(self, collection_identifier: str) -> list[str]:
        """[scoped patch] Resolve all item keys in collection live from local SQLite."""
        try:
            db_path = self.db_path
            if not db_path and self.config_path and os.path.exists(self.config_path):
                with open(self.config_path) as _f:
                    db_path = json.load(_f).get("semantic_search", {}).get("zotero_db_path")
            with LocalZoteroReader(db_path=db_path) as reader:
                return reader.resolve_collection_item_keys(collection_identifier)
        except Exception as e:
            logger.warning(f"collection item_keys resolution failed for '{collection_identifier}': {e}")
            return []

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
        # 2a. helpers before search()
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
        # 2b. where clause for live collection scope
        (
            '            where = filters\n'
            '            if group_id is not None:\n'
            '                group_clause = {"group_id": int(group_id)}\n'
            '                where = {"$and": [filters, group_clause]} if filters else group_clause',
            '            where = filters\n'
            '            if group_id is not None:\n'
            '                group_clause = {"group_id": int(group_id)}\n'
            '                where = {"$and": [filters, group_clause]} if filters else group_clause\n'
            '            # [scoped patch] live collection scope via item_key from local DB\n'
            '            if collection_key is not None:\n'
            '                target_keys = self._resolve_collection_item_keys(str(collection_key))\n'
            '                if target_keys:\n'
            '                    coll_clause = {"item_key": target_keys[0]} if len(target_keys) == 1 else {"item_key": {"$in": target_keys}}\n'
            '                else:\n'
            '                    coll_clause = {"item_key": "__EMPTY_OR_NONEXISTENT_COLLECTION__"}\n'
            '                where = {"$and": [where, coll_clause]} if where else coll_clause',
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
            '        "collection: optional collection key OR collection name to scope results to that "\n'
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
            '        collection: Optional collection key or collection name to scope results (subcollections\n'
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
