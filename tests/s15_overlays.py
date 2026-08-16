"""Focused contracts for the supported raw supplemental-risk paths."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core.s02_pipeline import (
    CASH_FLOW_PRODUCT_SPEC,
    DIRECT_PL_CLASSIFICATIONS,
    DIRECT_PL_CLASSIFICATIONS_BY_SOURCE_TYPE,
    DIRECT_PL_RISK_PAIRS,
    DRISK_THRESHOLD,
    MARKET_MOVE,
    NEW_POSITION_CASH_FLOW_CLASSIFICATION,
    OFFICIAL,
    OPEN,
    PL,
    PL_THRESHOLD,
    RELEASE_RISK_PAIRS,
    RISK,
    RISK_THRESHOLD,
    SOURCE_TYPE,
    SPLIT,
    RiskRefreshManager,
)
from core.s09_cross_gamma import (
    CROSS_GAMMA_COLUMNS,
    XGAMMA_SOURCE_RISK_GREEKS,
    XGAMMA_SPLIT,
)
from core.s10_new_trades import (
    CASHFLOW,
    MARKET,
    NEW_TRADES_SPLIT,
    NEW_TRADE_COLUMNS,
    ROW_TYPE,
)
from feeds.s01_sources import (
    get_cross_gamma_sensitivities,
    get_new_trades,
    get_portfolio_config,
    get_product_connector_adapters,
    get_reported_underlyings,
    get_risk_checker,
    get_risk_thresholds,
)


MARKET_DATE = pd.Timestamp("2026-07-20")


def test_active_supplemental_feeds_expose_raw_xgamma_and_unified_new_trades() -> None:
    cross_gamma = get_cross_gamma_sensitivities(MARKET_DATE)
    new_trades = get_new_trades(MARKET_DATE)

    assert not cross_gamma.empty
    assert tuple(cross_gamma.columns) == CROSS_GAMMA_COLUMNS
    assert set(cross_gamma["Risk Greek"]).issubset(XGAMMA_SOURCE_RISK_GREEKS)
    assert not new_trades.empty
    assert tuple(new_trades.columns) == NEW_TRADE_COLUMNS
    assert set(new_trades[ROW_TYPE]) == {MARKET, CASHFLOW}


def test_cashflow_identity_authority_remains_product_spec_backed() -> None:
    classification = NEW_POSITION_CASH_FLOW_CLASSIFICATION

    assert classification.product_spec is CASH_FLOW_PRODUCT_SPEC
    assert classification.source_type == "new-position/cash-flow"
    assert classification.risk_type == "Cash Flow"
    assert classification.risk_greek == "New"
    assert classification.split == NEW_TRADES_SPLIT
    assert CASH_FLOW_PRODUCT_SPEC.pl_formula == "identity"
    assert classification in DIRECT_PL_CLASSIFICATIONS
    assert DIRECT_PL_CLASSIFICATIONS_BY_SOURCE_TYPE[classification.source_type] is (
        classification
    )
    assert (classification.risk_type, classification.risk_greek) in (
        DIRECT_PL_RISK_PAIRS
    )
    assert DIRECT_PL_RISK_PAIRS.issubset(RELEASE_RISK_PAIRS)


def test_manager_releases_raw_supplemental_rows_and_positive_thresholds() -> None:
    manager = RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        reported_underlyings=get_reported_underlyings,
        risk_checker_loader=get_risk_checker,
        market_status_resolver=lambda _date: OFFICIAL,
        cross_gamma_matrix_loader=get_cross_gamma_sensitivities,
        new_trades_loader=get_new_trades,
        connector_adapters=get_product_connector_adapters(),
        clock=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )

    snapshot = manager.refresh(force_risk=True, force_pl=True)

    assert snapshot.errors == ()
    assert snapshot.dashboard_frame[SPLIT].eq(XGAMMA_SPLIT).any()
    assert snapshot.dashboard_frame[SPLIT].eq(NEW_TRADES_SPLIT).any()
    cashflow = snapshot.dashboard_frame.loc[
        snapshot.dashboard_frame[SOURCE_TYPE].eq("new-position/cash-flow")
    ]
    assert len(cashflow) == 1
    assert cashflow.iloc[0][RISK] == cashflow.iloc[0][PL] == 50_000.0
    assert pd.isna(cashflow.iloc[0][OPEN])
    assert pd.isna(cashflow.iloc[0][MARKET_MOVE])
    identity = "Cash Flow | New | Cash Flow"
    assert manager.search_combine_udl_options("cAsH fLoW") == (identity,)
    assert manager.search_market_udl_options("cash flow") == ()

    for column in (RISK_THRESHOLD, DRISK_THRESHOLD, PL_THRESHOLD):
        values = pd.to_numeric(snapshot.dashboard_frame[column], errors="raise")
        assert np.isfinite(values).all()
        assert values.gt(0).all()
