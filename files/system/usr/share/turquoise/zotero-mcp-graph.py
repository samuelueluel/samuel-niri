"""Deterministic Academic Citation & Co-Citation Graph for zotero-mcp.

Extracts ground-truth citation, authorship, and collection relationships
directly from local Zotero SQLite metadata and MinerU bibliography sidecars ([REF]).
Maintains an in-memory NetworkX directed graph for fast topological queries:
- Collection Hubs (In-Degree / PageRank)
- Methodological Lineage (Ancestor / Descendant chains)
- Connected Papers (Bibliographic Coupling / Co-Citation overlap)

Zero LLM indexing cost, zero hallucinated edges, instant local rebuilds.
"""

import json
import logging
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import networkx as nx

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_DB_PATH = Path.home() / ".config" / "zotero-mcp" / "citation_graph.sqlite"
DEFAULT_SIDECAR_DIR = Path.home() / ".config" / "zotero-mcp" / "mineru-sidecars"
DEFAULT_ZOTERO_DB = Path.home() / "Zotero" / "zotero.sqlite"

_STOPWORDS = {
    "with", "from", "that", "this", "what", "where", "when", "using",
    "evidence", "effect", "effects", "impact", "impacts", "journal",
    "economics", "review", "economic", "paper", "working", "series",
    "study", "analysis", "empirical", "model", "models", "approach",
}


@dataclass
class GraphNode:
    item_key: str
    title: str
    creators: str
    year: str
    citekey: str
    doi: str
    collections: list[str]


