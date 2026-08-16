"""Strict raw-blotter adapter scaffold for intraday new trades.

This module owns the raw ``MARKET``/``CASHFLOW`` blotter boundary. The shared
pipeline validates it again, joins MARKET rows to existing ProductSpec
MarketBooks, and releases both row types into the dashboard atomically.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
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
from core.s10_new_trades import (
    CASHFLOW,
    CASH_FLOW,
    CASH_FLOW_RISK_GREEK,
    CASH_FLOW_RISK_TYPE,
    MARKET,
    NEW_TRADE_BLOTTER_COLUMNS,
    NEW_TRADE_COLUMNS,
    NOTIONAL,
    POSITION_ID,
    ROW_TYPE,
    TRADED_LEVEL,
    TRADED_TRUE,
    TRADE_ID,
    TRADE_TIME,
    TRADER_CODE,
    TRADER_NAME,
)
from .s01_common import exact_frame

# Backward-compatible public names for callers that adopted the earlier
# adapter-scoped schema.  The core calculator now owns the exact column order.
NEW_POSITION_BLOTTER_COLUMNS = NEW_TRADE_BLOTTER_COLUMNS
NEW_POSITION_COLUMNS = NEW_TRADE_COLUMNS


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


def _numeric_column(
    frame: pd.DataFrame,
    column: str,
    *,
    allow_blank_text: bool = False,
) -> pd.Series:
    raw = frame[column]
    boolean = raw.map(lambda value: isinstance(value, (bool, np.bool_)))
    if boolean.any():
        rows = frame.index[boolean].tolist()[:5]
        raise ValueError(
            f"new-trade column {column!r} must contain numbers, not booleans, "
            f"at rows {rows}"
        )
    blank_text = raw.map(lambda value: isinstance(value, str) and not value.strip())
    normalized = raw.mask(blank_text) if allow_blank_text else raw
    converted = pd.to_numeric(normalized, errors="coerce")
    allowed_blank = (
        blank_text
        if allow_blank_text
        else pd.Series(False, index=frame.index, dtype=bool)
    )
    invalid = raw.notna() & ~allowed_blank & converted.isna()
    if invalid.any():
        rows = frame.index[invalid].tolist()[:5]
        raise ValueError(f"new-trade column {column!r} is nonnumeric at rows {rows}")
    finite = converted.dropna()
    if not finite.empty and not np.isfinite(finite.to_numpy(dtype=float)).all():
        rows = finite.index[~np.isfinite(finite.to_numpy(dtype=float))].tolist()[:5]
        raise ValueError(f"new-trade column {column!r} is non-finite at rows {rows}")
    return converted.astype(float)


def _trade_time_column(frame: pd.DataFrame) -> pd.Series:
    """Return timezone-naive trade timestamps without accepting numeric dates."""

    normalized: list[object] = []
    invalid_rows: list[object] = []
    for index, value in frame[TRADE_TIME].items():
        if (
            value is None
            or (isinstance(value, str) and not value.strip())
            or (pd.api.types.is_scalar(value) and pd.isna(value))
        ):
            normalized.append(pd.NaT)
            continue
        if isinstance(value, (bool, np.bool_)) or not isinstance(
            value,
            (str, date, datetime, np.datetime64, pd.Timestamp),
        ):
            invalid_rows.append(index)
            normalized.append(pd.NaT)
            continue
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError):
            invalid_rows.append(index)
            normalized.append(pd.NaT)
            continue
        if pd.isna(timestamp):
            invalid_rows.append(index)
            normalized.append(pd.NaT)
            continue
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_localize(None)
        normalized.append(timestamp)
    if invalid_rows:
        raise ValueError(
            f"new-trade column {TRADE_TIME!r} contains invalid timestamps at rows "
            f"{invalid_rows[:5]}"
        )
    return pd.Series(normalized, index=frame.index, dtype="datetime64[ns]")


def validate_new_positions(value: object) -> pd.DataFrame:
    """Validate one mixed raw blotter and derive only cashflow P&L.

    ``MARKET`` rows intentionally contain no Open, Current, or Market Move.
    Their P&L remains unavailable until a later pipeline stage joins the
    declared market identity to a MarketBook.  ``Traded True = False``
    is the explicit instruction for that stage to use Open as the trade level.

    ``CASHFLOW`` rows use the reserved ``Cash Flow``/``New`` classification and
    bypass market calculation: their P&L is exactly the supplied ``Cash Flow``
    amount.  MARKET ``Risk`` is the operative sensitivity; ``Notional`` is
    optional descriptive metadata and never substitutes for Risk.
    """

    result = exact_frame(
        value,
        columns=NEW_POSITION_BLOTTER_COLUMNS,
        label="New Trades blotter",
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
        TRADE_TIME,
        TRADER_CODE,
        TRADER_NAME,
    ):
        result[column] = result[column].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )

    _require_nonblank(
        result,
        all_rows,
        (TRADE_ID, POSITION_ID, PORTFOLIO),
        label="New Trades blotter",
    )
    duplicate_identity = result.duplicated([TRADE_ID, POSITION_ID], keep=False)
    if duplicate_identity.any():
        values = (
            result.loc[duplicate_identity, [TRADE_ID, POSITION_ID]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise ValueError(f"new-trade trade/position identity is duplicated: {values}")

    _require_nonblank(
        result,
        market_rows,
        (RISK_TYPE, RISK_GREEK, UNDERLYING, TRADER_CODE, TRADER_NAME),
        label="MARKET row",
    )
    reserved_market_identity = market_rows & (
        result[RISK_TYPE].eq(CASH_FLOW_RISK_TYPE)
        | result[RISK_GREEK].eq(CASH_FLOW_RISK_GREEK)
    )
    if reserved_market_identity.any():
        rows = result.index[reserved_market_identity].tolist()[:5]
        raise ValueError(
            "MARKET rows cannot use the reserved Cash Flow/New classification "
            f"at rows {rows}"
        )
    _require_text_or_blank(
        result,
        market_rows,
        (TENOR_SWAP, TENOR_OPTION),
        label="MARKET row",
    )
    invalid_cashflow_identity = cashflow_rows & (
        result[RISK_TYPE].ne(CASH_FLOW_RISK_TYPE)
        | result[RISK_GREEK].ne(CASH_FLOW_RISK_GREEK)
    )
    if invalid_cashflow_identity.any():
        rows = result.index[invalid_cashflow_identity].tolist()[:5]
        raise ValueError(
            "CASHFLOW rows require Risk Type='Cash Flow' and Risk Greek='New' "
            f"at rows {rows}"
        )
    _require_blank(
        result,
        cashflow_rows,
        (
            UNDERLYING,
            TENOR_SWAP,
            TENOR_OPTION,
            TRADE_TIME,
            TRADER_CODE,
            TRADER_NAME,
        ),
        label="CASHFLOW row",
    )

    for column in (RISK, TRADED_LEVEL, CASH_FLOW):
        result[column] = _numeric_column(result, column)
    result[NOTIONAL] = _numeric_column(
        result,
        NOTIONAL,
        allow_blank_text=True,
    )

    missing_market_risk = market_rows & result[RISK].isna()
    if missing_market_risk.any():
        rows = result.index[missing_market_risk].tolist()[:5]
        raise ValueError(f"MARKET rows require {RISK!r} at rows {rows}")
    _require_blank(
        result,
        cashflow_rows,
        (RISK, NOTIONAL),
        label="CASHFLOW row",
    )

    invalid_flags = ~result[TRADED_TRUE].map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    if invalid_flags.any():
        rows = result.index[invalid_flags].tolist()[:5]
        raise ValueError(f"{TRADED_TRUE!r} must be boolean at rows {rows}")
    result[TRADED_TRUE] = result[TRADED_TRUE].astype(bool)

    known_level = market_rows & result[TRADED_TRUE]
    missing_known_level = known_level & result[TRADED_LEVEL].isna()
    if missing_known_level.any():
        rows = result.index[missing_known_level].tolist()[:5]
        raise ValueError(f"known MARKET traded levels are missing at rows {rows}")
    must_fall_back_to_open = market_rows & ~result[TRADED_TRUE]
    supplied_unknown_level = must_fall_back_to_open & result[TRADED_LEVEL].notna()
    if supplied_unknown_level.any():
        rows = result.index[supplied_unknown_level].tolist()[:5]
        raise ValueError(
            "MARKET rows marked with unknown traded level must leave "
            f"{TRADED_LEVEL!r} blank at rows {rows}"
        )
    invalid_cashflow_level = cashflow_rows & (
        result[TRADED_TRUE] | result[TRADED_LEVEL].notna()
    )
    if invalid_cashflow_level.any():
        rows = result.index[invalid_cashflow_level].tolist()[:5]
        raise ValueError(f"CASHFLOW rows cannot carry a traded level at rows {rows}")

    result[TRADE_TIME] = _trade_time_column(result)
    missing_market_time = market_rows & result[TRADE_TIME].isna()
    if missing_market_time.any():
        rows = result.index[missing_market_time].tolist()[:5]
        raise ValueError(f"MARKET rows require {TRADE_TIME!r} at rows {rows}")

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

    trade_day = market_date.normalize()
    rows = [
        {
            ROW_TYPE: MARKET,
            TRADE_ID: "FAKE - CREDIT-001",
            POSITION_ID: "FAKE - CREDIT-POSITION-001",
            RISK_TYPE: "Credit",
            RISK_GREEK: "Delta",
            UNDERLYING: "FAKE_REPLACE_ME - CDX IG",
            TENOR_SWAP: "FAKE_REPLACE_ME - 5Y",
            TENOR_OPTION: "",
            PORTFOLIO: "FAKE_REPLACE_ME - BOOK_C",
            RISK: 18_000.0,
            NOTIONAL: 25_000_000.0,
            TRADED_LEVEL: 54.80,
            TRADED_TRUE: True,
            TRADE_TIME: trade_day + pd.Timedelta(hours=9, minutes=42),
            TRADER_CODE: "CRD01",
            TRADER_NAME: "Alex Morgan",
            CASH_FLOW: np.nan,
        },
        {
            ROW_TYPE: MARKET,
            TRADE_ID: "FAKE - CREDIT-002",
            POSITION_ID: "FAKE - CREDIT-POSITION-002",
            RISK_TYPE: "Credit",
            RISK_GREEK: "Delta",
            UNDERLYING: "FAKE_REPLACE_ME - iTraxx Main",
            TENOR_SWAP: "FAKE_REPLACE_ME - 7Y",
            TENOR_OPTION: "",
            PORTFOLIO: "FAKE_REPLACE_ME - BOOK_C",
            RISK: -12_500.0,
            NOTIONAL: 15_000_000.0,
            TRADED_LEVEL: np.nan,
            TRADED_TRUE: False,
            TRADE_TIME: trade_day + pd.Timedelta(hours=11, minutes=17),
            TRADER_CODE: "CRD02",
            TRADER_NAME: "Sam Taylor",
            CASH_FLOW: np.nan,
        },
        {
            ROW_TYPE: CASHFLOW,
            TRADE_ID: "FAKE - CASHFLOW-001",
            POSITION_ID: "FAKE - CASHFLOW-POSITION-001",
            RISK_TYPE: CASH_FLOW_RISK_TYPE,
            RISK_GREEK: CASH_FLOW_RISK_GREEK,
            UNDERLYING: "",
            TENOR_SWAP: "",
            TENOR_OPTION: "",
            PORTFOLIO: "FAKE_REPLACE_ME - BOOK_A",
            RISK: np.nan,
            NOTIONAL: np.nan,
            TRADED_LEVEL: np.nan,
            TRADED_TRUE: False,
            TRADE_TIME: pd.NaT,
            TRADER_CODE: "",
            TRADER_NAME: "",
            CASH_FLOW: 50_000.0,
        },
    ]
    return pd.DataFrame(rows, columns=list(NEW_POSITION_BLOTTER_COLUMNS))


_DEFAULT_ADAPTER = build_new_positions_adapter(blotter=_fake_new_positions)


def get_new_positions(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return the validated deterministic fake new-position blotter."""

    return _DEFAULT_ADAPTER(market_date)


# Compatibility with the business connector name supplied by the user. The feed
# exposes this adapter through its unified ``get_new_trades`` boundary.
GetNewPositions = get_new_positions


__all__ = [
    "CASHFLOW",
    "CASH_FLOW",
    "CASH_FLOW_RISK_GREEK",
    "CASH_FLOW_RISK_TYPE",
    "GetNewPositions",
    "MARKET",
    "NEW_POSITION_BLOTTER_COLUMNS",
    "NEW_POSITION_COLUMNS",
    "NewPositionsLoader",
    "NewPositionsSource",
    "NOTIONAL",
    "POSITION_ID",
    "ROW_TYPE",
    "TRADED_TRUE",
    "TRADED_LEVEL",
    "TRADE_ID",
    "TRADE_TIME",
    "TRADER_CODE",
    "TRADER_NAME",
    "build_new_positions_adapter",
    "get_new_positions",
    "validate_new_positions",
]
