"""Read-only reconciliation of tabular extracts with source-row evidence."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from .policy import canonical, is_sensitive_path


def _index(rows: Iterable, keys: list[str]) -> tuple[list[str], dict]:
    rows = iter(rows)
    headers = [str(value).strip() if value is not None else "" for value in next(rows, ())]
    if not headers or "" in headers or len(set(headers)) != len(headers):
        raise ValueError("The first row must contain unique, nonempty column names.")
    if not set(keys) <= set(headers):
        raise ValueError("Business key columns missing: " + ", ".join(sorted(set(keys) - set(headers))))
    indexed = {}
    for number, values in enumerate(rows, start=2):
        if number > 100_001:
            raise ValueError("Each extract may contain at most 100,000 data rows.")
        if not any(value not in (None, "") for value in values):
            continue
        if len(values) != len(headers):
            raise ValueError(f"Row {number} has a different number of columns than the header.")
        record = dict(zip(headers, values))
        key = tuple(str(record[k]).strip() if record[k] is not None else "" for k in keys)
        if not all(key) or key in indexed:
            raise ValueError(f"Row {number} has a blank or duplicate business key: {key!r}")
        indexed[key] = (number, record)
    return headers, indexed


def _read(raw: str, keys: list[str], sheet: str | None) -> tuple[list[str], dict]:
    path = canonical(raw)
    if is_sensitive_path(path) or not path.is_file():
        raise ValueError("Select an accessible extract file outside credential locations.")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Extracts may be at most 50 MiB.")
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as source:
            return _index(csv.reader(source), keys)
    if path.suffix.lower() == ".xlsx":
        try:
            from openpyxl import load_workbook
            from openpyxl.utils.exceptions import InvalidFileException
        except ImportError as exc:
            raise ValueError("Install openpyxl for XLSX, or export both extracts to CSV.") from exc
        try:
            book = load_workbook(path, read_only=True, data_only=True)
        except (BadZipFile, InvalidFileException, KeyError) as exc:
            raise ValueError("The selected file is not a readable XLSX workbook.") from exc
        try:
            if sheet and sheet not in book.sheetnames:
                raise ValueError(f"Worksheet {sheet!r} does not exist.")
            worksheet = book[sheet] if sheet else book.worksheets[0]
            return _index(worksheet.iter_rows(values_only=True), keys)
        finally:
            book.close()
    raise ValueError("Extracts must be CSV or XLSX files.")


def _report(left: tuple, right: tuple, sample_limit: int = 20, *,
            workbook: Any = None, keys: list[str] | None = None, metric: str | None = None) -> dict[str, Any]:
    left_headers, before = left
    right_headers, after = right
    counts = dict.fromkeys(("changed", "left_only", "right_only", "unchanged"), 0)
    sample = []
    columns = [field for field in left_headers if field in right_headers]
    if metric and (metric not in columns or metric in (keys or [])):
        raise ValueError("The variance metric must be a non-key column present in both extracts.")
    breaks = values = None
    value_rows = value_sheet = 0
    if workbook:
        breaks = workbook.create_sheet("Breaks")
        _append_safe(breaks, ["Status", "First row", "Second row", *(keys or [])])
        headers = ["Status", "First row", "Second row", *(keys or []), "Column", "First value", "Second value"]
    baseline = closing = updates = additions = removals = Decimal(0)
    left_fields = set(left_headers)
    right_fields = set(right_headers)
    for key in dict.fromkeys((*before, *after)):
        old_row, old = before.get(key, (None, {}))
        new_row, new = after.get(key, (None, {}))
        changes = [field for field in columns if old.get(field) != new.get(field)] if old and new else []
        status = ("changed" if changes else "unchanged") if old and new else ("left_only" if old else "right_only")
        counts[status] += 1
        if metric:
            old_number = _number(old.get(metric), old_row, metric)
            new_number = _number(new.get(metric), new_row, metric)
            baseline += old_number
            closing += new_number
            if not old:
                additions += new_number
            elif not new:
                removals -= old_number
            else:
                updates += new_number - old_number
        if breaks is not None and status != "unchanged":
            prefix = [status, old_row, new_row, *key]
            _append_safe(breaks, prefix)
            fields = changes if old and new else (left_headers if old else right_headers)
            for field in fields:
                if field not in (keys or []):
                    if not value_rows:
                        value_sheet += 1
                        title = "Cell differences" if value_sheet == 1 else f"Cell differences {value_sheet}"
                        values = workbook.create_sheet(title)
                        _append_safe(values, headers)
                    _append_safe(values, [*prefix, field, old.get(field), new.get(field)])
                    value_rows = (value_rows + 1) % 1_048_575
        if status != "unchanged" and len(sample) < sample_limit:
            sample.append({
                "key": [value[:120] for value in key],
                "status": status,
                "left_row": old_row,
                "right_row": new_row,
                "changed_columns": [field[:120] for field in changes[:10]],
            })
    waterfall = {}
    if metric:
        waterfall = {"opening": baseline, "removed": removals, "changed": updates, "added": additions, "closing": closing}
    if workbook:
        if values is None:
            _append_safe(workbook.create_sheet("Cell differences"), headers)
        summary = workbook.create_sheet("Summary", 0)
        summary.append(["Result", "Rows"])
        for label, count in counts.items():
            summary.append([label, count])
        for label, fields in (("First extract only", left_fields - right_fields),
                              ("Second extract only", right_fields - left_fields)):
            for field in sorted(fields):
                _append_safe(summary, [label, field])
        if metric:
            _write_waterfall(workbook, metric, waterfall)
    return {
        "counts": counts, "sample": sample,
        **({"metric": metric, "waterfall": {label: str(value) for label, value in waterfall.items()}} if metric else {}),
        "left_only_columns": [field[:120] for field in left_headers if field not in right_headers][:20],
        "right_only_columns": [field[:120] for field in right_headers if field not in left_headers][:20],
    }


def _number(value: Any, row: int | None, metric: str) -> Decimal:
    if row is None:
        return Decimal(0)
    try:
        number = Decimal(str(value).strip())
        if number.is_finite() and abs(number) < Decimal("1e100"):
            return number
    except InvalidOperation:
        pass
    raise ValueError(f"Metric {metric!r} has a missing or invalid number at row {row}.")


def _append_safe(sheet: Any, row: list) -> None:
    from openpyxl.cell import WriteOnlyCell

    cells = []
    for value in row:
        if isinstance(value, str):
            cell = WriteOnlyCell(sheet, value=value)
            cell.data_type = "s"
            cells.append(cell)
        else:
            cells.append(value)
    sheet.append(cells)


def _write_waterfall(book: Any, metric: str, waterfall: dict[str, Decimal]) -> None:
    from openpyxl.chart import BarChart, Reference
    from openpyxl.chart.label import DataLabelList

    sheet = book.create_sheet("Variance waterfall")
    sheet.append(["Stage", "Amount", "Balance", "Positive offset", "Negative offset",
                  "Increase (+)", "Decrease (+)", "Increase (-)", "Decrease (-)", "Total"])
    running = Decimal(0)
    for stage, amount in waterfall.items():
        previous = running
        is_total = stage in ("opening", "closing")
        running = amount if is_total else running + amount
        low, high = sorted((previous, running))
        positive = max(high, 0) - max(low, 0)
        negative = min(low, 0) - min(high, 0)
        if is_total:
            row = [stage, amount, running, 0, 0, 0, 0, 0, 0, running]
        else:
            row = [stage, amount, running, max(low, 0), min(high, 0),
                   positive if amount >= 0 else 0, positive if amount < 0 else 0,
                   negative if amount >= 0 else 0, negative if amount < 0 else 0, 0]
        sheet.append(row)
    chart = BarChart()
    chart.title = f"{metric} variance"
    chart.grouping = "stacked"
    chart.overlap = 100
    chart.add_data(Reference(sheet, min_col=4, max_col=10, min_row=1, max_row=6), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=6))
    for series in chart.series[:2]:
        series.graphicalProperties.noFill = True
        series.graphicalProperties.line.noFill = True
    colors = ("16835F", "BB3939", "16835F", "BB3939", "2864A4")
    for series, color in zip(chart.series[2:], colors, strict=True):
        series.graphicalProperties.solidFill = color
    chart.dLbls = DataLabelList(showVal=False)
    chart.legend = None
    sheet.add_chart(chart, "L2")


def reconcile(left: str, right: str, keys: list[str], left_sheet: str | None = None,
              right_sheet: str | None = None, metric: str | None = None, *,
              include_workbook: bool = False) -> dict[str, Any]:
    if not 1 <= len(keys) <= 10 or len(set(keys)) != len(keys) or not all(key.strip() for key in keys):
        raise ValueError("Choose between one and ten distinct business key columns.")
    first, second = _read(left, keys, left_sheet), _read(right, keys, right_sheet)
    if not include_workbook:
        return _report(first, second, metric=metric)
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ValueError("Install openpyxl to produce a reconciliation workbook.") from exc
    # Validate metric values before opening write-only worksheet streams.
    if metric:
        if any(metric not in extract[0] for extract in (first, second)) or metric in keys:
            raise ValueError("The variance metric must be a non-key column present in both extracts.")
        for _, records in (first, second):
            for row, values in records.values():
                _number(values.get(metric), row, metric)
    workbook = Workbook(write_only=True)
    try:
        result = _report(first, second, workbook=workbook, keys=keys, metric=metric)
        sources = workbook.create_sheet("Sources")
        sources.append(["Input", "File", "Worksheet"])
        _append_safe(sources, ["First", Path(left).name, left_sheet or "Default"])
        _append_safe(sources, ["Second", Path(right).name, right_sheet or "Default"])
        with BytesIO() as output:
            workbook.save(output)
            result["workbook"] = output.getvalue()
        return result
    finally:
        workbook.close()


def check_extract(path: str, keys: list[str], sheet: str | None) -> dict[str, Any]:
    try:
        columns, records = _read(path, keys, sheet)
    except (OSError, ValueError) as exc:
        return {"ready": False, "error": str(exc)}
    return {"ready": True, "columns": columns, "rows": len(records)}


def test_fixture() -> dict[str, Any]:
    """Exercise the same comparison on fixed, local sample rows."""
    left = _index([("id", "rwa"), ("A", "10"), ("B", "20"), ("C", "30")], ["id"])
    right = _index([("id", "rwa"), ("A", "12"), ("C", "30"), ("D", "40")], ["id"])
    report = _report(left, right)
    expected = dict.fromkeys(("changed", "left_only", "right_only", "unchanged"), 1)
    return {"passed": report["counts"] == expected, "expected": expected, **report}
