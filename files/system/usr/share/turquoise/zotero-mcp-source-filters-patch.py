#!/usr/bin/env python3
"""Apply unified source-group and tag filters to zotero-mcp.

The patch keeps Zotero's native ``itemType`` and tags as separate data.  It
adds a query-time many-to-few ``source_group`` mapping, live tag/type item-key
scoping for semantic search, source-group reporting on results, and strict
boolean-tag/type enforcement for metadata tag search. No PDF, sidecar, or
embedding data is changed.

The companion ``zotero-mcp-source-filters.py`` module is copied into the
installed package.  The transformation is component-idempotent and validates
all text anchors before writing package files.

Usage: zotero-mcp-source-filters-patch.py <path/to/zotero_mcp-package-dir>
Prints: ``applied`` | ``already`` | ``mismatch``.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


if len(sys.argv) != 2:
    print(
        "usage: zotero-mcp-source-filters-patch.py <zotero_mcp-package-dir>",
        file=sys.stderr,
    )
    sys.exit(2)

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
source_module = here / "zotero-mcp-source-filters.py"
paths = {
    "local_db.py": pkg / "local_db.py",
    "semantic_search.py": pkg / "semantic_search.py",
    "tools/search.py": pkg / "tools" / "search.py",
}
errors: list[str] = []
original: dict[str, str] = {}
work: dict[str, str] = {}

if not source_module.exists():
    errors.append("zotero-mcp-source-filters.py not found next to patch")
for name, path in paths.items():
    if not path.exists():
        errors.append(f"{name} not found")
        continue
    original[name] = path.read_text(encoding="utf-8")
    work[name] = original[name]

# The semantic patch passes a new keyword to the sparse leg. Refuse to add the
# caller before the companion sparse patch is present; otherwise an upstream
# sparse-patch mismatch would leave a running server with a TypeError.
sparse_index = pkg / "sparse_index.py"
if sparse_index.exists():
    sparse_source = sparse_index.read_text(encoding="utf-8")
    if "allowed_item_keys: set[str] | None = None" not in sparse_source:
        errors.append(
            "sparse_index.py is missing allowed_item_keys; "
            "apply the sparse patch first"
        )


def replace_once(name: str, old: str, new: str, label: str) -> None:
    src = work[name]
    count = src.count(old)
    if count != 1:
        errors.append(f"{name} {label} anchor count={count}")
        return
    work[name] = src.replace(old, new, 1)


def insert_before_once(name: str, anchor: str, addition: str, label: str) -> None:
    replace_once(name, anchor, addition + anchor, label)


LOCAL_DB_METHOD = '''    def resolve_semantic_filter_item_keys(
        self,
        item_types: list[str] | None = None,
        tags: list[str] | None = None,
        group_id: int | None = PERSONAL_LIBRARY_GROUP_ID,
    ) -> list[str] | None:
        """[source filters patch] Resolve live semantic filter membership.

        Returns matching parent item keys. ``None`` means the tag DSL could
        not be represented by this SQL backend (currently wildcard tags).
        The method intentionally reads current SQLite metadata rather than
        copying tags into embeddings, so tag and itemType changes take effect
        without re-embedding.
        """
        conn = self._get_connection()
        library_ids = self._resolve_scope_library_ids(group_id)
        if not library_ids:
            return []

        library_placeholders = ",".join("?" for _ in library_ids)
        clauses = [
            f"i.libraryID IN ({library_placeholders})",
            "i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            "it.typeName NOT IN ('attachment', 'note', 'annotation')",
        ]
        params: list[Any] = list(library_ids)

        if item_types:
            type_placeholders = ",".join("?" for _ in item_types)
            clauses.append(f"it.typeName IN ({type_placeholders})")
            params.extend(item_types)

        if tags:
            built = _tag_dsl_condition(tags)
            if built is None:
                return None
            tag_sql, tag_params = built
            clauses.append(tag_sql)
            params.extend(tag_params)

        rows = conn.execute(
            "SELECT DISTINCT i.key FROM items i "
            "JOIN itemTypes it ON i.itemTypeID = it.itemTypeID "
            f"WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall()
        return [row[0] for row in rows]

'''

# 1. Live SQLite key resolver for tags and native itemTypes.
if "local_db.py" in work and "def resolve_semantic_filter_item_keys(" not in work["local_db.py"]:
    insert_before_once(
        "local_db.py",
        "    def _fetch_creators(self, conn: sqlite3.Connection, item_ids: list[int]) -> dict[int, list[dict]]:",
        LOCAL_DB_METHOD,
        "semantic filter resolver",
    )

# 2. Semantic-search import, live filter helper, where construction, sparse scope,
#    result source-group enrichment.
if "semantic_search.py" in work:
    name = "semantic_search.py"
    if "from . import source_filters as _source_filters" not in work[name]:
        replace_once(
            name,
            "from .chroma_client import ChromaClient, create_chroma_client\n",
            "from .chroma_client import ChromaClient, create_chroma_client\n"
            "from . import source_filters as _source_filters  # [source filters patch]\n",
            "source filters import",
        )

    SOURCE_HELPER = '''    def _resolve_semantic_filter_item_keys(
        self,
        item_types: list[str],
        tags: list[str],
        group_id: int | None,
    ) -> set[str] | None:
        """[source filters patch] Resolve live tag/type membership.

        Native type filters can fall back to Chroma-only filtering outside
        local mode. Tags require local SQLite because indexed tag metadata is
        historical and is stored as one display string rather than a live tag
        relation.
        """
        reader = self._open_local_reader()
        if reader is None:
            if tags:
                raise RuntimeError(
                    "Live tag-filtered semantic search requires local Zotero "
                    "mode and a readable zotero.sqlite database"
                )
            return None
        try:
            resolved = reader.resolve_semantic_filter_item_keys(
                item_types=item_types or None,
                tags=tags or None,
                group_id=group_id,
            )
        finally:
            reader.close()
        if resolved is None:
            raise ValueError(
                "Semantic tag filters containing '*' or '%' are not supported "
                "by live local filtering"
            )
        return set(resolved)

'''
    if "def _resolve_semantic_filter_item_keys(" not in work[name]:
        insert_before_once(
            name,
            "    def _resolve_collection_item_keys(",
            SOURCE_HELPER,
            "semantic filter helper",
        )

    OLD_SEARCH_FILTERS = '''            where = filters
            if group_id is not None:
                group_clause = {"group_id": int(group_id)}
                where = {"$and": [filters, group_clause]} if filters else group_clause
'''
    NEW_SEARCH_FILTERS = '''            parsed_filters = _source_filters.parse_semantic_filters(filters)
            where = parsed_filters["where"] or None
            item_types = parsed_filters["item_types"]
            tag_conditions = parsed_filters["tags"]
            requested_item_keys = parsed_filters["item_keys"]
            allowed_item_keys: set[str] | None = None

            # Keep the paper-RAG default narrow even when older index records
            # contain excluded item types. This is a metadata predicate, not
            # an embedding change.
            excluded_clause = {
                "item_type": {
                    "$nin": sorted(_source_filters.DEFAULT_EXCLUDED_ITEM_TYPES)
                }
            }
            where = {"$and": [excluded_clause, where]} if where else excluded_clause

            # Resolve type/tag predicates to current parent item keys whenever
            # local SQLite is available. This makes both dense and sparse legs
            # obey the same live scope and avoids sparse top-N post-filter loss.
            if item_types or tag_conditions:
                resolved = self._resolve_semantic_filter_item_keys(
                    item_types=item_types,
                    tags=tag_conditions,
                    group_id=group_id,
                )
                if resolved is not None:
                    allowed_item_keys = set(resolved)

            # Explicit parent item keys (e.g. from the exact-source resolver)
            # are the resolved scope themselves. Intersect with any type/tag
            # scope and fail closed when nothing remains.
            if requested_item_keys:
                allowed_item_keys = (
                    set(requested_item_keys)
                    if allowed_item_keys is None
                    else allowed_item_keys.intersection(requested_item_keys)
                )

            if allowed_item_keys is not None:
                key_clause = (
                    {"item_key": "__EMPTY_OR_NONEXISTENT_SOURCE_FILTER__"}
                    if not allowed_item_keys
                    else {
                        "item_key": (
                            next(iter(allowed_item_keys))
                            if len(allowed_item_keys) == 1
                            else {"$in": sorted(allowed_item_keys)}
                        )
                    }
                )
                where = {"$and": [where, key_clause]} if where else key_clause

            if group_id is not None:
                group_clause = {"group_id": int(group_id)}
                where = {"$and": [where, group_clause]} if where else group_clause
'''
    PREV_SEARCH_FILTERS = '''            parsed_filters = _source_filters.parse_semantic_filters(filters)
            where = parsed_filters["where"] or None
            item_types = parsed_filters["item_types"]
            tag_conditions = parsed_filters["tags"]
            allowed_item_keys: set[str] | None = None

            # Keep the paper-RAG default narrow even when older index records
            # contain excluded item types. This is a metadata predicate, not
            # an embedding change.
            excluded_clause = {
                "item_type": {
                    "$nin": sorted(_source_filters.DEFAULT_EXCLUDED_ITEM_TYPES)
                }
            }
            where = {"$and": [excluded_clause, where]} if where else excluded_clause

            # Resolve type/tag predicates to current parent item keys whenever
            # local SQLite is available. This makes both dense and sparse legs
            # obey the same live scope and avoids sparse top-N post-filter loss.
            if item_types or tag_conditions:
                allowed_item_keys = self._resolve_semantic_filter_item_keys(
                    item_types=item_types,
                    tags=tag_conditions,
                    group_id=group_id,
                )
                if allowed_item_keys is not None:
                    key_clause = (
                        {"item_key": "__EMPTY_OR_NONEXISTENT_SOURCE_FILTER__"}
                        if not allowed_item_keys
                        else {
                            "item_key": (
                                next(iter(allowed_item_keys))
                                if len(allowed_item_keys) == 1
                                else {"$in": sorted(allowed_item_keys)}
                            )
                        }
                    )
                    where = {"$and": [where, key_clause]} if where else key_clause

            if group_id is not None:
                group_clause = {"group_id": int(group_id)}
                where = {"$and": [where, group_clause]} if where else group_clause
'''
    if "parsed_filters = _source_filters.parse_semantic_filters(filters)" not in work[name]:
        replace_once(name, OLD_SEARCH_FILTERS, NEW_SEARCH_FILTERS, "semantic filter construction")
    elif 'requested_item_keys = parsed_filters["item_keys"]' not in work[name]:
        # Refresh a previously installed block with the item_keys extension.
        replace_once(name, PREV_SEARCH_FILTERS, NEW_SEARCH_FILTERS, "semantic filter item_keys refresh")

    # The first item_keys refresh matched the old block without its trailing
    # group clause, leaving that clause duplicated. Collapse it once and keep
    # future runs idempotent.
    GROUP_CLAUSE = '''            if group_id is not None:
                group_clause = {"group_id": int(group_id)}
                where = {"$and": [where, group_clause]} if where else group_clause
'''
    duplicate_group_clause = GROUP_CLAUSE + "\n" + GROUP_CLAUSE
    if duplicate_group_clause in work[name]:
        replace_once(
            name,
            duplicate_group_clause,
            GROUP_CLAUSE,
            "duplicate group clause cleanup",
        )

    OLD_COLLECTION = '''            # [scoped patch] live collection scope via item_key from local DB
            if collection_key is not None:
                target_keys = self._resolve_collection_item_keys(str(collection_key))
                if target_keys:
                    coll_clause = {"item_key": target_keys[0]} if len(target_keys) == 1 else {"item_key": {"$in": target_keys}}
                else:
                    coll_clause = {"item_key": "__EMPTY_OR_NONEXISTENT_COLLECTION__"}
                where = {"$and": [where, coll_clause]} if where else coll_clause
'''
    NEW_COLLECTION = '''            # [scoped patch] live collection scope via item_key from local DB
            if collection_key is not None:
                target_keys = set(
                    self._resolve_collection_item_keys(str(collection_key))
                )
                allowed_item_keys = (
                    target_keys
                    if allowed_item_keys is None
                    else allowed_item_keys.intersection(target_keys)
                )
                coll_clause = (
                    {"item_key": "__EMPTY_OR_NONEXISTENT_COLLECTION__"}
                    if not allowed_item_keys
                    else {
                        "item_key": (
                            next(iter(allowed_item_keys))
                            if len(allowed_item_keys) == 1
                            else {"$in": sorted(allowed_item_keys)}
                        )
                    }
                )
                where = {"$and": [where, coll_clause]} if where else coll_clause
'''
    if "else allowed_item_keys.intersection(target_keys)" not in work[name]:
        replace_once(name, OLD_COLLECTION, NEW_COLLECTION, "collection key intersection")

    OLD_HYBRID_CALL = '''                results = self._hybrid_search(query, fetch_limit, where, sparse_idx)'''
    NEW_HYBRID_CALL = '''                results = self._hybrid_search(
                    query,
                    fetch_limit,
                    where,
                    sparse_idx,
                    allowed_item_keys=allowed_item_keys,
                )'''
    if OLD_HYBRID_CALL in work[name]:
        replace_once(name, OLD_HYBRID_CALL, NEW_HYBRID_CALL, "hybrid allowed keys")

    OLD_HYBRID_SIGNATURE = '''    def _hybrid_search(self, query: str, fetch_limit: int, where, sparse_idx) -> dict[str, Any]:'''
    NEW_HYBRID_SIGNATURE = '''    def _hybrid_search(
        self,
        query: str,
        fetch_limit: int,
        where,
        sparse_idx,
        allowed_item_keys: set[str] | None = None,
    ) -> dict[str, Any]:'''
    if OLD_HYBRID_SIGNATURE in work[name]:
        replace_once(name, OLD_HYBRID_SIGNATURE, NEW_HYBRID_SIGNATURE, "hybrid signature")

    OLD_SPARSE_SEARCH = '''        sparse_hits = sparse_idx.search(query, top_n=max(fetch_limit * 2, 20))'''
    NEW_SPARSE_SEARCH = '''        sparse_hits = sparse_idx.search(
            query,
            top_n=max(fetch_limit * 2, 20),
            allowed_item_keys=allowed_item_keys,
        )'''
    if OLD_SPARSE_SEARCH in work[name]:
        replace_once(name, OLD_SPARSE_SEARCH, NEW_SPARSE_SEARCH, "sparse allowed keys")

    OLD_FIGURE_SPARSE_SEARCH = '''                doc_id for doc_id, _ in sparse_idx.search("Figure Schema", top_n=30)'''
    NEW_FIGURE_SPARSE_SEARCH = '''                doc_id
                for doc_id, _ in sparse_idx.search(
                    "Figure Schema",
                    top_n=30,
                    allowed_item_keys=allowed_item_keys,
                )'''
    if OLD_FIGURE_SPARSE_SEARCH in work[name]:
        replace_once(
            name,
            OLD_FIGURE_SPARSE_SEARCH,
            NEW_FIGURE_SPARSE_SEARCH,
            "figure sparse allowed keys",
        )

    OLD_ENRICH_END = '''        self._attach_zotero_items(enriched)
        return enriched
'''
    NEW_ENRICH_END = '''        self._attach_zotero_items(enriched)
        for result in enriched:
            item = result.get("zotero_item") or {}
            item_data = item.get("data", {}) if isinstance(item, dict) else {}
            item_type = item_data.get("itemType") or (
                (result.get("metadata") or {}).get("item_type")
                if isinstance(result.get("metadata"), dict)
                else None
            )
            result["source_group"] = _source_filters.source_group_for_item_type(
                item_type
            )
        return enriched
'''
    if 'result["source_group"] = _source_filters.source_group_for_item_type' not in work[name]:
        replace_once(name, OLD_ENRICH_END, NEW_ENRICH_END, "source-group result enrichment")

# 3. Public semantic-search filter schema and source-group output.
if "tools/search.py" in work:
    name = "tools/search.py"

    TAG_FILTER_HELPERS = '''# [source filters patch] Enforce the documented tag DSL independently of
# Zotero API quirks. API parameters are only a narrowing hint; returned items
# are checked locally so `` OR ``, negative tags, and ``-itemType`` cannot leak
# false positives.
_TAG_FILTER_OR = re.compile(r"\\s+OR\\s+|\\|\\|")


def _item_matches_tag_filter(item: dict, conditions: list[str]) -> bool:
    data = item.get("data", item) if isinstance(item, dict) else {}
    names = {
        str(entry.get("tag", "") if isinstance(entry, dict) else entry).casefold()
        for entry in (data.get("tags") or [])
        if str(entry.get("tag", "") if isinstance(entry, dict) else entry).strip()
    }
    for condition in conditions:
        matched_terms: list[bool] = []
        for raw_term in _TAG_FILTER_OR.split(condition):
            term = raw_term.strip()
            if not term:
                continue
            negated = term.startswith("-")
            value = (term[1:] if negated else term).strip().casefold()
            if not value:
                continue
            present = value in names
            matched_terms.append(not present if negated else present)
        if matched_terms and not any(matched_terms):
            return False
    return True


def _item_matches_type_filter(item: dict, item_type: str) -> bool:
    if not item_type:
        return True
    data = item.get("data", item) if isinstance(item, dict) else {}
    actual = str(data.get("itemType", ""))
    if item_type.startswith("-") and item_type.count("-") == 1:
        return actual != item_type[1:]
    return actual == item_type


def _api_tag_narrowing(conditions: list[str]) -> list[str]:
    """Return only positive conditions, translated to Zotero's ``||`` DSL."""
    narrowed: list[str] = []
    for condition in conditions:
        terms = [term.strip() for term in _TAG_FILTER_OR.split(condition) if term.strip()]
        if terms and all(not term.startswith("-") for term in terms):
            narrowed.append(" || ".join(terms))
    return narrowed


