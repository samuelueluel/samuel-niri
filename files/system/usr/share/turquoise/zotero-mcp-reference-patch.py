#!/usr/bin/env python3
"""Install the Phase-2 bibliography/reference retrieval patch.

The patch adds a separate BM25 index over individual bibliography entries
parsed from local MinerU sidecars. It does not modify ChromaDB documents or call
an embedding model.

Marker comment: "[reference patch]".
Usage: zotero-mcp-reference-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch".
"""

import shutil
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
src_reference = here / "zotero-mcp-reference.py"
src_reference_parser = here / "zotero-mcp-reference-parser.py"

errors: list[str] = []
changed = False
MARKER = "[reference patch]"

REFERENCE_TOOLS_BLOCK = r'''

# [reference patch] bibliography/reference-only BM25 retrieval
from zotero_mcp.reference_index import (
    audit_reference_index,
    build_reference_index,
    search_reference_index,
)


def _reference_scope_label(collection_key: str, item_key: str) -> str:
    if collection_key:
        return f"collection={collection_key}"
    if item_key:
        return f"item={item_key}"
    return "library"


def _reference_marker(result: dict) -> str:
    return "[reference entry]"


@mcp.tool()
def zotero_rebuild_reference_index(ctx: Context = None) -> str:
    """Build the separate BM25 index over individual local bibliography entries.

    This parses MinerU sidecars and joins the graph's per-entry audit data. It
    performs no embedding and does not modify ChromaDB content.
    """
    try:
        stats = build_reference_index()
        status = stats.get("status_counts", {})
        return (
            "# Reference Index Rebuilt\n\n"
            f"- Reference sections: **{stats.get('reference_sections', 0)}**\n"
            f"- Reference entries: **{stats.get('entries', 0)}**\n"
            f"- Source items: **{stats.get('source_items', 0)}** "
            f"(library: **{stats.get('library_source_items', 0)}**; "
            f"orphan sidecar: **{stats.get('orphan_source_items', 0)}**)\n"
            f"- Source sidecars: **{stats.get('source_sidecars', 0)}** "
            f"(orphan: **{stats.get('orphan_source_sidecars', 0)}**)\n"
            f"- Entries with DOI: **{stats.get('doi_entries', 0)}**\n"
            f"- Resolved to Zotero: **{stats.get('resolved_entries', 0)}**\n"
            f"- External DOI entries: **{stats.get('external_doi_entries', 0)}**\n"
            f"- Ambiguous: **{stats.get('ambiguous_entries', 0)}**\n"
            f"- Unresolved: **{stats.get('unresolved_entries', 0)}**\n"
            f"- Orphan-source entries: **{stats.get('orphan_source_entries', 0)}**\n"
            f"- Status counts: `{status}`\n"
            f"- BM25 documents: **{stats.get('docs', 0)}**\n"
            f"- Terms: **{stats.get('terms', 0)}**\n"
            f"- Index: `{stats.get('path', '')}`\n"
            f"- Metadata: `{stats.get('metadata_path', '')}`\n"
            f"- Built: `{stats.get('built_at', '')}`"
        )
    except Exception as e:
        return f"Error rebuilding reference index: {e}"


@mcp.tool()
def zotero_audit_references(ctx: Context = None) -> str:
    """Report parsed bibliography coverage and resolution status."""
    try:
        stats = audit_reference_index()
        return (
            "# Bibliography Reference Audit\n\n"
            f"- Reference sections: **{stats.get('reference_sections', 0)}**\n"
            f"- Reference entries: **{stats.get('entries', 0)}**\n"
            f"- Source items: **{stats.get('source_items', 0)}** "
            f"(library: **{stats.get('library_source_items', 0)}**; "
            f"orphan sidecar: **{stats.get('orphan_source_items', 0)}**)\n"
            f"- Source sidecars: **{stats.get('source_sidecars', 0)}** "
            f"(orphan: **{stats.get('orphan_source_sidecars', 0)}**)\n"
            f"- Entries with DOI: **{stats.get('doi_entries', 0)}**\n"
            f"- Resolved to Zotero: **{stats.get('resolved_entries', 0)}**\n"
            f"- External DOI entries: **{stats.get('external_doi_entries', 0)}**\n"
            f"- Mixed entries: **{stats.get('mixed_entries', 0)}**\n"
            f"- Ambiguous: **{stats.get('ambiguous_entries', 0)}**\n"
            f"- Unresolved: **{stats.get('unresolved_entries', 0)}**\n"
            f"- Orphan-source entries: **{stats.get('orphan_source_entries', 0)}**\n"
            f"- Source item types: `{stats.get('source_item_types', {})}`\n"
            f"- Split methods: `{stats.get('split_methods', {})}`\n"
            f"- Status counts: `{stats.get('status_counts', {})}`\n"
            f"- Built: `{stats.get('built_at', '')}`"
        )
    except Exception as e:
        return f"Error auditing bibliography references: {e}"


@mcp.tool()
def zotero_search_references(
    query: str,
    limit: int = 10,
    collection_key: str = "",
    item_key: str = "",
    ctx: Context = None,
) -> str:
    """Search individual bibliography entries separately from content RAG.

    Uses BM25 over parsed local MinerU sidecar entries. Results include
    bibliographic metadata and graph-resolution status, not evidence of the
    cited paper's substantive findings.

    Args:
        query: Author, title, year, DOI, or other reference text to search.
        limit: Maximum number of reference entries to return (default: 10).
        collection_key: Optional source collection key to restrict the search.
        item_key: Optional citing Zotero item key to restrict the search.
    """
    try:
        limit = max(1, min(int(limit), 100))
        hits = search_reference_index(
            query,
            top_n=limit,
            collection_key=collection_key or None,
            item_key=item_key or None,
        )
        if not hits:
            return (
                f"No reference entries matched `{query}` in "
                f"{_reference_scope_label(collection_key, item_key)}."
            )

        lines = [
            f"# Reference Search: `{query}`",
            f"Scope: `{_reference_scope_label(collection_key, item_key)}`\n",
        ]
        for number, hit in enumerate(hits, 1):
            source_title = hit.get("source_title") or hit.get("source_key") or "Untitled source"
            lines.append(f"{number}. **{source_title}** {_reference_marker(hit)}")
            lines.append(
                f"   - Citing item: `{hit.get('source_key') or hit.get('citing_item_key', '')}`"
                f" | Entry: `{hit.get('entry_index', '')}` | BM25: **{hit.get('score', 0)}**"
            )
            lines.append(
                f"   - Source status: `{hit.get('source_status', 'unknown')}`"
                f" | Item type: `{hit.get('source_item_type', 'unknown')}`"
            )
            if hit.get("source_sidecar"):
                lines.append(f"   - Source sidecar: `{hit['source_sidecar']}`")
            if hit.get("section_heading"):
                lines.append(
                    f"   - Section: `{hit['section_heading']}`"
                    f" (#{hit.get('section_index', '')}); split: `{hit.get('split_method', '')}`"
                )
            if hit.get("dois"):
                lines.append(f"   - DOI(s): `{', '.join(hit['dois'])}`")
            lines.append(
                f"   - Resolution: `{hit.get('target_status', 'unresolved')}`"
                f" via `{hit.get('match_method', 'unresolved')}`"
                f" (confidence: **{hit.get('confidence', 0.0)}**;"
                f" parse: **{hit.get('parse_confidence', 0.0)}**)")
            if hit.get("target_keys"):
                lines.append(f"   - Target key(s): `{', '.join(hit['target_keys'])}`")
            if hit.get("target_types"):
                lines.append(f"   - Target type(s): `{', '.join(hit['target_types'])}`")
            if hit.get("collections"):
                lines.append(f"   - Collections: `{', '.join(hit['collections'])}`")
            lines.append("   - Raw reference:")
            lines.append(f"     > {hit.get('raw_reference', '')}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error searching bibliography references: {e}"
'''

