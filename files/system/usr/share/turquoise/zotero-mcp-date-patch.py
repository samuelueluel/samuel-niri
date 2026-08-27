#!/usr/bin/env python3
"""Idempotently apply the zotero-mcp date-filter patch.

The advanced-search API path receives Zotero's display dates (for example
``05/2020``), while range comparisons need chronological ordering. Newer
zotero-mcp releases moved the shared comparison semantics into
``search_semantics.py``; this patch targets that shared comparator and passes a
date-field flag from ``tools/search.py``. The SQLite backend already compares
Zotero's raw ISO multipart date and remains unchanged.

Supported date displays include YYYY, YYYY-M, YYYY-M-D, M/YYYY,
"Month D, YYYY", "Month YYYY", and ISO datetimes. Unknown month/day values
use inclusive extremes for range comparisons, and empty/unparseable dates do
not match a range.

Marker comment: "[date patch]". Re-applied by sjust update.
Usage: zotero-mcp-date-patch.py <path/to/zotero_mcp-package-dir>
Prints: "applied" | "already" | "mismatch" (mismatch exits 1).
"""
import sys
from pathlib import Path

pkg = Path(sys.argv[1])
errors: list[str] = []
changed = False
MARKER = "[date patch]"


def _replace(path: Path, edits: list[tuple[str, str]], name: str) -> None:
    global changed
    if not path.exists():
        errors.append(f"{name} not found")
        return
    src = path.read_text(encoding="utf-8")
    if MARKER in src:
        return
    work = src
    for old, new in edits:
        if old not in work:
            errors.append(f"{name} anchor missing")
            return
        work = work.replace(old, new, 1)
    path.write_text(work, encoding="utf-8")
    changed = True