def _fetch_tag_filtered_pagewise(
    method,
    *args,
    conditions: list[str],
    item_type: str,
    limit: int,
) -> list[dict]:
    """Fetch until ``limit`` verified matches are found or the API is exhausted."""
    results: list[dict] = []
    start = 0
    page_size = 100
    api_tags = _api_tag_narrowing(conditions)
    api_item_type = item_type if item_type and not item_type.startswith("-") else None
    while len(results) < limit:
        kwargs: dict[str, Any] = {"start": start, "limit": page_size}
        if api_tags:
            kwargs["tag"] = api_tags
        if api_item_type:
            kwargs["itemType"] = api_item_type
        batch = method(*args, **kwargs)
        if not batch:
            break
        for item in batch:
            if (
                _item_matches_tag_filter(item, conditions)
                and _item_matches_type_filter(item, item_type)
            ):
                results.append(item)
                if len(results) >= limit:
                    break
        if len(batch) < page_size:
            break
        start += page_size
    return results


'''
    if "def _fetch_tag_filtered_pagewise(" not in work[name]:
        insert_before_once(
            name,
            '@mcp.tool(\n    name="zotero_search_by_tag",\n',
            TAG_FILTER_HELPERS,
            "tag-filter helpers",
        )

    OLD_TAG_SEARCH = '''        # Search library-wide or scoped to a collection
        if collection_key:
            try:
                _col = zot.collection(collection_key)
            except Exception:
                _col = None
            if not _col or _col.get("key") != collection_key:
                return f"Collection not found: '{collection_key}'. Use zotero_get_collections or zotero_search_collections to find valid collection keys."
            scope_keys = _helpers.expand_collection_scope(
                zot, collection_key, include_subcollections
            )
            results = []
            _seen: set[str] = set()
            for _scope_key in scope_keys:
                for _item in _helpers._paginate(
                    zot.collection_items, _scope_key,
                    tag=tag, itemType=item_type, max_items=limit,
                ):
                    _key = _item.get("key")
                    if _key and _key in _seen:
                        continue
                    if _key:
                        _seen.add(_key)
                    results.append(_item)
            results = results[:limit]
        else:
            zot.add_parameters(q="", tag=tag, itemType=item_type, limit=limit)
            results = zot.items()
