"""One fast end-to-end refresh over the explicit fake connector boundaries."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from core.s02_pipeline import ProductConnectorAdapter, RiskRefreshManager
from feeds.s01_sources import (
    get_portfolio_config,
    get_product_connector_adapters,
    get_risk_checker,
    get_risk_thresholds,
)


def test_refresh_uses_one_checker_date_for_checker_portfolios_and_risk_plan() -> None:
    checker_calls: list[pd.Timestamp] = []
    portfolio_calls: list[pd.Timestamp] = []
    risk_calls: list[tuple[str, pd.Timestamp]] = []
    open_calls: list[tuple[str, pd.Timestamp, str, str]] = []
    current_calls: list[tuple[str, pd.Timestamp, str, str]] = []
    market_status_calls: list[pd.Timestamp] = []

    def checker(checker_date: pd.Timestamp):
        checker_calls.append(pd.Timestamp(checker_date))
        return get_risk_checker(checker_date)

    def portfolios(portfolio_date: pd.Timestamp):
        portfolio_calls.append(pd.Timestamp(portfolio_date))
        return get_portfolio_config(portfolio_date)

    def market_status(market_date: pd.Timestamp) -> str:
        market_status_calls.append(pd.Timestamp(market_date))
        # The authoritative service can switch today's source independently of
        # the calendar date (for example after an official close is published).
        return "Live" if len(market_status_calls) == 1 else "OFFICIAL"

    wrapped: dict[str, ProductConnectorAdapter] = {}
    for source_type, adapter in get_product_connector_adapters().items():

        def risk(
            risk_date: pd.Timestamp,
            *,
            _source: str = source_type,
            _adapter: ProductConnectorAdapter = adapter,
        ) -> pd.DataFrame:
            risk_calls.append((_source, pd.Timestamp(risk_date)))
            return _adapter.risk(risk_date)

        def opened(
            open_date: pd.Timestamp,
            underlying: str,
            *,
            market_status: str,
            _source: str = source_type,
            _adapter: ProductConnectorAdapter = adapter,
        ) -> pd.DataFrame:
            open_calls.append(
                (_source, pd.Timestamp(open_date), underlying, market_status)
            )
            return _adapter.market_open(
                open_date, underlying, market_status=market_status
            )

        def current(
            market_date: pd.Timestamp,
            underlying: str,
            *,
            market_status: str,
            _source: str = source_type,
            _adapter: ProductConnectorAdapter = adapter,
        ) -> pd.DataFrame:
            current_calls.append(
                (_source, pd.Timestamp(market_date), underlying, market_status)
            )
            return _adapter.market_status(
                market_date, underlying, market_status=market_status
            )

        wrapped[source_type] = ProductConnectorAdapter(risk, opened, current)

    manager = RiskRefreshManager(
        portfolios,
        thresholds=get_risk_thresholds,
        risk_checker_loader=checker,
        market_status_resolver=market_status,
        connector_adapters=wrapped,
        # Sunday defaults to Friday Market Date and Thursday T-1 sources.
        clock=lambda: datetime(2026, 8, 16, 12, tzinfo=timezone.utc),
        trading_timezone="Europe/London",
        # The natural weekend rollback is age zero for retention purposes.
        max_history_days=1,
    )

    snapshot = manager.refresh(force_risk=True, force_pl=True)
    first_attempt_id = manager.progress.attempt_id

    expected_market_date = pd.Timestamp("2026-08-14")
    expected_checker_date = pd.Timestamp("2026-08-13")
    assert snapshot.errors == ()
    assert snapshot.revision == 1
    assert snapshot.system_date == pd.Timestamp("2026-08-16")
    assert snapshot.market_date == expected_market_date
    assert snapshot.checker_date == expected_checker_date
    assert snapshot.market_status == "Live"
    assert market_status_calls == [expected_market_date]
    assert checker_calls == [expected_checker_date]
    assert portfolio_calls == [expected_checker_date]
    assert len(risk_calls) == len(snapshot.risk_dates)
    assert {
        source: risk_date for source, risk_date in risk_calls
    } == snapshot.risk_dates
    assert [
        (source, underlying, status) for source, _, underlying, status in open_calls
    ] == [
        (source, underlying, status) for source, _, underlying, status in current_calls
    ]
    assert all(call[1] == expected_checker_date for call in open_calls)
    assert all(call[1] == expected_market_date for call in current_calls)
    assert all(call[3] == "Live" for call in open_calls)
    assert all(call[2].strip() for call in open_calls)
    assert all(not call[0].startswith("commo/") for call in open_calls)
    assert not snapshot.dashboard_frame.empty
    assert not snapshot.market_frame.empty
    assert first_attempt_id
    assert manager.progress.running is False
    commodity_market = snapshot.market_frame.loc[
        snapshot.market_frame["Source Type"].str.startswith("commo/")
    ]
    commodity_dashboard = snapshot.dashboard_frame.loc[
        snapshot.dashboard_frame["Source Type"].str.startswith("commo/")
    ]
    assert not commodity_market.empty
    assert commodity_market["Open"].eq(0.0).all()
    assert commodity_market["Current"].eq(0.0).all()
    assert commodity_market["Market Data Status"].eq("Commodity market disabled").all()
    assert (
        commodity_dashboard["Market Data Status"].eq("Commodity market disabled").all()
    )

    first_open_count = len(open_calls)
    official_snapshot = manager.refresh(
        force_pl=True,
        expected_revision=snapshot.revision,
    )
    assert manager.progress.attempt_id
    assert manager.progress.attempt_id != first_attempt_id
    assert official_snapshot.market_status == "OFFICIAL"
    assert market_status_calls == [
        expected_market_date,
        expected_market_date,
    ]
    assert [
        (source, underlying, status)
        for source, _, underlying, status in open_calls[first_open_count:]
    ] == [
        (source, underlying, status)
        for source, _, underlying, status in current_calls[first_open_count:]
    ]
    assert all(
        call[1] == expected_checker_date for call in open_calls[first_open_count:]
    )
    assert all(
        call[1] == expected_market_date for call in current_calls[first_open_count:]
    )
    assert all(call[3] == "OFFICIAL" for call in open_calls[first_open_count:])

    portfolio_snapshot = manager.refresh_portfolios(
        expected_revision=official_snapshot.revision
    )
    assert portfolio_snapshot.market_status == "OFFICIAL"
    assert len(market_status_calls) == 2


def test_historical_view_rejects_forced_risk_after_checker_date() -> None:
    manager = RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        risk_checker_loader=get_risk_checker,
        market_status_resolver=lambda _date: "OFFICIAL",
        connector_adapters=get_product_connector_adapters(),
        clock=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )

    with pytest.raises(
        ValueError,
        match=r"must not be after checker date 2026-06-30.*market date 2026-07-01",
    ):
        manager.refresh(
            view_date="2026-07-01",
            forced_dates={"ir/delta": "2026-07-01"},
        )


@pytest.mark.parametrize(
    ("refresh_kwargs", "message"),
    [
        ({"view_date": "2026-07-18"}, "view date must be a business day"),
        (
            {"forced_dates": {"ir/delta": "2026-07-18"}},
            "forced date for ir/delta must be a business day",
        ),
    ],
)
def test_explicit_weekend_dates_remain_invalid(
    refresh_kwargs: dict[str, object],
    message: str,
) -> None:
    manager = RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        risk_checker_loader=get_risk_checker,
        market_status_resolver=lambda _date: "OFFICIAL",
        connector_adapters=get_product_connector_adapters(),
        clock=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match=message):
        manager.refresh(**refresh_kwargs)


def test_market_status_resolver_rejects_ambiguous_values_before_other_sources() -> None:
    checker_calls: list[pd.Timestamp] = []

    def checker(checker_date: pd.Timestamp):
        checker_calls.append(checker_date)
        return get_risk_checker(checker_date)

    manager = RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        risk_checker_loader=checker,
        market_status_resolver=lambda _date: "official",
        connector_adapters=get_product_connector_adapters(),
        clock=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
        trading_timezone="Europe/London",
    )

    with pytest.raises(ValueError, match="exactly 'Live' or 'OFFICIAL'"):
        manager.refresh()

    assert checker_calls == []
