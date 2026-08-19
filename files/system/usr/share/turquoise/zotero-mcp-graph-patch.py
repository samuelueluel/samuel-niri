#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp Deterministic Citation Graph patch.

Adds:
- citation_graph.py (copied from zotero-mcp-graph.py)
- zotero_get_collection_hubs
- zotero_get_paper_lineage
- zotero_find_connected_papers
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

errors: list[str] = []
changed = False

MARKER = "[graph patch]"

# 1. Copy citation_graph.py
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


@mcp.tool()
def zotero_get_collection_hubs(
    collection_key: str = "",
    top_n: int = 5,
    ctx: Context = None,
) -> str:
    """Find the foundational hub papers cited across a Zotero collection (or whole library).

    Computes inward citation in-degree across papers in the collection.

    Args:
        collection_key: 8-character collection key (e.g. TRGBCDX5), or empty for whole library.
        top_n: Number of hub papers to return (default: 5).
    """
    try:
        g = _get_graph()
        hubs = g.get_collection_hubs(collection_key, top_n=top_n)
        if not hubs:
            return f"No hub papers found for collection {collection_key or 'all'} (graph may need rebuild via update-db)."

        scope = f"collection {collection_key}" if collection_key else "entire library"
        lines = [f"# Foundational Hub Papers ({scope})\n"]
        for i, h in enumerate(hubs, 1):
            yr = f" ({h['year']})" if h['year'] else ""
            au = f" — *{h['creators']}*" if h['creators'] else ""
            lines.append(f"{i}. **{h['title']}**{yr}{au}")
            lines.append(f"   - Key: `{h['item_key']}` | Inward citations in library: **{h['inward_citations']}**")
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving collection hubs: {e}"


@mcp.tool()
def zotero_get_paper_lineage(
    item_key: str,
    depth: int = 1,
    ctx: Context = None,
) -> str:
    """Trace what a paper builds on (ancestor citations) and what subsequent papers cite it (descendants).

    Args:
        item_key: 8-character item key of the paper.
        depth: Traversal depth (default: 1).
    """
    try:
        g = _get_graph()
        data = g.get_paper_lineage(item_key, depth=depth)
        if "error" in data:
            return f"Error: {data['error']}"

        target = data["target_paper"]
        t_yr = f" ({target['year']})" if target['year'] else ""
        t_au = f" — *{target['creators']}*" if target['creators'] else ""

        lines = [f"# Citation Lineage for **{target['title']}**{t_yr}{t_au} (`{item_key}`)\n"]

        lines.append(f"## Foundational Papers Cited ({len(data['cites'])} in-library ancestors)")
        if data["cites"]:
            for i, c in enumerate(data["cites"], 1):
                yr = f" ({c['year']})" if c['year'] else ""
                au = f" — *{c['creators']}*" if c['creators'] else ""
                lines.append(f"{i}. **{c['title']}**{yr}{au} (`{c['item_key']}`)")
        else:
            lines.append("  (No cited papers found in local library)")

        lines.append(f"\n## Subsequent In-Library Papers Citing This ({len(data['cited_by'])} descendants)")
        if data["cited_by"]:
            for i, c in enumerate(data["cited_by"], 1):
                yr = f" ({c['year']})" if c['year'] else ""
                au = f" — *{c['creators']}*" if c['creators'] else ""
                lines.append(f"{i}. **{c['title']}**{yr}{au} (`{c['item_key']}`)")
        else:
            lines.append("  (No subsequent papers in local library cite this item)")

        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving paper lineage: {e}"


@mcp.tool()
def zotero_find_connected_papers(
    item_key: str,
    top_n: int = 5,
    ctx: Context = None,
) -> str:
    """Find structurally connected papers in the library via bibliographic coupling (shared citations).

    Identifies papers that cite the same foundational literature, discovering related work
    even if authors use different terminology.

    Args:
        item_key: 8-character item key of the target paper.
        top_n: Number of connected papers to return (default: 5).
    """
    try:
        g = _get_graph()
        connected = g.find_connected_papers(item_key, top_n=top_n)
        if not connected:
            return f"No connected papers found with shared bibliography overlap for `{item_key}`."

        lines = [f"# Structurally Connected Papers for `{item_key}` (Bibliographic Coupling)\n"]
        for i, c in enumerate(connected, 1):
            yr = f" ({c['year']})" if c['year'] else ""
            au = f" — *{c['creators']}*" if c['creators'] else ""
            lines.append(f"{i}. **{c['title']}**{yr}{au} (`{c['item_key']}`)")
            lines.append(f"   - Coupling Jaccard Score: **{c['coupling_score']}** ({c['shared_citations_count']} shared citations)")
            if c["shared_citations"]:
                lines.append(f"   - Key Shared Citations: {'; '.join(c['shared_citations'])}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error finding connected papers: {e}"
'''

if discovery_py.exists():
    src_disc = discovery_py.read_text(encoding="utf-8")
    if MARKER in src_disc:
        # Strip old broken marker block and re-apply cleanly
        src_disc = src_disc.split("# [graph patch]")[0].rstrip() + "\n"
    discovery_py.write_text(src_disc + GRAPH_TOOLS_BLOCK, encoding="utf-8")
    changed = True
else:
    errors.append("tools/discovery.py not found")

# 3. Patch semantic_search.py to auto-build graph on indexing
semantic_search_py = pkg / "semantic_search.py"
if semantic_search_py.exists():
    src_ss = semantic_search_py.read_text(encoding="utf-8")
    if "# [graph patch] build citation graph" not in src_ss:
        target_hook = 'logger.info("Indexing complete!")'
        replacement_hook = """logger.info("Indexing complete!")
        # [graph patch] build citation graph
        try:
            from .citation_graph import CitationGraph
            cg = CitationGraph()
            cg_stats = cg.build()
            logger.info("Citation graph built: %d nodes, %d directed citations", cg_stats.get("nodes", 0), cg_stats.get("directed_citations", 0))
        except Exception as e:
            logger.warning("Failed to build citation graph: %s", e)"""
        if target_hook in src_ss:
            src_ss = src_ss.replace(target_hook, replacement_hook, 1)
            semantic_search_py.write_text(src_ss, encoding="utf-8")
            changed = True
else:
    errors.append("semantic_search.py not found")

if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("applied" if changed else "already")
