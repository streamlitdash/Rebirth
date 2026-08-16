"""Integrated New Trades calculation and runtime-publication regressions."""

from __future__ import annotations

import pandas as pd
import pytest

from core.s02_pipeline import (
    CURRENT,
    GROUP,
    MARKET_AVAILABLE,
    MARKET_DATA_STATUS,
    MARKET_MOVE,
    OFFICIAL,
    OPEN,
    PL,
    PORTFOLIO,
    PRODUCT_SPECS_BY_SOURCE_TYPE,
    RISK,
    RISK_GREEK,
    RISK_TYPE,
    SOURCE_TYPE,
    SPLIT,
    UNDERLYING,
    get_product_market,
)
from core.s10_new_trades import (
    CASHFLOW,
    CASH_FLOW,
    CASH_FLOW_MARKET_STATUS,
    CASH_FLOW_SOURCE_TYPE,
    MARKET,
    NEW_TRADES_SPLIT,
    NOTIONAL,
    PL_MOVE,
    PL_REFERENCE_LEVEL,
    PL_REFERENCE_SOURCE,
    POSITION_ID,
    ROW_TYPE,
    TRADED_LEVEL,
    TRADED_TRUE,
    TRADE_ID,
    TRADE_TIME,
    TRADER_CODE,
    TRADER_NAME,
    build_new_trade_rows,
    validate_new_trade_rows,
)
from feeds.s01_sources import (
    build_production_refresh_manager,
    get_new_trades,
    get_product_connector_adapters,
)
from ui.s04_components import new_trade_detail_frame
from ui.s03_aggregate import (
    apply_credit_measure,
    credit_measure_available,
    prepare_risk_data,
)
from ui.s07_events import (
    _new_trade_detail_requested,
    _new_trade_details_for_selection,
)


MARKET_DATE = pd.Timestamp("2026-08-14")
CREDIT_SOURCE = "credit/delta"
TRADED_CREDIT_ID = "FAKE - CREDIT-001"
OPEN_CREDIT_ID = "FAKE - CREDIT-002"
CASHFLOW_ID = "FAKE - CASHFLOW-001"

