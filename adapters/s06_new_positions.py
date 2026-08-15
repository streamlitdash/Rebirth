"""Strict raw-blotter adapter scaffold for intraday new positions.

This module deliberately does not implement the pipeline conversion.  It owns
the raw ``MARKET``/``CASHFLOW`` blotter boundary that a later integration can
map to the existing product MarketBooks and dashboard release schema.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np
import pandas as pd

from core.s01_schema import TENOR_OPTION, TENOR_SWAP
from core.s02_pipeline import (
    PL,
    PORTFOLIO,
    RISK,
    RISK_GREEK,
    RISK_TYPE,
    UNDERLYING,
)
from .s01_common import exact_frame


ROW_TYPE = "Row Type"
MARKET = "MARKET"
CASHFLOW = "CASHFLOW"
TRADE_ID = "Trade ID"
POSITION_ID = "Position ID"
QUANTITY = "Quantity"
TRADED_LEVEL = "Traded Level"
TRADED_LEVEL_KNOWN = "Traded Level Known"
CASH_FLOW = "Cash Flow"

NEW_POSITION_BLOTTER_COLUMNS = (
    ROW_TYPE,
    TRADE_ID,
    POSITION_ID,
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    TENOR_SWAP,
    TENOR_OPTION,
    PORTFOLIO,
    RISK,
    QUANTITY,
    TRADED_LEVEL,
    TRADED_LEVEL_KNOWN,
    CASH_FLOW,
)
NEW_POSITION_COLUMNS = (*NEW_POSITION_BLOTTER_COLUMNS, PL)


class NewPositionsSource(Protocol):
    """Personal blotter callable bound by :func:`build_new_positions_adapter`."""

    def __call__(self, market_date: pd.Timestamp) -> pd.DataFrame: ...


NewPositionsLoader = Callable[[pd.Timestamp], pd.DataFrame]


def _normalized_date(value: object) -> pd.Timestamp:
    if value is None or isinstance(value, (bool, np.bool_)):
        raise TypeError("market_date must be a date-like value")
    try:
        date = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("market_date must be a valid scalar date") from exc
    if pd.isna(date):
        raise ValueError("market_date must be a valid scalar date")
    if date.tzinfo is not None:
        date = date.tz_localize(None)
    return date.normalize()


def _blank_mask(values: pd.Series) -> pd.Series:
    return values.isna() | values.map(
        lambda value: isinstance(value, str) and not value.strip()
    )


def _require_nonblank(
    frame: pd.DataFrame,
    mask: pd.Series,
    columns: tuple[str, ...],
    *,
    label: str,
) -> None:
    for column in columns:
        valid_text = frame[column].map(
            lambda value: isinstance(value, str) and bool(value.strip())
        )
        invalid = mask & ~valid_text
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"{label} column {column!r} must contain nonblank text at rows {rows}"
            )


def _require_text_or_blank(
    frame: pd.DataFrame,
    mask: pd.Series,
    columns: tuple[str, ...],
    *,
    label: str,
) -> None:
    for column in columns:
        valid = _blank_mask(frame[column]) | frame[column].map(
            lambda value: isinstance(value, str)
        )
        invalid = mask & ~valid
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"{label} column {column!r} must contain text or be blank at rows "
                f"{rows}"
            )


def _require_blank(
    frame: pd.DataFrame,
    mask: pd.Series,
    columns: tuple[str, ...],
    *,
    label: str,
) -> None:
    for column in columns:
        invalid = mask & ~_blank_mask(frame[column])
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(f"{label} column {column!r} must be blank at rows {rows}")


def _numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    raw = frame[column]
    boolean = raw.map(lambda value: isinstance(value, (bool, np.bool_)))
    if boolean.any():
        rows = frame.index[boolean].tolist()[:5]
        raise ValueError(
            f"new-position column {column!r} must contain numbers, not booleans, "
            f"at rows {rows}"
        )
    converted = pd.to_numeric(raw, errors="coerce")
    invalid = raw.notna() & converted.isna()
    if invalid.any():
        rows = frame.index[invalid].tolist()[:5]
        raise ValueError(f"new-position column {column!r} is nonnumeric at rows {rows}")
    finite = converted.dropna()
    if not finite.empty and not np.isfinite(finite.to_numpy(dtype=float)).all():
        rows = finite.index[~np.isfinite(finite.to_numpy(dtype=float))].tolist()[:5]
        raise ValueError(f"new-position column {column!r} is non-finite at rows {rows}")
    return converted.astype(float)


def validate_new_positions(value: object) -> pd.DataFrame:
    """Validate one mixed raw blotter and derive only cashflow P&L.

    ``MARKET`` rows intentionally contain no Open, Current, or Market Move.
    Their P&L remains unavailable until a later pipeline stage joins the
    declared market identity to a MarketBook.  ``Traded Level Known = False``
    is the explicit instruction for that stage to use Open as the trade level.

    ``CASHFLOW`` rows bypass market calculation: their P&L is exactly the
    supplied ``Cash Flow`` amount.
    """

    result = exact_frame(
        value,
        columns=NEW_POSITION_BLOTTER_COLUMNS,
        label="New Positions blotter",
    )
    if result.empty:
        result[PL] = pd.Series(dtype=float)
        return result.loc[:, list(NEW_POSITION_COLUMNS)]

    invalid_types = ~result[ROW_TYPE].isin({MARKET, CASHFLOW})
    if invalid_types.any():
        values = sorted(result.loc[invalid_types, ROW_TYPE].astype(str).unique())
        raise ValueError(
            f"{ROW_TYPE!r} must be exactly {MARKET!r} or {CASHFLOW!r}; invalid={values}"
        )

    all_rows = pd.Series(True, index=result.index)
    market_rows = result[ROW_TYPE].eq(MARKET)
    cashflow_rows = result[ROW_TYPE].eq(CASHFLOW)

    for column in (
        TRADE_ID,
        POSITION_ID,
        RISK_TYPE,
        RISK_GREEK,
        UNDERLYING,
        TENOR_SWAP,
        TENOR_OPTION,
        PORTFOLIO,
    ):
        result[column] = result[column].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )

    _require_nonblank(
        result,
        all_rows,
        (TRADE_ID, POSITION_ID, PORTFOLIO),
        label="New Positions blotter",
    )
    duplicate_identity = result.duplicated([TRADE_ID, POSITION_ID], keep=False)
    if duplicate_identity.any():
        values = (
            result.loc[duplicate_identity, [TRADE_ID, POSITION_ID]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise ValueError(
            f"new-position trade/position identity is duplicated: {values}"
        )

    _require_nonblank(
        result,
        market_rows,
        (RISK_TYPE, RISK_GREEK, UNDERLYING),
        label="MARKET row",
    )
    _require_text_or_blank(
        result,
        market_rows,
        (TENOR_SWAP, TENOR_OPTION),
        label="MARKET row",
    )
    _require_blank(
        result,
        cashflow_rows,
        (RISK_TYPE, RISK_GREEK, UNDERLYING, TENOR_SWAP, TENOR_OPTION),
        label="CASHFLOW row",
    )

    for column in (RISK, QUANTITY, TRADED_LEVEL, CASH_FLOW):
        result[column] = _numeric_column(result, column)

    no_market_size = market_rows & result[[RISK, QUANTITY]].isna().all(axis=1)
    if no_market_size.any():
        rows = result.index[no_market_size].tolist()[:5]
        raise ValueError(f"MARKET rows require Risk or Quantity at rows {rows}")
    _require_blank(
        result,
        cashflow_rows,
        (RISK, QUANTITY),
        label="CASHFLOW row",
    )

    invalid_flags = ~result[TRADED_LEVEL_KNOWN].map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    if invalid_flags.any():
        rows = result.index[invalid_flags].tolist()[:5]
        raise ValueError(f"{TRADED_LEVEL_KNOWN!r} must be boolean at rows {rows}")
    result[TRADED_LEVEL_KNOWN] = result[TRADED_LEVEL_KNOWN].astype(bool)

    known_level = market_rows & result[TRADED_LEVEL_KNOWN]
    missing_known_level = known_level & result[TRADED_LEVEL].isna()
    if missing_known_level.any():
        rows = result.index[missing_known_level].tolist()[:5]
        raise ValueError(f"known MARKET traded levels are missing at rows {rows}")
    must_fall_back_to_open = market_rows & ~result[TRADED_LEVEL_KNOWN]
    supplied_unknown_level = must_fall_back_to_open & result[TRADED_LEVEL].notna()
    if supplied_unknown_level.any():
        rows = result.index[supplied_unknown_level].tolist()[:5]
        raise ValueError(
            "MARKET rows marked with unknown traded level must leave "
            f"{TRADED_LEVEL!r} blank at rows {rows}"
        )
    invalid_cashflow_level = cashflow_rows & (
        result[TRADED_LEVEL_KNOWN] | result[TRADED_LEVEL].notna()
    )
    if invalid_cashflow_level.any():
        rows = result.index[invalid_cashflow_level].tolist()[:5]
        raise ValueError(f"CASHFLOW rows cannot carry a traded level at rows {rows}")

    market_cashflow = market_rows & result[CASH_FLOW].notna()
    if market_cashflow.any():
        rows = result.index[market_cashflow].tolist()[:5]
        raise ValueError(f"MARKET rows cannot carry {CASH_FLOW!r} at rows {rows}")
    missing_cashflow = cashflow_rows & result[CASH_FLOW].isna()
    if missing_cashflow.any():
        rows = result.index[missing_cashflow].tolist()[:5]
        raise ValueError(f"CASHFLOW rows require {CASH_FLOW!r} at rows {rows}")

    result[PL] = np.nan
    result.loc[cashflow_rows, PL] = result.loc[cashflow_rows, CASH_FLOW]
    return result.loc[:, list(NEW_POSITION_COLUMNS)].reset_index(drop=True)


def build_new_positions_adapter(
    *,
    blotter: NewPositionsSource,
) -> NewPositionsLoader:
    """Bind a personal raw-blotter function to the strict public contract."""

    if not callable(blotter):
        raise TypeError("blotter must be callable")

    def get_new_positions(market_date: pd.Timestamp) -> pd.DataFrame:
        selected_date = _normalized_date(market_date)
        return validate_new_positions(blotter(selected_date))

    return get_new_positions


def _fake_new_positions(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return deterministic illustrative rows; replace this source in production."""

    del market_date
    return pd.DataFrame(
        [
            [
                MARKET,
                "FAKE_REPLACE_ME - TRADE_001",
                "FAKE_REPLACE_ME - POSITION_001",
                "FX",
                "Delta",
                "FAKE_REPLACE_ME - EURUSD",
                "",
                "",
                "FAKE_REPLACE_ME - BOOK_A",
                125_000.0,
                np.nan,
                1.085,
                True,
                np.nan,
            ],
            [
                MARKET,
                "FAKE_REPLACE_ME - TRADE_002",
                "FAKE_REPLACE_ME - POSITION_002",
                "IR",
                "Delta",
                "FAKE_REPLACE_ME - USD-SOFR",
                "5Y",
                "",
                "FAKE_REPLACE_ME - BOOK_B",
                np.nan,
                5_000_000.0,
                np.nan,
                False,
                np.nan,
            ],
            [
                CASHFLOW,
                "FAKE_REPLACE_ME - TRADE_003",
                "FAKE_REPLACE_ME - CASHFLOW_001",
                "",
                "",
                "",
                "",
                "",
                "FAKE_REPLACE_ME - BOOK_A",
                np.nan,
                np.nan,
                np.nan,
                False,
                50_000.0,
            ],
        ],
        columns=list(NEW_POSITION_BLOTTER_COLUMNS),
    )


_DEFAULT_ADAPTER = build_new_positions_adapter(blotter=_fake_new_positions)


def get_new_positions(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return the validated deterministic fake new-position blotter."""

    return _DEFAULT_ADAPTER(market_date)


# Compatibility with the business connector name supplied by the user.  It is
# module-scoped and intentionally does not replace feeds.s01_sources.get_new_positions.
GetNewPositions = get_new_positions


__all__ = [
    "CASHFLOW",
    "CASH_FLOW",
    "GetNewPositions",
    "MARKET",
    "NEW_POSITION_BLOTTER_COLUMNS",
    "NEW_POSITION_COLUMNS",
    "NewPositionsLoader",
    "NewPositionsSource",
    "POSITION_ID",
    "QUANTITY",
    "ROW_TYPE",
    "TRADED_LEVEL",
    "TRADED_LEVEL_KNOWN",
    "TRADE_ID",
    "build_new_positions_adapter",
    "get_new_positions",
    "validate_new_positions",
]