'''
    NEW_TAG_SEARCH = '''        # Search library-wide or scoped to a collection. Zotero's API is used
        # only to narrow positive predicates; verify the complete DSL locally.
        if collection_key:
            try:
                _col = zot.collection(collection_key)
            except Exception:
                _col = None
            if not _col or _col.get("key") != collection_key:
                return f"Collection not found: '{collection_key}'. Use zotero_get_collections or zotero_search_collections to find valid collection keys."
            scope_keys = _helpers.expand_collection_scope(
                zot, collection_key, include_subcollections
            )
            results = []
            _seen: set[str] = set()
            for _scope_key in scope_keys:
                remaining = limit - len(results)
                if remaining <= 0:
                    break
                for _item in _fetch_tag_filtered_pagewise(
                    zot.collection_items,
                    _scope_key,
                    conditions=tag,
                    item_type=item_type,
                    limit=remaining,
                ):
                    _key = _item.get("key")
                    if _key and _key in _seen:
                        continue
                    if _key:
                        _seen.add(_key)
                    results.append(_item)
        else:
            results = _fetch_tag_filtered_pagewise(
                zot.items,
                conditions=tag,
                item_type=item_type,
                limit=limit,
            )
'''
    if "results = _fetch_tag_filtered_pagewise(" not in work[name]:
        replace_once(name, OLD_TAG_SEARCH, NEW_TAG_SEARCH, "strict tag search")

    if "from typing import Any, Literal" not in work[name]:
        replace_once(
            name,
            "from typing import Literal\n",
            "from typing import Any, Literal\n",
            "typing import",
        )

    if "tag-only filter is valid." not in work[name]:
        replace_once(
            name,
            "        \"filters: optional metadata filters as a dict (e.g. \"\n"
            "        \"{'itemType': 'journalArticle', 'year': '2023'}); also accepts a \"\n"
            "        \"JSON string. \"\n",
            "        \"filters: optional metadata filters as a dict (e.g. \"\n"
            "        \"{'itemType': 'journalArticle', 'year': '2023'}); also accepts a \"\n"
            "        \"JSON string. It also accepts source_group/source_groups, \"\n"
            "        \"item_type/item_types, and tag/tags/required_tags; these filters \"\n"
            "        \"are optional and combine with AND. A tag-only filter is valid. \"\n",
            "semantic filter description",
        )
    elif "item_key/item_keys" not in work[name]:
        # Refresh the description with the exact parent-item identity scope.
        replace_once(
            name,
            "        \"JSON string. It also accepts source_group/source_groups, \"\n"
            "        \"item_type/item_types, and tag/tags/required_tags; these filters \"\n",
            "        \"JSON string. It also accepts source_group/source_groups, \"\n"
            "        \"item_type/item_types, item_key/item_keys (exact parent-item \"\n"
            "        \"identity scope, e.g. from zotero_resolve_exact_source), and \"\n"
            "        \"tag/tags/required_tags; these filters \"\n",
            "semantic filter item_keys description",
        )

    if "filters: dict[str, Any] | str | None = None," not in work[name]:
        replace_once(
            name,
            "    filters: dict[str, str] | str | None = None,\n",
            "    filters: dict[str, Any] | str | None = None,\n",
            "semantic filter annotation",
        )

    if "filters: Optional metadata filters as dict or JSON string. Example: {\"item_type\": \"note\"}" in work[name]:
        replace_once(
            name,
            "filters: Optional metadata filters as dict or JSON string. Example: {\"item_type\": \"note\"}",
            "filters: Optional metadata filters as dict or JSON string. Supports native item_type/item_types, derived source_group/source_groups, exact item_key/item_keys parent-item scope, and tag/tags/required_tags. These are independent optional filters and may be used alone or together.",
            "semantic filter docstring",
        )
    elif "exact item_key/item_keys parent-item scope" not in work[name] and "derived source_group/source_groups, and tag/tags/required_tags" in work[name]:
        replace_once(
            name,
            "filters: Optional metadata filters as dict or JSON string. Supports native item_type/item_types, derived source_group/source_groups, and tag/tags/required_tags. These are independent optional filters and may be used alone or together.",
            "filters: Optional metadata filters as dict or JSON string. Supports native item_type/item_types, derived source_group/source_groups, exact item_key/item_keys parent-item scope, and tag/tags/required_tags. These are independent optional filters and may be used alone or together.",
            "semantic filter docstring refresh",
        )

    OLD_EXTRA = '''                if result.get("is_reference"):
                    extra["REF"] = (
                        "bibliography entry — use zotero_search_references; "
                        "do not cite as substantive evidence"
                    )
                if loc_bits:
