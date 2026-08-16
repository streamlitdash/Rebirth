"""Focused tests for the raw New Trades blotter adapter scaffold."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

from adapters.s06_new_positions import (
    CASHFLOW,
    CASH_FLOW,
    CASH_FLOW_RISK_GREEK,
    CASH_FLOW_RISK_TYPE,
    GetNewPositions,
    MARKET,
    NEW_POSITION_BLOTTER_COLUMNS,
    NEW_POSITION_COLUMNS,
    NOTIONAL,
    POSITION_ID,
    ROW_TYPE,
    TRADED_LEVEL,
    TRADED_TRUE,
    TRADE_ID,
    TRADE_TIME,
    TRADER_CODE,
    TRADER_NAME,
    build_new_positions_adapter,
    get_new_positions,
    validate_new_positions,
)
from core.s01_schema import TENOR_OPTION, TENOR_SWAP
from core.s02_pipeline import PL, PORTFOLIO, RISK, RISK_GREEK, RISK_TYPE, UNDERLYING


def _market_row(
    *,
    risk: object = 25_000.0,
    notional: object = 5_000_000.0,
    traded_level: object = 1.085,
    traded_true: object = True,
    trade_time: object = "2026-08-15 09:42:00",
    trader_code: object = "FX01",
    trader_name: object = "Alex Morgan",
    cash_flow: object = np.nan,
) -> pd.DataFrame:
    row = {
        ROW_TYPE: MARKET,
        TRADE_ID: "TRADE_001",
        POSITION_ID: "POSITION_001",
        RISK_TYPE: "FX",
        RISK_GREEK: "Delta",
        UNDERLYING: "EURUSD",
        TENOR_SWAP: "",
        TENOR_OPTION: "",
        PORTFOLIO: "BOOK_A",
        RISK: risk,
        NOTIONAL: notional,
        TRADED_LEVEL: traded_level,
        TRADED_TRUE: traded_true,
        TRADE_TIME: trade_time,
        TRADER_CODE: trader_code,
        TRADER_NAME: trader_name,
        CASH_FLOW: cash_flow,
    }
    return pd.DataFrame([row], columns=list(NEW_POSITION_BLOTTER_COLUMNS))


def _cashflow_row(
    *,
    cash_flow: object = 50_000.0,
    risk_type: object = CASH_FLOW_RISK_TYPE,
    risk_greek: object = CASH_FLOW_RISK_GREEK,
) -> pd.DataFrame:
    row = {
        ROW_TYPE: CASHFLOW,
        TRADE_ID: "TRADE_CF",
        POSITION_ID: "CASHFLOW_001",
        RISK_TYPE: risk_type,
        RISK_GREEK: risk_greek,
        UNDERLYING: "",
        TENOR_SWAP: "",
        TENOR_OPTION: "",
        PORTFOLIO: "BOOK_A",
        RISK: np.nan,
        NOTIONAL: np.nan,
        TRADED_LEVEL: np.nan,
        TRADED_TRUE: False,
        TRADE_TIME: pd.NaT,
        TRADER_CODE: "",
        TRADER_NAME: "",
        CASH_FLOW: cash_flow,
    }
    return pd.DataFrame([row], columns=list(NEW_POSITION_BLOTTER_COLUMNS))


def test_default_fake_adapter_models_market_and_cashflow_rows() -> None:
    result = get_new_positions(pd.Timestamp("2026-08-15 16:00"))

    assert GetNewPositions is get_new_positions
    assert tuple(result.columns) == NEW_POSITION_COLUMNS
    assert result[ROW_TYPE].tolist() == [MARKET, MARKET, CASHFLOW]
    assert not {"Open", "Current", "Market Move"}.intersection(result.columns)

    cashflow = result.loc[result[ROW_TYPE].eq(CASHFLOW)].iloc[0]
    assert cashflow[RISK_TYPE] == CASH_FLOW_RISK_TYPE
    assert cashflow[RISK_GREEK] == CASH_FLOW_RISK_GREEK
    assert cashflow[PL] == cashflow[CASH_FLOW] == 50_000.0

    market = result.loc[result[ROW_TYPE].eq(MARKET)]
    assert market[PL].isna().all()
    assert market[RISK_TYPE].eq("Credit").all()
    assert market[RISK_GREEK].eq("Delta").all()
    assert market[TRADED_TRUE].tolist() == [True, False]
    assert market[NOTIONAL].tolist() == [25_000_000.0, 15_000_000.0]
    assert market[TRADER_CODE].tolist() == ["CRD01", "CRD02"]
    assert market[TRADER_NAME].tolist() == ["Alex Morgan", "Sam Taylor"]
    assert str(market[TRADE_TIME].dtype) == "datetime64[ns]"
    fallback = market.loc[~market[TRADED_TRUE]].iloc[0]
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
    [
        TRADE_ID,
        POSITION_ID,
        PORTFOLIO,
        RISK_TYPE,
        RISK_GREEK,
        UNDERLYING,
        TRADER_CODE,
        TRADER_NAME,
    ],
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
        (_market_row, NOTIONAL),
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
        lambda frame: frame.assign(**{TRADED_LEVEL: 1.085, TRADED_TRUE: False}),
        lambda frame: frame.assign(**{TRADED_TRUE: "False"}),
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
        (_market_row(risk=np.nan), "require 'Risk'"),
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


@pytest.mark.parametrize("notional", [np.nan, None, "", "   "])
def test_market_notional_is_optional_metadata(notional: object) -> None:
    result = validate_new_positions(_market_row(notional=notional))

    assert result.loc[0, RISK] == 25_000.0
    assert pd.isna(result.loc[0, NOTIONAL])
    assert pd.isna(result.loc[0, PL])


def test_supplied_market_notional_is_normalized_but_not_used_as_risk() -> None:
    result = validate_new_positions(_market_row(risk=-125.0, notional="5000000"))

    assert result.loc[0, RISK] == -125.0
    assert result.loc[0, NOTIONAL] == 5_000_000.0
    assert pd.isna(result.loc[0, PL])


@pytest.mark.parametrize("notional", ["not-a-number", np.inf, -np.inf])
def test_supplied_market_notional_must_be_finite_numeric(notional: object) -> None:
    with pytest.raises(ValueError, match="nonnumeric|non-finite"):
        validate_new_positions(_market_row(notional=notional))


@pytest.mark.parametrize(
    ("risk_type", "risk_greek"),
    [
        ("", CASH_FLOW_RISK_GREEK),
        (CASH_FLOW_RISK_TYPE, ""),
        ("FX", "Delta"),
        ("cash flow", CASH_FLOW_RISK_GREEK),
    ],
)
def test_cashflow_requires_exact_reserved_identity(
    risk_type: str, risk_greek: str
) -> None:
    with pytest.raises(
        ValueError, match="require Risk Type='Cash Flow'.*Risk Greek='New'"
    ):
        validate_new_positions(
            _cashflow_row(risk_type=risk_type, risk_greek=risk_greek)
        )


@pytest.mark.parametrize(
    ("risk_type", "risk_greek"),
    [(CASH_FLOW_RISK_TYPE, "Delta"), ("FX", CASH_FLOW_RISK_GREEK)],
)
def test_market_rows_cannot_use_either_reserved_identity_value(
    risk_type: str, risk_greek: str
) -> None:
    frame = _market_row()
    frame.loc[0, RISK_TYPE] = risk_type
    frame.loc[0, RISK_GREEK] = risk_greek

    with pytest.raises(ValueError, match="reserved Cash Flow/New classification"):
        validate_new_positions(frame)


def test_cashflow_cannot_carry_market_fields_or_traded_level() -> None:
    with_identity = _cashflow_row()
    with_identity.loc[0, UNDERLYING] = "EURUSD"
    with pytest.raises(
        ValueError, match="CASHFLOW row column 'Underlying' must be blank"
    ):
        validate_new_positions(with_identity)

    with_level = _cashflow_row()
    with_level.loc[0, TRADED_LEVEL] = 1.0
    with pytest.raises(ValueError, match="cannot carry a traded level"):
        validate_new_positions(with_level)

    with_description = _cashflow_row()
    with_description.loc[0, TRADER_CODE] = "FX01"
    with pytest.raises(
        ValueError, match="CASHFLOW row column 'Trader Code' must be blank"
    ):
        validate_new_positions(with_description)


@pytest.mark.parametrize(
    "trade_time",
    [None, "", "not-a-time", True, 1_725_000_000, ["2026-08-15"]],
)
def test_market_trade_time_is_required_and_strict(trade_time: object) -> None:
    with pytest.raises(ValueError, match="Trade Time|invalid timestamps"):
        validate_new_positions(_market_row(trade_time=trade_time))


def test_market_trade_time_is_normalized_without_losing_clock_time() -> None:
    result = validate_new_positions(
        _market_row(trade_time=pd.Timestamp("2026-08-15 09:42", tz="Europe/London"))
    )

    assert result.loc[0, TRADE_TIME] == pd.Timestamp("2026-08-15 09:42")
    assert str(result[TRADE_TIME].dtype) == "datetime64[ns]"


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
    assert result[RISK_TYPE].eq(CASH_FLOW_RISK_TYPE).all()
    assert result[RISK_GREEK].eq(CASH_FLOW_RISK_GREEK).all()
    for column in (UNDERLYING, TENOR_SWAP, TENOR_OPTION):
        assert result[column].eq("").all()
    assert result[PORTFOLIO].tolist() == ["BOOK_A", "BOOK_A"]