# 1. Copy the shared entry parser and reference-index module.
dst_reference_parser = pkg / "reference_parser.py"
if src_reference_parser.exists():
    if (
        not dst_reference_parser.exists()
        or dst_reference_parser.read_bytes() != src_reference_parser.read_bytes()
    ):
        shutil.copy2(src_reference_parser, dst_reference_parser)
        changed = True
else:
    errors.append(f"reference-parser source {src_reference_parser} not found")

dst_reference = pkg / "reference_index.py"
if src_reference.exists():
    if not dst_reference.exists() or dst_reference.read_bytes() != src_reference.read_bytes():
        shutil.copy2(src_reference, dst_reference)
        changed = True
else:
    errors.append(f"reference-index source {src_reference} not found")

# 2. Register the reference tools in discovery.py.
discovery_py = pkg / "tools" / "discovery.py"
if discovery_py.exists():
    src_disc = discovery_py.read_text(encoding="utf-8")
    if MARKER in src_disc:
        base_disc = src_disc.split("# [reference patch]", 1)[0].rstrip() + "\n"
    else:
        base_disc = src_disc.rstrip() + "\n"
    desired_disc = base_disc + REFERENCE_TOOLS_BLOCK
    if src_disc != desired_disc:
        discovery_py.write_text(desired_disc, encoding="utf-8")
        changed = True
