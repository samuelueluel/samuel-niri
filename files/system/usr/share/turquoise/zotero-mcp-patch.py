#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp linked-file fulltext patch.

Why: tools/retrieval.py gates local-disk extraction behind is_local_mode();
the hybrid server (reads local DB + writes via web API) falls through to
download paths that 500/404 on linked file:// attachments. The patch makes
local-disk extraction (which resolves linked paths) run in all modes and
leaves the download path as a fallback.

Marker comment: "[local patch]". Re-applied by sjust update; see
General-Tooling §3.2.3. Usage: zotero-mcp-patch.py <path/to/retrieval.py>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys

path = sys.argv[1]
src = open(path).read()
if "[local patch]" in src:
    print("already")
    sys.exit(0)
old = "            if _utils.is_local_mode():"
new = "            if True:  # [local patch] also run local-disk extraction in hybrid mode (linked file:// support); download path remains the fallback"
if old in src:
    open(path, "w").write(src.replace(old, new, 1))
    print("applied")
else:
    print("mismatch")
    sys.exit(1)
