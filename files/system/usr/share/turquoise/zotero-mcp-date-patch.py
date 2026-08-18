#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp date-filter patch.

Why: advanced_search date range ops (isAfter / isBefore / isGreaterThan /
isLessThan) fell back to raw Python string comparison whenever float() could
not parse BOTH sides — and Zotero's month-first display dates ("5/2018",
"08/2024") never parse as floats. On a library where ~46% of dated items are
month-first, every date-range query mis-ordered them in both directions:
`date isAfter "2020"` returned papers from 1981 and excluded a real 2024
paper; `isBefore` vacuously matched every item with no date; year-only dates
mis-sorted against full-date bounds ("2023" < "2023-01-01" stringwise).

The patch adds a date-aware layer to tools/search.py `_compare()`: it parses
the display date (YYYY, YYYY-M, YYYY-M-D, M/YYYY, "Month D, YYYY", ISO
datetimes) and compares numerically, resolving unknown month/day to the
inclusive extreme (Dec 31 for isAfter/isGreaterThan, Jan 1 for
isBefore/isLessThan) so a year-only "2023" item matches
`isAfter "2023-01-01"` but not `isBefore "2023-01-01"`. Empty (no-date) items
no longer match any range filter. Non-date fields and the existing numeric
fast path are untouched. Also fixes the `year` field extractor, which took
date[:4] and turned "5/2018" into "5/20".

Marker comment: "[date patch]". Re-applied by sjust update; see Zotero-MCP.md
and General-Tooling.
Usage: zotero-mcp-date-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
errors: list[str] = []
changed = False

MARKER = "[date patch]"


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


# --- tools/search.py ------------------------------------------------------
target = pkg / "tools" / "search.py"
if target.exists():
    _apply(target, [
        # 1. date parser + field set, inserted after _as_float
        (
            '''        def _as_float(text: str) -> float | None:
            try:
                return float(text)
            except ValueError:
                return None
''',
            '''        def _as_float(text: str) -> float | None:
            try:
                return float(text)
            except ValueError:
                return None

        _MONTHS = {name: i + 1 for i, name in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        )}
        _MONTHS.update({full: i + 1 for i, full in enumerate(
            ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
        )})

        _DATE_FIELDS = {"date", "year", "dateadded", "datemodified", "accessdate"}

        def _parse_date(text: str) -> tuple[int, int, int] | None:
            """[date patch] Parse a Zotero date display into (year, month, day).

            Handles YYYY, YYYY-M, YYYY-M-D (single- or zero-padded), M/YYYY
            (Zotero's month-first display), "Month D, YYYY" / "Month YYYY",
            and ISO datetimes (dateAdded/dateModified). month/day are 0 when
            the date has no such component. Returns None for empty/unparseable
            input.
            """
            if not text or not text.strip():
                return None
            t = text.strip().lower()
            # ISO datetime: "2026-08-16T03:02:29Z" (dateAdded / dateModified)
            m = re.match(r"^(\\d{4})-(\\d{1,2})-(\\d{1,2})[t ]", t)
            if m:
                return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            # ISO / YYYY-M-D (Zotero stores both single- and zero-padded forms)
            m = re.match(r"^(\\d{4})-(\\d{1,2})(?:-(\\d{1,2}))?$", t)
            if m:
                return (int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else 0)
            # M/YYYY: Zotero's month-first display for month-known dates
            m = re.match(r"^(\\d{1,2})/(\\d{4})$", t)
            if m:
                return (int(m.group(2)), int(m.group(1)), 0)
            # YYYY (year-only)
            m = re.match(r"^(\\d{4})$", t)
            if m:
                return (int(m.group(1)), 0, 0)
            # "October 1, 2016" / "Oct. 1, 2016"
            m = re.match(r"^([a-z]{3,9})\\.?\\s+(\\d{1,2}),?\\s+(\\d{4})$", t)
            if m and m.group(1) in _MONTHS:
                return (int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
            # "October 2016" / "Oct. 2016"
            m = re.match(r"^([a-z]{3,9})\\.?\\s+(\\d{4})$", t)
            if m and m.group(1) in _MONTHS:
                return (int(m.group(2)), _MONTHS[m.group(1)], 0)
            return None
''',
        ),
        # 2. year field: real year from parsed date, not date[:4]
        (
            '''            if field_lower == "year":
                date_value = str(data.get("date", "")).strip()
                return [date_value[:4]] if len(date_value) >= 4 else []
''',
            '''            if field_lower == "year":
                date_value = str(data.get("date", "")).strip()
                # [date patch] extract the real year: date_value[:4] on "5/2018"
                # yields "5/20". Parse the display date and take the year part.
                parsed = _parse_date(date_value)
                return [str(parsed[0])] if parsed else []
''',
        ),
        # 3. _compare gains a date_field flag (backward compatible)
        (
            '''        def _compare(candidate: str, expected: str, operation: str) -> bool:
''',
            '''        def _compare(candidate: str, expected: str, operation: str, date_field: bool = False) -> bool:
''',
        ),
        # 4. date-aware comparison between the numeric path and the string fallback
        (
            '''            left_num = _as_float(left)
            right_num = _as_float(right)
            if (
                operation in {"isGreaterThan", "isLessThan", "isBefore", "isAfter"}
                and left_num is not None
                and right_num is not None
            ):
                if operation in {"isGreaterThan", "isAfter"}:
                    return left_num > right_num
                return left_num < right_num

            if operation in {"isGreaterThan", "isAfter"}:
                return left > right
            return left < right
''',
            '''            left_num = _as_float(left)
            right_num = _as_float(right)
            if (
                operation in {"isGreaterThan", "isLessThan", "isBefore", "isAfter"}
                and left_num is not None
                and right_num is not None
            ):
                if operation in {"isGreaterThan", "isAfter"}:
                    return left_num > right_num
                return left_num < right_num

            if operation in {"isGreaterThan", "isLessThan", "isBefore", "isAfter"}:
                # [date patch] float() only parses bare numbers, so month-first
                # ("5/2018") and full ("2019-09-01") dates fell through to raw
                # string comparison, mis-ordering them against YYYY bounds in
                # both directions. Compare parsed dates numerically instead;
                # unknown month/day resolve to the inclusive extreme (Dec 31
                # for isAfter/isGreaterThan, Jan 1 for isBefore/isLessThan) so
                # a year-only "2023" item matches `isAfter "2023-01-01"`.
                if date_field:
                    if not left:
                        return False  # no date value -> matches no date range
                    left_date = _parse_date(left)
                    right_date = _parse_date(right)
                    if left_date is not None and right_date is not None:
                        if operation in {"isGreaterThan", "isAfter"}:
                            return (left_date[0], left_date[1] or 12, left_date[2] or 31) > (
                                right_date[0], right_date[1] or 12, right_date[2] or 31
                            )
                        return (left_date[0], left_date[1] or 1, left_date[2] or 1) < (
                            right_date[0], right_date[1] or 1, right_date[2] or 1
                        )

            if operation in {"isGreaterThan", "isAfter"}:
                return left > right
            return left < right
''',
        ),
        # 5. pass date_field through from the condition
        (
            '''            operation = condition["operation"]
            target = condition["value"]
            comparisons = [_compare(value, target, operation) for value in values]
''',
            '''            operation = condition["operation"]
            target = condition["value"]
            comparisons = [
                _compare(value, target, operation, date_field=condition["field"].lower() in _DATE_FIELDS)
                for value in values
            ]
''',
        ),
    ], "tools/search.py")
else:
    errors.append("tools/search.py not found")

if errors:
    print("mismatch")
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print("applied" if changed else "already")
