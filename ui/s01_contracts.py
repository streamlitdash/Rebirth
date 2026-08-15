"""Structural types at the boundary between Dash and the refresh pipeline."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import (
    Iterable,
    Literal,
    Mapping,
    Protocol,
    Sequence,
    TypeAlias,
    runtime_checkable,
)

import pandas as pd


DateLike: TypeAlias = date | datetime | str | pd.Timestamp
FrameName: TypeAlias = Literal[
    "risk_status",
    "risk_checker",
    "combined_pl",
    "market_frame",
    "dashboard_frame",
    "unmapped_frame",
]


@runtime_checkable
class AdjustmentRepositoryProtocol(Protocol):
    """Date-scoped adjustment storage required by the PL callbacks."""

    def load(self, market_date: object) -> pd.DataFrame: ...

    def save(
        self,
        market_date: object,
        rows: pd.DataFrame,
        *,
        base_revision: object,
        replace_portfolios: Iterable[str] | None = None,
    ) -> Path | str | None: ...


@runtime_checkable
class RefreshProgressProtocol(Protocol):
    """Sanitized progress shape exposed by managers that support live progress."""

    @property
    def attempt_id(self) -> str | None: ...

    @property
    def function_name(self) -> str | None: ...

    @property
    def source_type(self) -> str | None: ...

    @property
    def underlying(self) -> str | None: ...

    @property
    def product_label(self) -> str | None: ...

    @property
    def product_index(self) -> int: ...

    @property
    def product_total(self) -> int: ...

    @property
    def hold_seconds(self) -> float: ...

    @property
    def stage(self) -> str: ...

    @property
    def current(self) -> int: ...

    @property
    def total(self) -> int: ...

    @property
    def message(self) -> str: ...

    @property
    def running(self) -> bool: ...

    @property
    def error(self) -> str | None: ...

    @property
    def started_at(self) -> datetime | None: ...

    @property
    def updated_at(self) -> datetime | None: ...

    @property
    def finished_at(self) -> datetime | None: ...


@runtime_checkable
class RefreshHealthProtocol(Protocol):
    """Small immutable metadata view that never copies risk DataFrames."""

    @property
    def revision(self) -> int: ...

    @property
    def refreshed_at(self) -> datetime | None: ...

    @property
    def last_attempt_at(self) -> datetime | None: ...

    @property
    def active_error_count(self) -> int: ...


@runtime_checkable
class SearchResultProtocol(Protocol):
    """Defensive quick-search result tied to one committed revision."""

    @property
    def revision(self) -> int: ...

    @property
    def frame(self) -> pd.DataFrame: ...

    @property
    def risk_dates(self) -> Mapping[str, pd.Timestamp]: ...

    @property
    def market_date(self) -> pd.Timestamp: ...

    @property
    def query(self) -> str: ...

    @property
    def total(self) -> int: ...


@runtime_checkable
class RefreshSnapshotProtocol(Protocol):
    """Read-only snapshot shape consumed by the dashboard."""

    @property
    def revision(self) -> int: ...

    @property
    def refreshed_at(self) -> datetime: ...

    @property
    def last_attempt_at(self) -> datetime: ...

    @property
    def refresh_reason(self) -> str: ...

    @property
    def system_date(self) -> pd.Timestamp: ...

    @property
    def market_date(self) -> pd.Timestamp: ...

    @property
    def checker_date(self) -> pd.Timestamp: ...

    @property
    def market_status(self) -> str: ...

    @property
    def forced_view_date(self) -> pd.Timestamp | None: ...

    @property
    def risk_status(self) -> pd.DataFrame: ...

    @property
    def risk_checker(self) -> pd.DataFrame: ...

    @property
    def risk_checker_enabled(self) -> bool: ...

    @property
    def commodity_market_enabled(self) -> bool: ...

    @property
    def risk_dates(self) -> dict[str, pd.Timestamp]: ...

    @property
    def forced_dates(self) -> dict[str, pd.Timestamp]: ...

    @property
    def changed_source_types(self) -> tuple[str, ...]: ...

    @property
    def open_refreshed_source_types(self) -> tuple[str, ...]: ...

    @property
    def market_refreshed_source_types(self) -> tuple[str, ...]: ...

    @property
    def combined_pl(self) -> pd.DataFrame: ...

    @property
    def market_frame(self) -> pd.DataFrame: ...

    @property
    def dashboard_frame(self) -> pd.DataFrame: ...

    @property
    def unmapped_frame(self) -> pd.DataFrame: ...

    @property
    def errors(self) -> tuple[str, ...]: ...


@runtime_checkable
class ControlSnapshotProtocol(Protocol):
    """Compact refresh/date-control view with only readiness row data."""

    @property
    def revision(self) -> int: ...

    @property
    def refreshed_at(self) -> datetime: ...

    @property
    def system_date(self) -> pd.Timestamp: ...

    @property
    def market_date(self) -> pd.Timestamp: ...

    @property
    def checker_date(self) -> pd.Timestamp: ...

    @property
    def market_status(self) -> str: ...

    @property
    def forced_view_date(self) -> pd.Timestamp | None: ...

    @property
    def risk_status(self) -> pd.DataFrame: ...

    @property
    def risk_checker_enabled(self) -> bool: ...

    @property
    def commodity_market_enabled(self) -> bool: ...

    @property
    def forced_dates(self) -> Mapping[str, pd.Timestamp]: ...

    @property
    def errors(self) -> tuple[str, ...]: ...


@runtime_checkable
class PLSnapshotProtocol(Protocol):
    """Revision-consistent committed P&L view."""

    @property
    def revision(self) -> int: ...

    @property
    def market_date(self) -> pd.Timestamp: ...

    @property
    def combined_pl(self) -> pd.DataFrame: ...


@runtime_checkable
class FrameReadProtocol(Protocol):
    """One requested committed frame plus same-revision metadata."""

    @property
    def revision(self) -> int: ...

    @property
    def market_date(self) -> pd.Timestamp: ...

    @property
    def checker_date(self) -> pd.Timestamp: ...

    @property
    def risk_checker_enabled(self) -> bool: ...

    @property
    def frame(self) -> pd.DataFrame: ...


@runtime_checkable
class RefreshManagerProtocol(Protocol):
    """Operations the Dash layer requires from any refresh manager."""

    @property
    def snapshot(self) -> RefreshSnapshotProtocol: ...

    @property
    def control_snapshot(self) -> ControlSnapshotProtocol: ...

    @property
    def pl_snapshot(self) -> PLSnapshotProtocol: ...

    def read_frame(self, name: FrameName) -> FrameReadProtocol: ...

    @property
    def health(self) -> RefreshHealthProtocol: ...

    @property
    def progress(self) -> RefreshProgressProtocol: ...

    @property
    def stage_delays(self) -> Mapping[str, float]: ...

    def combine_udl_options(
        self,
        *,
        identity_mode: str = "reported",
    ) -> tuple[str, ...]: ...

    def market_udl_options(self) -> tuple[str, ...]: ...

    def search_market_udl_options(
        self,
        search_value: str | None,
        *,
        limit: int = 100,
        include: str | None = None,
    ) -> tuple[str, ...]: ...

    def search_combine_udl_options(
        self,
        search_value: str | None,
        *,
        identity_mode: str = "reported",
        limit: int = 100,
        include: str | None = None,
    ) -> tuple[str, ...]: ...

    def pivot_market_exact(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = (
            "Underlying",
            "Tenor Swap",
            "Tenor Option",
        ),
        limit: int | None = None,
    ) -> SearchResultProtocol: ...

    def pivot_combined(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = (
            "Underlying",
            "Tenor Swap",
            "Tenor Option",
        ),
        limit: int = 500,
        identity_mode: str = "reported",
    ) -> SearchResultProtocol: ...

    def pivot_combined_hierarchy(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = (
            "Underlying",
            "Tenor Swap",
            "Tenor Option",
        ),
        leaf_limit: int = 500,
        identity_mode: str = "reported",
        risk_filters: Mapping[str, Sequence[str] | None] | None = None,
    ) -> SearchResultProtocol: ...

    def refresh(
        self,
        *,
        force_pl: bool = False,
        force_risk: bool = False,
        commodity_market_enabled: bool = False,
        risk_checker_enabled: bool | None = None,
        forced_dates: Mapping[str, DateLike] | None = None,
        view_date: DateLike | None = None,
        reason: str = "status",
        expected_revision: int | None = None,
    ) -> RefreshSnapshotProtocol: ...

    def refresh_portfolios(
        self,
        *,
        reason: str = "portfolio mapping",
        expected_revision: int | None = None,
    ) -> RefreshSnapshotProtocol: ...


__all__ = [
    "AdjustmentRepositoryProtocol",
    "ControlSnapshotProtocol",
    "DateLike",
    "FrameName",
    "FrameReadProtocol",
    "PLSnapshotProtocol",
    "RefreshHealthProtocol",
    "RefreshManagerProtocol",
    "RefreshProgressProtocol",
    "RefreshSnapshotProtocol",
    "SearchResultProtocol",
]
