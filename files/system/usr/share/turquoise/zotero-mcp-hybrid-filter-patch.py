#!/usr/bin/env python3
"""Idempotently apply retrieval-hygiene filters to zotero-mcp semantic search.

Four components (2026-08-15), all zero-redo (no re-embedding; the BM25 rebuild
is the existing ~2s direct rebuild):

1. is_reference_chunk(): classify bibliography-shaped chunks from (a) the DCR
   breadcrumb naming a reference section, or (b) author-year citation density.
2. _build_sparse_index() excludes reference chunks from the BM25 lexical leg
   (config semantic_search.hybrid.exclude_reference_chunks, default true).
   Dense keeps them, so citation queries ("who wrote X (1994)?") still work;
   rare tokens inside citation lists ("Journal of Monetary Economics") stop
   polluting lexical results. Count reported in the rebuild stats.
3. Rerank stage uses rerank_with_scores() and applies an optional score floor
   (semantic_search.hybrid.rerank_floor, default None/disabled) that drops weak
   matches instead of displaying them at ~0 relevance.
4. is_figure_query() + figure boost (semantic_search.hybrid.figure_boost,
   default 0.0/off): on figure-style queries, schema-bearing chunks ([Figure
   Schema] in text) get +figure_boost on their rerank score, so natural
   phrasing like "graph of coefficient estimates over time" surfaces the
   event-study schema chunk.

References: 2026 practice filters extraneous chunks at retrieval time
(ChunkRAG, arXiv:2410.19572) rather than dropping sections; citation-integrity
work (CiteFix, ACL 2025) argues references must stay in the corpus. This patch
implements "filter, don't drop" for the sparse leg + rerank stage.

Marker comment: "[hybrid filter patch]". Re-applied by sjust update.
Usage: zotero-mcp-hybrid-filter-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
target = pkg / "semantic_search.py"

errors: list[str] = []
changed = False

MARKER = "[hybrid filter patch]"

HELPERS_NEW = '''

# [hybrid filter patch] Retrieval-hygiene helpers: (1) reference-chunk
# classification (DCR breadcrumb + author-year density) so the lexical leg can
# exclude citation lists that pollute BM25 with rare-token matches; (2)
# figure-query detection so schema-bearing chunks can be boosted when the
# question is about a figure. 2026-08-15: "filter, don't drop" — dense keeps
# reference chunks (citation queries still work); only the sparse leg and the
# rerank stage filter.
_REFERENCE_BREADCRUMB_RE = re.compile(
    r"(?i)\\b(?:references|reference|bibliography|bibliographic|works cited|literature cited)\\b"
)
_AUTHOR_YEAR_RE = re.compile(
    r"\\([A-Z][^()]{0,90}?(?:et al\\.?)?[, ]+\\d{4}[a-z]?\\)"
    r"|[A-Z][A-Za-z.'-]+(?:\\s+(?:and|&)\\s+[A-Z][A-Za-z.'-]+)?[,.]\\s*(?:19|20)\\d{2}"
)
_FIGURE_QUERY_RE = re.compile(
    r"\\b(fig(?:ure|ures)?|graph(?:s)?|plot(?:s)?|chart(?:s)?|panel(?:s)?|"
    r"visual(?:s)?|depict(?:s|ed|ing)?|illustrat(?:e|es|ed|ing|ion)?|"
    r"schematic(?:s)?|scatter(?:plot|plots)?|histogram(?:s)?|heat\\s?map(?:s)?|curve(?:s)?)\\b",
    re.IGNORECASE,
)


def is_reference_chunk(text: str) -> bool:
    """[hybrid filter patch] Heuristic: does *text* look like a reference list?

    Two signals, OR'd: (1) the first line (DCR prefix) names a reference
    section (Section: References / Bibliography / Works Cited ...); (2)
    author-year citation density is high (>=3 matches and >=1 per ~200 chars).
    Content chunks citing prior work inline (1-2 citations per 2000-char
    chunk, or a lit-review paragraph with ~8) stay below the density bar;
    true bibliography blocks (15-25 short citation lines per 2000 chars) are
    flagged. Legacy chunks without a DCR prefix rely on signal (2).
    """
    if _REFERENCE_BREADCRUMB_RE.search(text.split("\\n", 1)[0]):
        return True
    matches = len(_AUTHOR_YEAR_RE.findall(text))
    return matches >= 3 and matches >= len(text) / 200.0


def is_figure_query(query: str) -> bool:
    """[hybrid filter patch] Does the query ask about a figure (graph/plot/...)? """
    return bool(_FIGURE_QUERY_RE.search(query))
'''

CONFIG_OLD = '''_DEFAULT_HYBRID_CONFIG: dict[str, Any] = {
    "enabled": False,
    "bm25_k1": 1.5,  # [sparse patch] Okapi BM25 defaults
    "bm25_b": 0.75,
    "rrf_k": 60,  # [sparse patch] Reciprocal Rank Fusion constant (Cormack et al. 2009)
    "index_path": "",
}'''

CONFIG_NEW = '''_DEFAULT_HYBRID_CONFIG: dict[str, Any] = {
    "enabled": False,
    "bm25_k1": 1.5,  # [sparse patch] Okapi BM25 defaults
    "bm25_b": 0.75,
    "rrf_k": 60,  # [sparse patch] Reciprocal Rank Fusion constant (Cormack et al. 2009)
    "index_path": "",
    # [hybrid filter patch] retrieval-hygiene knobs (2026-08-15):
    "exclude_reference_chunks": True,  # drop reference-shaped chunks from the BM25 leg (dense keeps them)
    "figure_boost": 0.0,  # add to rerank score of [Figure Schema] chunks on figure-style queries (0 = off)
    "rerank_floor": None,  # drop reranked candidates below this score (None = off; e.g. -2.0)
}'''

SPARSE_LOOP_OLD = '''        idx = _sparse.BM25Index(index_path)
        docs: list[tuple[str, str]] = []
        for ids, documents, _metas in self.chroma_client.iter_documents():
            for doc_id, text in zip(ids, documents):
                if text:
                    docs.append((doc_id, text))
        idx.build(docs)
        idx.save()
        stats = idx.stats()
        stats["ms"] = int((_time.monotonic() - t0) * 1000)'''

SPARSE_LOOP_NEW = '''        idx = _sparse.BM25Index(index_path)
        docs: list[tuple[str, str]] = []
        # [hybrid filter patch] exclude reference-shaped chunks from the lexical
        # leg (dense keeps them, so citation queries still work): rare tokens
        # inside citation lists ("Journal of Monetary Economics") otherwise
        # match and pollute BM25 results.
        _excl_refs = bool(self._hybrid_config.get("exclude_reference_chunks", True))
        _n_refs = 0
        for ids, documents, _metas in self.chroma_client.iter_documents():
            for doc_id, text in zip(ids, documents):
                if not text:
                    continue
                if _excl_refs and is_reference_chunk(text):
                    _n_refs += 1
                    continue
                docs.append((doc_id, text))
        idx.build(docs)
        idx.save()
        stats = idx.stats()
        stats["ms"] = int((_time.monotonic() - t0) * 1000)
        stats["excluded_reference_chunks"] = _n_refs'''

RERANK_OLD = '''            if reranker and results.get("documents") and results["documents"][0]:
                documents = results["documents"][0]
                top_k = len(documents) if self._chunking_enabled else limit
                ranked_indices = reranker.rerank(query, documents, top_k=top_k)
                for key in ["ids", "distances", "documents", "metadatas"]:
                    if results.get(key) and results[key][0]:
                        results[key][0] = [results[key][0][i] for i in ranked_indices]'''

RERANK_NEW = '''            if reranker and results.get("documents") and results["documents"][0]:
                documents = results["documents"][0]
                top_k = len(documents) if self._chunking_enabled else limit
                # [hybrid filter patch] score-based rerank with a figure boost
                # for schema-bearing chunks on figure-style queries, plus an
                # optional rerank-score floor (drops weak matches, e.g.
                # reference-list chunks, instead of displaying them at ~0).
                _scored = reranker.rerank_with_scores(query, documents, top_k=top_k)
                _fig_boost = float(self._hybrid_config.get("figure_boost", 0.0) or 0.0)
                _floor = self._hybrid_config.get("rerank_floor", None)
                _fig_q = _fig_boost > 0 and is_figure_query(query)
                _kept: list[tuple[int, float]] = []
                for _ridx, _score in _scored:
                    _adj = _score + (
                        _fig_boost if _fig_q and "[Figure Schema]" in documents[_ridx] else 0.0
                    )
                    if _floor is not None and _adj < float(_floor):
                        continue
                    _kept.append((_ridx, _adj))
                _kept.sort(key=lambda _t: _t[1], reverse=True)
                ranked_indices = [r for r, _ in _kept]
                _raw_scores = dict(_scored)
                for key in ["ids", "distances", "documents", "metadatas"]:
                    if results.get(key) and results[key][0]:
                        results[key][0] = [results[key][0][i] for i in ranked_indices]
                if results.get("ids") and results["ids"][0]:
                    results["rerank_scores"] = [[_raw_scores[r] for r in ranked_indices]]'''

# v3 rerank tail (pre rerank-score exposure) -> v4 migration.
RERANK_V4_MARK = 'results["rerank_scores"]'
RERANK_V3_TAIL_OLD = '''                _kept.sort(key=lambda _t: _t[1], reverse=True)
                ranked_indices = [r for r, _ in _kept]
                for key in ["ids", "distances", "documents", "metadatas"]:
                    if results.get(key) and results[key][0]:
                        results[key][0] = [results[key][0][i] for i in ranked_indices]'''

RERANK_V4_TAIL_NEW = '''                _kept.sort(key=lambda _t: _t[1], reverse=True)
                ranked_indices = [r for r, _ in _kept]
                _raw_scores = dict(_scored)
                for key in ["ids", "distances", "documents", "metadatas"]:
                    if results.get(key) and results[key][0]:
                        results[key][0] = [results[key][0][i] for i in ranked_indices]
                if results.get("ids") and results["ids"][0]:
                    results["rerank_scores"] = [[_raw_scores[r] for r in ranked_indices]]'''

ENRICH_READS_OLD = '''        ids = chroma_results["ids"][0]
        distances = chroma_results.get("distances", [[]])[0]
        documents = chroma_results.get("documents", [[]])[0]
        metadatas = chroma_results.get("metadatas", [[]])[0]'''

ENRICH_READS_NEW = '''        ids = chroma_results["ids"][0]
        distances = chroma_results.get("distances", [[]])[0]
        documents = chroma_results.get("documents", [[]])[0]
        metadatas = chroma_results.get("metadatas", [[]])[0]
        rerank_scores = chroma_results.get("rerank_scores", [[]])[0]'''

ENRICH_DICT_OLD = '''            enriched_result: dict[str, Any] = {
                "item_key": item_key,
                "similarity_score": (1 - distance) if distance is not None else 0,
                "matched_text": document,
                "matched_passage": passage,
                "metadata": meta if isinstance(meta, dict) else {},
                "query": query,
            }'''

ENRICH_DICT_NEW = '''            enriched_result: dict[str, Any] = {
                "item_key": item_key,
                "similarity_score": (1 - distance) if distance is not None else 0,
                "matched_text": document,
                "matched_passage": passage,
                "metadata": meta if isinstance(meta, dict) else {},
                "query": query,
            }
            # [hybrid filter patch] calibrated confidence signal: the raw
            # cross-encoder rerank score for this chunk (None when reranking
            # is off). Agents can gate claims on it (positive = confident,
            # < -4 = junk).
            enriched_result["rerank_score"] = (
                rerank_scores[i] if i < len(rerank_scores) else None
            )'''

ENRICH_MARK = '"rerank_score"'

TOOLS_OLD = '''            extra = {"Relevance": f"{similarity_score:.3f}"}'''

TOOLS_NEW = '''            extra = {"Relevance": f"{similarity_score:.3f}"}
            # [hybrid filter patch] calibrated confidence signal (raw rerank
            # score when available; None when reranking is off).
            if (rs := result.get("rerank_score")) is not None:
                extra["Rerank"] = f"{rs:+.2f}"'''

TOOLS_MARK = 'extra["Rerank"]'

FUSION_OLD = '''        fused = _sparse.rrf_merge(
            [dense_ids, [d for d, _ in sparse_hits]],
            k=int(self._hybrid_config.get("rrf_k", 60) or 60),
        )'''

FUSION_NEW = '''        _rank_lists = [dense_ids, [d for d, _ in sparse_hits]]
        # [hybrid filter patch] figure queries (figure_boost > 0): inject
        # schema-bearing candidates via a fixed "Figure Schema" BM25 probe —
        # the natural-query legs usually don't rank schema YAML ("graph of
        # coefficient estimates over time" shares no tokens with the schema),
        # so schema chunks must enter the candidate set explicitly. The rerank
        # figure_boost then lifts them past prose. Zero re-embed.
        if (
            float(self._hybrid_config.get("figure_boost", 0.0) or 0.0) > 0
            and is_figure_query(query)
        ):
            _schema_ids = [d for d, _ in sparse_idx.search("Figure Schema", top_n=30)]
            _schema_ids = [d for d in _schema_ids if d not in _rank_lists[0]]
            if _schema_ids:
                _rank_lists.append(_schema_ids)
        fused = _sparse.rrf_merge(
            _rank_lists,
            k=int(self._hybrid_config.get("rrf_k", 60) or 60),
        )'''

# v1 (2026-08-15, earlier same day): injection scanned the sparse_hits texts for
# the marker; v2 probes the sparse index directly (the natural-query legs rarely
# contain schema chunks at all). Migration replaces v1 with v2 in place.
FUSION_V1_OLD = '''        _rank_lists = [dense_ids, [d for d, _ in sparse_hits]]
        # [hybrid filter patch] figure queries (figure_boost > 0): inject
        # schema-bearing candidates (chunks containing [Figure Schema]) into
        # the fusion, so a natural question like "graph of coefficient
        # estimates over time" can surface the event-study schema chunk even
        # when the dense leg never ranked it. The rerank figure_boost then
        # lifts it past prose. Zero re-embed: the marker lives in the text.
        if (
            float(self._hybrid_config.get("figure_boost", 0.0) or 0.0) > 0
            and is_figure_query(query)
        ):
            _sp = self.chroma_client.get_documents([d for d, _ in sparse_hits])
            _sp_txt = dict(zip(_sp["ids"], _sp["documents"]))
            _schema_ids = [d for d, _ in sparse_hits if "[Figure Schema]" in _sp_txt.get(d, "")]
            if _schema_ids:
                _rank_lists.append(_schema_ids)
        fused = _sparse.rrf_merge(
            _rank_lists,
            k=int(self._hybrid_config.get("rrf_k", 60) or 60),
        )'''

FUSION_V2_MARK = 'sparse_idx.search("Figure Schema", top_n=30)'

RESCUE_OLD = '''        missing = [d for d in fused_ids if d not in docs_by_id]
        if missing:
            fetched = self.chroma_client.get_documents(missing)
            for fid, fdoc, fmeta in zip(fetched["ids"], fetched["documents"], fetched["metadatas"]):
                docs_by_id[fid] = fdoc
                metas_by_id[fid] = fmeta
                dists_by_id[fid] = 1.0'''

RESCUE_NEW = '''        missing = [d for d in fused_ids if d not in docs_by_id]
        if missing:
            fetched = self.chroma_client.get_documents(missing)
            # [hybrid filter patch] rescue-score fix (2026-08-15): rescued
            # chunks (BM25-only, incl. the figure-probe schema chunks) used to
            # display similarity 0.000 (hardcoded distance 1.0). Their
            # embeddings already exist in chroma, so compute the real cosine
            # distance vs the query embedding. Display-only: RRF order is
            # untouched.
            _qemb = None
            _ef = getattr(self.chroma_client, "embedding_function", None)
            if _ef is not None and hasattr(_ef, "embed_query"):
                try:
                    _qemb = _ef.embed_query(query)
                except Exception:
                    _qemb = None
            _emb: dict[str, list[float]] = {}
            if _qemb is not None:
                try:
                    _r = self.chroma_client.collection.get(ids=missing, include=["embeddings"])
                    _e_vals = _r.get("embeddings")
                    if _e_vals is not None and len(_e_vals):
                        _emb = dict(zip(_r["ids"], _e_vals))
                except Exception:
                    _emb = {}
            import math as _math
            for fid, fdoc, fmeta in zip(fetched["ids"], fetched["documents"], fetched["metadatas"]):
                docs_by_id[fid] = fdoc
                metas_by_id[fid] = fmeta
                _e = _emb.get(fid)
                if _qemb is not None and _e is not None and len(_qemb) == len(_e):
                    _den = _math.sqrt(sum(a * a for a in _qemb)) * _math.sqrt(sum(b * b for b in _e))
                    _dot = sum(a * b for a, b in zip(_qemb, _e))
                    _cos = (_dot / _den) if _den else 0.0
                    dists_by_id[fid] = 1.0 - _cos
                else:
                    dists_by_id[fid] = 1.0'''

RESCUE_MARK = 'rescue-score fix (2026-08-15): rescued'
# v3a bug (2026-08-15, found in live testing): `_r["embeddings"] or []` raises
# "truth value ambiguous" on numpy arrays, swallowing the fetch -> rescues fell
# back to 1.0. v3b guards the array truthiness properly. Migration replaces the
# buggy line in place.
RESCUE_V3B_MARK = '_e_vals = _r.get("embeddings")'
RESCUE_V3A_OLD = '''                    _emb = dict(zip(_r["ids"], _r["embeddings"] or []))'''
RESCUE_V3B_NEW = '''                    _e_vals = _r.get("embeddings")
                    if _e_vals is not None and len(_e_vals):
                        _emb = dict(zip(_r["ids"], _e_vals))'''


def _apply(path: Path, old: str, new: str, name: str) -> None:
    """Replace *old* with *new* in *path* (single occurrence)."""
    global changed
    src = path.read_text(encoding="utf-8")
    if old not in src:
        errors.append(f"{name} anchor missing")
        return
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    changed = True


# ================= v5 (2026-08-16): W1 upgrade =================
# Gated dense-leg reference suppression (breadcrumb signal only — the audited
# high-precision signal; density stays out of suppression so lit reviews,
# abstracts and conclusions survive), [REF] annotation on bibliography
# entries, and breadcrumb-only sparse exclusion. Audit: 596/596 breadcrumb-
# flagged chunks were genuine bibliography sections; the 34 density-only
# chunks were prose that must stay retrievable.
V5_BIB_MARK = "def is_bibliography_chunk"
V5_HELPERS_OLD = '''    matches = len(_AUTHOR_YEAR_RE.findall(text))
    return matches >= 3 and matches >= len(text) / 200.0'''
V5_HELPERS_NEW = r'''    matches = len(_AUTHOR_YEAR_RE.findall(text))
    return matches >= 3 and matches >= len(text) / 200.0


def is_bibliography_chunk(text: str) -> bool:
    """[hybrid filter patch] v5 (2026-08-16): breadcrumb-only reference signal.

    Audit (18,950 stored chunks): all 596 breadcrumb-flagged chunks were
    genuine References/Bibliography sections (near-zero false positives),
    while the density signal alone flagged 34 PROSE chunks (abstracts,
    conclusions, one literature review) that must stay retrievable. Reference
    suppression therefore keys on the breadcrumb; density stays out of
    suppression entirely (lit-review safety).
    """
    return bool(_REFERENCE_BREADCRUMB_RE.search(text.split("\n", 1)[0]))


_CITATION_QUERY_RE = re.compile(
    r"(?i)\b(?:cite|cited|citation|citations|reference|references|bibliography|"
    r"works cited|bibtex)\b"
    r"|[A-Z][A-Za-z.'-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z.'-]+)?[,.]?\s*(?:19|20)\d{2}"
)


def is_citation_query(query: str) -> bool:
    """[hybrid filter patch] v5 (2026-08-16): is the query citation-shaped
    (author-year pattern, or cite/reference words)? When true, the dense-leg
    reference-suppression gate opens: bibliography chunks stay in the
    candidate set so citation lookups still work (tagged [REF] in display).
    """
    return bool(_CITATION_QUERY_RE.search(query))'''

V5_CONFIG_MARK = '"reference_chunk_signal"'
V5_CONFIG_OLD = '''    "exclude_reference_chunks": True,  # drop reference-shaped chunks from the BM25 leg (dense keeps them)'''
V5_CONFIG_NEW = '''    "exclude_reference_chunks": True,  # drop bibliography chunks from the BM25 leg
    "reference_chunk_signal": "breadcrumb",  # v5 (2026-08-16): "breadcrumb" (audited, high precision) | "either" (legacy density-inclusive)
    "suppress_reference_chunks_dense": True,  # v5: gate-suppressed dense-leg removal (citation queries keep them, tagged [REF])
    "annotate_reference_chunks": True,  # v5: mark bibliography chunks [REF] in search results'''

V5_DENSE_MARK = "gate-suppressed dense-leg removal"
V5_DENSE_OLD = '''        dense = self.chroma_client.search(query_texts=[query], n_results=fetch_limit, where=where)
        dense_ids = (dense.get("ids") or [[]])[0]'''
V5_DENSE_NEW = '''        dense = self.chroma_client.search(query_texts=[query], n_results=fetch_limit, where=where)
        # [hybrid filter patch] v5 (2026-08-16): dense-leg reference
        # suppression, gated by the citation-query detector. Bibliography
        # chunks (breadcrumb signal — the audited high-precision one) are
        # dropped from the dense leg BEFORE RRF fusion so they can't occupy
        # candidate slots or clear the rerank floor with a positive score
        # (observed 2026-08-16: Diamond-McQuade's bibliography citing
        # Greenstone-Gallagher at rank 2, rerank +2.23). Citation-shaped
        # queries open the gate — citation lookups still work, [REF]-tagged.
        if bool(self._hybrid_config.get("suppress_reference_chunks_dense", False)) and not is_citation_query(query):
            _d_ids = dense.get("ids") or [[]]
            if _d_ids[0]:
                _dkeep = [
                    _i for _i, _d in enumerate(dense["documents"][0])
                    if not is_bibliography_chunk(_d)
                ]
                for _key in ("ids", "documents", "metadatas", "distances"):
                    if dense.get(_key) and dense[_key][0]:
                        dense[_key][0] = [dense[_key][0][_i] for _i in _dkeep]
        dense_ids = (dense.get("ids") or [[]])[0]'''

V5_SPARSE_MARK = "_ref_fn = ("
V5_SPARSE_OLD = '''        _excl_refs = bool(self._hybrid_config.get("exclude_reference_chunks", True))
        _n_refs = 0'''
V5_SPARSE_NEW = '''        _excl_refs = bool(self._hybrid_config.get("exclude_reference_chunks", True))
        # [hybrid filter patch] v5 (2026-08-16): breadcrumb-only by default
        # (audit-validated: signal A covers every bibliography, density-only
        # chunks are prose that must stay in BM25). "either" restores the
        # legacy density-inclusive behavior for non-DCR corpora.
        _ref_fn = (
            is_bibliography_chunk
            if self._hybrid_config.get("reference_chunk_signal", "breadcrumb") == "breadcrumb"
            else is_reference_chunk
        )
        _n_refs = 0'''
V5_SPARSE_CALL_OLD = '''                if _excl_refs and is_reference_chunk(text):'''
V5_SPARSE_CALL_NEW = '''                if _excl_refs and _ref_fn(text):'''

V5_REFANNOT_MARK = '"is_reference"'
V5_REFANNOT_OLD = '''            enriched_result["rerank_score"] = (
                rerank_scores[i] if i < len(rerank_scores) else None
            )'''
V5_REFANNOT_NEW = '''            enriched_result["rerank_score"] = (
                rerank_scores[i] if i < len(rerank_scores) else None
            )
            # [hybrid filter patch] v5 (2026-08-16): bibliography-chunk
            # annotation. Agents can distinguish a citation-list entry (use to
            # FIND the paper) from source content (use to CITE a claim).
            enriched_result["is_reference"] = (
                bool(document) and is_bibliography_chunk(document)
            )'''

V5_TOOLS_REF_MARK = 'extra["REF"]'
V5_TOOLS_OLD = '''            if (rs := result.get("rerank_score")) is not None:
                extra["Rerank"] = f"{rs:+.2f}"'''
V5_TOOLS_NEW = '''            if (rs := result.get("rerank_score")) is not None:
                extra["Rerank"] = f"{rs:+.2f}"
                # [hybrid filter patch] v5 (2026-08-16): bibliography entries
                # are tagged so a citation-list hit isn't mistaken for source
                # content.
                if result.get("is_reference"):
                    extra["REF"] = "bibliography entry - use to find the paper, not as a claim source"'''


def _ensure_v5() -> bool:
    """Apply the v5 (2026-08-16) W1 upgrade to semantic_search.py + tools;
    returns True if anything changed. Idempotent: marks guard each block."""
    changed = False
    src = target.read_text(encoding="utf-8")
    if V5_BIB_MARK not in src:
        if V5_HELPERS_OLD in src:
            src = src.replace(V5_HELPERS_OLD, V5_HELPERS_NEW, 1); changed = True
        else:
            errors.append("v5 helpers anchor missing")
    if V5_CONFIG_MARK not in src:
        if V5_CONFIG_OLD in src:
            src = src.replace(V5_CONFIG_OLD, V5_CONFIG_NEW, 1); changed = True
        else:
            errors.append("v5 config anchor missing")
    if V5_DENSE_MARK not in src:
        if V5_DENSE_OLD in src:
            src = src.replace(V5_DENSE_OLD, V5_DENSE_NEW, 1); changed = True
        else:
            errors.append("v5 dense anchor missing")
    if V5_SPARSE_MARK not in src:
        if V5_SPARSE_OLD in src:
            src = src.replace(V5_SPARSE_OLD, V5_SPARSE_NEW, 1); changed = True
        else:
            errors.append("v5 sparse anchor missing")
        if V5_SPARSE_CALL_OLD in src:
            src = src.replace(V5_SPARSE_CALL_OLD, V5_SPARSE_CALL_NEW, 1); changed = True
        else:
            errors.append("v5 sparse-call anchor missing")
    if V5_REFANNOT_MARK not in src:
        if V5_REFANNOT_OLD in src:
            src = src.replace(V5_REFANNOT_OLD, V5_REFANNOT_NEW, 1); changed = True
        else:
            errors.append("v5 enrich-ref anchor missing")
    if errors:
        print("mismatch")
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    target.write_text(src, encoding="utf-8")
    _v5_tools_changed = False
    _tools = pkg / "tools" / "search.py"
    _t = _tools.read_text(encoding="utf-8")
    if V5_TOOLS_REF_MARK not in _t:
        if V5_TOOLS_OLD in _t:
            _tools.write_text(_t.replace(V5_TOOLS_OLD, V5_TOOLS_NEW, 1), encoding="utf-8")
            _v5_tools_changed = True
        else:
            errors.append("v5 tools-ref anchor missing")
    if errors:
        print("mismatch")
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    return changed or _v5_tools_changed


src0 = target.read_text(encoding="utf-8")
if MARKER not in src0:
    _apply(target, '\n\ndef warmup_reranker(config_path: str | None = None) -> bool:',
           HELPERS_NEW + '\n\ndef warmup_reranker(config_path: str | None = None) -> bool:',
           "helpers anchor")
    _apply(target, CONFIG_OLD, CONFIG_NEW, "config anchor")
    _apply(target, SPARSE_LOOP_OLD, SPARSE_LOOP_NEW, "sparse loop anchor")
    _apply(target, RERANK_OLD, RERANK_NEW, "rerank anchor")
    _apply(target, FUSION_OLD, FUSION_NEW, "fusion anchor")
    _apply(target, RESCUE_OLD, RESCUE_NEW, "rescue anchor")
    _apply(target, ENRICH_READS_OLD, ENRICH_READS_NEW, "enrich reads anchor")
    _apply(target, ENRICH_DICT_OLD, ENRICH_DICT_NEW, "enrich dict anchor")
    if errors:
        print("mismatch")
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    # tools/search.py: expose the rerank score in the MCP result formatting.
    _tools = pkg / "tools" / "search.py"
    _t = _tools.read_text(encoding="utf-8")
    if TOOLS_MARK in _t:
        pass
    elif TOOLS_OLD in _t:
        _tools.write_text(_t.replace(TOOLS_OLD, TOOLS_NEW, 1), encoding="utf-8")
    else:
        errors.append("tools anchor missing")
    if errors:
        print("mismatch")
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    _ensure_v5()
    print("applied")
    sys.exit(0)

# Marker present (2026-08-15 incremental): ensure fusion is at v2 (probe-based)
# and the rescue-score fix (v3) is applied. State machine over the two blocks.
_src1 = target.read_text(encoding="utf-8")
# v3a -> v3b bugfix first (numpy truthiness in the embeddings fetch); inert
# when the fixed line is already present.
if RESCUE_V3A_OLD in _src1:
    _src1 = _src1.replace(RESCUE_V3A_OLD, RESCUE_V3B_NEW, 1)
    target.write_text(_src1, encoding="utf-8")
    _ensure_v5()
    print("applied")
    sys.exit(0)
_need_fusion = FUSION_V2_MARK not in _src1
_need_rescue = RESCUE_MARK not in _src1
_need_v4 = RERANK_V4_MARK not in _src1
_need_enrich = ENRICH_MARK not in _src1
if not (_need_fusion or _need_rescue or _need_v4 or _need_enrich):
    # tools/search.py may still need the Rerank display after an upgrade.
    _tools = pkg / "tools" / "search.py"
    _t = _tools.read_text(encoding="utf-8")
    if TOOLS_MARK not in _t:
        if TOOLS_OLD in _t:
            _tools.write_text(_t.replace(TOOLS_OLD, TOOLS_NEW, 1), encoding="utf-8")
            _ensure_v5()
            print("applied")
            sys.exit(0)
        errors.append("tools anchor missing")
    if errors:
        print("mismatch")
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    _v5d = _ensure_v5()
    print("applied" if _v5d else "already")
    sys.exit(0)
if _need_v4:
    if RERANK_V3_TAIL_OLD in _src1:
        _src1 = _src1.replace(RERANK_V3_TAIL_OLD, RERANK_V4_TAIL_NEW, 1)
    else:
        errors.append("rerank v4 anchor missing")
if _need_enrich:
    if ENRICH_READS_OLD in _src1:
        _src1 = _src1.replace(ENRICH_READS_OLD, ENRICH_READS_NEW, 1)
    else:
        errors.append("enrich reads anchor missing")
    if ENRICH_DICT_OLD in _src1:
        _src1 = _src1.replace(ENRICH_DICT_OLD, ENRICH_DICT_NEW, 1)
    else:
        errors.append("enrich dict anchor missing")
if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)
target.write_text(_src1, encoding="utf-8")
# tools/search.py: expose the rerank score (same marker guard).
_tools = pkg / "tools" / "search.py"
_t = _tools.read_text(encoding="utf-8")
if TOOLS_MARK not in _t:
    if TOOLS_OLD in _t:
        _tools.write_text(_t.replace(TOOLS_OLD, TOOLS_NEW, 1), encoding="utf-8")
    else:
        errors.append("tools anchor missing")
if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)
print("applied")
if _need_fusion:
    if FUSION_V1_OLD in _src1:
        _src1 = _src1.replace(FUSION_V1_OLD, FUSION_NEW, 1)
    elif FUSION_OLD in _src1:
        _src1 = _src1.replace(FUSION_OLD, FUSION_NEW, 1)
    else:
        errors.append("fusion anchor missing")
if _need_rescue:
    if RESCUE_V3A_OLD in _src1:
        _src1 = _src1.replace(RESCUE_V3A_OLD, RESCUE_V3B_NEW, 1)
    elif RESCUE_OLD in _src1:
        _src1 = _src1.replace(RESCUE_OLD, RESCUE_NEW, 1)
    else:
        errors.append("rescue anchor missing")
if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)
target.write_text(_src1, encoding="utf-8")
_ensure_v5()
print("applied")