'''
    NEW_EXTRA = '''                if result.get("is_reference"):
                    extra["REF"] = (
                        "bibliography entry — use zotero_search_references; "
                        "do not cite as substantive evidence"
                    )
                if result.get("source_group"):
                    extra["Source Group"] = result["source_group"]
                if loc_bits:
'''
    if 'extra["Source Group"]' not in work[name]:
        replace_once(name, OLD_EXTRA, NEW_EXTRA, "source-group output")

    OLD_FALLBACK = '''                if result.get("is_reference"):
                    output.append(
                        "**REF:** bibliography entry — use zotero_search_references; "
                        "do not cite as substantive evidence"
                    )
                if loc_bits:
'''
    NEW_FALLBACK = '''                if result.get("is_reference"):
                    output.append(
                        "**REF:** bibliography entry — use zotero_search_references; "
                        "do not cite as substantive evidence"
                    )
                if result.get("source_group"):
                    output.append(f"**Source Group:** {result['source_group']}")
                if loc_bits:
'''
    if "**Source Group:** {result['source_group']}" not in work[name]:
        replace_once(name, OLD_FALLBACK, NEW_FALLBACK, "fallback source-group output")

if errors:
    print("mismatch", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

changed = [name for name in work if work[name] != original[name]]
for name in changed:
    path = paths[name]
    tmp = path.with_name(path.name + ".source-filters-patch.tmp")
    tmp.write_text(work[name], encoding="utf-8")
    tmp.replace(path)

# Copy the companion module only after all anchors have passed.
destination_module = pkg / "source_filters.py"
module_changed = False
if source_module.exists() and (
    not destination_module.exists()
    or destination_module.read_bytes() != source_module.read_bytes()
):
    tmp = destination_module.with_name(destination_module.name + ".source-filters-patch.tmp")
    shutil.copy2(source_module, tmp)
    tmp.replace(destination_module)
    module_changed = True

print("applied" if changed or module_changed else "already")
