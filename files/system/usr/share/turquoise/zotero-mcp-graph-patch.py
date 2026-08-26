#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp Deterministic Citation Graph patch.

Adds:
- reference_parser.py (copied from zotero-mcp-reference-parser.py)
- citation_graph.py (copied from zotero-mcp-graph.py)
- per-entry bibliography audit and DOI-backed external-reference nodes from MinerU sidecars
- zotero_rebuild_citation_graph
- zotero_get_collection_hubs with explicit graph scopes
- zotero_get_paper_lineage with explicit graph scopes
- zotero_find_connected_papers with explicit graph scopes
- automatic graph build during update-db / indexing

Marker comment: "[graph patch]".
Usage: zotero-mcp-graph-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""

import shutil
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
src_graph = here / "zotero-mcp-graph.py"
src_reference_parser = here / "zotero-mcp-reference-parser.py"

errors: list[str] = []
changed = False

MARKER = "[graph patch]"

# 1. Copy the shared entry parser and citation_graph.py.
if not src_reference_parser.exists():
    errors.append(f"source {src_reference_parser} not found")
else:
    dst_reference_parser = pkg / "reference_parser.py"
    try:
        parser_bytes = src_reference_parser.read_bytes()
        if not dst_reference_parser.exists() or dst_reference_parser.read_bytes() != parser_bytes:
            dst_reference_parser.write_bytes(parser_bytes)
            changed = True
    except Exception as e:
        errors.append(f"failed to copy reference_parser.py: {e}")

if not src_graph.exists():
    errors.append(f"source {src_graph} not found")
else:
    dst_graph = pkg / "citation_graph.py"
    try:
        src_bytes = src_graph.read_bytes()
        if not dst_graph.exists() or dst_graph.read_bytes() != src_bytes:
            dst_graph.write_bytes(src_bytes)
            changed = True
    except Exception as e:
        errors.append(f"failed to copy citation_graph.py: {e}")

# 2. Patch tools/discovery.py
discovery_py = pkg / "tools" / "discovery.py"

