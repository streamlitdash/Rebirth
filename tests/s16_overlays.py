"""Focused contracts for supplied developed-risk overlays and threshold release."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.s01_schema import TENOR_OPTION, TENOR_SWAP, TENOR_SWAP_ORDER
from core.s02_pipeline import (
    CURRENT,
    DRISK,
    DRISK_THRESHOLD,
    GROUP,
    MARKET_AVAILABLE,
    MARKET_DATA_STATUS,
    MARKET_MOVE,
    MARKET_STATUS,
    OFFICIAL,
    OPEN,
    PL,
    PL_THRESHOLD,
    PORTFOLIO,
    PRODUCT_SPECS_BY_SOURCE_TYPE,
    RISK,
    RISK_GREEK,
    RISK_OVERLAY_COLUMNS,
    RISK_THRESHOLD,
    RISK_TYPE,
    RiskRefreshManager,
    SPLIT,
    UNDERLYING,
    build_risk_overlay,
    get_product_market,
)
from feeds.s01_sources import (
    get_cross_gamma_risk,
    get_new_positions,
    get_portfolio_config,
    get_product_connector_adapters,
    get_reported_underlyings,
    get_risk_checker,
    get_risk_thresholds,
)


MARKET_DATE = pd.Timestamp("2026-07-20")


def _overlay_row(
    source_type: str,
    underlying: str,
    risk: float,
    *,
    tenor_swap: str = "",
    portfolio: str = "BOOK_A",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                source_type,
                underlying,
                tenor_swap,
                "",
                portfolio,
                "Cross Asset",
                risk,
            ]
        ],
        columns=list(RISK_OVERLAY_COLUMNS),
    )


def _fx_market() -> pd.DataFrame:
    spec = PRODUCT_SPECS_BY_SOURCE_TYPE["fx/delta"]
    opened = pd.DataFrame([["EURUSD", 1.08]], columns=[UNDERLYING, OPEN])
    current = pd.DataFrame(
        [["EURUSD", 1.09, OFFICIAL]],
        columns=[UNDERLYING, CURRENT, MARKET_STATUS],
    )
    return get_product_market(
        spec,
        MARKET_DATE,
        opened,
        current,
        market_status=OFFICIAL,
    )


def _ir_market() -> pd.DataFrame:
    spec = PRODUCT_SPECS_BY_SOURCE_TYPE["ir/delta"]
    opened = pd.DataFrame(
        [["USD-SOFR", "5Y", 0, 4.00]],
        columns=[UNDERLYING, TENOR_SWAP, TENOR_SWAP_ORDER, OPEN],
    )
    current = pd.DataFrame(
        [["USD-SOFR", "5Y", 0, 4.05, OFFICIAL]],
        columns=[
            UNDERLYING,
            TENOR_SWAP,
            TENOR_SWAP_ORDER,
            CURRENT,
            MARKET_STATUS,
        ],
    )
    return get_product_market(
        spec,
        MARKET_DATE,
        opened,
        current,
        market_status=OFFICIAL,
    )


@pytest.mark.parametrize(
    ("split", "source", "market_frames", "expected_type", "expected_tenor"),
    [
        (
            "Cross Gamma",
            _overlay_row("fx/delta", "EURUSD", 125.0),
            {"fx/delta": _fx_market()},
            "FX",
            "Spot",
        ),
        (
            "New Position",
            _overlay_row("ir/delta", "USD-SOFR", -40.0, tenor_swap="5Y"),
            {"ir/delta": _ir_market()},
            "IR",
            "5Y",
        ),
    ],
)
def test_overlay_api_derives_product_identity_and_attaches_target_market(
    split: str,
    source: pd.DataFrame,
    market_frames: dict[str, pd.DataFrame],
    expected_type: str,
    expected_tenor: str,
) -> None:
    result = build_risk_overlay(
        source,
        split=split,
        market_frames=market_frames,
    )

    row = result.iloc[0]
    assert row[RISK_TYPE] == expected_type
    assert row[RISK_GREEK] == "Delta"
    assert row[SPLIT] == split
    assert row[TENOR_SWAP] == expected_tenor
    assert row[TENOR_OPTION] == "N/A"
    assert row[PORTFOLIO] == "BOOK_A"
    assert row[GROUP] == "Cross Asset"
    assert pd.isna(row[DRISK])
    assert row[PL] == 0.0
    assert bool(row[MARKET_AVAILABLE]) is True


def test_overlay_without_target_market_is_retained_as_unavailable() -> None:
    result = build_risk_overlay(
        _overlay_row("fx/delta", "GBPUSD", 75.0),
        split="Cross Gamma",
        market_frames={},
    )

    row = result.iloc[0]
    assert row[UNDERLYING] == "GBPUSD"
    assert row[RISK] == 75.0
    assert bool(row[MARKET_AVAILABLE]) is False
    assert row[MARKET_DATA_STATUS] == "No matching market row"
    assert pd.isna(row[OPEN])
    assert pd.isna(row[CURRENT])
    assert pd.isna(row[MARKET_MOVE])
    assert row[PL] == 0.0


def test_fixture_overlay_hooks_return_the_exact_empty_contract() -> None:
    expected_columns = list(RISK_OVERLAY_COLUMNS)

    for frame in (
        get_cross_gamma_risk(MARKET_DATE),
        get_new_positions(MARKET_DATE),
    ):
        assert frame.empty
        assert frame.columns.tolist() == expected_columns


def test_refresh_replaces_overlay_rows_and_releases_positive_thresholds() -> None:
    overlay_calls: list[pd.Timestamp] = []

    def cross_gamma(market_date: pd.Timestamp) -> pd.DataFrame:
        overlay_calls.append(pd.Timestamp(market_date))
        return _overlay_row(
            "fx/delta",
            "FAKE_REPLACE_ME - EUR/USD",
            125.0,
            portfolio="FAKE_REPLACE_ME - BOOK_A",
        )

    manager = RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        reported_underlyings=get_reported_underlyings,
        risk_checker_loader=get_risk_checker,
        market_status_resolver=lambda _date: OFFICIAL,
        cross_gamma_loader=cross_gamma,
        new_position_loader=get_new_positions,
        connector_adapters=get_product_connector_adapters(),
        clock=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )

    first = manager.refresh(force_risk=True, force_pl=True)
    second = manager.refresh(force_pl=True, expected_revision=first.revision)

    for snapshot in (first, second):
        cross_rows = snapshot.dashboard_frame.loc[
            snapshot.dashboard_frame[SPLIT].eq("Cross Gamma")
        ]
        assert len(cross_rows) == 1
        assert cross_rows.iloc[0][RISK] == 125.0
        for column in (RISK_THRESHOLD, DRISK_THRESHOLD, PL_THRESHOLD):
            values = pd.to_numeric(snapshot.dashboard_frame[column], errors="raise")
            assert np.isfinite(values).all()
            assert values.gt(0).all()

    assert overlay_calls == [MARKET_DATE, MARKET_DATE]
