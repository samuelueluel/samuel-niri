#!/usr/bin/env python3
"""Apply retrieval-hygiene and score-provenance patches to zotero_mcp.

This patch keeps ordinary semantic RAG separate from bibliography retrieval:

* bibliography chunks are removed from both dense and BM25 semantic candidates;
  ``zotero_search_references`` is the dedicated bibliography route;
* dense and sparse candidates are fused with RRF, then ranked by the mandatory
  local HTTP reranker;
* raw cross-encoder scores are retained and displayed for evidence gating;
* a configurable floor removes weak candidates;
* figure-schema candidates can be injected and rank-boosted for figure queries,
  while the displayed confidence remains the unboosted raw reranker score;
* BM25-only rescues receive their real dense cosine distance for display.

The transformation normalizes both a clean supported package and older partial
applications. Every required anchor is validated before either target file is
written, preventing an upstream mismatch from leaving another partial patch.

Usage: zotero-mcp-hybrid-filter-patch.py <path/to/zotero_mcp-package-dir>
Prints: ``applied`` | ``already`` | ``mismatch``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


if len(sys.argv) != 2:
    print("usage: zotero-mcp-hybrid-filter-patch.py <zotero_mcp-package-dir>", file=sys.stderr)
    sys.exit(2)

pkg = Path(sys.argv[1])
semantic_path = pkg / "semantic_search.py"
tools_path = pkg / "tools" / "search.py"
errors: list[str] = []

if not semantic_path.exists():
    errors.append("semantic_search.py not found")
if not tools_path.exists():
    errors.append("tools/search.py not found")
if errors:
    print("mismatch")
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

semantic_original = semantic_path.read_text(encoding="utf-8")
tools_original = tools_path.read_text(encoding="utf-8")
semantic = semantic_original
tools = tools_original


def replace_region(src: str, start: str, end: str, replacement: str, label: str) -> str:
    start_count = src.count(start)
    if start_count != 1:
        errors.append(f"{label} start anchor count={start_count}")
        return src
    start_pos = src.index(start)
    end_pos = src.find(end, start_pos + len(start))
    if end_pos < 0:
        errors.append(f"{label} end anchor missing")
        return src
    return src[:start_pos] + replacement + src[end_pos:]


HELPERS = r'''# [hybrid filter patch] Retrieval-hygiene helpers.
_DCR_SECTION_RE = re.compile(r"\|\s*Section:\s*([^\]]+)\]", re.IGNORECASE)
_BIB_SECTION_RE = re.compile(
    r"^(?:references?|bibliography|bibliographic references|works cited|literature cited)$",
    re.IGNORECASE,
)
_MARKDOWN_BIB_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+(?:references?|bibliography|bibliographic references|works cited|literature cited)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BIB_IDENTIFIER_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|\bdoi\s*:\s*10\.|"
    r"\barxiv\s*:\s*\d{4}|https?://arxiv\.org/)",
    re.IGNORECASE,
)
_AUTHOR_YEAR_RE = re.compile(
    r"\([A-Z][^()]{0,90}?(?:et al\.?)?[, ]+\d{4}[a-z]?\)"
    r"|[A-Z][A-Za-z.'-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z.'-]+)?[,.]\s*(?:19|20)\d{2}"
)
_FIGURE_QUERY_RE = re.compile(
    r"\b(fig(?:ure|ures)?|graph(?:s)?|plot(?:s)?|chart(?:s)?|panel(?:s)?|"
    r"visual(?:s)?|depict(?:s|ed|ing)?|illustrat(?:e|es|ed|ing|ion)?|"
    r"schematic(?:s)?|scatter(?:plot|plots)?|histogram(?:s)?|heat\s?map(?:s)?|curve(?:s)?)\b",
    re.IGNORECASE,
)


def is_bibliography_chunk(text: str) -> bool:
    """Return True only for a bibliography-section chunk.

    DCR prefixes are parsed at the ``Section:`` field and only the final
    breadcrumb component is tested. This deliberately does not scan the paper
    title, so a title such as ``Reference Manual`` cannot suppress every chunk
    in that book. Legacy chunks fall back to an exact Markdown heading anywhere
    in the chunk, then a strict author-year plus DOI/arXiv multi-signal check.
    """
    if not text:
        return False
    first_line = text.split("\n", 1)[0]
    match = _DCR_SECTION_RE.search(first_line)
    if match:
        leaf = match.group(1).split(">")[-1].strip()
        if _BIB_SECTION_RE.fullmatch(leaf):
            return True
    # Older chunks can contain a References heading after a short prose tail,
    # while their stored DCR breadcrumb still names the preceding section.
    if _MARKDOWN_BIB_HEADING_RE.search(text):
        return True
    # One audited legacy chunk begins mid-bibliography with no heading. Require
    # both high author-year density and several DOI/arXiv identifiers; this is
    # deliberately much stricter than density alone, which catches lit reviews.
    return (
        len(_AUTHOR_YEAR_RE.findall(text)) >= 6
        and len(_BIB_IDENTIFIER_RE.findall(text)) >= 3
    )


def is_reference_chunk(text: str) -> bool:
    """Legacy diagnostic classifier: bibliography breadcrumb OR high density."""
    if is_bibliography_chunk(text):
        return True
    matches = len(_AUTHOR_YEAR_RE.findall(text or ""))
    return matches >= 3 and matches >= len(text or "") / 200.0


def is_figure_query(query: str) -> bool:
    """Return whether a query asks about a figure, graph, plot, or chart."""
    return bool(_FIGURE_QUERY_RE.search(query))


'''

CONFIG = '''_DEFAULT_HYBRID_CONFIG: dict[str, Any] = {
    "enabled": False,
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
    "rrf_k": 60,
    "index_path": "",
    # [hybrid filter patch] bibliography retrieval is a separate index/tool.
    "exclude_reference_chunks": True,
    "suppress_reference_chunks_dense": True,
    "annotate_reference_chunks": True,
    "figure_boost": 0.0,
    "rerank_floor": None,
}


'''

BUILD_SPARSE_METHOD = '''    def _build_sparse_index(self) -> dict[str, int | str]:
        """[hybrid filter patch] Build BM25 from non-bibliography chunks."""
        import time as _time

        t0 = _time.monotonic()
        index_path = self._hybrid_config.get("index_path") or str(
            Path.home() / ".config" / "zotero-mcp" / "bm25_index.json"
        )
        idx = _sparse.BM25Index(index_path)
        docs: list[tuple[str, str]] = []
        excluded = 0
        exclude_references = bool(
            self._hybrid_config.get("exclude_reference_chunks", True)
        )
        for ids, documents, _metas in self.chroma_client.iter_documents():
            for doc_id, text in zip(ids, documents):
                if not text:
                    continue
                if exclude_references and is_bibliography_chunk(text):
                    excluded += 1
                    continue
                docs.append((doc_id, text))
        idx.build(docs)
        idx.save()
        stats = idx.stats()
        stats["ms"] = int((_time.monotonic() - t0) * 1000)
        stats["excluded_reference_chunks"] = excluded
        _SPARSE_CACHE.pop(index_path, None)
        return stats

'''

HYBRID_METHOD = '''    def _hybrid_search(self, query: str, fetch_limit: int, where, sparse_idx) -> dict[str, Any]:
        """[hybrid filter patch] Dense + BM25 -> RRF candidate set."""
        dense = self.chroma_client.search(
            query_texts=[query], n_results=fetch_limit, where=where
        )

        # Ordinary semantic RAG never uses bibliography-section chunks. Exact
        # DOI/title/citation lookups belong in zotero_search_references.
        if bool(self._hybrid_config.get("suppress_reference_chunks_dense", True)):
            dense_ids_all = (dense.get("ids") or [[]])[0]
            if dense_ids_all:
                keep = [
                    index
                    for index, document in enumerate((dense.get("documents") or [[]])[0])
                    if not is_bibliography_chunk(document)
                ]
                for key in ("ids", "documents", "metadatas", "distances"):
                    if dense.get(key) and dense[key][0]:
                        dense[key][0] = [dense[key][0][index] for index in keep]

        dense_ids = (dense.get("ids") or [[]])[0]
        sparse_hits = sparse_idx.search(query, top_n=max(fetch_limit * 2, 20))
        if not sparse_hits:
            return dense

        # Post-filter stale sparse indexes too, so hygiene and collection scope
        # are correct immediately even before the next fast BM25 rebuild.
        sparse_payload = self.chroma_client.get_documents([doc_id for doc_id, _ in sparse_hits])
        sparse_docs = dict(zip(sparse_payload["ids"], sparse_payload["documents"]))
        sparse_metas = dict(zip(sparse_payload["ids"], sparse_payload["metadatas"]))
        exclude_sparse_refs = bool(
            self._hybrid_config.get("exclude_reference_chunks", True)
        )
        sparse_hits = [
            (doc_id, score)
            for doc_id, score in sparse_hits
            if (not exclude_sparse_refs or not is_bibliography_chunk(sparse_docs.get(doc_id, "")))
            and self._where_matches(sparse_metas.get(doc_id, {}), where)
        ]

        rank_lists = [dense_ids, [doc_id for doc_id, _ in sparse_hits]]
        if (
            float(self._hybrid_config.get("figure_boost", 0.0) or 0.0) > 0
            and is_figure_query(query)
        ):
            schema_ids = [
                doc_id for doc_id, _ in sparse_idx.search("Figure Schema", top_n=30)
            ]
            if schema_ids:
                schema_payload = self.chroma_client.get_documents(schema_ids)
                schema_ids = [
                    doc_id
                    for doc_id, document, metadata in zip(
                        schema_payload["ids"],
                        schema_payload["documents"],
                        schema_payload["metadatas"],
                    )
                    if "[Figure Schema]" in (document or "")
                    and self._where_matches(metadata or {}, where)
                ]
                already_ranked = {doc_id for ranks in rank_lists for doc_id in ranks}
                schema_ids = [doc_id for doc_id in schema_ids if doc_id not in already_ranked]
                if schema_ids:
                    rank_lists.append(schema_ids)

        fused = _sparse.rrf_merge(
            rank_lists,
            k=int(self._hybrid_config.get("rrf_k", 60) or 60),
        )
        fused_ids = [doc_id for doc_id, _ in fused[:fetch_limit]]
        if not fused_ids:
            return dense

        docs_by_id: dict[str, str] = {}
        metas_by_id: dict[str, Any] = {}
        dists_by_id: dict[str, float] = {}
        for index, doc_id in enumerate(dense_ids):
            docs_by_id[doc_id] = dense["documents"][0][index]
            metas_by_id[doc_id] = dense["metadatas"][0][index]
            dists_by_id[doc_id] = dense["distances"][0][index]

        missing = [doc_id for doc_id in fused_ids if doc_id not in docs_by_id]
        if missing:
            fetched = self.chroma_client.get_documents(missing)
            query_embedding = None
            embedding_function = getattr(self.chroma_client, "embedding_function", None)
            if embedding_function is not None and hasattr(embedding_function, "embed_query"):
                try:
                    query_embedding = embedding_function.embed_query(query)
                except Exception:
                    query_embedding = None

            embeddings: dict[str, list[float]] = {}
            if query_embedding is not None:
                try:
                    response = self.chroma_client.collection.get(
                        ids=missing, include=["embeddings"]
                    )
                    values = response.get("embeddings")
                    if values is not None and len(values):
                        embeddings = dict(zip(response["ids"], values))
                except Exception:
                    embeddings = {}

            import math as _math

            for doc_id, document, metadata in zip(
                fetched["ids"], fetched["documents"], fetched["metadatas"]
            ):
                docs_by_id[doc_id] = document
                metas_by_id[doc_id] = metadata
                embedding = embeddings.get(doc_id)
                if (
                    query_embedding is not None
                    and embedding is not None
                    and len(query_embedding) == len(embedding)
                ):
                    denominator = _math.sqrt(sum(x * x for x in query_embedding)) * _math.sqrt(
                        sum(x * x for x in embedding)
                    )
                    cosine = (
                        sum(x * y for x, y in zip(query_embedding, embedding)) / denominator
                        if denominator
                        else 0.0
                    )
                    dists_by_id[doc_id] = 1.0 - cosine
                else:
                    dists_by_id[doc_id] = 1.0

        return {
            "ids": [fused_ids],
            "documents": [[docs_by_id[doc_id] for doc_id in fused_ids]],
            "metadatas": [[metas_by_id[doc_id] for doc_id in fused_ids]],
            "distances": [[dists_by_id[doc_id] for doc_id in fused_ids]],
        }

'''

RERANK_BLOCK = '''            # [hybrid filter patch] Preserve raw local cross-encoder scores.
            # Figure boosts affect ordering/floor admission only; displayed
            # confidence remains the unboosted score used by citation-integrity.
            if reranker and results.get("documents") and results["documents"][0]:
                documents = results["documents"][0]
                top_k = len(documents) if self._chunking_enabled else limit
                scored = reranker.rerank_with_scores(query, documents, top_k=top_k)
                figure_boost = float(
                    self._hybrid_config.get("figure_boost", 0.0) or 0.0
                )
                floor = self._hybrid_config.get("rerank_floor")
                figure_query = figure_boost > 0 and is_figure_query(query)
                kept: list[tuple[int, float, float]] = []
                for result_index, raw_score in scored:
                    adjusted_score = raw_score + (
                        figure_boost
                        if figure_query and "[Figure Schema]" in documents[result_index]
                        else 0.0
                    )
                    if floor is not None and adjusted_score < float(floor):
                        continue
                    kept.append((result_index, raw_score, adjusted_score))
                kept.sort(key=lambda row: row[2], reverse=True)
                ranked_indices = [row[0] for row in kept]
                for key in ("ids", "distances", "documents", "metadatas"):
                    if results.get(key) and results[key][0]:
                        results[key][0] = [
                            results[key][0][result_index]
                            for result_index in ranked_indices
                        ]
                results["rerank_scores"] = [[row[1] for row in kept]]

'''

ENRICH_RESULT_BLOCK = '''            enriched_result: dict[str, Any] = {
                "item_key": item_key,
                "similarity_score": (1 - distance) if distance is not None else 0,
                "matched_text": document,
                "matched_passage": passage,
                "metadata": meta if isinstance(meta, dict) else {},
                "query": query,
            }
            enriched_result["rerank_score"] = (
                rerank_scores[i] if i < len(rerank_scores) else None
            )
            # A defensive annotation remains useful if suppression is disabled
            # in config or a legacy candidate slips through.
            enriched_result["is_reference"] = is_bibliography_chunk(document)
'''

TOOLS_RESULT_BLOCK = '''            if zotero_item:
                extra = {"Relevance": f"{similarity_score:.3f}"}
                if (rerank_score := result.get("rerank_score")) is not None:
                    extra["Rerank"] = f"{rerank_score:+.2f}"
                if result.get("is_reference"):
                    extra["REF"] = (
                        "bibliography entry — use zotero_search_references; "
                        "do not cite as substantive evidence"
                    )
                if loc_bits:
                    extra["Location"] = ", ".join(loc_bits)
                if snippet:
                    extra["Matched Passage"] = snippet
                zotero_item.setdefault("key", result.get("item_key", ""))
                output.extend(
                    _utils.format_item_result(
                        zotero_item,
                        index=i,
                        extra_fields=extra,
                        show_library=search_all_libraries,
                    )
                )
            else:
                # Fallback if full Zotero item metadata is unavailable.
                output.append(f"## {i}. Item {result.get('item_key', 'Unknown')}")
                output.append(f"**Relevance:** {similarity_score:.3f}")
                if (rerank_score := result.get("rerank_score")) is not None:
                    output.append(f"**Rerank:** {rerank_score:+.2f}")
                if result.get("is_reference"):
                    output.append(
                        "**REF:** bibliography entry — use zotero_search_references; "
                        "do not cite as substantive evidence"
                    )
                if loc_bits:
                    output.append(f"**Location:** {', '.join(loc_bits)}")
                if snippet:
                    output.append(f"**Matched Passage:** {snippet}")
                if error := result.get("error"):
                    output.append(f"**Error:** {error}")
                output.append("")

'''

# Normalize helper and config regions.
helper_start = "# [hybrid filter patch] Retrieval-hygiene helpers"
if helper_start in semantic:
    semantic = replace_region(
        semantic, helper_start, "def warmup_reranker", HELPERS, "helpers"
    )
else:
    anchor = "def warmup_reranker"
    if semantic.count(anchor) != 1:
        errors.append(f"helpers insertion anchor count={semantic.count(anchor)}")
    else:
        semantic = semantic.replace(anchor, HELPERS + anchor, 1)

semantic = replace_region(
    semantic,
    "_DEFAULT_HYBRID_CONFIG: dict[str, Any] = {",
    "def load_hybrid_config",
    CONFIG,
    "hybrid config",
)
semantic = replace_region(
    semantic,
    "    def _build_sparse_index(",
    "    def _hybrid_search(",
    BUILD_SPARSE_METHOD,
    "sparse builder",
)

hybrid_start_pos = semantic.find("    def _hybrid_search(")
hybrid_end_candidates = [
    anchor
    for anchor in ("    def _resolve_collection_item_keys(", "    def search(self,")
    if semantic.find(anchor, hybrid_start_pos + 1) >= 0
]
if not hybrid_end_candidates:
    errors.append("hybrid search end anchor missing")
else:
    hybrid_end = min(
        hybrid_end_candidates,
        key=lambda anchor: semantic.find(anchor, hybrid_start_pos + 1),
    )
    semantic = replace_region(
        semantic,
        "    def _hybrid_search(",
        hybrid_end,
        HYBRID_METHOD,
        "hybrid search",
    )
rerank_start = (
    "            # [hybrid filter patch] Preserve raw local cross-encoder scores."
    if "            # [hybrid filter patch] Preserve raw local cross-encoder scores."
    in semantic
    else "            # Re-rank results with cross-encoder if enabled."
)
semantic = replace_region(
    semantic,
    rerank_start,
    "            # Enrich results with full Zotero item data",
    RERANK_BLOCK,
    "rerank stage",
)

# Normalize enrichment score/reference fields without replacing upstream
# metadata hydration and cross-library behavior.
if "        rerank_scores = chroma_results.get(\"rerank_scores\", [[]])[0]" not in semantic:
    old = "        metadatas = chroma_results.get(\"metadatas\", [[]])[0]\n"
    if semantic.count(old) != 1:
        errors.append(f"enrich score-read anchor count={semantic.count(old)}")
    else:
        semantic = semantic.replace(
            old,
            old + "        rerank_scores = chroma_results.get(\"rerank_scores\", [[]])[0]\n",
            1,
        )
semantic = replace_region(
    semantic,
    "            enriched_result: dict[str, Any] = {",
    "            # Passage provenance —",
    ENRICH_RESULT_BLOCK,
    "enriched result",
)

# Normalize public formatting so results remain visible with or without a
# reranker and raw scores are always exposed when present.
semantic_tool_pos = tools.find('name="zotero_semantic_search"')
if semantic_tool_pos < 0:
    errors.append("semantic tool marker missing")
else:
    result_start = tools.find("            if zotero_item:\n", semantic_tool_pos)
    result_end = tools.find('        return "\\n".join(output)', result_start)
    if result_start < 0 or result_end < 0:
        errors.append("semantic result-format anchors missing")
    else:
        tools = tools[:result_start] + TOOLS_RESULT_BLOCK + tools[result_end:]

if errors:
    print("mismatch")
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

changed = []
if semantic != semantic_original:
    changed.append((semantic_path, semantic, ".hybrid-patch.tmp"))
if tools != tools_original:
    changed.append((tools_path, tools, ".hybrid-patch.tmp"))

for path, content, suffix in changed:
    tmp = path.with_name(path.name + suffix)
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

print("applied" if changed else "already")