GRAPH_TOOLS_BLOCK = r'''

# [graph patch] Deterministic Citation Graph tools
from zotero_mcp.citation_graph import CitationGraph

_GRAPH_INSTANCE = None


def _get_graph() -> CitationGraph:
    global _GRAPH_INSTANCE
    if _GRAPH_INSTANCE is None:
        _GRAPH_INSTANCE = CitationGraph()
        _GRAPH_INSTANCE.load()
    return _GRAPH_INSTANCE


def _node_marker(node: dict) -> str:
    return " [external reference]" if node.get("node_type") == "external_reference" else ""


def _scope_label(scope: str, collection_key: str = "") -> str:
    if collection_key:
        return f"{scope}; collection={collection_key}"
    return scope


@mcp.tool()
def zotero_rebuild_citation_graph(ctx: Context = None) -> str:
    """Rebuild the local citation graph from Zotero metadata and MinerU sidecars.

    This is graph-only and does not re-embed ChromaDB or rebuild the semantic
    search database. It also refreshes the process-local graph used by the
    other citation tools.
    """
    global _GRAPH_INSTANCE
    try:
        _GRAPH_INSTANCE = CitationGraph()
        stats = _GRAPH_INSTANCE.build()
        try:
            from zotero_mcp.reference_index import invalidate_reference_index_cache
            invalidate_reference_index_cache()
        except Exception:
            pass
        return (
            "# Citation Graph Rebuilt\n\n"
            f"- Library nodes: **{stats.get('library_nodes', 0)}**\n"
            f"- External-reference nodes: **{stats.get('external_nodes', 0)}**\n"
            f"- Citation edges: **{stats.get('directed_citations', 0)}**\n"
            f"- Resolved citation edges: **{stats.get('resolved_citations', 0)}**\n"
            f"- External citation edges: **{stats.get('external_citations', 0)}**\n"
            f"- Reference evidence records: **{stats.get('reference_evidence', 0)}**\n"
            f"- Parsed reference sections: **{stats.get('reference_sections', 0)}**\n"
            f"- Parsed reference entries: **{stats.get('reference_entries', 0)}**\n"
            f"- DOI-bearing entries: **{stats.get('reference_entries_with_doi', 0)}**\n"
            f"- Resolved entries: **{stats.get('resolved_reference_entries', 0)}**\n"
            f"- External DOI entries: **{stats.get('external_reference_entries', 0)}**\n"
            f"- Ambiguous entries: **{stats.get('ambiguous_reference_entries', 0)}**\n"
            f"- Unresolved entries: **{stats.get('unresolved_reference_entries', 0)}**\n"
            f"- Orphan sidecars: **{stats.get('orphan_reference_sidecars', 0)}** "
            f"({stats.get('orphan_reference_entries', 0)} entries)\n"
            f"- Database: `{stats.get('db_path', '')}`"
        )
    except Exception as e:
        return f"Error rebuilding citation graph: {e}"


@mcp.tool()
def zotero_get_collection_hubs(
    collection_key: str = "",
    top_n: int = 5,
    scope: str = "library",
    ctx: Context = None,
) -> str:
    """Find hub nodes under an explicit collection/library graph scope.

    Scopes are ``collection``, ``library`` (legacy default),
    ``collection-expanded``, and ``library-expanded``. Expanded scopes may
    return external-reference nodes recovered from sidecar bibliographies.

    Args:
        collection_key: Collection key; required for collection scopes.
        top_n: Number of hub nodes to return (default: 5).
        scope: Graph scope (default: library).
    """
    try:
        g = _get_graph()
        hubs = g.get_collection_hubs(collection_key, top_n=top_n, scope=scope)
        if not hubs:
            return f"No hub nodes found for {_scope_label(scope, collection_key)}."

        lines = [f"# Citation Hub Nodes ({_scope_label(scope, collection_key)})\n"]
        for i, hub in enumerate(hubs, 1):
            yr = f" ({hub['year']})" if hub['year'] else ""
            au = f" — *{hub['creators']}*" if hub['creators'] else ""
            marker = _node_marker(hub)
            lines.append(f"{i}. **{hub['title']}**{yr}{au}{marker}")
            lines.append(
                f"   - Key: `{hub['item_key']}` | Inward citations: **{hub['inward_citations']}**"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving citation hubs: {e}"


@mcp.tool()
def zotero_get_paper_lineage(
    item_key: str,
    depth: int = 1,
    scope: str = "library",
    collection_key: str = "",
    ctx: Context = None,
) -> str:
    """Trace citation ancestors and descendants under an explicit graph scope.

    Expanded scopes can expose external-reference nodes. Such nodes represent
    bibliography evidence only; their own lineage requires their full text.

    Args:
        item_key: Zotero item key or external-reference graph key.
        depth: Traversal depth (direct neighbors are currently returned).
        scope: Graph scope (default: library).
        collection_key: Required for collection scopes.
    """
    try:
        g = _get_graph()
        data = g.get_paper_lineage(
            item_key,
            depth=depth,
            scope=scope,
            collection_key=collection_key,
        )
        if "error" in data:
            return f"Error: {data['error']}"

        target = data["target_paper"]
        t_yr = f" ({target['year']})" if target['year'] else ""
        t_au = f" — *{target['creators']}*" if target['creators'] else ""
        marker = _node_marker(target)
        lines = [
            f"# Citation Lineage for **{target['title']}**{t_yr}{t_au}{marker} (`{item_key}`)",
            f"Scope: `{_scope_label(data.get('scope', scope), collection_key)}`\n",
        ]

        lines.append(f"## Papers Cited ({len(data['cites'])})")
        if data["cites"]:
            for i, cited in enumerate(data["cites"], 1):
                yr = f" ({cited['year']})" if cited['year'] else ""
                au = f" — *{cited['creators']}*" if cited['creators'] else ""
                lines.append(
                    f"{i}. **{cited['title']}**{yr}{au}{_node_marker(cited)} (`{cited['item_key']}`)"
                )
        else:
            lines.append("  (No cited nodes found in this scope)")

        lines.append(f"\n## Papers Citing This ({len(data['cited_by'])})")
        if data["cited_by"]:
            for i, citer in enumerate(data["cited_by"], 1):
                yr = f" ({citer['year']})" if citer['year'] else ""
                au = f" — *{citer['creators']}*" if citer['creators'] else ""
                lines.append(
                    f"{i}. **{citer['title']}**{yr}{au}{_node_marker(citer)} (`{citer['item_key']}`)"
                )
        else:
            lines.append("  (No citing nodes found in this scope)")

        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving paper lineage: {e}"


@mcp.tool()
def zotero_find_connected_papers(
    item_key: str,
    top_n: int = 5,
    scope: str = "library",
    collection_key: str = "",
    ctx: Context = None,
) -> str:
    """Find resolved papers connected by shared citations under a graph scope.

    In expanded scopes, external-reference nodes may participate as shared
    citation targets, while result papers remain resolved Zotero items.

    Args:
        item_key: Zotero item key of the target paper.
        top_n: Number of connected papers to return (default: 5).
        scope: Graph scope (default: library).
        collection_key: Required for collection scopes.
    """
    try:
        g = _get_graph()
        connected = g.find_connected_papers(
            item_key,
            top_n=top_n,
            scope=scope,
            collection_key=collection_key,
        )
        if not connected:
            return f"No connected papers found for `{item_key}` in {_scope_label(scope, collection_key)}."

        lines = [
            f"# Structurally Connected Papers for `{item_key}` (Bibliographic Coupling)",
            f"Scope: `{_scope_label(scope, collection_key)}`\n",
        ]
        for i, paper in enumerate(connected, 1):
            yr = f" ({paper['year']})" if paper['year'] else ""
            au = f" — *{paper['creators']}*" if paper['creators'] else ""
            lines.append(f"{i}. **{paper['title']}**{yr}{au} (`{paper['item_key']}`)")
            lines.append(
                f"   - Coupling Jaccard Score: **{paper['coupling_score']}** "
                f"({paper['shared_citations_count']} shared citations)"
            )
            if paper["shared_citations"]:
                lines.append(f"   - Key Shared Citations: {'; '.join(paper['shared_citations'])}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error finding connected papers: {e}"
'''

