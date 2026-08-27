#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp PDF-outline child-script patch.

Why: zotero_get_pdf_outline extracts the TOC in a throwaway child process.
The child's stdout must be pure JSON. PyMuPDF >=1.26 emits a deprecation
notice when importing the legacy ``fitz`` name, which corrupts that protocol.
The patch makes the child import the canonical ``pymupdf`` name instead.

Recent zotero-mcp releases also added a sentinel-delimited child protocol that
is equivalent (and stronger) than this import-only patch. The updater treats
that upstream implementation as already fixed, so sjust update stays quiet.

Marker comment: "[toc patch]". Re-applied by sjust update.
Usage: zotero-mcp-toc-patch.py <path/to/write.py>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

path = Path(sys.argv[1])
src = path.read_text(encoding="utf-8")

if "[toc patch]" in src:
    print("already")
    sys.exit(0)

# Upstream's current child script uses pymupdf and a sentinel before parsing
# stdout. Either form eliminates the PyMuPDF deprecation contamination that
# motivated this patch.
if (
    "_TOC_CHILD_SCRIPT" in src
    and '"    import pymupdf as fitz\\n"' in src
    and "_TOC_SENTINEL" in src
):
    print("already")
    sys.exit(0)

old = '"    import fitz\\n"'
new = (
    '"    import pymupdf as fitz  # [toc patch] legacy fitz shim prints a '
    'deprecation warning to STDOUT (PyMuPDF>=1.26), corrupting the child JSON '
    'protocol\\n"'
)
if old not in src:
    print("mismatch")
    sys.exit(1)

path.write_text(src.replace(old, new, 1), encoding="utf-8")
print("applied")
