#!/usr/bin/env python3
"""Idempotently apply the Bounded AST Chunker patch to zotero-mcp.

Why: Fixed-window chunking blindly slices text every N characters, splitting
22.9% of regression tables and breaking LaTeX derivations across boundaries.
This patch replaces the naive character slicer with a Bounded AST-Aware
Markdown Chunker:
- Atomic structural blocks (HTML tables, LaTeX display math, [Figure Schema] blocks)
  remain 100% unsplit up to 3,800 chars (>94% table atomicity).
- Heading boundary fences (#, ##, ###) prevent cross-section bleed.
- Sibling paragraph packing (min 600 chars) eliminates vector starvation.
- Prose token ceiling (2,400 chars) maintains dense vector sharpness.
- Row-wise table splitting with duplicate header preservation handles giant tables.
- Sets DEFAULT_REQUEST_BATCH_SIZE to 4 to prevent HTTP 400 context overflows on llama-server.

Files:
- ast_chunker.py     copied from zotero-mcp-ast-chunker.py next to this script
- semantic_search.py split_into_passages delegates to bounded_ast_split_passages
- chroma_client.py   DEFAULT_REQUEST_BATCH_SIZE = 4

Marker comment: "[ast chunker patch]". Re-applied by sjust update.
Usage: zotero-mcp-ast-chunker-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import shutil
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
src_chunker = here / "zotero-mcp-ast-chunker.py"

errors: list[str] = []
changed = False

MARKER = "[ast chunker patch]"

# 1. Copy ast_chunker.py into package dir
dst_chunker = pkg / "ast_chunker.py"
if not dst_chunker.exists() or dst_chunker.read_bytes() != src_chunker.read_bytes():
    shutil.copy2(src_chunker, dst_chunker)
    changed = True

# 2. Patch semantic_search.py
target_ss = pkg / "semantic_search.py"
ss_text = target_ss.read_text(encoding="utf-8")

if MARKER not in ss_text:
    # 2a. Add import
    import_hook = "from .chroma_client import ChromaClient, create_chroma_client"
    import_new = f"""from .chroma_client import ChromaClient, create_chroma_client
from . import ast_chunker as _ast_chunker  # {MARKER}"""

    if import_hook in ss_text:
        ss_text = ss_text.replace(import_hook, import_new, 1)
        changed = True
    else:
        errors.append("could not find import hook in semantic_search.py")

    # 2b. Patch split_into_passages
    old_func_start = "def split_into_passages("
    
    if old_func_start in ss_text:
        next_func = "def _attachment_priority_changed("
        pos1 = ss_text.find(old_func_start)
        pos2 = ss_text.find(next_func, pos1)
        
        if pos1 != -1 and pos2 != -1:
            ast_func_replacement = f'''def split_into_passages(
    text: str,
    chunk_size: int = 2400,
    overlap: int = 200,
    max_chunks: int = 3000,
) -> list[tuple[str, int, int]]:
    """[ast chunker patch] Bounded AST-Aware Markdown passage splitter.
    
    Preserves atomic tables, display LaTeX math, and figure schemas while enforcing
    heading fences, node packing floors (>=600 chars), and prose sentence ceilings.
    """
    return _ast_chunker.bounded_ast_split_passages(
        text=text,
        chunk_size=chunk_size,
        overlap=overlap,
        max_chunks=max_chunks,
    )


'''
            ss_text = ss_text[:pos1] + ast_func_replacement + ss_text[pos2:]
            changed = True
        else:
            errors.append("could not locate split_into_passages boundaries in semantic_search.py")
    else:
        errors.append("could not find def split_into_passages in semantic_search.py")

    if changed and not errors:
        target_ss.write_text(ss_text, encoding="utf-8")

# 3. Patch chroma_client.py DEFAULT_REQUEST_BATCH_SIZE = 1
target_cc = pkg / "chroma_client.py"
if target_cc.exists():
    cc_text = target_cc.read_text(encoding="utf-8")
    if "DEFAULT_REQUEST_BATCH_SIZE = 64" in cc_text:
        cc_text = cc_text.replace("DEFAULT_REQUEST_BATCH_SIZE = 64", "DEFAULT_REQUEST_BATCH_SIZE = 1  # [ast chunker patch]", 1)
        target_cc.write_text(cc_text, encoding="utf-8")
        changed = True
    elif "DEFAULT_REQUEST_BATCH_SIZE = 4" in cc_text:
        cc_text = cc_text.replace("DEFAULT_REQUEST_BATCH_SIZE = 4", "DEFAULT_REQUEST_BATCH_SIZE = 1  # [ast chunker patch]", 1)
        target_cc.write_text(cc_text, encoding="utf-8")
        changed = True

# 4. Patch chromadb built-in openai_embedding_function.py to sub-batch HTTP requests
target_chroma_ef = pkg.parent / "chromadb" / "utils" / "embedding_functions" / "openai_embedding_function.py"
if target_chroma_ef.exists():
    chroma_ef_text = target_chroma_ef.read_text(encoding="utf-8")
    old_ef_call = '''        # Prepare embedding parameters
        embedding_params: Dict[str, Any] = {
            "model": self.model_name,
            "input": input,
        }

        if self.dimensions is not None and "text-embedding-3" in self.model_name:
            embedding_params["dimensions"] = self.dimensions

        # Get embeddings
        response = self.client.embeddings.create(**embedding_params)

        # Extract embeddings from response
        return [np.array(data.embedding, dtype=np.float32) for data in response.data]'''

    new_ef_call = '''        # [ast chunker patch] Sub-batch requests to avoid llama-server context limit
        embeddings = []
        batch_size = 1
        for i in range(0, len(input), batch_size):
            sub_input = input[i:i + batch_size]
            embedding_params: Dict[str, Any] = {
                "model": self.model_name,
                "input": sub_input,
            }
            if self.dimensions is not None and "text-embedding-3" in self.model_name:
                embedding_params["dimensions"] = self.dimensions
            response = self.client.embeddings.create(**embedding_params)
            embeddings.extend([np.array(data.embedding, dtype=np.float32) for data in response.data])

        return embeddings'''

    if old_ef_call in chroma_ef_text:
        chroma_ef_text = chroma_ef_text.replace(old_ef_call, new_ef_call, 1)
        target_chroma_ef.write_text(chroma_ef_text, encoding="utf-8")
        changed = True

if errors:
    print(f"mismatch: {'; '.join(errors)}", file=sys.stderr)
    sys.exit(1)

if changed:
    print("applied")
else:
    print("already")
