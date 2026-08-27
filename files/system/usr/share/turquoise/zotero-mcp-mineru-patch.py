#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp auto-MinerU patch.

Why: zotero semantic search extracts text-layer PDF text only, so equations
arrive as garbled Unicode and tables as run-on text. This patch slots MinerU
(magic-pdf) in before embedding: for every item being (re)embedded that has a
PDF and no cached sidecar, MinerU parses it, the sidecar Markdown becomes the
embedded fulltext, and answer-time fulltext reads prefer the sidecar.

Files (all in the zotero_mcp package dir passed as argv[1]):
- mineru.py               copied from zotero-mcp-mineru.py next to this script
- semantic_search.py      import + current extraction-batch hook
- tools/retrieval.py      import + sidecar preference in get_item_fulltext
- tools/search.py         embedder pre-check warning

Marker comment: "[mineru patch]". Re-applied by sjust update; see
General-Tooling §3.2.5 and MinerU-Setup.md.
Usage: zotero-mcp-mineru-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import shutil
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
here = Path(__file__).resolve().parent
src_mineru = here / "zotero-mcp-mineru.py"

errors: list[str] = []
changed = False

# --- 1. mineru.py ----------------------------------------------------------
if not (pkg / "mineru.py").exists():
    if src_mineru.exists():
        shutil.copy2(src_mineru, pkg / "mineru.py")
        changed = True
    else:
        errors.append("mineru.py source (zotero-mcp-mineru.py) not found next to this script")


def _apply(path: Path, edits: list[tuple[str, str]], name: str) -> None:
    """Apply edits to a file only when every anchor is present (all-or-nothing)."""
    global changed
    if "[mineru patch]" in path.read_text(encoding="utf-8"):
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


# --- 2. semantic_search.py -------------------------------------------------
ss = pkg / "semantic_search.py"
if ss.exists():
    _apply(ss, [
        (
            "from .local_db import PERSONAL_LIBRARY_GROUP_ID, LocalZoteroReader",
            "from .local_db import PERSONAL_LIBRARY_GROUP_ID, LocalZoteroReader\n"
            "from . import mineru as _mineru  # [mineru patch] auto-MinerU before embedding (see zotero-mcp-mineru-patch.py)",
        ),
        (
            '''def _extract_fulltext_batch(reader, items):
    """Yield ``(item_id, (text, source) | None)`` for every item in ``items``.

    Prefers the reader's batch API, which parallelises across a process pool
    when configured. Falls back to one call per item for readers that do not
    provide it — the minimal doubles used in tests implement only
    ``extract_fulltext_for_item(item_id)``, and they should not have to grow
    a new method just because the real reader gained a faster path.
    """
    batch = getattr(reader, "extract_fulltext_for_items", None)
    if batch is not None:
        yield from batch(items)
        return
    for item_id, _item_key in items:
        yield item_id, reader.extract_fulltext_for_item(item_id)
''',
            '''def _extract_fulltext_batch(reader, items):
    """Yield ``(item_id, (text, source) | None)`` for every item in ``items``.

    [mineru patch] Use cached MinerU sidecars when present and, when enabled,
    parse PDFs before falling back to the normal text-layer extractor. The
    unmodified batch path is retained when MinerU is disabled and no sidecar
    exists, preserving the reader's process-pool extraction behavior.
    """
    items = list(items)
    mineru_config = _mineru.load_mineru_config()
    sidecars = [
        (item_id, item_key, _mineru.read_sidecar(mineru_config, item_key))
        for item_id, item_key in items
    ]

    # Keep the upstream batch extractor on the ordinary path. A cached sidecar
    # or enabled auto-MinerU intentionally opts into the per-item path below.
    if not mineru_config.get("enabled") and not any(
        text is not None for _item_id, _item_key, text in sidecars
    ):
        batch = getattr(reader, "extract_fulltext_for_items", None)
        if batch is not None:
            yield from batch(items)
            return
        for item_id, _item_key in items:
            yield item_id, reader.extract_fulltext_for_item(item_id)
        return

    for item_id, item_key, sidecar in sidecars:
        if sidecar is not None:
            yield item_id, (sidecar, "mineru-sidecar")
            continue
        mineru_fulltext = _mineru.try_auto_parse(item_key, reader)
        if mineru_fulltext:
            yield item_id, mineru_fulltext
        else:
            yield item_id, reader.extract_fulltext_for_item(item_id)
''',
        ),
    ], "semantic_search.py")
else:
    errors.append("semantic_search.py not found")

