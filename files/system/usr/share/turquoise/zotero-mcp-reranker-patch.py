#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp HTTP reranker patch.

Why: the packaged CrossEncoderReranker runs sentence-transformers in-process on
CPU-only torch (the zotero-mcp-server venv has no GPU torch), so a quality
reranker like bge-reranker-v2-m3 takes minutes per search. This patch adds an
HttpCrossEncoderReranker that delegates scoring to a local llama.cpp
llama-server running ``--reranking`` (ramalama ``reranker`` container, port
8083, bge-reranker-v2-m3 Q8_0) over the OpenAI-compatible /v1/rerank endpoint.
Enabled by setting ``semantic_search.reranker.url`` in
~/.config/zotero-mcp/config.json; when ``url`` is unset the in-process
sentence-transformers path is unchanged (config flip only — nothing else
changes).

Context-window guard: bge-reranker-v2-m3 caps at 8192 tokens and the search
path over-fetches 20-40 candidate chunks (~400-600 tokens each), so the HTTP
call is split into batches of ``reranker.batch_size`` documents (default 12);
a single unbatched call 500s on context overflow.

Files (all in the zotero_mcp package dir passed as argv[1]):
- semantic_search.py   HttpCrossEncoderReranker + url-aware _get_reranker + warmup skip
- config.py            RerankerConfig gains url/timeout/batch_size fields

Marker comment: "[http reranker patch]". Re-applied by sjust update; see
New-RAG-Setup.md (Reranker + hybrid integration effort) and General-Tooling.
Usage: zotero-mcp-reranker-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
errors: list[str] = []
changed = False

MARKER = "[http reranker patch]"

