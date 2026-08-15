"""Strict helpers shared by the example product adapters."""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from core.s02_pipeline import LIVE, OFFICIAL


class RiskSource(Protocol):
    def __call__(self, risk_date: pd.Timestamp) -> pd.DataFrame: ...


class MarketSource(Protocol):
    def __call__(
        self,
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame: ...


def exact_frame(
    value: object,
    *,
    columns: tuple[str, ...],
    label: str,
) -> pd.DataFrame:
    """Copy a DataFrame only when its ordered public schema is exact."""

    if not isinstance(value, pd.DataFrame):
        raise TypeError(f"{label} must return a pandas DataFrame")
    actual = tuple(value.columns)
    if actual != columns:
        raise ValueError(
            f"{label} columns must be exactly {list(columns)} in that order; "
            f"found {list(actual)}"
        )
    return value.copy()


def exact_status(value: object) -> str:
    """Require the manager-owned Live/OFFICIAL routing instruction."""

    if value not in {LIVE, OFFICIAL}:
        raise ValueError("market_status must be exactly 'Live' or 'OFFICIAL'")
    return str(value)


def exact_underlying(value: object) -> str:
    """Require one nonblank Underlying; batching belongs to the framework."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("underlying must be nonblank text")
    return value.strip()


def market_frame(
    source: MarketSource,
    market_date: pd.Timestamp,
    underlying: str,
    *,
    market_status: str,
    columns: tuple[str, ...],
    label: str,
    attach_status: bool = False,
) -> pd.DataFrame:
    """Call one personal market function and validate identity and routing."""

    selected_underlying = exact_underlying(underlying)
    selected_status = exact_status(market_status)
    frame = exact_frame(
        source(
            market_date,
            selected_underlying,
            market_status=selected_status,
        ),
        columns=columns,
        label=f"{label} for {selected_underlying!r}",
    )
    if not frame.empty and not frame["Underlying"].eq(selected_underlying).all():
        raise ValueError(
            f"{label} for {selected_underlying!r} returned another Underlying"
        )
    if attach_status:
        frame["Market Status"] = selected_status
    return frame


__all__ = [
    "MarketSource",
    "RiskSource",
    "exact_frame",
    "exact_status",
    "exact_underlying",
    "market_frame",
]
