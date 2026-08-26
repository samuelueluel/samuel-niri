#!/usr/bin/env python3
"""Apply the BM25 + Reciprocal Rank Fusion patch to zotero_mcp.

The patch adds a pure-stdlib sparse index over the same chunks stored in
ChromaDB, fuses dense and BM25 ranks with RRF, and sends the fused candidates
to the existing reranker. The later hybrid-filter patch tightens bibliography
hygiene and score provenance; this script establishes a correct standalone
hybrid baseline first.

Every required anchor is validated before package files are written. Component
markers, rather than one file-wide marker, make interrupted/partial historical
applications recoverable.

Usage: zotero-mcp-sparse-patch.py <path/to/zotero_mcp-package-dir>
Prints: ``applied`` | ``already`` | ``mismatch``.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


if len(sys.argv) != 2:
    print("usage: zotero-mcp-sparse-patch.py <zotero_mcp-package-dir>", file=sys.stderr)
    sys.exit(2)

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
source_sparse = here / "zotero-mcp-sparse.py"
paths = {
    "chroma_client.py": pkg / "chroma_client.py",
    "semantic_search.py": pkg / "semantic_search.py",
    "config.py": pkg / "config.py",
}
errors: list[str] = []
original: dict[str, str] = {}
work: dict[str, str] = {}

if not source_sparse.exists():
    errors.append("zotero-mcp-sparse.py not found next to patch")
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


CHROMA_METHODS = '''    def iter_documents(
        self, batch_size: int = 500
    ) -> Iterator[tuple[list[str], list[str], list[dict[str, Any]]]]:
        """[sparse patch] Stream ids, documents, and metadata in bounded batches."""
        batch_size = min(int(batch_size), 5000)
        all_ids = sorted(self.collection.get(include=[]).get("ids") or [])
        for start in range(0, len(all_ids), batch_size):
            chunk = all_ids[start : start + batch_size]
            result = self.collection.get(
                ids=chunk, include=["documents", "metadatas"]
            )
            ids = result.get("ids") or []
            if ids:
                yield (
                    ids,
                    result.get("documents") or [],
                    result.get("metadatas") or [],
                )

    def get_documents(self, ids: list[str]) -> dict[str, Any]:
        """[sparse patch] Fetch documents and metadata for candidate ids."""
        if not ids:
            return {"ids": [], "documents": [], "metadatas": []}
        result = self.collection.get(ids=ids, include=["documents", "metadatas"])
        return {
            "ids": result.get("ids") or [],
            "documents": result.get("documents") or [],
            "metadatas": result.get("metadatas") or [],
        }

'''

HYBRID_CONFIG = '''

_DEFAULT_HYBRID_CONFIG: dict[str, Any] = {
    "enabled": False,
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
    "rrf_k": 60,
    "index_path": "",
}


def load_hybrid_config(config_path: str | None) -> dict[str, Any]:
    """[sparse patch] Read the semantic-search ``hybrid`` block."""
    config = dict(_DEFAULT_HYBRID_CONFIG)
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path) as config_file:
                file_config = json.load(config_file)
            config.update(file_config.get("semantic_search", {}).get("hybrid", {}))
        except Exception as exc:
            logger.warning("Error loading hybrid config: %s", exc)
    return config
'''

CACHE_BLOCK = '''# [sparse patch] process-wide BM25 cache. A fresh search object is created per
# request, so loading the persisted index per request would be wasteful.
_SPARSE_CACHE: dict[str, "_sparse.BM25Index"] = {}
_SPARSE_CACHE_LOCK = threading.Lock()


def get_cached_sparse_index(index_path: str, k1: float = 1.5, b: float = 0.75):
    """Return the cached BM25 index, or None when no persisted index exists."""
    cached = _SPARSE_CACHE.get(index_path)
    if cached is not None:
        return cached
    with _SPARSE_CACHE_LOCK:
        cached = _SPARSE_CACHE.get(index_path)
        if cached is None:
            index = _sparse.BM25Index(index_path, k1=k1, b=b)
            if not index.load():
                return None
            _SPARSE_CACHE[index_path] = index
        return _SPARSE_CACHE[index_path]


'''

HYBRID_METHODS = '''    @staticmethod
    def _where_matches(meta: dict[str, Any], where: dict[str, Any] | None) -> bool:
        """[sparse patch] Evaluate generated Chroma where-clauses for BM25 hits."""
        if where is None:
            return True
        for key, condition in where.items():
            if key == "$and":
                if not all(ZoteroSemanticSearch._where_matches(meta, part) for part in condition):
                    return False
                continue
            if key == "$or":
                if not any(ZoteroSemanticSearch._where_matches(meta, part) for part in condition):
                    return False
                continue
            value = meta.get(key)
            if isinstance(condition, dict):
                for operator, operand in condition.items():
                    if operator == "$contains":
                        if not isinstance(value, list) or operand not in value:
                            return False
                    elif operator == "$not_contains":
                        if isinstance(value, list) and operand in value:
                            return False
                    elif operator == "$in":
                        if value not in operand:
                            return False
                    elif operator == "$nin":
                        if value in operand:
                            return False
                    elif operator == "$eq" and value != operand:
                        return False
                    elif operator == "$ne" and value == operand:
                        return False
                    elif operator == "$gt" and not (value is not None and value > operand):
                        return False
                    elif operator == "$gte" and not (value is not None and value >= operand):
                        return False
                    elif operator == "$lt" and not (value is not None and value < operand):
                        return False
                    elif operator == "$lte" and not (value is not None and value <= operand):
                        return False
            elif value != condition:
                return False
        return True

    def _get_sparse_index(self):
        """[sparse patch] Return the configured process-cached BM25 index."""
        config = self._hybrid_config
        if not config.get("enabled", False):
            return None
        index_path = config.get("index_path") or str(
            Path.home() / ".config" / "zotero-mcp" / "bm25_index.json"
        )
        return get_cached_sparse_index(
            index_path,
            k1=float(config.get("bm25_k1", 1.5) or 1.5),
            b=float(config.get("bm25_b", 0.75) or 0.75),
        )

    def _build_sparse_index(self) -> dict[str, int | str]:
        """[sparse patch] Build and persist BM25 from current Chroma chunks."""
        import time as _time

        started = _time.monotonic()
        index_path = self._hybrid_config.get("index_path") or str(
            Path.home() / ".config" / "zotero-mcp" / "bm25_index.json"
        )
        index = _sparse.BM25Index(index_path)
        documents: list[tuple[str, str]] = []
        for ids, texts, _metadatas in self.chroma_client.iter_documents():
            documents.extend(
                (doc_id, text)
                for doc_id, text in zip(ids, texts)
                if text
            )
        index.build(documents)
        index.save()
        stats = index.stats()
        stats["ms"] = int((_time.monotonic() - started) * 1000)
        _SPARSE_CACHE.pop(index_path, None)
        return stats

    def _hybrid_search(self, query: str, fetch_limit: int, where, sparse_idx) -> dict[str, Any]:
        """[sparse patch] Fuse dense and BM25 ranks with RRF."""
        dense = self.chroma_client.search(
            query_texts=[query], n_results=fetch_limit, where=where
        )
        dense_ids = (dense.get("ids") or [[]])[0]
        sparse_hits = sparse_idx.search(query, top_n=max(fetch_limit * 2, 20))
        if not sparse_hits:
            return dense
        if where is not None:
            payload = self.chroma_client.get_documents(
                [doc_id for doc_id, _ in sparse_hits]
            )
            allowed = {
                doc_id
                for doc_id, metadata in zip(payload["ids"], payload["metadatas"])
                if self._where_matches(metadata, where)
            }
            sparse_hits = [
                (doc_id, score)
                for doc_id, score in sparse_hits
                if doc_id in allowed
            ]
        fused = _sparse.rrf_merge(
            [dense_ids, [doc_id for doc_id, _ in sparse_hits]],
            k=int(self._hybrid_config.get("rrf_k", 60) or 60),
        )
        fused_ids = [doc_id for doc_id, _ in fused[:fetch_limit]]
        if not fused_ids:
            return dense

        documents_by_id: dict[str, str] = {}
        metadata_by_id: dict[str, Any] = {}
        distance_by_id: dict[str, float] = {}
        for index, doc_id in enumerate(dense_ids):
            documents_by_id[doc_id] = dense["documents"][0][index]
            metadata_by_id[doc_id] = dense["metadatas"][0][index]
            distance_by_id[doc_id] = dense["distances"][0][index]
        missing = [doc_id for doc_id in fused_ids if doc_id not in documents_by_id]
        if missing:
            payload = self.chroma_client.get_documents(missing)
            for doc_id, document, metadata in zip(
                payload["ids"], payload["documents"], payload["metadatas"]
            ):
                documents_by_id[doc_id] = document
                metadata_by_id[doc_id] = metadata
                distance_by_id[doc_id] = 1.0
        return {
            "ids": [fused_ids],
            "documents": [[documents_by_id[doc_id] for doc_id in fused_ids]],
            "metadatas": [[metadata_by_id[doc_id] for doc_id in fused_ids]],
            "distances": [[distance_by_id[doc_id] for doc_id in fused_ids]],
        }

'''

HYBRID_DATACLASS = '''@dataclass
class HybridConfig:  # [sparse patch]
    enabled: bool = False
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    rrf_k: int = 60
    index_path: str = ""


'''

# Chroma candidate/document iteration.
if "chroma_client.py" in work and "def iter_documents(" not in work["chroma_client.py"]:
    insert_before_once(
        "chroma_client.py",
        "    def get_document_metadata(self, doc_id: str) -> dict[str, Any] | None:",
        CHROMA_METHODS,
        "document helpers",
    )

# Hybrid plumbing in semantic_search.py.
if "semantic_search.py" in work:
    name = "semantic_search.py"
    if "from . import sparse_index as _sparse" not in work[name]:
        replace_once(
            name,
            "from .chroma_client import ChromaClient, create_chroma_client\n",
            "from .chroma_client import ChromaClient, create_chroma_client\n"
            "from . import sparse_index as _sparse  # [sparse patch]\n",
            "sparse import",
        )
    if "def load_hybrid_config(" not in work[name]:
        replace_once(
            name,
            "from .utils import _paginate, format_creators, is_local_mode, suppress_stdout\n",
            "from .utils import _paginate, format_creators, is_local_mode, suppress_stdout\n"
            + HYBRID_CONFIG,
            "hybrid config",
        )
    if "_SPARSE_CACHE:" not in work[name]:
        insert_before_once(name, "class ZoteroSemanticSearch:", CACHE_BLOCK, "cache")
    if "self._hybrid_config = self._load_hybrid_config()" not in work[name]:
        replace_once(
            name,
            "        self._reranker_config = self._load_reranker_config()\n",
            "        self._reranker_config = self._load_reranker_config()\n"
            "        self._hybrid_config = self._load_hybrid_config()  # [sparse patch]\n",
            "instance config",
        )
    if "    def _load_hybrid_config(" not in work[name]:
        replace_once(
            name,
            "    def _load_reranker_config(self) -> dict[str, Any]:\n"
            "        \"\"\"Load reranker configuration from file or use defaults.\"\"\"\n"
            "        return load_reranker_config(self.config_path)\n",
            "    def _load_reranker_config(self) -> dict[str, Any]:\n"
            "        \"\"\"Load reranker configuration from file or use defaults.\"\"\"\n"
            "        return load_reranker_config(self.config_path)\n\n"
            "    def _load_hybrid_config(self) -> dict[str, Any]:\n"
            "        \"\"\"[sparse patch] Load hybrid-search configuration.\"\"\"\n"
            "        return load_hybrid_config(self.config_path)\n",
            "config loader",
        )
    if "    def _get_sparse_index(" not in work[name]:
        anchor = (
            "    def _resolve_collection_item_keys("
            if "    def _resolve_collection_item_keys(" in work[name]
            else "    def search(self,"
        )
        insert_before_once(name, anchor, HYBRID_METHODS, "hybrid methods")
    if "# [sparse patch] hybrid search:" not in work[name]:
        replace_once(
            name,
            "            # Perform semantic search\n"
            "            results = self.chroma_client.search(query_texts=[query], n_results=fetch_limit, where=where)",
            "            # [sparse patch] hybrid search: dense + BM25 -> RRF -> candidates.\n"
            "            sparse_index = self._get_sparse_index()\n"
            "            if sparse_index is not None:\n"
            "                results = self._hybrid_search(query, fetch_limit, where, sparse_index)\n"
            "            else:\n"
            "                results = self.chroma_client.search(\n"
            "                    query_texts=[query], n_results=fetch_limit, where=where\n"
            "                )",
            "search branch",
        )
    if "# [sparse patch] rebuild BM25 after successful indexing" not in work[name]:
        insert_before_once(
            name,
            "            # Update last update time, and promote last_sync_version on success.\n",
            "            # [sparse patch] rebuild BM25 after successful indexing.\n"
            "            if self._hybrid_config.get(\"enabled\", False):\n"
            "                try:\n"
            "                    sparse_stats = self._build_sparse_index()\n"
            "                    logger.info(\n"
            "                        \"Rebuilt sparse index: %s docs, %s terms, %s ms\",\n"
            "                        sparse_stats.get(\"docs\"),\n"
            "                        sparse_stats.get(\"terms\"),\n"
            "                        sparse_stats.get(\"ms\"),\n"
            "                    )\n"
            "                except Exception as exc:\n"
            "                    logger.warning(\"sparse index build failed: %s\", exc)\n\n",
            "build hook",
        )

# Typed config support.
if "config.py" in work:
    name = "config.py"
    if "class HybridConfig" not in work[name]:
        insert_before_once(name, "@dataclass\nclass ChunkingConfig:", HYBRID_DATACLASS, "dataclass")
    if "    hybrid: HybridConfig" not in work[name]:
        replace_once(
            name,
            "    reranker: RerankerConfig = field(default_factory=RerankerConfig)\n"
            "    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)",
            "    reranker: RerankerConfig = field(default_factory=RerankerConfig)\n"
            "    hybrid: HybridConfig = field(default_factory=HybridConfig)  # [sparse patch]\n"
            "    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)",
            "semantic config field",
        )

if errors:
    print("mismatch")
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

changed_files = [name for name in work if work[name] != original[name]]
for name in changed_files:
    path = paths[name]
    tmp = path.with_name(path.name + ".sparse-patch.tmp")
    tmp.write_text(work[name], encoding="utf-8")
    os.replace(tmp, path)

sparse_changed = False
destination_sparse = pkg / "sparse_index.py"
if source_sparse.exists() and (
    not destination_sparse.exists()
    or destination_sparse.read_bytes() != source_sparse.read_bytes()
):
    tmp_sparse = destination_sparse.with_name(destination_sparse.name + ".sparse-patch.tmp")
    shutil.copy2(source_sparse, tmp_sparse)
    os.replace(tmp_sparse, destination_sparse)
    sparse_changed = True

print("applied" if changed_files or sparse_changed else "already")
