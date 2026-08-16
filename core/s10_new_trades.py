"""Pure New Trades validation, MarketBook joins, and P&L calculation.

The raw blotter stays at trade/position grain.  Market quotes remain at their
canonical ProductSpec grain and are joined many-to-one.  A supplied traded
level is a row-local P&L reference; it never overwrites the shared opening
MarketBook.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from numbers import Real
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from core.s01_schema import (
    TENOR_OPTION,
    TENOR_OPTION_ORDER,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
)

if TYPE_CHECKING:
    from core.s02_pipeline import ProductSpec


# Canonical cube labels are duplicated as literals so ``core.s02_pipeline`` can
# import this module without a cycle.  Tests assert these labels against the
# pipeline's public constants.
RISK_TYPE = "Risk Type"
SOURCE_TYPE = "Source Type"
RISK_GREEK = "Risk Greek"
SPLIT = "Split"
UNDERLYING = "Underlying"
PORTFOLIO = "Portfolio"
GROUP = "Group"
RISK = "Risk"
DRISK = "dRisk"
OPEN = "Open"
CURRENT = "Current"
MARKET_STATUS = "Market Status"
MARKET_MOVE = "Move"
MARKET_AVAILABLE = "Market Available"
MARKET_DATA_STATUS = "Market Data Status"
PL = "PL"

ROW_TYPE = "Row Type"
MARKET = "MARKET"
CASHFLOW = "CASHFLOW"
TRADE_ID = "Trade ID"
POSITION_ID = "Position ID"
NOTIONAL = "Notional"
TRADED_LEVEL = "Traded Level"
TRADED_TRUE = "Traded True"
TRADE_TIME = "Trade Time"
TRADER_CODE = "Trader Code"
TRADER_NAME = "Trader Name"
CASH_FLOW = "Cash Flow"
CASH_FLOW_RISK_TYPE = "Cash Flow"
CASH_FLOW_RISK_GREEK = "New"

NEW_TRADES_SPLIT = "New Trades"
# Compatibility for connector code that imported the earlier symbol name.
NEW_POSITION_SPLIT = NEW_TRADES_SPLIT
NEW_TRADE_GROUP = "New Trades"
CASH_FLOW_SOURCE_TYPE = "new-position/cash-flow"
CASH_FLOW_GROUP = "Cash Flow"
CASH_FLOW_UNDERLYING = "Cash Flow"
CASH_FLOW_MARKET_STATUS = "Identity P&L; market data not applicable"

PL_REFERENCE_LEVEL = "P&L Reference Level"
PL_REFERENCE_SOURCE = "P&L Reference Source"
PL_MOVE = "P&L Move"

NEW_TRADE_BLOTTER_COLUMNS = (
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
    NOTIONAL,
    TRADED_LEVEL,
    TRADED_TRUE,
    TRADE_TIME,
    TRADER_CODE,
    TRADER_NAME,
    CASH_FLOW,
)
NEW_TRADE_COLUMNS = (*NEW_TRADE_BLOTTER_COLUMNS, PL)

NEW_TRADE_DETAIL_COLUMNS = (
    TRADE_ID,
    RISK,
    NOTIONAL,
    TRADED_LEVEL,
    TRADE_TIME,
    TRADER_CODE,
    TRADER_NAME,
)

_TRACE_COLUMNS = (
    ROW_TYPE,
    TRADE_ID,
    POSITION_ID,
    NOTIONAL,
    TRADED_LEVEL,
    TRADED_TRUE,
    TRADE_TIME,
    TRADER_CODE,
    TRADER_NAME,
    CASH_FLOW,
    PL_REFERENCE_LEVEL,
    PL_REFERENCE_SOURCE,
    PL_MOVE,
)


def _product_catalogue() -> tuple[
    dict[tuple[str, str], ProductSpec], dict[str, ProductSpec]
]:
    from core.s02_pipeline import PRODUCT_SPECS

    by_pair = {
        (spec.risk_type, spec.risk_greek): spec for spec in PRODUCT_SPECS.values()
    }
    by_source = {spec.source_type: spec for spec in PRODUCT_SPECS.values()}
    return by_pair, by_source


def _normalized_text(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    if value is None or (pd.api.types.is_scalar(value) and pd.isna(value)):
        return ""
    return value


def _finite_numeric(frame: pd.DataFrame, column: str, mask: pd.Series) -> pd.Series:
    raw = frame.loc[mask, column]
    booleans = raw.map(lambda value: isinstance(value, (bool, np.bool_)))
    numeric = pd.to_numeric(raw, errors="coerce")
    invalid = booleans | numeric.isna() | ~numeric.fillna(0.0).map(np.isfinite)
    if invalid.any():
        rows = raw.index[invalid].tolist()[:5]
        raise ValueError(
            f"New Trades column {column!r} must contain finite numbers at rows {rows}"
        )
    return numeric.astype(float)


def _optional_finite_numeric(
    frame: pd.DataFrame, column: str, mask: pd.Series
) -> pd.Series:
    """Validate supplied numeric metadata while allowing genuinely blank values."""

    raw = frame.loc[mask, column]
    blank = raw.map(
        lambda value: (
            value is None
            or (pd.api.types.is_scalar(value) and pd.isna(value))
            or (isinstance(value, str) and not value.strip())
        )
    )
    supplied = ~blank
    supplied_raw = raw.loc[supplied]
    booleans = supplied_raw.map(lambda value: isinstance(value, (bool, np.bool_)))
    numeric = pd.to_numeric(supplied_raw, errors="coerce")
    invalid = booleans | numeric.isna() | ~numeric.fillna(0.0).map(np.isfinite)
    if invalid.any():
        rows = supplied_raw.index[invalid].tolist()[:5]
        raise ValueError(
            f"New Trades column {column!r} must be blank or contain finite numbers "
            f"at rows {rows}"
        )
    result = pd.Series(np.nan, index=raw.index, dtype=float)
    result.loc[supplied] = numeric.astype(float)
    return result


def _validate_axes(
    rows: pd.DataFrame,
    *,
    row_index: object,
    spec: ProductSpec,
) -> None:
    declared = set(spec.tenor_columns)
    for column in (TENOR_SWAP, TENOR_OPTION):
        value = rows.at[row_index, column]
        if column in declared and not value:
            raise ValueError(
                f"New Trades pair {(spec.risk_type, spec.risk_greek)!r} requires "
                f"{column!r} at row {row_index}"
            )
        if column not in declared and value:
            raise ValueError(
                f"New Trades pair {(spec.risk_type, spec.risk_greek)!r} does not "
                f"use {column!r}; row {row_index} must leave it blank"
            )


def validate_new_trade_rows(raw: object) -> pd.DataFrame:
    """Validate the release boundary independently of the personal adapter."""

    if not isinstance(raw, pd.DataFrame):
        raise TypeError("New Trades loader must return a pandas DataFrame")
    if tuple(raw.columns) != NEW_TRADE_COLUMNS:
        raise ValueError(
            "New Trades columns must be exactly "
            f"{list(NEW_TRADE_COLUMNS)} in that order; found {list(raw.columns)}"
        )
    rows = raw.copy()
    text_columns = (
        ROW_TYPE,
        TRADE_ID,
        POSITION_ID,
        RISK_TYPE,
        RISK_GREEK,
        UNDERLYING,
        TENOR_SWAP,
        TENOR_OPTION,
        PORTFOLIO,
        TRADER_CODE,
        TRADER_NAME,
    )
    for column in text_columns:
        rows[column] = rows[column].map(_normalized_text)
        invalid_text = ~rows[column].map(lambda value: isinstance(value, str))
        if invalid_text.any():
            invalid = rows.index[invalid_text].tolist()[:5]
            raise ValueError(
                f"New Trades column {column!r} must contain text at rows {invalid}"
            )
    required_all = (ROW_TYPE, TRADE_ID, POSITION_ID, PORTFOLIO)
    for column in required_all:
        blank = rows[column].eq("")
        if blank.any():
            invalid = rows.index[blank].tolist()[:5]
            raise ValueError(
                f"New Trades column {column!r} must be nonblank at rows {invalid}"
            )
    invalid_type = ~rows[ROW_TYPE].isin({MARKET, CASHFLOW})
    if invalid_type.any():
        invalid = rows.index[invalid_type].tolist()[:5]
        raise ValueError(f"New Trades has invalid Row Type at rows {invalid}")
    duplicates = rows.duplicated([TRADE_ID, POSITION_ID], keep=False)
    if duplicates.any():
        invalid = rows.index[duplicates].tolist()[:10]
        raise ValueError(f"New Trades has duplicate trade/position rows {invalid}")

    market = rows[ROW_TYPE].eq(MARKET)
    cashflow = rows[ROW_TYPE].eq(CASHFLOW)
    for column in (RISK_TYPE, RISK_GREEK, UNDERLYING, TRADER_CODE, TRADER_NAME):
        blank = market & rows[column].eq("")
        if blank.any():
            invalid = rows.index[blank].tolist()[:5]
            raise ValueError(f"MARKET rows require {column!r} at rows {invalid}")
    invalid_cashflow_identity = cashflow & (
        rows[RISK_TYPE].ne(CASH_FLOW_RISK_TYPE)
        | rows[RISK_GREEK].ne(CASH_FLOW_RISK_GREEK)
    )
    if invalid_cashflow_identity.any():
        invalid = rows.index[invalid_cashflow_identity].tolist()[:5]
        raise ValueError(
            f"CASHFLOW rows require the Cash Flow/New identity at rows {invalid}"
        )
    invalid_reserved_market = market & (
        rows[RISK_TYPE].eq(CASH_FLOW_RISK_TYPE)
        | rows[RISK_GREEK].eq(CASH_FLOW_RISK_GREEK)
    )
    if invalid_reserved_market.any():
        invalid = rows.index[invalid_reserved_market].tolist()[:5]
        raise ValueError(f"MARKET rows cannot use Cash Flow/New at rows {invalid}")

    if not rows.empty:
        rows.loc[market, RISK] = _finite_numeric(rows, RISK, market)
        rows.loc[market, NOTIONAL] = _optional_finite_numeric(rows, NOTIONAL, market)
        rows.loc[cashflow, CASH_FLOW] = _finite_numeric(rows, CASH_FLOW, cashflow)
    invalid_flags = ~rows[TRADED_TRUE].map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    if invalid_flags.any():
        invalid = rows.index[invalid_flags].tolist()[:5]
        raise ValueError(f"{TRADED_TRUE!r} must be boolean at rows {invalid}")
    known = market & rows[TRADED_TRUE].astype(bool)
    traded_numeric = pd.to_numeric(rows[TRADED_LEVEL], errors="coerce")
    missing_known = known & traded_numeric.isna()
    unexpected_unknown = (
        market & ~rows[TRADED_TRUE].astype(bool) & traded_numeric.notna()
    )
    if missing_known.any() or unexpected_unknown.any():
        invalid = rows.index[missing_known | unexpected_unknown].tolist()[:5]
        raise ValueError(
            "Traded True requires a finite Traded Level and False requires it blank; "
            f"invalid rows {invalid}"
        )
    nonfinite_traded = known & ~traded_numeric.fillna(0.0).map(np.isfinite)
    if nonfinite_traded.any():
        invalid = rows.index[nonfinite_traded].tolist()[:5]
        raise ValueError(f"Traded Level must be finite at rows {invalid}")
    rows[TRADED_LEVEL] = traded_numeric.astype(float)

    pairs, _ = _product_catalogue()
    for row_index, row in rows.loc[market].iterrows():
        pair = (row[RISK_TYPE], row[RISK_GREEK])
        try:
            spec = pairs[pair]
        except KeyError as exc:
            raise ValueError(
                f"New Trades pair {pair!r} is not in PRODUCT_SPECS at row {row_index}"
            ) from exc
        _validate_axes(rows, row_index=row_index, spec=spec)
    return rows.reset_index(drop=True)


def new_trade_market_scope(raw_or_validated: object) -> dict[str, tuple[str, ...]]:
    """Return ordered ProductSpec/Underlying scope required by MARKET trades."""

    rows = validate_new_trade_rows(raw_or_validated)
    pairs, _ = _product_catalogue()
    scope: defaultdict[str, list[str]] = defaultdict(list)
    for row in rows.loc[rows[ROW_TYPE].eq(MARKET)].to_dict("records"):
        spec = pairs[(row[RISK_TYPE], row[RISK_GREEK])]
        underlying = row[UNDERLYING]
        if underlying not in scope[spec.source_type]:
            scope[spec.source_type].append(underlying)
    return {source_type: tuple(values) for source_type, values in scope.items()}


def _market_for_spec(
    market_frames: Mapping[str, pd.DataFrame], spec: ProductSpec
) -> pd.DataFrame:
    columns = [
        *spec.market_keys,
        *spec.tenor_order_columns,
        OPEN,
        CURRENT,
        MARKET_STATUS,
        MARKET_MOVE,
        MARKET_AVAILABLE,
        MARKET_DATA_STATUS,
    ]
    market = market_frames.get(spec.source_type)
    if market is None:
        return pd.DataFrame(columns=columns)
    if not isinstance(market, pd.DataFrame):
        raise TypeError(f"MarketBook {spec.source_type!r} must be a DataFrame")
    missing = [column for column in columns if column not in market]
    if missing:
        raise ValueError(
            f"MarketBook {spec.source_type!r} is missing columns {missing}"
        )
    selected = market.loc[:, columns].copy()
    if selected.duplicated(spec.market_keys).any():
        raise ValueError(
            f"MarketBook {spec.source_type!r} has duplicate canonical quote keys"
        )
    return selected


def _validated_multipliers(
    multipliers: Mapping[str, float] | None,
) -> dict[str, float]:
    raw = dict(multipliers or {})
    from core.s02_pipeline import PRODUCT_SPECS

    unknown = sorted(set(raw) - set(PRODUCT_SPECS))
    if unknown:
        raise ValueError(f"unknown New Trades multiplier keys: {unknown}")
    result: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
            raise TypeError(f"multiplier for {key!r} must be a real number")
        numeric = float(value)
        if not np.isfinite(numeric):
            raise ValueError(f"multiplier for {key!r} must be finite")
        result[key] = numeric
    return result


def _complete_tenors(frame: pd.DataFrame, spec: ProductSpec) -> pd.DataFrame:
    result = frame.copy()
    if TENOR_SWAP not in spec.tenor_columns:
        result[TENOR_SWAP] = "Spot" if spec.key == "fxdelta" else "N/A"
    if TENOR_OPTION not in spec.tenor_columns:
        result[TENOR_OPTION] = "N/A"
    for order_column in (TENOR_SWAP_ORDER, TENOR_OPTION_ORDER):
        if order_column not in result:
            result[order_column] = pd.Series(pd.NA, index=result.index, dtype="Int64")
        else:
            result[order_column] = pd.to_numeric(
                result[order_column], errors="raise"
            ).astype("Int64")
    return result


def _cashflow_rows(rows: pd.DataFrame) -> pd.DataFrame:
    cashflows = rows.loc[rows[ROW_TYPE].eq(CASHFLOW)].copy()
    if cashflows.empty:
        return cashflows
    cashflows[SOURCE_TYPE] = CASH_FLOW_SOURCE_TYPE
    cashflows[SPLIT] = NEW_TRADES_SPLIT
    cashflows[GROUP] = CASH_FLOW_GROUP
    cashflows[UNDERLYING] = CASH_FLOW_UNDERLYING
    cashflows[TENOR_SWAP] = "N/A"
    cashflows[TENOR_OPTION] = "N/A"
    cashflows[TENOR_SWAP_ORDER] = pd.Series(pd.NA, index=cashflows.index, dtype="Int64")
    cashflows[TENOR_OPTION_ORDER] = pd.Series(
        pd.NA, index=cashflows.index, dtype="Int64"
    )
    cashflows[RISK] = pd.to_numeric(cashflows[CASH_FLOW], errors="raise").astype(float)
    cashflows[DRISK] = np.nan
    cashflows[OPEN] = np.nan
    cashflows[CURRENT] = np.nan
    cashflows[MARKET_STATUS] = "N/A"
    cashflows[MARKET_MOVE] = np.nan
    cashflows[MARKET_AVAILABLE] = False
    cashflows[MARKET_DATA_STATUS] = CASH_FLOW_MARKET_STATUS
    cashflows[PL] = cashflows[RISK]
    cashflows[PL_REFERENCE_LEVEL] = 1.0
    cashflows[PL_REFERENCE_SOURCE] = "Identity factor"
    cashflows[PL_MOVE] = 1.0
    return cashflows


def _market_trade_rows(
    rows: pd.DataFrame,
    market_frames: Mapping[str, pd.DataFrame],
    multipliers: Mapping[str, float],
) -> list[pd.DataFrame]:
    pairs, _ = _product_catalogue()
    released: list[pd.DataFrame] = []
    market_rows = rows.loc[rows[ROW_TYPE].eq(MARKET)]
    for pair, pair_rows in market_rows.groupby(
        [RISK_TYPE, RISK_GREEK], sort=False, dropna=False
    ):
        spec = pairs[pair]
        result = pair_rows.copy()
        market = _market_for_spec(market_frames, spec)
        result = result.merge(
            market,
            on=spec.market_keys,
            how="left",
            validate="many_to_one",
            indicator="__New Trade Market Merge__",
        )
        missing_match = result["__New Trade Market Merge__"].ne("both")
        result[MARKET_AVAILABLE] = result[MARKET_AVAILABLE].fillna(False).astype(bool)
        result.loc[missing_match, MARKET_DATA_STATUS] = "No matching market row"
        result[MARKET_DATA_STATUS] = result[MARKET_DATA_STATUS].fillna(
            "No matching market row"
        )
        result = result.drop(columns="__New Trade Market Merge__")
        result[SOURCE_TYPE] = spec.source_type
        result[SPLIT] = NEW_TRADES_SPLIT
        result[GROUP] = NEW_TRADE_GROUP
        result[DRISK] = np.nan

        traded_true = result[TRADED_TRUE].astype(bool)
        result[PL_REFERENCE_LEVEL] = result[OPEN]
        result.loc[traded_true, PL_REFERENCE_LEVEL] = result.loc[
            traded_true, TRADED_LEVEL
        ]
        result[PL_REFERENCE_SOURCE] = np.where(
            traded_true, "Traded Level", "Market Open"
        )
        result[PL_MOVE] = result[CURRENT] - result[PL_REFERENCE_LEVEL]

        reference = pd.to_numeric(result[PL_REFERENCE_LEVEL], errors="coerce")
        current = pd.to_numeric(result[CURRENT], errors="coerce")
        raw_move = current - reference
        pl_available = current.notna() & reference.notna()
        multiplier = multipliers.get(spec.key, 1.0)
        if spec.pl_formula == "percentage":
            nonzero = reference.ne(0.0)
            pnl_move = raw_move / reference.where(nonzero)
            pl_available &= nonzero
            result.loc[reference.eq(0.0), MARKET_DATA_STATUS] = (
                "P&L reference is zero; percentage P&L unavailable"
            )
        else:
            pnl_move = raw_move

        if spec.pl_formula != "taylor_gamma":
            result[PL] = result[RISK].astype(float) * pnl_move * multiplier
            result.loc[~pl_available, PL] = np.nan
            missing_open_with_trade = (
                traded_true & result[OPEN].isna() & current.notna()
            )
            result.loc[missing_open_with_trade, MARKET_DATA_STATUS] = (
                "P&L available from Traded Level; official Open unavailable"
            )
            released.append(_complete_tenors(result, spec))
            continue

        taylor_move = raw_move * spec.gamma_move_scale
        developed_risk = result[RISK].astype(float) * taylor_move / spec.gamma_risk_step
        sourced = result.copy()
        sourced[PL] = 0.5 * developed_risk * taylor_move * multiplier
        sourced.loc[~pl_available, PL] = np.nan
        released.append(_complete_tenors(sourced, spec))

        derived = result.loc[pl_available].copy()
        derived[RISK_GREEK] = "Delta"
        derived[RISK] = developed_risk.loc[derived.index]
        derived[DRISK] = np.nan
        derived[PL] = 0.0
        released.append(_complete_tenors(derived, spec))
    return released


def build_new_trade_rows(
    raw_or_validated: object,
    market_frames: Mapping[str, pd.DataFrame],
    *,
    multipliers: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Release New Trades at position grain using the existing MarketBooks."""

    if not isinstance(market_frames, Mapping):
        raise TypeError("market_frames must map Source Type to validated MarketBooks")
    rows = validate_new_trade_rows(raw_or_validated)
    validated_multipliers = _validated_multipliers(multipliers)
    frames = _market_trade_rows(rows, market_frames, validated_multipliers)
    cashflows = _cashflow_rows(rows)
    if not cashflows.empty:
        frames.append(cashflows)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True, sort=False)
    # Keep the complete audit trail; the standard release path selects its own
    # dashboard columns and leaves these fields in combined_pl for detail use.
    for column in _TRACE_COLUMNS:
        if column not in result:
            result[column] = np.nan
    return result


__all__ = [
    "CASHFLOW",
    "CASH_FLOW",
    "CASH_FLOW_GROUP",
    "CASH_FLOW_MARKET_STATUS",
    "CASH_FLOW_RISK_GREEK",
    "CASH_FLOW_RISK_TYPE",
    "CASH_FLOW_SOURCE_TYPE",
    "MARKET",
    "NEW_POSITION_SPLIT",
    "NEW_TRADES_SPLIT",
    "NEW_TRADE_BLOTTER_COLUMNS",
    "NEW_TRADE_COLUMNS",
    "NEW_TRADE_DETAIL_COLUMNS",
    "NOTIONAL",
    "PL_MOVE",
    "PL_REFERENCE_LEVEL",
    "PL_REFERENCE_SOURCE",
    "POSITION_ID",
    "ROW_TYPE",
    "TRADED_LEVEL",
    "TRADED_TRUE",
    "TRADE_ID",
    "TRADE_TIME",
    "TRADER_CODE",
    "TRADER_NAME",
    "build_new_trade_rows",
    "new_trade_market_scope",
    "validate_new_trade_rows",
]
