#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp hybrid search (BM25 + RRF) patch.

Why: zotero_semantic_search is dense-only (ChromaDB cosine), which misses
exact-match-critical academic content — variable names, model acronyms, author
names, formula fragments. This patch adds the sparse leg per mid-2026 best
practice: an Okapi BM25 inverted index over the SAME chunk documents stored in
ChromaDB, fused with the dense ranking via Reciprocal Rank Fusion (k=60,
rank-based — never score blending), feeding the existing cross-encoder
reranker. Pipeline: dense + BM25 -> RRF -> candidates -> reranker -> top-k.

Files (all in the zotero_mcp package dir passed as argv[1]):
- sparse_index.py          copied from zotero-mcp-sparse.py next to this script
- chroma_client.py         iter_documents() + get_documents(ids)
- semantic_search.py       config, process-wide index cache, _hybrid_search,
                           build hook in update-db, search() fusion branch
- config.py                HybridConfig dataclass + SemanticSearchConfig field

Marker comment: "[sparse patch]". Re-applied by sjust update; see
New-RAG-Setup.md (hybrid search) and Zotero-MCP.md.
Usage: zotero-mcp-sparse-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import shutil
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
src_sparse = here / "zotero-mcp-sparse.py"

errors: list[str] = []
changed = False

MARKER = "[sparse patch]"

CACHE_BLOCK = '''# [sparse patch] process-wide BM25 index cache (mirrors the reranker cache: the
# search path builds a fresh ZoteroSemanticSearch per request, so the index must
# be loaded once per process, not once per query).
_SPARSE_CACHE: dict[str, "BM25Index"] = {}
_SPARSE_CACHE_LOCK = threading.Lock()


def get_cached_sparse_index(index_path: str, k1: float = 1.5, b: float = 0.75):
    """Return the process-cached BM25Index for ``index_path``, or None if missing."""
    cached = _SPARSE_CACHE.get(index_path)
    if cached is not None:
        return cached
    with _SPARSE_CACHE_LOCK:
        cached = _SPARSE_CACHE.get(index_path)
        if cached is None:
            idx = _sparse.BM25Index(index_path, k1=k1, b=b)
            if not idx.load():
                return None
            _SPARSE_CACHE[index_path] = idx
        return _SPARSE_CACHE[index_path]


'''

