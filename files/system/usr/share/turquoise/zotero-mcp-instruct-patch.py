#!/usr/bin/env python3
"""Idempotently apply the Qwen3-Embedding query-instruct patch to zotero-mcp.

Why: Qwen3-Embedding is instruction-aware — queries should be formatted as
`Instruct: <task>\nQuery: <query>` while documents stay raw. The official
model guidance warns that omitting the query instruction can reduce retrieval
quality. Newer zotero-mcp releases moved the shared query hook from
``chroma_client.py`` to ``embeddings/base.py``; this patch supports both
layouts.

Marker comment: "[instruct patch]". Re-applied by sjust update; see
Zotero-MCP.md.
Usage: zotero-mcp-instruct-patch.py <path/to/zotero-mcp package dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
MARKER = "[instruct patch]"


def equivalent(src: str) -> bool:
    """Recognize the behavior even when an upstream implementation has no marker."""
    return (
        "Instruct: Given a research question" in src
        and "Query: " in src
        and ("def _prepare_query" in src or "def embed_query" in src)
    )


# Current zotero-mcp: RemoteEmbeddingFunction._prepare_query() is called by
# embed_query(), while _prepare_document() continues to leave documents raw.
base = pkg / "embeddings" / "base.py"
if base.exists():
    src = base.read_text(encoding="utf-8")
    if MARKER in src or equivalent(src):
        print("already")
        sys.exit(0)
    old = '''    def _prepare_query(self, text: str) -> str:
        """Transform a query before sending it (identity by default)."""
        return text
'''
    new = '''    def _prepare_query(self, text: str) -> str:
        """Prepare a query, adding Qwen3's task instruction when required."""
        # [instruct patch] Qwen3-Embedding is instruction-aware: query inputs
        # get an Instruct/Query prefix; document inputs remain raw through
        # _prepare_document().
        model_name = str(getattr(self, "model_name", "")).lower()
        if "qwen3" not in model_name:
            return text
        query = str(text).strip()
        if query.startswith("Instruct:"):
            return query
        return (
            "Instruct: Given a research question, retrieve relevant academic "
            "passages from an economics literature library that answer it.\\n"
            "Query: " + query
        )
'''
    if old not in src:
        print("mismatch")
        sys.exit(1)
    base.write_text(src.replace(old, new, 1), encoding="utf-8")
    print("applied")
    sys.exit(0)

# Legacy zotero-mcp layout: keep the old target working for older packages.
legacy = pkg / "chroma_client.py"
if legacy.exists():
    src = legacy.read_text(encoding="utf-8")
    if MARKER in src or equivalent(src):
        print("already")
        sys.exit(0)
    old = '''    def embed_query(self, text: str) -> list[float]:
        """Embed a query string. No special handling needed for OpenAI."""
        return self.__call__([text])[0]
'''
    new = '''    def embed_query(self, text: str) -> list[float]:
        # [instruct patch] Qwen3-Embedding is instruction-aware: queries get
        # an `Instruct: <task>\\nQuery: <query>` prefix; documents stay raw.
        q = text.strip()
        is_qwen3 = "qwen3" in self.model_name.lower()
        if not is_qwen3 or q.startswith("Instruct:"):
            prompt = q
        else:
            prompt = (
                "Instruct: Given a research question, retrieve relevant academic "
                "passages from an economics literature library that answer it.\\n"
                "Query: " + q
            )
        return self.__call__([prompt])[0]
'''
    if old not in src:
        print("mismatch")
        sys.exit(1)
    legacy.write_text(src.replace(old, new, 1), encoding="utf-8")
    print("applied")
    sys.exit(0)

print("mismatch")
sys.exit(1)
