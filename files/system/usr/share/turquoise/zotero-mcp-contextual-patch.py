#!/usr/bin/env python3
"""Idempotently apply the Deterministic Contextual Retrieval (DCR) patch to zotero-mcp.

Why: chunks are embedded in isolation, so a regression model or proof sitting
under `### IV. Identification Strategy` on page 14 is a "semantic orphan" — the
vector and BM25 indexes have no idea which paper or section it belongs to. DCR
prepends a lean structural prefix to every chunk IN MEMORY before it is stored
and embedded:

    [Paper: <short title> | Section: <breadcrumb>]\n

The breadcrumb is the active heading hierarchy (#/##/###/####) at the chunk's
char offset in the item's markdown text, e.g. "IV. Empirical Model > B.
Instrumental Variables". Zero disk mutation: sidecar .md files stay clean for
human reads and get_item_fulltext. Because the sparse BM25 index is built from
ChromaDB iter_documents() (the [sparse patch]), the contextual tokens flow into
the sparse leg automatically — no sparse code changes.

Config: semantic_search.contextual.{enabled,max_title_chars,max_breadcrumb_chars,max_depth}
    enabled (default false) — must be turned on in config.json, then chunks must
    be re-indexed (update-db --fulltext --force-rebuild) to carry prefixes.

Marker comment: "[contextual patch]". Re-applied by sjust update; see
New-RAG-Setup.md (Extension 1: Deterministic Contextual Retrieval).
Usage: zotero-mcp-contextual-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
target = pkg / "semantic_search.py"

errors: list[str] = []
changed = False

MARKER = "[contextual patch]"

INIT_NEW = '''        self._chunking_config = self._load_chunking_config()
        self._contextual_config = self._load_contextual_config()  # [contextual patch] DCR
'''

METHODS_OLD = '''            except Exception as e:
                logger.warning(f"Error loading chunking config: {e}")
        return config

    @property
    def _chunking_enabled(self) -> bool:
'''

METHODS_NEW = '''            except Exception as e:
                logger.warning(f"Error loading chunking config: {e}")
        return config

    # [contextual patch] Deterministic Contextual Retrieval (DCR): a lean
    # structural prefix ([Paper: <title> (<author> <year>) | Section: <breadcrumb>])
    # is prepended to every chunk IN MEMORY before storing/embedding, so empirical
    # models and proofs keep their paper/section identity in both the dense and
    # the BM25 index. Sidecar .md files on disk stay untouched. See New-RAG-Setup.md.
    _HEADING_RE = re.compile(r"(?m)^(#{1,4})[ \\t]+(.*?)[ \\t]*$")

    def _load_contextual_config(self) -> dict[str, Any]:
        """[contextual patch] Load DCR configuration from file or use defaults."""
        config: dict[str, Any] = {
            "enabled": False,
            "max_title_chars": 48,
            "max_breadcrumb_chars": 120,
            "max_depth": 3,
        }
        if self.config_path and os.path.exists(self.config_path):
            try:
                with open(self.config_path) as f:
                    file_config = json.load(f)
                    config.update(file_config.get("semantic_search", {}).get("contextual", {}))
            except Exception as e:
                logger.warning(f"Error loading contextual config: {e}")
        return config

    @staticmethod
    def _format_citation(item: dict[str, Any]) -> str:
        """[contextual patch] Extract a compact '<Author> <Year>' citation string."""
        data = item.get("data", {}) if isinstance(item, dict) else {}
        creators = data.get("creators", [])
        author_names = []
        if isinstance(creators, list):
            for c in creators:
                if not isinstance(c, dict):
                    continue
                last = (c.get("lastName") or c.get("name") or c.get("firstName") or "").strip()
                if last:
                    author_names.append(last)
        author_str = ""
        if len(author_names) == 1:
            author_str = author_names[0]
        elif len(author_names) == 2:
            author_str = f"{author_names[0]} & {author_names[1]}"
        elif len(author_names) >= 3:
            author_str = f"{author_names[0]} et al."

        raw_date = str(data.get("date") or "").strip()
        year_match = re.search(r"\\b(19\\d\\d|20\\d\\d)\\b", raw_date)
        year = year_match.group(1) if year_match else ""

        if author_str and year:
            return f"{author_str} {year}"
        if author_str:
            return author_str
        if year:
            return year
        return ""

    @staticmethod
    def _clean_heading(text: str) -> str:
        """[contextual patch] Strip markdown decorations from a heading."""
        s = re.sub(r"\\[([^\\]]*)\\]\\([^)]*\\)", r"\\1", text)  # [t](url) -> t
        s = s.replace("`", "").replace("**", "").replace("__", "")
        s = re.sub(r"[*$]", "", s)
        return re.sub(r"\\s+", " ", s).strip()

    def _heading_breadcrumbs(self, doc_text: str) -> tuple[list[int], list[str]]:
        """[contextual patch] (char offsets, breadcrumbs) of every heading in *doc_text*.

        A breadcrumb is the active heading hierarchy at that offset, e.g.
        "IV. Empirical Model > B. Instrumental Variables". Runs once per item,
        not per chunk (monograph-size docs scan once).
        """
        positions: list[int] = []
        crumbs: list[str] = []
        stack: list[str] = []
        for m in self._HEADING_RE.finditer(doc_text):
            level = len(m.group(1))
            text = self._clean_heading(m.group(2))
            if not text:
                continue
            while stack and len(stack) >= level:
                stack.pop()
            stack.append(text)
            positions.append(m.start())
            crumbs.append(" > ".join(stack))
        return positions, crumbs

    def _contextualize_chunk(self, item, doc_text, chunk_text, offset,
                             positions, crumbs) -> str:
        """[contextual patch] Return *chunk_text* with the DCR prefix prepended.

        Prefix shape: ``[Paper: <title> (<author> <year>) | Section: <breadcrumb>]\\n``
        (title-only when no section applies). Kept lean (~15-20 tokens): strictly
        structural — no abstract or content injection.
        """
        if not self._contextual_config.get("enabled", False):
            return chunk_text
        title = (item.get("data", {}).get("title") or "").strip()
        citation = self._format_citation(item)

        breadcrumb = ""
        if positions:
            import bisect
            idx = bisect.bisect_right(positions, offset) - 1
            if idx >= 0:
                parts = crumbs[idx].split(" > ")
                max_depth = int(self._contextual_config.get("max_depth", 3) or 3)
                if len(parts) > max_depth:
                    parts = parts[-max_depth:]
                breadcrumb = " > ".join(parts)
        max_bc = int(self._contextual_config.get("max_breadcrumb_chars", 120) or 120)
        if len(breadcrumb) > max_bc:
            breadcrumb = breadcrumb[: max_bc - 3].rstrip() + "..."
        max_t = int(self._contextual_config.get("max_title_chars", 48) or 48)
        if len(title) > max_t:
            title = title[: max_t - 3].rstrip() + "..."

        paper_label = title
        if title and citation:
            paper_label = f"{title} ({citation})"
        elif citation:
            paper_label = citation

        if paper_label and breadcrumb:
            return f"[Paper: {paper_label} | Section: {breadcrumb}]\\n" + chunk_text
        if paper_label:
            return f"[Paper: {paper_label}]\\n" + chunk_text
        if breadcrumb:
            return f"[Section: {breadcrumb}]\\n" + chunk_text
        return chunk_text

    @property
    def _chunking_enabled(self) -> bool:
'''

LOOP_OLD = '''                    n_chunks = len(passages)
                    for ci, (chunk_text, c0, c1) in enumerate(passages):
'''

LOOP_NEW = '''                    n_chunks = len(passages)
                    # [contextual patch] DCR: precompute the heading breadcrumbs
                    # once per item (one scan of the markdown, reused per chunk).
                    _ctx_pos: list[int] = []
                    _ctx_crumbs: list[str] = []
                    if self._contextual_config.get("enabled", False):
                        _ctx_pos, _ctx_crumbs = self._heading_breadcrumbs(doc_text)
                    for ci, (chunk_text, c0, c1) in enumerate(passages):
'''

APPEND_OLD = '''                        documents.append(self.chroma_client.truncate_text(chunk_text))
'''

APPEND_NEW = '''                        documents.append(self.chroma_client.truncate_text(
                            self._contextualize_chunk(item, doc_text, chunk_text, c0,
                                                      _ctx_pos, _ctx_crumbs)))
'''


def _apply(path: Path, old: str, new: str, name: str) -> None:
    """Replace *old* with *new* in *path* (single occurrence)."""
    global changed
    src = path.read_text(encoding="utf-8")
    if old not in src:
        errors.append(f"{name} anchor missing")
        return
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    changed = True


src0 = target.read_text(encoding="utf-8")
if MARKER in src0:
    print("already")
    sys.exit(0)

_apply(target, '        self._chunking_config = self._load_chunking_config()\n',
       INIT_NEW, "init anchor")
_apply(target, METHODS_OLD, METHODS_NEW, "methods anchor")
_apply(target, LOOP_OLD, LOOP_NEW, "loop anchor")
_apply(target, APPEND_OLD, APPEND_NEW, "append anchor")

if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("applied")