HYBRID_HELPERS = '''    @staticmethod
    def _where_matches(meta: dict[str, Any], where: dict[str, Any] | None) -> bool:
        """[sparse patch] Evaluate a ChromaDB ``where`` clause against one metadata dict.

        Implements the operators this pipeline generates (equality on scalars,
        $and/$or, $contains/$not_contains on list fields) plus the common
        comparison operators, so the sparse leg respects the same group_id /
        collection / user-filter scoping as the dense leg. Applied as a
        post-filter over the sparse candidate set only — final order is
        rank-fused and reranked.
        """
        if where is None:
            return True
        for key, cond in where.items():
            if key == "$and":
                if not all(ZoteroSemanticSearch._where_matches(meta, c) for c in cond):
                    return False
                continue
            if key == "$or":
                if not any(ZoteroSemanticSearch._where_matches(meta, c) for c in cond):
                    return False
                continue
            val = meta.get(key)
            if isinstance(cond, dict):
                for op, operand in cond.items():
                    if op == "$contains":
                        if not (isinstance(val, list) and operand in val):
                            return False
                    elif op == "$not_contains":
                        if isinstance(val, list) and operand in val:
                            return False
                    elif op == "$in":
                        if val not in operand:
                            return False
                    elif op == "$nin":
                        if val in operand:
                            return False
                    elif op == "$eq":
                        if val != operand:
                            return False
                    elif op == "$ne":
                        if val == operand:
                            return False
                    elif op == "$gt":
                        if not (val is not None and val > operand):
                            return False
                    elif op == "$gte":
                        if not (val is not None and val >= operand):
                            return False
                    elif op == "$lt":
                        if not (val is not None and val < operand):
                            return False
                    elif op == "$lte":
                        if not (val is not None and val <= operand):
                            return False
            else:
                if val != cond:
                    return False
        return True

    def _get_sparse_index(self):
        """[sparse patch] Return the process-cached BM25 index, or None."""
        cfg = self._hybrid_config
        if not cfg.get("enabled", False):
            return None
        index_path = cfg.get("index_path") or str(
            Path.home() / ".config" / "zotero-mcp" / "bm25_index.json"
        )
        return get_cached_sparse_index(
            index_path,
            k1=float(cfg.get("bm25_k1", 1.5) or 1.5),
            b=float(cfg.get("bm25_b", 0.75) or 0.75),
        )

    def _build_sparse_index(self) -> dict[str, int | str]:
        """[sparse patch] Build + persist the BM25 index from current chunks."""
        import time as _time

        t0 = _time.monotonic()
        index_path = self._hybrid_config.get("index_path") or str(
            Path.home() / ".config" / "zotero-mcp" / "bm25_index.json"
        )
        idx = _sparse.BM25Index(index_path)
        docs: list[tuple[str, str]] = []
        for ids, documents, _metas in self.chroma_client.iter_documents():
            for doc_id, text in zip(ids, documents):
                if text:
                    docs.append((doc_id, text))
        idx.build(docs)
        idx.save()
        stats = idx.stats()
        stats["ms"] = int((_time.monotonic() - t0) * 1000)
        # Invalidate the process cache so queries see the fresh index.
        _SPARSE_CACHE.pop(index_path, None)
        return stats

    def _hybrid_search(self, query: str, fetch_limit: int, where, sparse_idx) -> dict[str, Any]:
        """[sparse patch] Dense + BM25 -> RRF -> candidate result set.

        Returns a results dict shaped like ``chroma_client.search`` (ids /
        documents / metadatas / distances as lists-of-lists) in fused order, so
        the existing reranker + enrichment stages run unchanged. Distances keep
        the dense cosine distance where the doc came from the dense leg;
        BM25-only rescues get 1.0 (no dense similarity measured — the display
        reads ``similarity = 1 - distance``, so they show ~0, which is honest:
        they surfaced via exact-match, not similarity).
        """
        dense = self.chroma_client.search(query_texts=[query], n_results=fetch_limit, where=where)
        dense_ids = (dense.get("ids") or [[]])[0]
        sparse_hits = sparse_idx.search(query, top_n=max(fetch_limit * 2, 20))
        if not sparse_hits:
            return dense
        # Respect scope filters on the sparse leg (post-filter the candidate set).
        if where is not None:
            _meta = self.chroma_client.get_documents([d for d, _ in sparse_hits])
            _keep = {
                mid
                for mid, m in zip(_meta["ids"], _meta["metadatas"])
                if self._where_matches(m, where)
            }
            sparse_hits = [(d, s) for d, s in sparse_hits if d in _keep]
        fused = _sparse.rrf_merge(
            [dense_ids, [d for d, _ in sparse_hits]],
            k=int(self._hybrid_config.get("rrf_k", 60) or 60),
        )
        fused_ids = [doc_id for doc_id, _ in fused[:fetch_limit]]
        if not fused_ids:
            return dense
        docs_by_id: dict[str, str] = {}
        metas_by_id: dict[str, Any] = {}
        dists_by_id: dict[str, float] = {}
        for i, doc_id in enumerate(dense_ids):
            docs_by_id[doc_id] = dense["documents"][0][i]
            metas_by_id[doc_id] = dense["metadatas"][0][i]
            dists_by_id[doc_id] = dense["distances"][0][i]
        missing = [d for d in fused_ids if d not in docs_by_id]
        if missing:
            fetched = self.chroma_client.get_documents(missing)
            for fid, fdoc, fmeta in zip(fetched["ids"], fetched["documents"], fetched["metadatas"]):
                docs_by_id[fid] = fdoc
                metas_by_id[fid] = fmeta
                dists_by_id[fid] = 1.0
        return {
            "ids": [fused_ids],
            "documents": [[docs_by_id[d] for d in fused_ids]],
            "metadatas": [[metas_by_id[d] for d in fused_ids]],
            "distances": [[dists_by_id[d] for d in fused_ids]],
        }

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


# --- 0. sparse_index.py -----------------------------------------------------
if not (pkg / "sparse_index.py").exists():
    if src_sparse.exists():
        shutil.copy2(src_sparse, pkg / "sparse_index.py")
        changed = True
    else:
        errors.append("sparse_index.py source (zotero-mcp-sparse.py) not found next to this script")

# --- 1. chroma_client.py ----------------------------------------------------
cc = pkg / "chroma_client.py"
if cc.exists():
    _apply(cc, [
        (
            '    def get_document_metadata(self, doc_id: str) -> dict[str, Any] | None:',
            '    def iter_documents(self, batch_size: int = 500) -> Iterator[tuple[list[str], list[str], list[dict[str, Any]]]]:\n'
            '        """[sparse patch] Stream ``(ids, documents, metadatas)`` over the collection.\n'
            '\n'
            '        Same snapshot-and-page discipline as :meth:`iter_metadatas` (no unbounded\n'
            '        ids list; no limit/offset pagination), but includes the document text —\n'
            '        the hybrid sparse index is built from exactly these documents so its\n'
            '        chunk boundaries match the dense leg by construction.\n'
            '        """\n'
            '        batch_size = min(int(batch_size), 5000)\n'
            '        all_ids = sorted(self.collection.get(include=[]).get("ids") or [])\n'
            '        for start in range(0, len(all_ids), batch_size):\n'
            '            chunk = all_ids[start:start + batch_size]\n'
            '            result = self.collection.get(ids=chunk, include=["documents", "metadatas"])\n'
            '            ids = result.get("ids") or []\n'
            '            if not ids:\n'
            '                continue\n'
            '            yield ids, result.get("documents") or [], result.get("metadatas") or []\n'
            '\n'
            '    def get_documents(self, ids: list[str]) -> dict[str, Any]:\n'
            '        """[sparse patch] Fetch documents + metadata by ids (hybrid candidate fetch)."""\n'
            '        if not ids:\n'
            '            return {"ids": [], "documents": [], "metadatas": []}\n'
            '        res = self.collection.get(ids=ids, include=["documents", "metadatas"])\n'
            '        return {\n'
            '            "ids": res.get("ids") or [],\n'
            '            "documents": res.get("documents") or [],\n'
            '            "metadatas": res.get("metadatas") or [],\n'
            '        }\n'
            '\n'
            '    def get_document_metadata(self, doc_id: str) -> dict[str, Any] | None:',
        ),
    ], "chroma_client.py")
else:
    errors.append("chroma_client.py not found")

# --- 2. semantic_search.py --------------------------------------------------
ss = pkg / "semantic_search.py"
if ss.exists():
    _apply(ss, [
        # 2a. import
        (
            'from . import mineru as _mineru  # [mineru patch] auto-MinerU before embedding (see zotero-mcp-mineru-patch.py)',
            'from . import mineru as _mineru  # [mineru patch] auto-MinerU before embedding (see zotero-mcp-mineru-patch.py)\n'
            'from . import sparse_index as _sparse  # [sparse patch] hybrid BM25+RRF (see zotero-mcp-sparse-patch.py)',
        ),
        # 2b. default + loader after load_reranker_config
        (
            '            config.update(file_config.get("semantic_search", {}).get("reranker", {}))\n'
            '        except Exception as e:\n'
            '            logger.warning(f"Error loading reranker config: {e}")\n'
            '    return config',
            '            config.update(file_config.get("semantic_search", {}).get("reranker", {}))\n'
            '        except Exception as e:\n'
            '            logger.warning(f"Error loading reranker config: {e}")\n'
            '    return config\n'
            '\n'
            '\n'
            '_DEFAULT_HYBRID_CONFIG: dict[str, Any] = {\n'
            '    "enabled": False,\n'
            '    "bm25_k1": 1.5,  # [sparse patch] Okapi BM25 defaults\n'
            '    "bm25_b": 0.75,\n'
            '    "rrf_k": 60,  # [sparse patch] Reciprocal Rank Fusion constant (Cormack et al. 2009)\n'
            '    "index_path": "",\n'
            '}\n'
            '\n'
            '\n'
            'def load_hybrid_config(config_path: str | None) -> dict[str, Any]:\n'
            '    """[sparse patch] Read the semantic-search ``hybrid`` block from disk."""\n'
            '    config = dict(_DEFAULT_HYBRID_CONFIG)\n'
            '    if config_path and os.path.exists(config_path):\n'
            '        try:\n'
            '            with open(config_path) as f:\n'
            '                file_config = json.load(f)\n'
            '            config.update(file_config.get("semantic_search", {}).get("hybrid", {}))\n'
            '        except Exception as e:\n'
            '            logger.warning(f"Error loading hybrid config: {e}")\n'
            '    return config',
        ),
        # 2c. instance config in __init__
        (
            '        self._reranker_config = self._load_reranker_config()',
            '        self._reranker_config = self._load_reranker_config()\n'
            '        self._hybrid_config = self._load_hybrid_config()  # [sparse patch]',
        ),
        # 2d. _load_hybrid_config method
        (
            '    def _load_reranker_config(self) -> dict[str, Any]:\n'
            '        """Load reranker configuration from file or use defaults."""\n'
            '        return load_reranker_config(self.config_path)',
            '    def _load_reranker_config(self) -> dict[str, Any]:\n'
            '        """Load reranker configuration from file or use defaults."""\n'
            '        return load_reranker_config(self.config_path)\n'
            '\n'
            '    def _load_hybrid_config(self) -> dict[str, Any]:\n'
            '        """[sparse patch] Load hybrid-search configuration from file or use defaults."""\n'
            '        return load_hybrid_config(self.config_path)',
        ),
        # 2e. process-wide cache after the reranker cache
        (
            '        if cached is None:\n'
            '            cached = CrossEncoderReranker(model_name=model_name)\n'
            '            _RERANKER_CACHE[model_name] = cached\n'
            '        return cached',
            '        if cached is None:\n'
            '            cached = CrossEncoderReranker(model_name=model_name)\n'
            '            _RERANKER_CACHE[model_name] = cached\n'
            '        return cached\n'
            '\n'
            '\n'
            + CACHE_BLOCK,
        ),
        # 2f. hybrid helpers before search()
        (
            '    def search(self,\n'
            '               query: str,\n'
            '               limit: int = 10,\n'
            '               filters: dict[str, Any] | None = None,\n'
            '               group_id: int | None = None,\n'
            '               collection_key: str | None = None) -> dict[str, Any]:',
            HYBRID_HELPERS +
            '    def search(self,\n'
            '               query: str,\n'
            '               limit: int = 10,\n'
            '               filters: dict[str, Any] | None = None,\n'
            '               group_id: int | None = None,\n'
            '               collection_key: str | None = None) -> dict[str, Any]:',
        ),
        # 2g. fusion branch in search()
        (
            '            # Perform semantic search\n'
            '            results = self.chroma_client.search(query_texts=[query], n_results=fetch_limit, where=where)',
            '            # [sparse patch] hybrid search: dense + BM25 -> RRF -> candidates.\n'
            '            sparse_idx = self._get_sparse_index()\n'
            '            if sparse_idx is not None:\n'
            '                results = self._hybrid_search(query, fetch_limit, where, sparse_idx)\n'
            '            else:\n'
            '                results = self.chroma_client.search(query_texts=[query], n_results=fetch_limit, where=where)',
        ),
        # 2h. build hook after the collections sync
        (
            '                    logger.warning(f"collections metadata sync failed: {e}")\n'
            '\n'
            '            # Unattributed docs are excluded from library-filtered search and',
            '                    logger.warning(f"collections metadata sync failed: {e}")\n'
            '\n'
            '            # [sparse patch] rebuild the BM25 sparse index from current chunks\n'
            '            # (cheap local pass: ~seconds; keeps the hybrid leg fresh after any\n'
            '            # add/update/delete). Chunk boundaries match ChromaDB by construction.\n'
            '            try:\n'
            '                _sparse_stats = self._build_sparse_index()\n'
            '                sys.stderr.write(\n'
            '                    f"Rebuilt sparse index: {_sparse_stats[\'docs\']} docs, "\n'
            '                    f"{_sparse_stats[\'terms\']} terms, {_sparse_stats[\'ms\']} ms.\\n"\n'
            '                )\n'
            '            except Exception as _e:\n'
            '                logger.warning(f"sparse index build failed: {_e}")\n'
            '\n'
            '            # Unattributed docs are excluded from library-filtered search and',
        ),
    ], "semantic_search.py")
else:
    errors.append("semantic_search.py not found")

# --- 3. config.py -----------------------------------------------------------
cfg = pkg / "config.py"
if cfg.exists():
    _apply(cfg, [
        (
            '@dataclass\n'
            'class ChunkingConfig:',
            '@dataclass\n'
            'class HybridConfig:  # [sparse patch] hybrid search (BM25 + RRF)\n'
            '    enabled: bool = False\n'
            '    bm25_k1: float = 1.5\n'
            '    bm25_b: float = 0.75\n'
            '    rrf_k: int = 60\n'
            '    index_path: str = ""\n'
            '\n'
            '\n'
            '@dataclass\n'
            'class ChunkingConfig:',
        ),
        (
            '    reranker: RerankerConfig = field(default_factory=RerankerConfig)\n'
            '    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)',
            '    reranker: RerankerConfig = field(default_factory=RerankerConfig)\n'
            '    hybrid: HybridConfig = field(default_factory=HybridConfig)  # [sparse patch]\n'
            '    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)',
        ),
    ], "config.py")
else:
    errors.append("config.py not found")

if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("applied" if changed else "already")
