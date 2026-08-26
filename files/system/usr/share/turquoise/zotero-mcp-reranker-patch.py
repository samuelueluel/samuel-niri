#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp HTTP reranker patch.

The local Zotero service runs a llama.cpp reranker at
``http://127.0.0.1:8083/v1/rerank``.  The upstream semantic-search module uses
sentence-transformers when it sees an enabled reranker.  This patch makes the
configured ``semantic_search.reranker.url`` authoritative, so the MCP server
uses the local endpoint instead of attempting to resolve the model name from
Hugging Face.

The patch is intentionally applied to the installed package at runtime; this
file is the durable source kept in the Turquoise image recipe.  It supports
both the current package layout (where reranker defaults live in
``config_light.py``) and the older layout (where they were defined in
``semantic_search.py``).

Marker comment: "[http reranker patch]".
"""
from __future__ import annotations

import sys
from pathlib import Path


if len(sys.argv) != 2:
    print("usage: zotero-mcp-reranker-patch.py <zotero_mcp-package-dir>", file=sys.stderr)
    sys.exit(2)

pkg = Path(sys.argv[1])
errors: list[str] = []
changed = False
MARKER = "[http reranker patch]"
LOCAL_ONLY_MARKER = "[local-only reranker patch]"

HTTP_CLASS = '''class HttpCrossEncoderReranker:
    """[http reranker patch] [local-only reranker patch] Local HTTP reranker.

    The endpoint is served by the local bge-reranker-v2-m3 GGUF model.  It has
    the same public methods as :class:`CrossEncoderReranker`, but this pipeline
    never falls back to an in-process or Hugging Face model. Requests are
    batched to stay below the model context window.
    """

    def __init__(self, url: str, timeout: float = 60.0, batch_size: int = 12):
        import requests

        self.endpoint = url.rstrip("/")
        self.timeout = timeout
        self.batch_size = max(1, int(batch_size))
        self._requests = requests

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[int]:
        return [idx for idx, _ in self.rerank_with_scores(query, documents, top_k)]

    def rerank_with_scores(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """Score documents in bounded batches and return descending scores.

        Local-only policy: if the endpoint fails, raise a clear error rather
        than silently substituting unreranked or remote/model-hub retrieval.
        """
        if not documents:
            return []
        n = len(documents)
        scores: dict[int, float] = {}
        for start in range(0, n, self.batch_size):
            batch = documents[start : start + self.batch_size]
            try:
                response = self._requests.post(
                    self.endpoint,
                    json={"query": query, "documents": batch},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                rows = response.json().get("results", [])
                for row in rows:
                    index = int(row["index"])
                    if 0 <= index < len(batch):
                        scores[start + index] = float(row.get("relevance_score", 0.0))
                if len(scores) < start + len(batch):
                    raise ValueError("reranker response omitted one or more documents")
            except Exception as exc:
                # Local-only policy: do not silently substitute unreranked or
                # remote/model-hub retrieval when the required local service fails.
                logger.error(
                    f"Local HTTP reranker error ({self.endpoint}, batch {start // self.batch_size}): {exc}"
                )
                raise RuntimeError(
                    f"Local reranker endpoint failed at {self.endpoint} "
                    f"(batch {start // self.batch_size}): {exc}"
                ) from exc
        ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        return ranked[:top_k]


'''


def patch_file(path: Path, replacements: list[tuple[str, str]], name: str, *, marker_ok: bool = True) -> None:
    """Apply all replacements atomically when every anchor is present."""
    global changed
    if not path.exists():
        errors.append(f"{name} not found")
        return
    src = path.read_text(encoding="utf-8")
    if marker_ok and MARKER in src:
        return
    work = src
    for old, new in replacements:
        if old not in work:
            errors.append(f"{name} anchor missing")
            return
        work = work.replace(old, new, 1)
    if work != src:
        path.write_text(work, encoding="utf-8")
        changed = True


def patch_semantic_search(path: Path) -> None:
    """Patch both current and legacy semantic_search layouts atomically."""
    global changed
    if not path.exists():
        errors.append("semantic_search.py not found")
        return
    src = path.read_text(encoding="utf-8")
    if LOCAL_ONLY_MARKER in src:
        return
    if MARKER in src and "HttpCrossEncoderReranker" in src:
        # Migrate an older application of this patch in place.  This matters
        # after a live upgrade: the URL branch existed, but it still retained
        # the old CrossEncoder/unranked fallbacks.
        local_replacements = [
            (
                        '''    cfg = load_reranker_config(config_path)
    if not cfg.get("enabled", False):
        return False
    if cfg.get("url"):  # [http reranker patch] HTTP reranker is stateless - nothing to warm
        return True
    model = cfg.get("model", _DEFAULT_RERANKER_CONFIG["model"])
    try:
        get_cached_reranker(model)
        return True
    except Exception as e:
        logger.warning(f"Reranker warmup failed for '{model}': {e}")
        return False''',
                '''    cfg = load_reranker_config(config_path)
    if not cfg.get("enabled", False):
        return False
    url = str(cfg.get("url") or "").strip()
    if not url:
        logger.error("[local-only reranker patch] reranker is enabled but no local endpoint is configured")
        return False
    return True''',
            ),
            (
                '''        if not self._reranker_config.get("enabled", False):
            return None
        url = self._reranker_config.get("url") or ""
        if url:
            # [http reranker patch] HTTP reranker: stateless client, no warm cache needed
            if self._reranker is None or getattr(self._reranker, "endpoint", None) != url.rstrip("/"):
                self._reranker = HttpCrossEncoderReranker(
                    url,
                    timeout=float(self._reranker_config.get("timeout", 60) or 60),
                    batch_size=int(self._reranker_config.get("batch_size", 12) or 12),
                )
            return self._reranker
        if self._reranker is None:
            model = self._reranker_config.get("model", _DEFAULT_RERANKER_CONFIG["model"])
            self._reranker = get_cached_reranker(model)
        return self._reranker''',
                '''        if not self._reranker_config.get("enabled", False):
            return None
        # [local-only reranker patch] The local HTTP endpoint is mandatory;
        # never fall back to sentence-transformers/Hugging Face.
        url = str(self._reranker_config.get("url") or "").strip()
        if not url:
            raise RuntimeError(
                "Local reranker is enabled but semantic_search.reranker.url is empty; "
                "no in-process or Hugging Face fallback is permitted"
            )
        if self._reranker is None or getattr(self._reranker, "endpoint", None) != url.rstrip("/"):
            self._reranker = HttpCrossEncoderReranker(
                url,
                timeout=float(self._reranker_config.get("timeout", 60) or 60),
                batch_size=int(self._reranker_config.get("batch_size", 12) or 12),
            )
        return self._reranker''',
            ),
            (
                '''            except Exception as exc:
                # Keep retrieval useful if the optional reranker is down.
                # The server logger records the endpoint and batch, not secrets.
                logger.warning(
                    f"HTTP reranker error ({self.endpoint}, batch {start // self.batch_size}): "
                    f"{exc}; returning unreranked order"
                )
                return [(index, 0.0) for index in range(min(top_k, n))]''',
                '''            except Exception as exc:
                # [local-only reranker patch] Do not silently substitute
                # unreranked or remote/model-hub retrieval.
                logger.error(
                    f"Local HTTP reranker error ({self.endpoint}, batch {start // self.batch_size}): {exc}"
                )
                raise RuntimeError(
                    f"Local reranker endpoint failed at {self.endpoint} "
                    f"(batch {start // self.batch_size}): {exc}"
                ) from exc''',
            ),
            (
                '''        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name)''',
                '''        # [local-only reranker patch] The in-process CrossEncoder path is
        # intentionally disabled; all reranking must use the local HTTP model.
        raise RuntimeError(
            "In-process reranking is disabled; configure the local "
            "semantic_search.reranker.url endpoint"
        )''',
            ),
        ]
        work = src
        for old, new in local_replacements:
            if old not in work:
                errors.append("semantic_search.py local-only migration anchor missing")
                return
            work = work.replace(old, new, 1)
        path.write_text(work, encoding="utf-8")
        changed = True
        return

    # Current package layout: _DEFAULT_RERANKER_CONFIG is imported from
    # config_light.py, so only the warmup and request-path anchors are needed.
    warmup_old = '''    cfg = load_reranker_config(config_path)
    if not cfg.get("enabled", False):
        return False
    model = cfg.get("model", _DEFAULT_RERANKER_CONFIG["model"])
    try:
        get_cached_reranker(model)
        return True
    except Exception as e:
        logger.warning(f"Reranker warmup failed for '{model}': {e}")
        return False'''
    warmup_new = '''    cfg = load_reranker_config(config_path)
    if not cfg.get("enabled", False):
        return False
    url = str(cfg.get("url") or "").strip()
    if not url:
        logger.error("[local-only reranker patch] reranker is enabled but no local endpoint is configured")
        return False
    return True'''

    reranker_old = '''        if not self._reranker_config.get("enabled", False):
            return None
        if self._reranker is None:
            model = self._reranker_config.get("model", _DEFAULT_RERANKER_CONFIG["model"])
            self._reranker = get_cached_reranker(model)
        return self._reranker'''
    reranker_new = '''        if not self._reranker_config.get("enabled", False):
            return None
        # [local-only reranker patch] The local HTTP endpoint is mandatory;
        # never fall back to sentence-transformers/Hugging Face.
        url = str(self._reranker_config.get("url") or "").strip()
        if not url:
            raise RuntimeError(
                "Local reranker is enabled but semantic_search.reranker.url is empty; "
                "no in-process or Hugging Face fallback is permitted"
            )
        if self._reranker is None or getattr(self._reranker, "endpoint", None) != url.rstrip("/"):
            self._reranker = HttpCrossEncoderReranker(
                url,
                timeout=float(self._reranker_config.get("timeout", 60) or 60),
                batch_size=int(self._reranker_config.get("batch_size", 12) or 12),
            )
        return self._reranker'''

    cross_encoder_init_old = '''        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name)'''
    cross_encoder_init_new = '''        # [local-only reranker patch] The in-process CrossEncoder path is
        # intentionally disabled; all reranking must use the local HTTP model.
        raise RuntimeError(
            "In-process reranking is disabled; configure the local "
            "semantic_search.reranker.url endpoint"
        )'''

    # If an older package has the defaults locally, extend them too.  Current
    # releases keep them in config_light.py and are handled below.
    default_old = '''_DEFAULT_RERANKER_CONFIG: dict[str, Any] = {
    "enabled": False,
    "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "candidate_multiplier": 3,
}'''
    default_new = '''_DEFAULT_RERANKER_CONFIG: dict[str, Any] = {
    "enabled": False,
    "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "candidate_multiplier": 3,
    "url": "",  # [http reranker patch] local /v1/rerank endpoint
    "timeout": 60.0,
    "batch_size": 12,
}'''

    work = src
    if default_old in work:
        work = work.replace(default_old, default_new, 1)
    elif "_DEFAULT_RERANKER_CONFIG" not in work:
        errors.append("semantic_search.py defaults anchor missing")

    if warmup_old not in work:
        errors.append("semantic_search.py warmup anchor missing")
    else:
        work = work.replace(warmup_old, warmup_new, 1)

    if cross_encoder_init_old not in work:
        errors.append("semantic_search.py CrossEncoder guard anchor missing")
    else:
        work = work.replace(cross_encoder_init_old, cross_encoder_init_new, 1)

    cache_anchor = "# Process-wide reranker cache (issue #283)."
    if cache_anchor not in work:
        errors.append("semantic_search.py cache anchor missing")
    else:
        work = work.replace(cache_anchor, HTTP_CLASS + cache_anchor, 1)

    if reranker_old not in work:
        errors.append("semantic_search.py request anchor missing")
    else:
        work = work.replace(reranker_old, reranker_new, 1)

    # Do not write a partial semantic_search.py if one anchor changed upstream.
    if errors:
        return
    if work != src:
        path.write_text(work, encoding="utf-8")
        changed = True


patch_semantic_search(pkg / "semantic_search.py")

# Current packages load the cheap reranker defaults from config_light.py.
config_light = pkg / "config_light.py"
config_light_old = '''_DEFAULT_RERANKER_CONFIG: dict[str, Any] = {
    "enabled": False,
    "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "candidate_multiplier": 3,
}'''
config_light_new = '''_DEFAULT_RERANKER_CONFIG: dict[str, Any] = {
    "enabled": False,
    "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "candidate_multiplier": 3,
    "url": "",  # [http reranker patch] local /v1/rerank endpoint
    "timeout": 60.0,
    "batch_size": 12,
}'''
patch_file(config_light, [(config_light_old, config_light_new)], "config_light.py")

# Older/current typed config modules may already contain these fields.  Patch
# only when the marker is absent, leaving an existing correct patch untouched.
config = pkg / "config.py"
config_old = '''@dataclass
class RerankerConfig:
    enabled: bool = False
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    candidate_multiplier: int = 3'''
config_new = '''@dataclass
class RerankerConfig:
    enabled: bool = False
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    candidate_multiplier: int = 3
    url: str = ""  # [http reranker patch] local /v1/rerank endpoint
    timeout: float = 60.0
    batch_size: int = 12'''
patch_file(config, [(config_old, config_new)], "config.py")

if errors:
    print("mismatch")
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

print("applied" if changed else "already")
