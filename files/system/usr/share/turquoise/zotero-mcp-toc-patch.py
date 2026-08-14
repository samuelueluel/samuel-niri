#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp PDF-outline child-script patch.

Why: zotero_get_pdf_outline extracts the TOC in a throwaway child process
(write.py _TOC_CHILD_SCRIPT, #372) whose stdout must be pure JSON. PyMuPDF
>=1.26 emits "The `fitz` API is deprecated ..." to STDOUT when importing the
legacy `fitz` name (a raw print, not a Python warning — PYTHONWARNINGS cannot
suppress it), so the child's JSON fails to parse and the tool returns
"unreadable outline data". The patch makes the child import the canonical
`pymupdf` name instead, which is warning-free and API-identical.

Marker comment: "[toc patch]". Re-applied by sjust update (same pattern as the
other zotero-mcp patches). Usage: zotero-mcp-toc-patch.py <path/to/write.py>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys

path = sys.argv[1]
src = open(path).read()
if "[toc patch]" in src:
    print("already")
    sys.exit(0)
old = '"    import fitz\\n"'
new = '"    import pymupdf as fitz  # [toc patch] legacy fitz shim prints a deprecation warning to STDOUT (PyMuPDF>=1.26), corrupting the child JSON protocol\\n"'
if old in src:
    open(path, "w").write(src.replace(old, new, 1))
    print("applied")
else:
    print("mismatch")
    sys.exit(1)