# Shared comparator introduced by zotero-mcp's search-backend parity refactor.
semantics = pkg / "search_semantics.py"
_replace(
    semantics,
    [
        (
            "import sqlite3\nfrom typing import Iterable, Sequence\n",
            "import re\nimport sqlite3\nfrom typing import Iterable, Sequence\n",
        ),
        (
            '''def compare(candidate: str, expected: str, operation: str) -> bool:
    """Evaluate one operator against one candidate value.

    Both sides are normalized first. Ordering operators compare numerically
    when both sides parse as numbers and lexically otherwise, so ``year
    isGreaterThan 2010`` orders by magnitude while a string field still
    orders sensibly.
    """
    left = normalize(candidate)
    right = normalize(expected)

    if operation == "is":
        return left == right
    if operation == "isNot":
        return left != right
    if operation == "contains":
        return right in left
    if operation == "doesNotContain":
        return right not in left
    if operation == "beginsWith":
        return left.startswith(right)
    if operation == "endsWith":
        return left.endswith(right)

    left_num = _as_float(left)
    right_num = _as_float(right)
    if operation in RANGE_OPS and left_num is not None and right_num is not None:
        if operation in {"isGreaterThan", "isAfter"}:
            return left_num > right_num
        return left_num < right_num

    if operation in {"isGreaterThan", "isAfter"}:
        return left > right
    return left < right
''',
            '''_MONTHS = {name: i + 1 for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}
_MONTHS.update({full: i + 1 for i, full in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"]
)})


def parse_date(text: str | None) -> tuple[int, int, int] | None:
    """[date patch] Parse Zotero display/ISO dates into (year, month, day)."""
    if text is None:
        return None
    value = str(text).strip().lower()
    if not value or value in {"no date", "n.d.", "n/a", "na"}:
        return None

    # ISO date or Zotero's raw multipart prefix. Month/day may be 00.
    match = re.match(r"^(\\d{4})-(\\d{1,2})-(\\d{1,2})(?:[t ].*)?$", value)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    # ISO year-month, accepted for manually entered dates.
    match = re.match(r"^(\\d{4})-(\\d{1,2})$", value)
    if match:
        return int(match.group(1)), int(match.group(2)), 0

    # Zotero's common month-first display.
    match = re.match(r"^(\\d{1,2})/(\\d{4})$", value)
    if match:
        return int(match.group(2)), int(match.group(1)), 0

    # Year-only display.
    match = re.match(r"^(\\d{4})$", value)
    if match:
        return int(match.group(1)), 0, 0

    # Month names: October 1, 2016; Oct. 1, 2016; October 2016.
    match = re.match(r"^([a-z]{3,9})\\.?\\s+(\\d{1,2}),?\\s+(\\d{4})$", value)
    if match and match.group(1) in _MONTHS:
        return int(match.group(3)), _MONTHS[match.group(1)], int(match.group(2))
    match = re.match(r"^([a-z]{3,9})\\.?\\s+(\\d{4})$", value)
    if match and match.group(1) in _MONTHS:
        return int(match.group(2)), _MONTHS[match.group(1)], 0
    return None


def _date_extreme(value: tuple[int, int, int], *, upper: bool) -> tuple[int, int, int]:
    """Resolve unknown month/day to the inclusive range-comparison edge."""
    year, month, day = value
    return year, month or (12 if upper else 1), day or (31 if upper else 1)


def compare(
    candidate: str, expected: str, operation: str, date_field: bool = False
) -> bool:
    """Evaluate one operator against one candidate value.

    Both sides are normalized first. Date fields use parsed chronological
    comparison for range operators; all other fields retain the prior numeric
    then lexical behavior.
    """
    left = normalize(candidate)
    right = normalize(expected)

    if operation == "is":
        return left == right
    if operation == "isNot":
        return left != right
    if operation == "contains":
        return right in left
    if operation == "doesNotContain":
        return right not in left
    if operation == "beginsWith":
        return left.startswith(right)
    if operation == "endsWith":
        return left.endswith(right)

    if operation in RANGE_OPS and date_field:
        # [date patch] API display dates are not lexically sortable. Missing
        # values must not satisfy a date range, and an invalid bound is a
        # caller error rather than a reason to compare arbitrary strings.
        left_date = parse_date(left)
        right_date = parse_date(right)
        if left_date is None or right_date is None:
            return False
        if operation in {"isGreaterThan", "isAfter"}:
            return _date_extreme(left_date, upper=True) > _date_extreme(right_date, upper=True)
        return _date_extreme(left_date, upper=False) < _date_extreme(right_date, upper=False)

    left_num = _as_float(left)
    right_num = _as_float(right)
    if operation in RANGE_OPS and left_num is not None and right_num is not None:
        if operation in {"isGreaterThan", "isAfter"}:
            return left_num > right_num
        return left_num < right_num

    if operation in {"isGreaterThan", "isAfter"}:
        return left > right
    return left < right
''',
        ),
        (
            '''def matches(values: Sequence[str] | Iterable[str], expected: str, operation: str) -> bool:
''',
            '''def matches(
    values: Sequence[str] | Iterable[str],
    expected: str,
    operation: str,
    date_field: bool = False,
) -> bool:
''',
        ),
        (
            "    comparisons = [compare(value, expected, operation) for value in values]\n",
            "    comparisons = [\n"
            "        compare(value, expected, operation, date_field=date_field)\n"
            "        for value in values\n"
            "    ]\n",
        ),
    ],
    "search_semantics.py",
)

# API/client-side advanced search: pass date context and extract year from the
# parsed display date rather than slicing the first four characters of M/YYYY.
search = pkg / "tools" / "search.py"
_replace(
    search,
    [
        (
            '''            if field_lower == "year":
                date_value = str(data.get("date", "")).strip()
                return [date_value[:4]] if len(date_value) >= 4 else []
''',
            '''            if field_lower == "year":
                date_value = str(data.get("date", "")).strip()
                # [date patch] date[:4] turns "05/2020" into "05/2".
                parsed = _semantics.parse_date(date_value)
                return [str(parsed[0])] if parsed else []
''',
        ),
        (
            "            return _semantics.matches(values, target, operation)\n",
            '''            return _semantics.matches(
                values,
                target,
                operation,
                date_field=condition["field"].lower()
                in {"date", "year", "dateadded", "datemodified", "accessdate"},
            )
''',
        ),
    ],
    "tools/search.py",
)

if errors:
    print("mismatch: " + "; ".join(errors))
    sys.exit(1)
print("applied" if changed else "already")