# --- 3. tools/retrieval.py -------------------------------------------------
rv = pkg / "tools" / "retrieval.py"
if rv.exists():
    _apply(rv, [
        (
            "from zotero_mcp import client as _client\nfrom zotero_mcp import utils as _utils\n",
            "from zotero_mcp import client as _client\n"
            "from zotero_mcp import utils as _utils\n"
            "from zotero_mcp import mineru as _mineru  # [mineru patch] sidecar preference for fulltext reads\n",
        ),
        (
            "                    local_item = reader.get_item_by_key(item_key)\n"
            "                    if local_item:\n"
            "                        extracted = reader.extract_fulltext_for_item(local_item.item_id)",
            "                    local_item = reader.get_item_by_key(item_key)\n"
            "                    if local_item:\n"
            "                        # [mineru patch] prefer the MinerU sidecar (clean equations/tables) when present\n"
            "                        _mineru_text = _mineru.read_sidecar(_mineru.load_mineru_config(), item_key)\n"
            "                        if _mineru_text:\n"
            "                            ctx.info(\"Retrieved full text from MinerU sidecar\")\n"
            "                            return _helpers._prepend_size_warning(\n"
            "                                f\"{metadata}\\n\\n---\\n\\n## Full Text\\n\\n{_mineru_text}\",\n"
            "                                \"Consider using zotero_semantic_search to find specific content instead of reading full papers.\"\n"
            "                            )\n"
            "                        extracted = reader.extract_fulltext_for_item(local_item.item_id)",
        ),
    ], "tools/retrieval.py")
else:
    errors.append("tools/retrieval.py not found")

# --- 4. tools/search.py (embedder pre-check warning) -------------------------
sc = pkg / "tools" / "search.py"
if sc.exists():
    _apply(sc, [
        (
            '        ctx.info("Starting semantic search database update...")\n\n'
            '        # Import semantic search module',
            '        ctx.info("Starting semantic search database update...")\n\n'
            '        # [mineru patch] pre-check the embedding backend so a dead embedder\n'
            '        # produces an actionable message instead of a pile of upsert errors\n'
            '        embedder_warning = ""\n'
            '        try:\n'
            '            import json as _json\n'
            '            import urllib.request as _ur\n'
            '            _cfg = _json.loads((Path.home() / ".config" / "zotero-mcp" / "config.json").read_text(encoding="utf-8"))\n'
            '            _ss = _cfg.get("semantic_search", {}) or {}\n'
            '            _ec = _ss.get("embedding_config", {}) or {}\n'
            '            _url = (_ec.get("base_url") or "").rstrip("/")\n'
            '            if _ss.get("embedding_model") == "openai" and _url:\n'
            '                try:\n'
            '                    with _ur.urlopen(_url + "/models", timeout=2):\n'
            '                        pass\n'
            '                except Exception:\n'
            '                    embedder_warning = (\n'
            '                        "\\n\\n⚠️ The embedding backend at %s is unreachable.\\n"\n'
            '                        "MinerU parses still ran and sidecars are saved (nothing lost), but the "\n'
            '                        "embedding/upsert phase FAILED. Start it with `serve-embedder`, then "\n'
            '                        "re-run this update to finish indexing." % _url\n'
            '                    )\n'
            '                    ctx.info(\n'
            '                        "Embedder at %s is down; parses will run but embedding will fail. "\n'
            '                        "Start it with `serve-embedder` and re-run." % _url\n'
            '                    )\n'
            '        except Exception:\n'
            '            pass\n\n'
            '        # Import semantic search module',
        ),
        (
            '            if stats.get(\'start_time\'):\n'
            '                output.append(f"**Started:** {stats[\'start_time\']}")\n'
            '            if stats.get(\'end_time\'):\n'
            '                output.append(f"**Completed:** {stats[\'end_time\']}")\n\n'
            '        return "\\n".join(output)',
            '            if stats.get(\'start_time\'):\n'
            '                output.append(f"**Started:** {stats[\'start_time\']}")\n'
            '            if stats.get(\'end_time\'):\n'
            '                output.append(f"**Completed:** {stats[\'end_time\']}")\n\n'
            '        if embedder_warning:\n'
            '            output.append(embedder_warning)\n\n'
            '        return "\\n".join(output)',
        ),
    ], "tools/search.py")
else:
    errors.append("tools/search.py not found")

if errors:
    print("mismatch: " + "; ".join(errors))
    sys.exit(1)

print("applied" if changed else "already")
