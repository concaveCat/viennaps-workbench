"""Bounded CSV rate-table assets for the native ``CSVFileProcess`` model.

The installed ViennaPS 4.6.2 binding reads the table through
``viennaps::RateGrid<NumericType, D>::loadFromCSV``. That loader takes exactly
``D`` numeric columns per row and no header:

* 2D (``D == 2``): ``x,rate`` — one rate per lateral x position.
* 3D (``D == 3``): ``x,y,rate`` — one rate per (x, y) position.

There is no vertical/z column in either dimension. The audit note that expected
``x,z,rate`` does not match the installed binding; this module is the verified
schema, and it is the single place the workbench parses an uploaded table.

Lines whose first non-space character is ``#`` are comments, matching the
native reader. Blank lines, non-finite numbers, mixed column counts and
non-numeric cells are rejected because ``loadFromCSV`` returns ``false`` for
them and ``CSVFileProcess`` then silently evaluates to a zero velocity field.

The native interpolation modes are ``LINEAR``, ``IDW`` and ``CUSTOM``. Only the
first two are exposed: ``CUSTOM`` installs an arbitrary Python callable and is
intentionally unreachable from submitted JSON.
"""
from __future__ import annotations

import math
import re
from typing import Any

MAX_CSV_BYTES = 16 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_COORDINATE = 1_000_000.0
MAX_RATE = 1_000_000.0

# Same numeric grammar for every cell. Native strtod also accepts a leading
# sign, a bare fraction and an exponent; underscores, hex floats and
# inf/nan spellings are rejected.
_NUMBER = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")

COLUMNS_BY_DIMENSION = {2: ["x", "rate"], 3: ["x", "y", "rate"]}


class RateCsvError(ValueError):
    pass


def _finite(text: str, *, line: int, column: str) -> float:
    token = text.strip()
    if not _NUMBER.fullmatch(token):
        raise RateCsvError(f"line {line}: column {column} is not a plain decimal number: {text!r}")
    value = float(token)
    if not math.isfinite(value):
        raise RateCsvError(f"line {line}: column {column} is not finite")
    return value


def inspect_rate_csv(data: bytes, dimension: int) -> dict[str, Any]:
    """Validate a rate table and return its canonical summary.

    ``dimension`` selects the native column count (2 or 3); the same file cannot
    serve both dimensions.
    """
    if type(dimension) is not int or dimension not in (2, 3):
        raise RateCsvError("rate tables require dimension 2 or 3")
    if not data:
        raise RateCsvError("rate table is empty")
    if len(data) > MAX_CSV_BYTES:
        raise RateCsvError(f"rate table exceeds the {MAX_CSV_BYTES // (1024 * 1024)} MiB limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RateCsvError("rate table must be UTF-8 text") from exc
    expected_columns = len(COLUMNS_BY_DIMENSION[dimension])
    rows: list[list[float]] = []
    lines = text.split("\n")
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r").strip()
        if not line:
            # The native reader does not skip empty lines: an empty line makes
            # CSVReader::readContent return an empty optional, which
            # loadFromCSV treats as a failed load. Only the empty element after
            # a terminating newline at EOF is allowed.
            if line_number == len(lines) and raw_line == "":
                continue
            raise RateCsvError(f"line {line_number}: blank lines are not allowed")
        if line.startswith("#"):
            continue
        cells = line.split(",")
        if len(cells) != expected_columns:
            raise RateCsvError(
                f"line {line_number}: expected {expected_columns} columns "
                f"({','.join(COLUMNS_BY_DIMENSION[dimension])}), found {len(cells)}")
        values = [_finite(cell, line=line_number,
                          column=COLUMNS_BY_DIMENSION[dimension][index])
                  for index, cell in enumerate(cells)]
        for coordinate in values[:-1]:
            if abs(coordinate) > MAX_COORDINATE:
                raise RateCsvError(f"line {line_number}: coordinate exceeds ±{MAX_COORDINATE:g} um")
        if abs(values[-1]) > MAX_RATE:
            raise RateCsvError(f"line {line_number}: rate exceeds ±{MAX_RATE:g} um/s")
        rows.append(values)
        if len(rows) > MAX_ROWS:
            raise RateCsvError(f"rate table exceeds the {MAX_ROWS} row limit")
    if len(rows) < 2:
        raise RateCsvError("rate table needs at least two data rows")

    xs = sorted({row[0] for row in rows})
    unique_x = len(xs) == len(rows) if dimension == 2 else len(xs) >= 2
    if dimension == 2:
        linear_compatible = len(xs) == len(rows) and all(
            rows[index][0] < rows[index + 1][0] for index in range(len(rows) - 1))
        y_values = None
    else:
        ys = sorted({row[1] for row in rows})
        pairs = {(row[0], row[1]) for row in rows}
        linear_compatible = (len(xs) >= 2 and len(ys) >= 2
                             and len(pairs) == len(rows) == len(xs) * len(ys))
        y_values = {"min": ys[0], "max": ys[-1], "count": len(ys)}
    rates = [row[-1] for row in rows]
    summary: dict[str, Any] = {
        "schema": "viennaps-rate-grid-v1",
        "dimension": dimension,
        "columns": list(COLUMNS_BY_DIMENSION[dimension]),
        "rowCount": len(rows),
        "coordinateUnit": "um",
        "rateUnit": "um/s",
        "x": {"min": xs[0], "max": xs[-1], "count": len(xs)},
        "rate": {"min": min(rates), "max": max(rates),
                 "sign": "deposit" if min(rates) > 0 else "etch" if max(rates) < 0 else "mixed"},
        "linearCompatible": linear_compatible,
        "idwCompatible": True,
        "uniqueX": unique_x,
        "sizeBytes": len(data),
    }
    if y_values is not None:
        summary["y"] = y_values
    return summary