if discovery_py.exists():
    src_disc = discovery_py.read_text(encoding="utf-8")
    if MARKER in src_disc:
        # Strip only the old graph block. Preserve later independent blocks
        # (notably the reference patch) so patch order remains harmless.
        before_graph, after_graph = src_disc.split("# [graph patch]", 1)
        suffix = ""
        reference_marker = "# [reference patch]"
        if reference_marker in after_graph:
            suffix = "\n\n" + reference_marker + after_graph.split(reference_marker, 1)[1]
        base_disc = before_graph.rstrip() + "\n"
    else:
        suffix = ""
        base_disc = src_disc.rstrip() + "\n"
    desired_disc = base_disc + GRAPH_TOOLS_BLOCK + suffix
    if src_disc != desired_disc:
        discovery_py.write_text(desired_disc, encoding="utf-8")
        changed = True
else:
    errors.append("tools/discovery.py not found")

# 3. Patch the MCP update wrapper to rebuild the graph after indexing.
# This is more stable than patching semantic_search.py internals because the
# wrapper is the public boundary used by `zotero_update_search_database`.
search_tools_py = pkg / "tools" / "search.py"
if search_tools_py.exists():
    src_search = search_tools_py.read_text(encoding="utf-8")
    marker = "# [graph patch] rebuild citation graph after search update"
    if marker not in src_search:
        target_hook = "        # Format results\n"
        replacement_hook = """        # [graph patch] rebuild citation graph after search update
        try:
            from zotero_mcp.citation_graph import CitationGraph
            cg_stats = CitationGraph().build()
            _search_logger.info(
                "Citation graph built: %d nodes, %d directed citations",
                cg_stats.get("nodes", 0),
                cg_stats.get("directed_citations", 0),
            )
        except Exception as e:
            _search_logger.warning("Failed to build citation graph: %s", e)

"""
        if target_hook in src_search:
            src_search = src_search.replace(target_hook, replacement_hook + target_hook, 1)
            search_tools_py.write_text(src_search, encoding="utf-8")
            changed = True
        else:
            print(
                "warning: search.py update wrapper anchor not found; "
                "automatic graph rebuild was not installed",
                file=sys.stderr,
            )
else:
    errors.append("tools/search.py not found")

if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("applied" if changed else "already")
