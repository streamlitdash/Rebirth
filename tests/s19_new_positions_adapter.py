"""Focused tests for the raw New Positions blotter adapter scaffold."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

from adapters.s06_new_positions import (
    CASHFLOW,
    CASH_FLOW,
    GetNewPositions,
    MARKET,
    NEW_POSITION_BLOTTER_COLUMNS,
    NEW_POSITION_COLUMNS,
    POSITION_ID,
    ROW_TYPE,
    TRADED_LEVEL,
    TRADED_LEVEL_KNOWN,
    TRADE_ID,
    build_new_positions_adapter,
    get_new_positions,
    validate_new_positions,
)
from core.s01_schema import TENOR_OPTION, TENOR_SWAP
from core.s02_pipeline import PL, PORTFOLIO, RISK, RISK_GREEK, RISK_TYPE, UNDERLYING


def _market_row(
    *,
    risk: object = 25_000.0,
    quantity: object = np.nan,
    traded_level: object = 1.085,
    traded_level_known: object = True,
    cash_flow: object = np.nan,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                MARKET,
                "TRADE_001",
                "POSITION_001",
                "FX",
                "Delta",
                "EURUSD",
                "",
                "",
                "BOOK_A",
                risk,
                quantity,
                traded_level,
                traded_level_known,
                cash_flow,
            ]
        ],
        columns=list(NEW_POSITION_BLOTTER_COLUMNS),
    )


def _cashflow_row(*, cash_flow: object = 50_000.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                CASHFLOW,
                "TRADE_CF",
                "CASHFLOW_001",
                "",
                "",
                "",
                "",
                "",
                "BOOK_A",
                np.nan,
                np.nan,
                np.nan,
                False,
                cash_flow,
            ]
        ],
        columns=list(NEW_POSITION_BLOTTER_COLUMNS),
    )


def test_default_fake_adapter_models_market_and_cashflow_rows() -> None:
    result = get_new_positions(pd.Timestamp("2026-08-15 16:00"))

    assert GetNewPositions is get_new_positions
    assert tuple(result.columns) == NEW_POSITION_COLUMNS
    assert result[ROW_TYPE].tolist() == [MARKET, MARKET, CASHFLOW]
    assert not {"Open", "Current", "Market Move"}.intersection(result.columns)

    cashflow = result.loc[result[ROW_TYPE].eq(CASHFLOW)].iloc[0]
    assert cashflow[PL] == cashflow[CASH_FLOW] == 50_000.0

    market = result.loc[result[ROW_TYPE].eq(MARKET)]
    assert market[PL].isna().all()
    fallback = market.loc[~market[TRADED_LEVEL_KNOWN]].iloc[0]
    assert pd.isna(fallback[TRADED_LEVEL])


def test_personal_adapter_receives_normalized_date_and_copies_source() -> None:
    calls: list[pd.Timestamp] = []
    source = _market_row()

    def blotter(market_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(market_date)
        return source

    adapter = build_new_positions_adapter(blotter=blotter)
    result = adapter(pd.Timestamp("2026-08-15 16:45", tz="Europe/London"))
    result.loc[0, RISK] = -1.0

    assert calls == [pd.Timestamp("2026-08-15")]
    assert source.loc[0, RISK] == 25_000.0


@pytest.mark.parametrize(
    "column",
    [TRADE_ID, POSITION_ID, PORTFOLIO, RISK_TYPE, RISK_GREEK, UNDERLYING],
)
def test_required_market_identity_is_strict_text(column: str) -> None:
    frame = _market_row()
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = 123

    with pytest.raises(ValueError, match=f"{column!r} must contain nonblank text"):
        validate_new_positions(frame)


def test_optional_market_tenor_rejects_non_text_values() -> None:
    frame = _market_row()
    frame[TENOR_SWAP] = frame[TENOR_SWAP].astype(object)
    frame.loc[0, TENOR_SWAP] = 5

    with pytest.raises(ValueError, match="'Tenor Swap' must contain text or be blank"):
        validate_new_positions(frame)


@pytest.mark.parametrize(
    ("row_factory", "column"),
    [
        (_market_row, RISK),
        (_market_row, "Quantity"),
        (_market_row, TRADED_LEVEL),
        (_cashflow_row, CASH_FLOW),
    ],
)
def test_financial_numeric_fields_reject_booleans(row_factory, column: str) -> None:
    frame = row_factory()
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = True

    with pytest.raises(ValueError, match="numbers, not booleans"):
        validate_new_positions(frame)


@pytest.mark.parametrize(
    "transform",
    [
        lambda frame: frame.assign(**{TRADED_LEVEL: np.nan}),
        lambda frame: frame.assign(**{TRADED_LEVEL: 1.085, TRADED_LEVEL_KNOWN: False}),
        lambda frame: frame.assign(**{TRADED_LEVEL_KNOWN: "False"}),
    ],
)
def test_market_traded_level_availability_is_explicit(
    transform: Callable[[pd.DataFrame], pd.DataFrame],
) -> None:
    with pytest.raises(ValueError, match="traded level|boolean"):
        validate_new_positions(transform(_market_row()))


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (_market_row(risk=np.nan, quantity=np.nan), "require Risk or Quantity"),
        (_market_row(cash_flow=1.0), "MARKET rows cannot carry 'Cash Flow'"),
        (_cashflow_row(cash_flow=np.nan), "CASHFLOW rows require 'Cash Flow'"),
    ],
)
def test_row_types_reject_ambiguous_financial_fields(
    frame: pd.DataFrame,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_new_positions(frame)


def test_cashflow_cannot_carry_market_identity_or_traded_level() -> None:
    with_identity = _cashflow_row()
    with_identity.loc[0, RISK_TYPE] = "FX"
    with pytest.raises(
        ValueError, match="CASHFLOW row column 'Risk Type' must be blank"
    ):
        validate_new_positions(with_identity)

    with_level = _cashflow_row()
    with_level.loc[0, TRADED_LEVEL] = 1.0
    with pytest.raises(ValueError, match="cannot carry a traded level"):
        validate_new_positions(with_level)


def test_schema_is_exact_and_trade_position_identity_is_unique() -> None:
    wrong_order = _market_row().loc[:, list(reversed(NEW_POSITION_BLOTTER_COLUMNS))]
    with pytest.raises(ValueError, match="columns must be exactly"):
        validate_new_positions(wrong_order)

    duplicated = pd.concat([_market_row(), _market_row()], ignore_index=True)
    with pytest.raises(ValueError, match="trade/position identity is duplicated"):
        validate_new_positions(duplicated)

    whitespace_duplicate = pd.concat([_market_row(), _market_row()], ignore_index=True)
    whitespace_duplicate.loc[1, TRADE_ID] = " TRADE_001 "
    whitespace_duplicate.loc[1, POSITION_ID] = " POSITION_001 "
    with pytest.raises(ValueError, match="trade/position identity is duplicated"):
        validate_new_positions(whitespace_duplicate)


def test_cashflow_pl_is_exactly_the_signed_cashflow_amount() -> None:
    rows = pd.concat(
        [_cashflow_row(cash_flow=50_000.0), _cashflow_row(cash_flow=-12_500.0)],
        ignore_index=True,
    )
    rows.loc[1, TRADE_ID] = "TRADE_CF_2"
    rows.loc[1, POSITION_ID] = "CASHFLOW_002"

    result = validate_new_positions(rows)

    assert result[PL].tolist() == [50_000.0, -12_500.0]
    for column in (RISK_TYPE, RISK_GREEK, UNDERLYING, TENOR_SWAP, TENOR_OPTION):
        assert result[column].eq("").all()
    assert result[PORTFOLIO].tolist() == ["BOOK_A", "BOOK_A"]
