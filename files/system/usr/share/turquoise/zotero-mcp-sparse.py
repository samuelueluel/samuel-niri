"""BM25 sparse leg + RRF fusion for zotero hybrid search. [sparse patch]

Copied into the zotero_mcp package by zotero-mcp-sparse-patch.py (same pattern
as zotero-mcp-mineru.py). Pure stdlib — no new dependencies, so `uv tool
upgrade` cannot break it.

Best-practice notes (mid-2026):
- RRF fuses RANKS with k=60 (Cormack, Clarke, Buettcher 2009) — never blend
  raw scores: BM25 scores are unbounded while cosine similarity is bounded in
  [-1, 1], so a naive weighted blend is "BM25 with noise". k=60 is the
  untuned default used by Elasticsearch, OpenSearch, Qdrant, Weaviate and
  Azure AI Search.
- Okapi BM25 defaults k1=1.5, b=0.75.
- Academic-aware tokenization: lowercase alphanumeric runs, no stemming
  (variable names, acronyms, formula fragments are the sparse leg's job).
"""

import json
import math
import re
from pathlib import Path

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Analyzer tuned for academic text.

    Lowercase alphanumeric runs only (drops punctuation, LaTeX braces and
    backslashes, HTML tags). No stemming: exact-match tokens like variable
    names, acronyms and formula fragments are the point of the sparse leg;
    stemming would mangle them. Single-char tokens dropped as noise.
    """
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1]


def rrf_merge(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion.

    score(d) = sum over lists of 1 / (k + rank(d)). Fuses ranks, never raw
    scores. Returns (doc_id, rrf_score) pairs sorted desc.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


class BM25Index:
    """Okapi BM25 inverted index over chunk documents.

    Built from the exact documents stored in ChromaDB (``iter_documents``), so
    chunk boundaries match the dense leg by construction. Persisted as JSON;
    rebuilt on each update-db run (~seconds at this library size) and loaded
    once per process at query time.
    """

    def __init__(self, path: str | Path, k1: float = 1.5, b: float = 0.75):
        self.path = Path(path)
        self.k1 = k1
        self.b = b
        self._df: dict[str, int] = {}
        self._postings: dict[str, dict[str, int]] = {}
        self._doc_len: dict[str, int] = {}
        self._n_docs = 0
        self._avgdl = 0.0

    # -- build ---------------------------------------------------------------
    def build(self, documents: list[tuple[str, str]]) -> None:
        self._df = {}
        self._postings = {}
        self._doc_len = {}
        for doc_id, text in documents:
            tokens = tokenize(text)
            if not tokens:
                continue
            self._doc_len[doc_id] = len(tokens)
            seen: set[str] = set()
            for tok in tokens:
                post = self._postings.setdefault(tok, {})
                post[doc_id] = post.get(doc_id, 0) + 1
                if tok not in seen:
                    seen.add(tok)
                    self._df[tok] = self._df.get(tok, 0) + 1
        self._n_docs = len(self._doc_len)
        self._avgdl = (sum(self._doc_len.values()) / self._n_docs) if self._n_docs else 0.0

    # -- persistence ---------------------------------------------------------
    def save(self) -> None:
        payload = {
            "k1": self.k1,
            "b": self.b,
            "n_docs": self._n_docs,
            "avgdl": self._avgdl,
            "df": self._df,
            "postings": self._postings,
            "doc_len": self._doc_len,
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return False
        self.k1 = float(payload.get("k1", 1.5))
        self.b = float(payload.get("b", 0.75))
        self._n_docs = int(payload.get("n_docs", 0))
        self._avgdl = float(payload.get("avgdl", 0.0))
        self._df = payload.get("df", {})
        self._postings = payload.get("postings", {})
        self._doc_len = payload.get("doc_len", {})
        return self._n_docs > 0

    # -- query ---------------------------------------------------------------
    def search(self, query: str, top_n: int = 50) -> list[tuple[str, float]]:
        """Return (doc_id, bm25_score) pairs for the top_n documents, desc."""
        terms = [t for t in tokenize(query) if t in self._postings]
        if not terms or not self._n_docs:
            return []
        idf = {
            t: math.log(1.0 + (self._n_docs - self._df[t] + 0.5) / (self._df[t] + 0.5))
            for t in terms
        }
        scores: dict[str, float] = {}
        avgdl = self._avgdl or 1.0
        for t in terms:
            t_idf = idf[t]
            for doc_id, tf in self._postings[t].items():
                dl = self._doc_len.get(doc_id, 0)
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                scores[doc_id] = scores.get(doc_id, 0.0) + t_idf * (tf * (self.k1 + 1.0)) / denom
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    def stats(self) -> dict[str, int | str]:
        return {"docs": self._n_docs, "terms": len(self._df), "path": str(self.path)}
