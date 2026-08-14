#!/usr/bin/env python3
"""Idempotently apply the Qwen3-Embedding query-instruct patch to zotero-mcp.

Why: Qwen3-Embedding is instruction-aware — queries should be formatted as
`Instruct: <task>\nQuery: <query>` while documents stay raw. The official
model card warns that omitting the instruct prefix on the query side costs
~1-5% retrieval performance. zotero-mcp embeds queries with the raw text, so
this patch prepends the prefix in OpenAIEmbeddingFunction.embed_query.

Hook (verified): ChromaClient.search() calls embedding_function.embed_query(qt)
DIRECTLY for query texts (chroma_client.py), so patching embed_query touches
queries only — documents flow through __call__ (collection.add) and stay raw.
The BM25 and reranker legs keep the raw query in semantic_search.py, so the
prefix never pollutes keyword matching.

Gate: only applies when the configured model name contains "qwen3" (case-
insensitive) and the query doesn't already start with "Instruct:".

Marker comment: "[instruct patch]". Re-applied by sjust update; see
New-RAG-Setup.md.
Usage: zotero-mcp-instruct-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
target = pkg / "chroma_client.py"
src = target.read_text()

MARKER = "[instruct patch]"

if MARKER in src:
    print("already")
    sys.exit(0)

OLD = '''    def embed_query(self, text: str) -> list[float]:
        """Embed a query string. No special handling needed for OpenAI."""
        return self.__call__([text])[0]
'''

NEW = '''    def embed_query(self, text: str) -> list[float]:
        # [instruct patch] Qwen3-Embedding is instruction-aware: queries get an
        # `Instruct: <task>\\nQuery: <query>` prefix (documents stay raw via
        # __call__). Official guidance: omitting it costs ~1-5% retrieval.
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

if OLD not in src:
    print("mismatch")
    sys.exit(1)

target.write_text(src.replace(OLD, NEW, 1))
print("applied")
