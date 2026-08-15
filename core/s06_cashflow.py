"""Canonical Intraday Cashflows connector contract and validation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd


CASHFLOW_ID = "Cashflow ID"
CASHFLOW_TIME = "Cashflow Time"
VALUE_DATE = "Value Date"
PORTFOLIO = "Portfolio"
SIGNOFF_GROUP = "SignoffGroup"
CURRENCY = "Currency"
CASHFLOW_TYPE = "Cashflow Type"
AMOUNT = "Amount"
STATUS = "Status"

INTRADAY_CASHFLOW_COLUMNS = (
    CASHFLOW_ID,
    CASHFLOW_TIME,
    VALUE_DATE,
    PORTFOLIO,
    SIGNOFF_GROUP,
    CURRENCY,
    CASHFLOW_TYPE,
    AMOUNT,
    STATUS,
)
INTRADAY_CASHFLOW_STATUSES = (
    "Pending",
    "Confirmed",
    "Sent",
    "Failed",
    "Cancelled",
)
_STATUS_BY_CASEFOLD = {value.casefold(): value for value in INTRADAY_CASHFLOW_STATUSES}
_TEXT_COLUMNS = (
    CASHFLOW_ID,
    PORTFOLIO,
    SIGNOFF_GROUP,
    CURRENCY,
    CASHFLOW_TYPE,
    STATUS,
)


class IntradayCashflowSchemaError(ValueError):
    """Raised when a connector result violates the canonical schema."""


@runtime_checkable
class IntradayCashflowLoader(Protocol):
    """Return exact-schema cashflows for one normalized business date."""

    def __call__(self, cashflow_date: pd.Timestamp, /) -> pd.DataFrame: ...


def normalize_cashflow_date(
    value: date | datetime | str | pd.Timestamp,
) -> pd.Timestamp:
    """Return a timezone-naive midnight date for a connector call."""
    if isinstance(value, (bool, np.bool_)):
        raise TypeError("cashflow_date must be a date-like value, not boolean")
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("cashflow_date is invalid") from exc
    if pd.isna(timestamp):
        raise ValueError("cashflow_date must not be blank or NaT")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def empty_intraday_cashflows() -> pd.DataFrame:
    """Return an empty frame with the exact public connector schema."""
    return pd.DataFrame(columns=list(INTRADAY_CASHFLOW_COLUMNS))


def _require_text(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column]
    normalized = values.astype("string").str.strip()
    invalid = (
        ~values.map(lambda value: isinstance(value, str))
        | normalized.eq("")
        | normalized.isna()
    )
    if invalid.any():
        rows = frame.index[invalid].tolist()[:5]
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {column!r} contains blank or non-text "
            f"values at rows {rows}"
        )
    return normalized


def validate_intraday_cashflows(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize one connector result without mutating it."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("intraday cashflow loader must return a pandas DataFrame")
    duplicate_columns = frame.columns[frame.columns.duplicated()].unique().tolist()
    if duplicate_columns:
        raise IntradayCashflowSchemaError(
            f"intraday cashflows contain duplicate columns: {duplicate_columns}"
        )
    actual = tuple(str(column) for column in frame.columns)
    if actual != INTRADAY_CASHFLOW_COLUMNS:
        missing = [
            column for column in INTRADAY_CASHFLOW_COLUMNS if column not in actual
        ]
        extra = [column for column in actual if column not in INTRADAY_CASHFLOW_COLUMNS]
        raise IntradayCashflowSchemaError(
            "intraday cashflow columns must match the canonical schema in order; "
            f"missing={missing}, extra={extra}, "
            f"expected={list(INTRADAY_CASHFLOW_COLUMNS)}"
        )

    result = frame.copy(deep=True)
    for column in _TEXT_COLUMNS:
        result[column] = _require_text(result, column)

    try:
        result[CASHFLOW_TIME] = pd.to_datetime(
            result[CASHFLOW_TIME], errors="raise", utc=True
        ).astype("datetime64[ns, UTC]")
    except (TypeError, ValueError) as exc:
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {CASHFLOW_TIME!r} contains invalid timestamps"
        ) from exc
    if result[CASHFLOW_TIME].isna().any():
        rows = result.index[result[CASHFLOW_TIME].isna()].tolist()[:5]
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {CASHFLOW_TIME!r} contains blank "
            f"timestamps at rows {rows}"
        )

    try:
        value_dates = pd.to_datetime(result[VALUE_DATE], errors="raise")
    except (TypeError, ValueError) as exc:
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {VALUE_DATE!r} contains invalid dates"
        ) from exc
    if getattr(value_dates.dt, "tz", None) is not None:
        value_dates = value_dates.dt.tz_localize(None)
    if value_dates.isna().any():
        rows = result.index[value_dates.isna()].tolist()[:5]
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {VALUE_DATE!r} contains blank dates at rows {rows}"
        )
    result[VALUE_DATE] = value_dates.dt.normalize()

    boolean_amounts = result[AMOUNT].map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    amounts = pd.to_numeric(result[AMOUNT], errors="coerce")
    invalid_amounts = boolean_amounts | amounts.isna() | ~np.isfinite(amounts)
    if invalid_amounts.any():
        rows = result.index[invalid_amounts].tolist()[:5]
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {AMOUNT!r} must contain finite numbers; "
            f"invalid rows {rows}"
        )
    result[AMOUNT] = amounts.astype(float)

    result[CURRENCY] = result[CURRENCY].str.upper()
    invalid_currency = ~result[CURRENCY].str.fullmatch(r"[A-Z]{3}")
    if invalid_currency.any():
        rows = result.index[invalid_currency].tolist()[:5]
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {CURRENCY!r} must use three-letter "
            f"currency codes; invalid rows {rows}"
        )

    normalized_status = result[STATUS].str.casefold().map(_STATUS_BY_CASEFOLD)
    if normalized_status.isna().any():
        values = sorted(result.loc[normalized_status.isna(), STATUS].unique().tolist())
        raise IntradayCashflowSchemaError(
            f"intraday cashflows column {STATUS!r} must use "
            f"{list(INTRADAY_CASHFLOW_STATUSES)}; invalid={values}"
        )
    result[STATUS] = normalized_status

    duplicate_ids = result[CASHFLOW_ID].duplicated(keep=False)
    if duplicate_ids.any():
        values = sorted(result.loc[duplicate_ids, CASHFLOW_ID].unique().tolist())
        raise IntradayCashflowSchemaError(
            f"intraday cashflows contain duplicate Cashflow ID values: {values[:5]}"
        )

    return result.sort_values(
        [CASHFLOW_TIME, CASHFLOW_ID], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)


def load_intraday_cashflows(
    loader: IntradayCashflowLoader,
    cashflow_date: date | datetime | str | pd.Timestamp,
) -> pd.DataFrame:
    """Call a site connector once and validate its complete result."""
    if not callable(loader):
        raise TypeError("intraday cashflow loader must be callable")
    selected_date = normalize_cashflow_date(cashflow_date)
    return validate_intraday_cashflows(loader(selected_date))


__all__ = [
    "AMOUNT",
    "CASHFLOW_ID",
    "CASHFLOW_TIME",
    "CASHFLOW_TYPE",
    "CURRENCY",
    "INTRADAY_CASHFLOW_COLUMNS",
    "INTRADAY_CASHFLOW_STATUSES",
    "IntradayCashflowLoader",
    "IntradayCashflowSchemaError",
    "PORTFOLIO",
    "SIGNOFF_GROUP",
    "STATUS",
    "VALUE_DATE",
    "empty_intraday_cashflows",
    "load_intraday_cashflows",
    "normalize_cashflow_date",
    "validate_intraday_cashflows",
]
