#!/usr/bin/env python3
"""Install the strict exact-source resolver into zotero-mcp.

The resolver is intentionally separate from ``zotero_search_items``.  The
existing search tool is a discovery tool with a documented fallback cascade;
this patch adds a metadata-only identity tool whose result explicitly
separates exact, ambiguous, and related records.

The transformation is idempotent, validates all anchors before writing, and
copies the companion module only after the wrapper patch is valid.

Usage: zotero-mcp-exact-resolver-patch.py <path/to/zotero_mcp-package-dir>
Prints: ``applied`` | ``already`` | ``mismatch`` (mismatch exits 1).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


if len(sys.argv) != 2:
    print(
        "usage: zotero-mcp-exact-resolver-patch.py <zotero_mcp-package-dir>",
        file=sys.stderr,
    )
    sys.exit(2)

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
source_module = here / "zotero-mcp-exact-resolver.py"
search_path = pkg / "tools" / "search.py"
destination_module = pkg / "exact_resolver.py"
errors: list[str] = []
changed = False

if not source_module.exists():
    errors.append("zotero-mcp-exact-resolver.py not found next to patch")
if not search_path.exists():
    errors.append("tools/search.py not found")

search_source = search_path.read_text(encoding="utf-8") if search_path.exists() else ""
source_module_text = (
    source_module.read_text(encoding="utf-8") if source_module.exists() else ""
)

MARKER = "[exact resolver patch]"

# Keep the wrapper small: implementation lives in the copied companion module
# so it can be tested and updated without a fragile multi-hundred-line search
# module replacement.  Importing it from search.py is sufficient for tools/
# __init__.py's existing decorator-side-effect registration.
WRAPPER_IMPORT = (
    "from zotero_mcp import exact_resolver as _exact_resolver  "
    "# [exact resolver patch]\n"
)
WRAPPER = '''
@mcp.tool(
    name="zotero_resolve_exact_source",
    description=(
        "Resolve a named Zotero source by exact metadata without semantic "
        "fallback. Returns a JSON object with identity_status: exact, "
        "ambiguous, or absent; exact_matches; ambiguous_matches; "
        "related_matches; conflicts; match_basis; and collection_scope. "
        "Each match summary includes in_requested_scope and scope_basis "
        "when interpreting requested collection membership. "
        "Use this before substantive retrieval when the user names a "
        "specific title, DOI, citation key, author/year, or Zotero item key. "
        "The source argument is the original identifier or request; pass "
        "title, author, year, doi, citation_key, or item_key explicitly when "
        "available. Related records are never exact matches. This tool does "
        "not perform semantic search, full-text retrieval, or the ordinary "
        "search_items fallback cascade. collection_key optionally restricts "
        "membership, and include_subcollections defaults to True, matching "
        "semantic collection-scope behavior. "
        "search_all_libraries requires local SQLite and cannot be combined "
        "with collection_key or item_key."
    )
)
@with_zotero_api_lock
def resolve_exact_source(
    source: str,
    identifier_type: Literal["auto", "title", "doi", "citation_key", "item_key"] = "auto",
    title: str | None = None,
    author: str | None = None,
    year: str | None = None,
    doi: str | None = None,
    citation_key: str | None = None,
    item_key: str | None = None,
    collection_key: str | None = None,
    include_subcollections: bool = True,
    search_all_libraries: bool = False,
    limit: int | str | None = 20,
    *,
    ctx: Context,
) -> str:
    """Resolve a named source's identity using metadata only."""
    return _exact_resolver.resolve_exact_source(
        source=source,
        identifier_type=identifier_type,
        title=title,
        author=author,
        year=year,
        doi=doi,
        citation_key=citation_key,
        item_key=item_key,
        collection_key=collection_key,
        include_subcollections=include_subcollections,
        search_all_libraries=search_all_libraries,
        limit=limit,
        ctx=ctx,
    )

'''

if search_source and MARKER not in search_source:
    import_anchor = "from zotero_mcp import utils as _utils\n"
    decorator_anchor = '@mcp.tool(\n    name="zotero_search_by_tag",\n'
    if search_source.count(import_anchor) != 1:
        errors.append(
            f"tools/search.py import anchor count={search_source.count(import_anchor)}"
        )
    if search_source.count(decorator_anchor) != 1:
        errors.append(
            "tools/search.py search_by_tag decorator anchor count="
            f"{search_source.count(decorator_anchor)}"
        )
    if not errors:
        search_source = search_source.replace(
            import_anchor, import_anchor + WRAPPER_IMPORT, 1
        )
        search_source = search_source.replace(
            decorator_anchor, WRAPPER + decorator_anchor, 1
        )
        changed = True
elif search_source and MARKER in search_source:
    # A partially upgraded package must still contain the companion import;
    # otherwise the decorator wrapper would fail at server import time. Also
    # refresh our fully managed wrapper so later patch revisions (schema,
    # defaults, or description) reach an already-patched installation.
    if WRAPPER_IMPORT not in search_source:
        errors.append("tools/search.py has exact resolver marker but missing import")
    wrapper_start = search_source.find('\n@mcp.tool(\n    name="zotero_resolve_exact_source",')
    wrapper_end = search_source.find(
        '@mcp.tool(\n    name="zotero_search_by_tag",',
        wrapper_start + 1,
    )
    if wrapper_start < 0 or wrapper_end < 0:
        errors.append("tools/search.py has exact resolver marker but wrapper anchors are missing")
    else:
        managed_wrapper = WRAPPER.lstrip("\n")
        if search_source[wrapper_start + 1 : wrapper_end] != managed_wrapper:
            search_source = (
                search_source[: wrapper_start + 1]
                + managed_wrapper
                + search_source[wrapper_end:]
            )
            changed = True

if errors:
    print("mismatch", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

if search_source and changed:
    tmp = search_path.with_name(search_path.name + ".exact-resolver-patch.tmp")
    tmp.write_text(search_source, encoding="utf-8")
    tmp.replace(search_path)

if source_module.exists() and (
    not destination_module.exists()
    or destination_module.read_text(encoding="utf-8") != source_module_text
):
    tmp = destination_module.with_name(destination_module.name + ".exact-resolver-patch.tmp")
    shutil.copy2(source_module, tmp)
    tmp.replace(destination_module)
    changed = True

print("applied" if changed else "already")