class CitationGraph:
    """Deterministic Citation Graph over a local Zotero library."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_GRAPH_DB_PATH
        self.graph = nx.DiGraph()
        self._loaded = False

    # -- Building -------------------------------------------------------------
    def build(
        self,
        zotero_db_path: Optional[Path | str] = None,
        sidecar_dir: Optional[Path | str] = None,
    ) -> dict[str, Any]:
        """Build the citation graph from Zotero SQLite + MinerU sidecars."""
        z_path = Path(zotero_db_path) if zotero_db_path else DEFAULT_ZOTERO_DB
        sc_dir = Path(sidecar_dir) if sidecar_dir else DEFAULT_SIDECAR_DIR

        if not z_path.exists():
            raise FileNotFoundError(f"Zotero database not found at {z_path}")

        # 1. Read library items from Zotero SQLite (immutable mode bypasses locks)
        uri = f"file:{z_path.resolve()}?immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Query items
        query_items = """
        SELECT
            i.itemID,
            i.key,
            (SELECT value FROM itemDataValues JOIN itemData ON itemData.valueID = itemDataValues.valueID JOIN fields ON fields.fieldID = itemData.fieldID WHERE itemData.itemID = i.itemID AND fields.fieldName = 'title') as title,
            (SELECT value FROM itemDataValues JOIN itemData ON itemData.valueID = itemDataValues.valueID JOIN fields ON fields.fieldID = itemData.fieldID WHERE itemData.itemID = i.itemID AND fields.fieldName = 'date') as date,
            (SELECT value FROM itemDataValues JOIN itemData ON itemData.valueID = itemDataValues.valueID JOIN fields ON fields.fieldID = itemData.fieldID WHERE itemData.itemID = i.itemID AND fields.fieldName = 'extra') as extra,
            (SELECT value FROM itemDataValues JOIN itemData ON itemData.valueID = itemDataValues.valueID JOIN fields ON fields.fieldID = itemData.fieldID WHERE itemData.itemID = i.itemID AND fields.fieldName = 'DOI') as doi,
            (SELECT GROUP_CONCAT(CASE WHEN c.firstName IS NOT NULL AND c.lastName IS NOT NULL THEN c.lastName || ', ' || c.firstName WHEN c.lastName IS NOT NULL THEN c.lastName ELSE c.firstName END, '; ')
             FROM itemCreators ic
             JOIN creators c ON ic.creatorID = c.creatorID
             WHERE ic.itemID = i.itemID
             ORDER BY ic.orderIndex) as creators
        FROM items i
        WHERE i.itemTypeID NOT IN (1, 14)  -- exclude attachments and standalone notes
        """
        cur.execute(query_items)
        rows = cur.fetchall()

        # Query collections for items
        query_cols = """
        SELECT i.key as item_key, c.key as collection_key
        FROM collectionItems ci
        JOIN items i ON ci.itemID = i.itemID
        JOIN collections c ON ci.collectionID = c.collectionID
        """
        cur.execute(query_cols)
        item_cols = defaultdict(list)
        for r in cur.fetchall():
            item_cols[r["item_key"]].append(r["collection_key"])

        conn.close()

        nodes: dict[str, GraphNode] = {}
        by_title_words: list[tuple[set[str], str, str, list[str], str]] = []
        by_doi: dict[str, str] = {}
        by_citekey: dict[str, str] = {}

        for r in rows:
            key = r["key"]
            title = (r["title"] or "").strip()
            creators = (r["creators"] or "").strip()
            extra = (r["extra"] or "").strip()
            doi = (r["doi"] or "").strip().lower()
            date_str = (r["date"] or "").strip()

            # Year resolution
            year = ""
            m_year = re.search(r"\b(19\d\d|20\d\d)\b", date_str or extra)
            if m_year:
                year = m_year.group(1)

            # Citekey resolution from Extra
            citekey = ""
            m_ck = re.search(r"Citation Key:\s*([^\s\n]+)", extra, re.IGNORECASE)
            if m_ck:
                citekey = m_ck.group(1).strip()
                by_citekey[citekey.lower()] = key

            if doi:
                by_doi[doi] = key

            # Clean creator last names
            creators_clean = []
            if creators:
                for c in creators.split(";"):
                    last = c.strip().split(",")[0].strip().lower()
                    if len(last) > 2:
                        creators_clean.append(last)

            # Title keywords
            if title and len(title) > 8:
                words = {
                    w for w in re.findall(r"[a-z0-9]+", title.lower())
                    if len(w) > 3 and w not in _STOPWORDS
                }
                if len(words) >= 2:
                    by_title_words.append((words, key, title, creators_clean, year))

            cols = item_cols.get(key, [])
            nodes[key] = GraphNode(
                item_key=key,
                title=title,
                creators=creators,
                year=year,
                citekey=citekey,
                doi=doi,
                collections=cols,
            )

        # 2. Parse sidecar bibliographies for citation edges
        edges: set[tuple[str, str, str, float]] = set()

        if sc_dir.exists():
            for sc_path in sc_dir.glob("*.md"):
                src_key = sc_path.stem
                if src_key not in nodes:
                    continue

                try:
                    text = sc_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                ref_match = re.search(r"#+\s+References[\s\S]*?(?=\n#\s+[A-Z]|\Z)", text, re.IGNORECASE)
                if not ref_match:
                    continue

                ref_text = ref_match.group(0).lower()

                # Check DOI matches in references
                for d, target_key in by_doi.items():
                    if target_key != src_key and d in ref_text:
                        edges.add((src_key, target_key, "cites", 1.0))

                # Check Citekey matches
                for ck, target_key in by_citekey.items():
                    if target_key != src_key and ck in ref_text:
                        edges.add((src_key, target_key, "cites", 1.0))

                # Check Title + Author matches
                for words, target_key, target_title, target_creators, target_year in by_title_words:
                    if target_key == src_key:
                        continue

                    matching_words = [w for w in words if w in ref_text]
                    if len(matching_words) >= 3 and len(matching_words) >= len(words) * 0.7:
                        author_match = (
                            any(c in ref_text for c in target_creators)
                            if target_creators
                            else True
                        )
                        if author_match:
                            edges.add((src_key, target_key, "cites", 1.0))

        # 3. Add co-authorship and shared-collection edges
        author_papers = defaultdict(list)
        for key, node in nodes.items():
            if node.creators:
                for c in node.creators.split(";"):
                    c_clean = c.strip().split(",")[0].strip().lower()
                    if len(c_clean) > 3:
                        author_papers[c_clean].append(key)

        for author, p_keys in author_papers.items():
            if len(p_keys) > 1:
                for i in range(len(p_keys)):
                    for j in range(i + 1, len(p_keys)):
                        k1, k2 = p_keys[i], p_keys[j]
                        edges.add((k1, k2, "coauthor", 0.5))
                        edges.add((k2, k1, "coauthor", 0.5))

        # 4. Save to local SQLite database
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db_conn = sqlite3.connect(self.db_path)
        db_cur = db_conn.cursor()

        db_cur.execute("DROP TABLE IF EXISTS nodes")
        db_cur.execute("DROP TABLE IF EXISTS edges")

        db_cur.execute("""
        CREATE TABLE nodes (
            item_key TEXT PRIMARY KEY,
            title TEXT,
            creators TEXT,
            year TEXT,
            citekey TEXT,
            doi TEXT,
            collections TEXT
        )
        """)

        db_cur.execute("""
        CREATE TABLE edges (
            source_key TEXT,
            target_key TEXT,
            relation TEXT,
            weight REAL,
            PRIMARY KEY (source_key, target_key, relation)
        )
        """)

        for node in nodes.values():
            db_cur.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    node.item_key,
                    node.title,
                    node.creators,
                    node.year,
                    node.citekey,
                    node.doi,
                    json.dumps(node.collections),
                ),
            )

        for src, tgt, rel, w in edges:
            db_cur.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?)", (src, tgt, rel, w))

        db_cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(source_key)")
        db_cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges(target_key)")
        db_conn.commit()
        db_conn.close()

        # Refresh in-memory graph
        self.load()

        stats = {
            "nodes": len(nodes),
            "directed_citations": len([e for e in edges if e[2] == "cites"]),
            "total_edges": len(edges),
            "db_path": str(self.db_path),
        }
        return stats

    # -- Loading & Querying ---------------------------------------------------
    def load(self) -> bool:
        """Load SQLite database into in-memory NetworkX DiGraph."""
        if not self.db_path.exists():
            return False

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        self.graph.clear()

        cur.execute("SELECT * FROM nodes")
        for r in cur.fetchall():
            cols = json.loads(r["collections"]) if r["collections"] else []
            self.graph.add_node(
                r["item_key"],
                title=r["title"] or "",
                creators=r["creators"] or "",
                year=r["year"] or "",
                citekey=r["citekey"] or "",
                doi=r["doi"] or "",
                collections=cols,
            )

        cur.execute("SELECT * FROM edges")
        for r in cur.fetchall():
            self.graph.add_edge(
                r["source_key"],
                r["target_key"],
                relation=r["relation"],
                weight=float(r["weight"] or 1.0),
            )

        conn.close()
        self._loaded = True
        return True

    def get_collection_hubs(self, collection_key: str = "", top_n: int = 5) -> list[dict[str, Any]]:
        """Find the foundational papers cited across a collection (or library)."""
        if not self._loaded and not self.load():
            return []

        # Filter nodes
        if collection_key:
            scoped_nodes = [
                n for n, d in self.graph.nodes(data=True)
                if collection_key in d.get("collections", [])
            ]
        else:
            scoped_nodes = list(self.graph.nodes())

        if not scoped_nodes:
            return []

        # Citation in-degree (how many papers in the library cite this paper)
        citation_edges = [
            (u, v) for u, v, d in self.graph.edges(data=True) if d.get("relation") == "cites"
        ]
        G_cit = nx.DiGraph()
        G_cit.add_nodes_from(self.graph.nodes(data=True))
        G_cit.add_edges_from(citation_edges)

        in_degrees = G_cit.in_degree(scoped_nodes)
        sorted_hubs = sorted(in_degrees, key=lambda x: x[1], reverse=True)[:top_n]

        results = []
        for key, deg in sorted_hubs:
            data = self.graph.nodes[key]
            results.append({
                "item_key": key,
                "title": data.get("title", ""),
                "creators": data.get("creators", ""),
                "year": data.get("year", ""),
                "citekey": data.get("citekey", ""),
                "inward_citations": deg,
            })
        return results

    def get_paper_lineage(self, item_key: str, depth: int = 1) -> dict[str, Any]:
        """Return direct citation ancestors (cites) and descendants (cited by)."""
        if not self._loaded and not self.load():
            return {"error": "Graph not loaded"}

        if item_key not in self.graph:
            return {"error": f"Item {item_key} not found in graph"}

        cit_sub = nx.DiGraph([
            (u, v) for u, v, d in self.graph.edges(data=True) if d.get("relation") == "cites"
        ])

        # Ancestors: papers that item_key cites (successors in directed graph)
        ancestors = list(cit_sub.successors(item_key)) if item_key in cit_sub else []
        # Descendants: papers that cite item_key (predecessors in directed graph)
        descendants = list(cit_sub.predecessors(item_key)) if item_key in cit_sub else []

        def _format_node(k):
            d = self.graph.nodes.get(k, {})
            return {
                "item_key": k,
                "title": d.get("title", ""),
                "creators": d.get("creators", ""),
                "year": d.get("year", ""),
                "citekey": d.get("citekey", ""),
            }

        return {
            "target_paper": _format_node(item_key),
            "cites": [_format_node(k) for k in ancestors],
            "cited_by": [_format_node(k) for k in descendants],
        }

    def find_connected_papers(self, item_key: str, top_n: int = 5) -> list[dict[str, Any]]:
        """Find structurally similar papers via bibliographic coupling (shared citations)."""
        if not self._loaded and not self.load():
            return []

        if item_key not in self.graph:
            return []

        cit_sub = nx.DiGraph([
            (u, v) for u, v, d in self.graph.edges(data=True) if d.get("relation") == "cites"
        ])

        target_cites = set(cit_sub.successors(item_key)) if item_key in cit_sub else set()
        if not target_cites:
            return []

        scores = []
        for other_key in self.graph.nodes():
            if other_key == item_key:
                continue

            other_cites = set(cit_sub.successors(other_key)) if other_key in cit_sub else set()
            if not other_cites:
                continue

            shared = target_cites.intersection(other_cites)
            if shared:
                jaccard = len(shared) / len(target_cites.union(other_cites))
                scores.append((other_key, jaccard, list(shared)))

        scores.sort(key=lambda x: x[1], reverse=True)
        results = []
        for k, score, shared in scores[:top_n]:
            d = self.graph.nodes[k]
            shared_titles = [
                self.graph.nodes.get(s, {}).get("title", s) for s in shared
            ]
            results.append({
                "item_key": k,
                "title": d.get("title", ""),
                "creators": d.get("creators", ""),
                "year": d.get("year", ""),
                "coupling_score": round(score, 3),
                "shared_citations_count": len(shared),
                "shared_citations": shared_titles[:3],
            })
        return results