CLASS = '''class HttpCrossEncoderReranker:
    """[http reranker patch] Cross-encoder reranker over a local llama.cpp /v1/rerank endpoint.

    Same interface as :class:`CrossEncoderReranker` (``rerank`` /
    ``rerank_with_scores``) but delegates scoring to a llama-server
    running ``--reranking`` (ramalama ``reranker`` container, port 8083,
    bge-reranker-v2-m3 Q8_0). Stateless: a fresh instance per request is
    cheap, so no process-wide cache is needed. Enabled by setting
    ``semantic_search.reranker.url`` in the server config; when unset the
    in-process sentence-transformers path is unchanged.

    Requests are sent in batches of ``batch_size`` documents so the total
    prompt stays under the model's context window (bge-reranker-v2-m3 caps
    at 8192 tokens; ~400-600-token chunks x 20-40 candidates overflow it).
    """

    def __init__(self, url: str, timeout: float = 60.0, batch_size: int = 12):
        import requests

        self.endpoint = url
        self.timeout = timeout
        self.batch_size = max(1, int(batch_size))
        self._requests = requests

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[int]:
        """Re-rank documents by relevance to query; return top_k indices desc."""
        return [idx for idx, _ in self.rerank_with_scores(query, documents, top_k)]

    def rerank_with_scores(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """Re-rank documents, returning ``(index, score)`` pairs desc by score.

        Documents are scored in batches of ``batch_size`` (context-window
        guard); batch results are merged by original index. On endpoint
        failure returns the original candidate order with zero scores
        (graceful degradation - the search still returns results, just
        unreranked) and logs a warning.
        """
        if not documents:
            return []
        n = len(documents)
        scores: dict[int, float] = {}
        for start in range(0, n, self.batch_size):
            batch = documents[start:start + self.batch_size]
            try:
                resp = self._requests.post(
                    self.endpoint,
                    json={"query": query, "documents": batch},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                for r in resp.json().get("results", []):
                    scores[start + int(r["index"])] = float(r.get("relevance_score", 0.0))
            except Exception as e:
                logger.warning(
                    f"HTTP reranker error ({self.endpoint}, batch {start // self.batch_size}): "
                    f"{e}; returning unreranked order"
                )
                return [(i, 0.0) for i in range(min(top_k, n))]
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]


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


# --- 1. semantic_search.py -------------------------------------------------
ss = pkg / "semantic_search.py"
if ss.exists():
    _apply(ss, [
        # 1a. default config gains url/timeout/batch_size keys
        (
            '_DEFAULT_RERANKER_CONFIG: dict[str, Any] = {\n'
            '    "enabled": False,\n'
            '    "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",\n'
            '    "candidate_multiplier": 3,\n'
            '}',
            '_DEFAULT_RERANKER_CONFIG: dict[str, Any] = {\n'
            '    "enabled": False,\n'
            '    "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",\n'
            '    "candidate_multiplier": 3,\n'
            '    "url": "",  # [http reranker patch] llama.cpp /v1/rerank endpoint (e.g. http://127.0.0.1:8083/v1/rerank); when set, rerank over HTTP\n'
            '    "timeout": 60.0,\n'
            '    "batch_size": 12,  # [http reranker patch] docs per /v1/rerank call (ctx-window guard; bge-reranker-v2-m3 caps at 8192 tokens)\n'
            '}',
        ),
        # 1b. warmup skips the in-process load when the HTTP endpoint is set
        (
            '    cfg = load_reranker_config(config_path)\n'
            '    if not cfg.get("enabled", False):\n'
            '        return False\n'
            '    model = cfg.get("model", _DEFAULT_RERANKER_CONFIG["model"])\n'
            '    try:\n'
            '        get_cached_reranker(model)\n'
            '        return True',
            '    cfg = load_reranker_config(config_path)\n'
            '    if not cfg.get("enabled", False):\n'
            '        return False\n'
            '    if cfg.get("url"):  # [http reranker patch] HTTP reranker is stateless - nothing to warm\n'
            '        return True\n'
            '    model = cfg.get("model", _DEFAULT_RERANKER_CONFIG["model"])\n'
            '    try:\n'
            '        get_cached_reranker(model)\n'
            '        return True',
        ),
        # 1c. insert HttpCrossEncoderReranker before the process-wide cache
        (
            '# Process-wide reranker cache (issue #283).',
            CLASS + '# Process-wide reranker cache (issue #283).',
        ),
        # 1d. url-aware _get_reranker
        (
            '        if not self._reranker_config.get("enabled", False):\n'
            '            return None\n'
            '        if self._reranker is None:\n'
            '            model = self._reranker_config.get("model", _DEFAULT_RERANKER_CONFIG["model"])\n'
            '            self._reranker = get_cached_reranker(model)\n'
            '        return self._reranker',
            '        if not self._reranker_config.get("enabled", False):\n'
            '            return None\n'
            '        url = self._reranker_config.get("url") or ""\n'
            '        if url:\n'
            '            # [http reranker patch] HTTP reranker: stateless client, no warm cache needed\n'
            '            if self._reranker is None or getattr(self._reranker, "endpoint", None) != url:\n'
            '                self._reranker = HttpCrossEncoderReranker(\n'
            '                    url,\n'
            '                    timeout=float(self._reranker_config.get("timeout", 60) or 60),\n'
            '                    batch_size=int(self._reranker_config.get("batch_size", 12) or 12),\n'
            '                )\n'
            '            return self._reranker\n'
            '        if self._reranker is None:\n'
            '            model = self._reranker_config.get("model", _DEFAULT_RERANKER_CONFIG["model"])\n'
            '            self._reranker = get_cached_reranker(model)\n'
            '        return self._reranker',
        ),
    ], "semantic_search.py")
else:
    errors.append("semantic_search.py not found")

# --- 2. config.py ----------------------------------------------------------
cfg = pkg / "config.py"
if cfg.exists():
    _apply(cfg, [
        (
            '@dataclass\n'
            'class RerankerConfig:\n'
            '    enabled: bool = False\n'
            '    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"\n'
            '    candidate_multiplier: int = 3',
            '@dataclass\n'
            'class RerankerConfig:\n'
            '    enabled: bool = False\n'
            '    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"\n'
            '    candidate_multiplier: int = 3\n'
            '    url: str = ""  # [http reranker patch] llama.cpp /v1/rerank endpoint; when set, rerank over HTTP\n'
            '    timeout: float = 60.0\n'
            '    batch_size: int = 12  # [http reranker patch] docs per /v1/rerank call (ctx-window guard)',
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