else:
    errors.append("tools/discovery.py not found")

# 3. Rebuild the reference index after the public semantic update wrapper.
search_py = pkg / "tools" / "search.py"
if search_py.exists():
    src_search = search_py.read_text(encoding="utf-8")
    marker = "# [reference patch] rebuild reference index after search update"
    if marker not in src_search:
        target_hook = "        # Format results\n"
        replacement_hook = """        # [reference patch] rebuild reference index after search update
        try:
            from zotero_mcp.reference_index import build_reference_index
            _reference_stats = build_reference_index()
            _search_logger.info(
                "Reference index built: %d entries, %d source items",
                _reference_stats.get("entries", 0),
                _reference_stats.get("source_items", 0),
            )
        except Exception as e:
            _search_logger.warning("Failed to build reference index: %s", e)

"""
        if target_hook in src_search:
            src_search = src_search.replace(target_hook, replacement_hook + target_hook, 1)
            search_py.write_text(src_search, encoding="utf-8")
            changed = True
        else:
            print(
                "warning: search.py update wrapper anchor not found; "
                "automatic reference-index rebuild was not installed",
                file=sys.stderr,
            )
    else:
        old_hook = """        # [reference patch] rebuild reference index after search update
        try:
            from zotero_mcp.reference_index import build_reference_index
            _reference_stats = build_reference_index()
            _search_logger.info(
                "Reference index built: %d chunks, %d source items",
                _reference_stats.get("docs", 0),
                _reference_stats.get("source_items", 0),
            )
        except Exception as e:
            _search_logger.warning("Failed to build reference index: %s", e)

"""
        replacement_hook = """        # [reference patch] rebuild reference index after search update
        try:
            from zotero_mcp.reference_index import build_reference_index
            _reference_stats = build_reference_index()
            _search_logger.info(
                "Reference index built: %d entries, %d source items",
                _reference_stats.get("entries", 0),
                _reference_stats.get("source_items", 0),
            )
        except Exception as e:
            _search_logger.warning("Failed to build reference index: %s", e)

"""
        if old_hook in src_search and src_search != src_search.replace(old_hook, replacement_hook, 1):
            src_search = src_search.replace(old_hook, replacement_hook, 1)
            search_py.write_text(src_search, encoding="utf-8")
            changed = True
else:
    errors.append("tools/search.py not found")

if errors:
    print("mismatch")
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

print("applied" if changed else "already")