TRACE_COLUMNS = (
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


def _fixture_credit_market(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the validated Credit MarketBook for the fake New Trades scope."""

    adapter = get_product_connector_adapters()[CREDIT_SOURCE]
    underlyings = (
        raw.loc[raw[ROW_TYPE].eq(MARKET), UNDERLYING].drop_duplicates().tolist()
    )
    opened = pd.concat(
        [
            adapter.market_open(
                MARKET_DATE,
                underlying,
                market_status=OFFICIAL,
            )
            for underlying in underlyings
        ],
        ignore_index=True,
    )
    current = pd.concat(
        [
            adapter.market_status(
                MARKET_DATE,
                underlying,
                market_status=OFFICIAL,
            )
            for underlying in underlyings
        ],
        ignore_index=True,
    )
    return get_product_market(
        PRODUCT_SPECS_BY_SOURCE_TYPE[CREDIT_SOURCE],
        MARKET_DATE,
        opened,
        current,
        market_status=OFFICIAL,
    )


@pytest.fixture(scope="module")
def released_fake_trades() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw = get_new_trades(MARKET_DATE)
    market = _fixture_credit_market(raw)
    released = build_new_trade_rows(raw, {CREDIT_SOURCE: market})
    return raw, market, released


def _trade(frame: pd.DataFrame, trade_id: str) -> pd.Series:
    selected = frame.loc[frame[TRADE_ID].eq(trade_id)]
    assert len(selected) == 1
    return selected.iloc[0]


def test_traded_true_uses_row_local_level_and_preserves_official_open(
    released_fake_trades: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    _raw, market, released = released_fake_trades
    market_before = market.copy(deep=True)
    row = _trade(released, TRADED_CREDIT_ID)

    assert bool(row[TRADED_TRUE]) is True
    assert row[TRADED_LEVEL] == pytest.approx(54.80)
    assert row[OPEN] == pytest.approx(55.52)
    assert row[CURRENT] == pytest.approx(55.02)
    assert row[MARKET_MOVE] == pytest.approx(-0.50)
    assert row[PL_REFERENCE_LEVEL] == pytest.approx(54.80)
    assert row[PL_REFERENCE_SOURCE] == "Traded Level"
    assert row[PL_MOVE] == pytest.approx(0.22)
    assert row[PL] == pytest.approx(3_960.0)
    assert bool(row[MARKET_AVAILABLE]) is True
    pd.testing.assert_frame_equal(market, market_before)


def test_traded_false_falls_back_to_the_official_open(
    released_fake_trades: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    _raw, _market, released = released_fake_trades
    row = _trade(released, OPEN_CREDIT_ID)

    assert bool(row[TRADED_TRUE]) is False
    assert pd.isna(row[TRADED_LEVEL])
    assert row[OPEN] == pytest.approx(335.276)
    assert row[CURRENT] == pytest.approx(332.776)
    assert row[MARKET_MOVE] == pytest.approx(-2.50)
    assert row[PL_REFERENCE_LEVEL] == pytest.approx(row[OPEN])
    assert row[PL_REFERENCE_SOURCE] == "Market Open"
    assert row[PL_MOVE] == pytest.approx(-2.50)
    assert row[PL] == pytest.approx(31_250.0)


def test_risk_is_required_and_notional_is_optional_metadata() -> None:
    raw = get_new_trades(MARKET_DATE)
    market_mask = raw[ROW_TYPE].eq(MARKET)
    raw[NOTIONAL] = raw[NOTIONAL].astype(object)
    raw.loc[market_mask, NOTIONAL] = ["   ", "15000000"]
    raw[RISK] = raw[RISK].astype(object)
    raw.loc[raw[TRADE_ID].eq(TRADED_CREDIT_ID), RISK] = "18000"

    validated = validate_new_trade_rows(raw)
    released = build_new_trade_rows(
        validated,
        {CREDIT_SOURCE: _fixture_credit_market(validated)},
    )

    validated_traded = _trade(validated, TRADED_CREDIT_ID)
    validated_fallback = _trade(validated, OPEN_CREDIT_ID)
    assert validated_traded[RISK] == 18_000.0
    assert pd.isna(validated_traded[NOTIONAL])
    assert validated_fallback[NOTIONAL] == 15_000_000.0
    assert _trade(released, TRADED_CREDIT_ID)[PL] == pytest.approx(3_960.0)
    assert _trade(released, OPEN_CREDIT_ID)[PL] == pytest.approx(31_250.0)

    missing_risk = raw.copy()
    missing_risk.loc[market_mask, RISK] = pd.NA
    with pytest.raises(ValueError, match="Risk.*finite"):
        validate_new_trade_rows(missing_risk)


def test_fake_credit_metadata_and_cashflow_identity_are_released_exactly(
    released_fake_trades: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    _raw, _market, released = released_fake_trades
    assert released[TRADE_ID].tolist() == [
        TRADED_CREDIT_ID,
        OPEN_CREDIT_ID,
        CASHFLOW_ID,
    ]
    assert set(TRACE_COLUMNS).issubset(released.columns)

    traded = _trade(released, TRADED_CREDIT_ID)
    assert traded[ROW_TYPE] == MARKET
    assert traded[SOURCE_TYPE] == CREDIT_SOURCE
    assert traded[RISK_TYPE] == "Credit"
    assert traded[RISK_GREEK] == "Delta"
    assert traded[SPLIT] == NEW_TRADES_SPLIT
    assert traded[GROUP] == "New Trades"
    assert traded[PORTFOLIO] == "FAKE_REPLACE_ME - BOOK_C"
    assert traded[RISK] == 18_000.0
    assert traded[NOTIONAL] == 25_000_000.0
    assert traded[TRADE_TIME] == MARKET_DATE + pd.Timedelta(hours=9, minutes=42)
    assert traded[TRADER_CODE] == "CRD01"
    assert traded[TRADER_NAME] == "Alex Morgan"

    fallback = _trade(released, OPEN_CREDIT_ID)
    assert fallback[ROW_TYPE] == MARKET
    assert fallback[RISK] == -12_500.0
    assert fallback[NOTIONAL] == 15_000_000.0
    assert fallback[TRADE_TIME] == MARKET_DATE + pd.Timedelta(hours=11, minutes=17)
    assert fallback[TRADER_CODE] == "CRD02"
    assert fallback[TRADER_NAME] == "Sam Taylor"

    cashflow = _trade(released, CASHFLOW_ID)
    assert cashflow[ROW_TYPE] == CASHFLOW
    assert cashflow[SOURCE_TYPE] == CASH_FLOW_SOURCE_TYPE
    assert cashflow[RISK_TYPE] == "Cash Flow"
    assert cashflow[RISK_GREEK] == "New"
    assert cashflow[SPLIT] == NEW_TRADES_SPLIT
    assert cashflow[RISK] == cashflow[CASH_FLOW] == cashflow[PL] == 50_000.0
    assert cashflow[PL_REFERENCE_LEVEL] == 1.0
    assert cashflow[PL_REFERENCE_SOURCE] == "Identity factor"
    assert cashflow[PL_MOVE] == 1.0
    assert bool(cashflow[MARKET_AVAILABLE]) is False
    assert cashflow[MARKET_DATA_STATUS] == CASH_FLOW_MARKET_STATUS


def test_production_manager_publishes_new_trades_and_combined_trace_rows() -> None:
    manager = build_production_refresh_manager()
    refreshed = manager.refresh(force_risk=True, force_pl=True)
    published = manager.read_frame("combined_pl")

    assert refreshed.errors == ()
    assert published.revision == refreshed.revision == manager.snapshot.revision
    assert set(TRACE_COLUMNS).issubset(refreshed.combined_pl.columns)
    trace = refreshed.combined_pl.loc[refreshed.combined_pl[TRADE_ID].notna()].copy()
    published_trace = published.frame.loc[published.frame[TRADE_ID].notna()].copy()
    assert trace[TRADE_ID].tolist() == [
        TRADED_CREDIT_ID,
        OPEN_CREDIT_ID,
        CASHFLOW_ID,
    ]
    pd.testing.assert_frame_equal(
        trace.reset_index(drop=True),
        published_trace.reset_index(drop=True),
    )

    dashboard = refreshed.dashboard_frame.loc[
        refreshed.dashboard_frame[SPLIT].eq(NEW_TRADES_SPLIT)
    ]
    assert len(dashboard) == 3
    assert set(dashboard[SOURCE_TYPE]) == {CREDIT_SOURCE, CASH_FLOW_SOURCE_TYPE}
    assert sorted(dashboard[PL].tolist()) == pytest.approx(
        [3_960.0, 31_250.0, 50_000.0]
    )
    credit_dashboard = dashboard.loc[dashboard[RISK_TYPE].eq("Credit")]
    assert credit_dashboard["Risk SP01"].tolist() == credit_dashboard[RISK].tolist()
    assert credit_dashboard["dRisk SP01"].isna().all()
    prepared_credit = prepare_risk_data(refreshed.dashboard_frame)
    prepared_credit = prepared_credit.loc[
        prepared_credit["risk type"].eq("Credit")
        & prepared_credit["split"].eq(NEW_TRADES_SPLIT)
    ]
    assert credit_measure_available(prepared_credit, "SP01")
    displayed_credit = apply_credit_measure(prepared_credit, "SP01")
    assert displayed_credit["risk"].tolist() == credit_dashboard[RISK].tolist()
    assert displayed_credit["drisk"].isna().all()
    published_cashflow = _trade(trace, CASHFLOW_ID)
    assert published_cashflow[RISK] == published_cashflow[PL] == 50_000.0

    other_details = _new_trade_details_for_selection(
        refreshed.combined_pl,
        {
            "risk greek": "Delta",
            "display bucket": "Other",
            "split": NEW_TRADES_SPLIT,
        },
        "Credit",
        None,
        [NEW_TRADES_SPLIT],
        {},
    )
    assert other_details[TRADE_ID].tolist() == [TRADED_CREDIT_ID]
    assert new_trade_detail_frame(
        other_details,
        {
            "risk greek": "Delta",
            "display bucket": "Other",
            "split": NEW_TRADES_SPLIT,
        },
    )["trade id"].tolist() == [TRADED_CREDIT_ID]

    named_details = _new_trade_details_for_selection(
        refreshed.combined_pl,
        {
            "risk greek": "Delta",
            "display bucket": "FAKE_REPLACE_ME - iTraxx Main",
            "split": NEW_TRADES_SPLIT,
        },
        "Credit",
        None,
        [NEW_TRADES_SPLIT],
        {},
    )
    assert named_details[TRADE_ID].tolist() == [OPEN_CREDIT_ID]


def test_detail_is_requested_from_row_path_or_exact_new_trades_filter() -> None:
    assert _new_trade_detail_requested({"split": NEW_TRADES_SPLIT}, None)
    assert _new_trade_detail_requested(
        {"risk greek": "Delta", "display bucket": "CDX IG"},
        [NEW_TRADES_SPLIT],
    )
    assert not _new_trade_detail_requested(
        {"risk greek": "Delta"},
        ["Risk", NEW_TRADES_SPLIT],
    )
    assert not _new_trade_detail_requested({"split": "Risk"}, [NEW_TRADES_SPLIT])
