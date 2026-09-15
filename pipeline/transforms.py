"""Row transforms a confirmed mapping may request (canonical.py `Transform`), applied bronze → silver.

Each canonical column is parsed to its type after its transforms run. A non-blank value that cannot be parsed
becomes NULL in silver and is reported back as a parse error with its raw value — never silently dropped.
Blank stays NULL without an error (missing values are a validation question, V001, not a parsing one).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from pipeline.canonical import CanonicalField, Transform

LB_PER_KG = 2.2046226218
EXCEL_EPOCH = pd.Timestamp(date(1899, 12, 30))
DATE_FORMATS = {"parse_date_iso": "%Y-%m-%d", "parse_date_us": "%m/%d/%Y", "parse_date_dmy": "%d/%m/%Y"}


def apply(raw: pd.Series, field: CanonicalField, transforms: list[Transform]) -> tuple[pd.Series, pd.Series]:
    """Return (typed values with None for NULL, boolean mask of parse errors)."""
    values = raw.astype("string")
    if "trim" in transforms:
        values = values.str.strip()
    blank = values.isna() | (values.str.strip() == "")

    typed: pd.Series
    if field.kind == "date":
        parser = next(t for t in transforms if t in DATE_FORMATS or t == "parse_excel_serial")
        if parser == "parse_excel_serial":
            serial = pd.to_numeric(values.str.strip(), errors="coerce")
            whole = serial.notna() & (serial % 1 == 0)
            typed = (EXCEL_EPOCH + pd.to_timedelta(serial.where(whole), unit="D")).dt.date
        else:
            typed = pd.to_datetime(values.str.strip(), format=DATE_FORMATS[parser], errors="coerce").dt.date
    elif field.kind in ("qty", "weight"):
        typed = pd.to_numeric(values.str.strip(), errors="coerce").astype("float64")
        if "kg_to_lb" in transforms:
            typed = typed * LB_PER_KG
    elif field.kind == "line":
        number = pd.to_numeric(values.str.strip(), errors="coerce")
        typed = number.where(number.notna() & (number % 1 == 0)).astype("Int64")
    elif field.kind == "customer" and "normalize_customer_no" in transforms:
        digits = values.str.replace(r"\D", "", regex=True)
        typed = ("C" + digits.str.lstrip("0").replace("", "0").str.zfill(6)).where(digits.str.len() > 0)
    else:
        typed = values

    missing = pd.isna(typed) if not isinstance(typed.dtype, pd.StringDtype) else typed.isna()
    errors = missing & ~blank
    return typed.astype(object).where(~missing & ~blank, None), errors
