#!/usr/bin/env python3
"""Apply collection-scoped semantic search to an installed zotero_mcp package.

The MCP tool gains a ``collection`` argument (collection key preferred; exact
name accepted). At query time the patch resolves that collection and all of
its descendants from Zotero's local SQLite database, then applies the resulting
item-key set to both ChromaDB and BM25 candidates. No re-embedding or metadata
backfill is required when collection membership changes.

The transformation is component-idempotent and validates every required anchor
before writing any file, so an upstream mismatch cannot leave a partial patch.

Usage: zotero-mcp-scoped-patch.py <path/to/zotero_mcp-package-dir>
Prints: ``applied`` | ``already`` | ``mismatch``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


if len(sys.argv) != 2:
    print("usage: zotero-mcp-scoped-patch.py <zotero_mcp-package-dir>", file=sys.stderr)
    sys.exit(2)

pkg = Path(sys.argv[1])
paths = {
    "local_db.py": pkg / "local_db.py",
    "semantic_search.py": pkg / "semantic_search.py",
    "tools/search.py": pkg / "tools" / "search.py",
}
errors: list[str] = []
original: dict[str, str] = {}
work: dict[str, str] = {}

for name, path in paths.items():
    if not path.exists():
        errors.append(f"{name} not found")
        continue
    original[name] = path.read_text(encoding="utf-8")
    work[name] = original[name]


def replace_once(name: str, old: str, new: str, label: str) -> None:
    src = work[name]
    count = src.count(old)
    if count != 1:
        errors.append(f"{name} {label} anchor count={count}")
        return
    work[name] = src.replace(old, new, 1)


def insert_before_once(name: str, anchor: str, addition: str, label: str) -> None:
    replace_once(name, anchor, addition + anchor, label)


LOCAL_DB_METHODS = '''    # [scoped patch] collection-scoped semantic-search helpers
    def resolve_collection_keys(self, collection_key: str) -> list[str]:
        """Return ``collection_key`` plus every descendant key."""
        conn = self._get_connection()
        out: list[str] = []
        seen: set[str] = set()
        frontier = [collection_key]
        while frontier:
            key = frontier.pop()
            if key in seen:
                continue
            seen.add(key)
            row = conn.execute(
                "SELECT collectionID FROM collections WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                continue
            out.append(key)
            frontier.extend(
                child[0]
                for child in conn.execute(
                    "SELECT key FROM collections WHERE parentCollectionID = ?", (row[0],)
                ).fetchall()
            )
        return out

    def resolve_collection_item_keys(self, collection_identifier: str) -> list[str]:
        """Return top-level item keys in a collection and its descendants.

        ``collection_identifier`` may be an exact key or an exact
        case-insensitive name. A key is preferred because names need not be
        unique across a library.
        """
        conn = self._get_connection()
        target_key = collection_identifier
        row = conn.execute(
            "SELECT key FROM collections WHERE key = ?", (collection_identifier,)
        ).fetchone()
        if row is None:
            rows = conn.execute(
                "SELECT key FROM collections "
                "WHERE collectionName = ? COLLATE NOCASE ORDER BY collectionID",
                (collection_identifier,),
            ).fetchall()
            if len(rows) != 1:
                return []
            target_key = rows[0][0]

        collection_keys = self.resolve_collection_keys(target_key)
        if not collection_keys:
            return []

        placeholders = ",".join("?" for _ in collection_keys)
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('collectionItems', 'itemCollections') "
            "ORDER BY CASE name WHEN 'collectionItems' THEN 0 ELSE 1 END LIMIT 1"
        ).fetchone()
        join_table = table_row[0] if table_row else "collectionItems"
        rows = conn.execute(
            f"""
            SELECT DISTINCT i.key
            FROM {join_table} ic
            JOIN items i ON i.itemID = ic.itemID
            JOIN collections c ON c.collectionID = ic.collectionID
            WHERE c.key IN ({placeholders})
            """,
            collection_keys,
        ).fetchall()
        return [row[0] for row in rows]

'''

SEMANTIC_HELPER = '''    def _resolve_collection_item_keys(self, collection_identifier: str) -> list[str]:
        """[scoped patch] Resolve live collection membership from SQLite."""
        try:
            db_path = self.db_path
            if not db_path and self.config_path and os.path.exists(self.config_path):
                with open(self.config_path) as config_file:
                    config = json.load(config_file)
                db_path = config.get("zotero_db_path") or config.get(
                    "semantic_search", {}
                ).get("zotero_db_path")
            with LocalZoteroReader(db_path=db_path) as reader:
                return reader.resolve_collection_item_keys(collection_identifier)
        except Exception as exc:
            logger.warning(
                "collection item-key resolution failed for %r: %s",
                collection_identifier,
                exc,
            )
            return []

'''

# 1. Local SQLite resolver.
if "local_db.py" in work and "def resolve_collection_item_keys(" not in work["local_db.py"]:
    insert_before_once(
        "local_db.py",
        "    def get_libraries(self) -> list[dict[str, Any]]:",
        LOCAL_DB_METHODS,
        "get_libraries",
    )

# 2. Semantic-search internal argument and live item-key where-clause.
if "semantic_search.py" in work:
    name = "semantic_search.py"
    if "def _resolve_collection_item_keys(" not in work[name]:
        insert_before_once(name, "    def search(self,\n", SEMANTIC_HELPER, "search helper")

    if "collection_key: str | None = None" not in work[name]:
        replace_once(
            name,
            "               group_id: int | None = None) -> dict[str, Any]:",
            "               group_id: int | None = None,\n"
            "               collection_key: str | None = None) -> dict[str, Any]:",
            "search signature",
        )

    if "# [scoped patch] live collection scope" not in work[name]:
        replace_once(
            name,
            "            if group_id is not None:\n"
            "                group_clause = {\"group_id\": int(group_id)}\n"
            "                where = {\"$and\": [filters, group_clause]} if filters else group_clause\n",
            "            if group_id is not None:\n"
            "                group_clause = {\"group_id\": int(group_id)}\n"
            "                where = {\"$and\": [filters, group_clause]} if filters else group_clause\n"
            "            # [scoped patch] live collection scope via local item keys\n"
            "            if collection_key is not None:\n"
            "                target_keys = self._resolve_collection_item_keys(str(collection_key))\n"
            "                coll_clause = (\n"
            "                    {\"item_key\": target_keys[0]}\n"
            "                    if len(target_keys) == 1\n"
            "                    else {\"item_key\": {\"$in\": target_keys}}\n"
            "                    if target_keys\n"
            "                    else {\"item_key\": \"__EMPTY_OR_UNKNOWN_COLLECTION__\"}\n"
            "                )\n"
            "                where = {\"$and\": [where, coll_clause]} if where else coll_clause\n",
            "where clause",
        )

# 3. Public MCP tool argument, description, call-through, and visible scope.
if "tools/search.py" in work:
    name = "tools/search.py"
    if "collection: optional — scope to a Zotero collection" not in work[name]:
        replace_once(
            name,
            "        \"zotero_list_libraries). search_all_libraries: search every indexed \"\n",
            "        \"zotero_list_libraries). collection: optional — scope to a Zotero collection \"\n"
            "        \"key (preferred) or exact name, including all subcollections; find keys \"\n"
            "        \"with zotero_search_collections. search_all_libraries: search every indexed \"\n",
            "tool description",
        )

    if "    collection: str | None = None," not in work[name]:
        replace_once(
            name,
            "    library_id: int | str | None = None,\n"
            "    search_all_libraries: bool = False,",
            "    library_id: int | str | None = None,\n"
            "    collection: str | None = None,\n"
            "    search_all_libraries: bool = False,",
            "tool signature",
        )

    if "        collection: Optional collection key" not in work[name]:
        replace_once(
            name,
            "        library_id: Optional library scope — 0/\"user\" for the personal library\n"
            "            or a groupID for a group library. Defaults to the active library.\n",
            "        library_id: Optional library scope — 0/\"user\" for the personal library\n"
            "            or a groupID for a group library. Defaults to the active library.\n"
            "        collection: Optional collection key (preferred) or exact name. The\n"
            "            collection and all subcollections are searched using live SQLite\n"
            "            membership; no re-embedding is required after item moves.\n",
            "tool docstring",
        )

    if "collection_key=collection" not in work[name]:
        replace_once(
            name,
            "        results = search.search(query=query, limit=limit, filters=filters, group_id=group_id)",
            "        results = search.search(\n"
            "            query=query, limit=limit, filters=filters, group_id=group_id,\n"
            "            collection_key=collection,  # [scoped patch]\n"
            "        )",
            "tool call",
        )

    if "*Collection scope:" not in work[name]:
        replace_once(
            name,
            "        if search_all_libraries:\n"
            "            output.append(\"*Scope: all indexed libraries.*\")\n"
            "            output.append(\"\")\n"
            "        output.append(f\"Found {len(search_results)} similar items:\")",
            "        if search_all_libraries:\n"
            "            output.append(\"*Scope: all indexed libraries.*\")\n"
            "            output.append(\"\")\n"
            "        if collection:\n"
            "            output.append(f\"*Collection scope: `{collection}` (subcollections included).*\")\n"
            "            output.append(\"\")\n"
            "        output.append(f\"Found {len(search_results)} similar items:\")",
            "scope display",
        )

if errors:
    print("mismatch")
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

changed = [name for name in work if work[name] != original[name]]
for name in changed:
    path = paths[name]
    tmp = path.with_name(path.name + ".scoped-patch.tmp")
    tmp.write_text(work[name], encoding="utf-8")
    os.replace(tmp, path)

print("applied" if changed else "already")
