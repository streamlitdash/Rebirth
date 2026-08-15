"""Validated Stock connector boundary with a replaceable fake implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from core.s08_stock import (
    STOCK_COLUMNS,
    STOCK_NUMERIC_COLUMNS,
    STOCK_TEXT_COLUMNS,
    validate_stock_frame,
)


class StockSource(Protocol):
    """Shape of the site's replaceable ``GetStock`` function."""

    def __call__(self, stock_date: pd.Timestamp) -> pd.DataFrame: ...


def normalize_stock_date(value: object) -> pd.Timestamp:
    """Return one normalized stock date without silently accepting null input."""

    if value is None or isinstance(value, (bool, np.bool_)):
        raise TypeError("stock_date must be a date-like value")
    try:
        selected = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("stock_date must be a valid date-like value") from exc
    if pd.isna(selected):
        raise ValueError("stock_date must be a valid date-like value")
    if selected.tzinfo is not None:
        selected = selected.tz_localize(None)
    return selected.normalize()


@dataclass(frozen=True)
class StockConnectorAdapter:
    """Bind one personal ``GetStock`` function to the validated Stock contract."""

    stock: StockSource

    def get_stock(self, stock_date: object) -> pd.DataFrame:
        selected_date = normalize_stock_date(stock_date)
        return validate_stock_frame(
            self.stock(selected_date),
            label=f"Stock for {selected_date.date().isoformat()}",
        )


def build_stock_adapter(*, stock: StockSource) -> StockConnectorAdapter:
    """Return a validated adapter around the site's personal Stock function."""

    if not callable(stock):
        raise TypeError("stock must be callable")
    return StockConnectorAdapter(stock=stock)


def _fake_stock_source(stock_date: pd.Timestamp) -> pd.DataFrame:
    """Return visible fake rows until the real Stock connector is supplied."""

    # Keep the placeholder deterministic but date-sensitive so the checked-in
    # comparison page visibly proves that it requested two distinct snapshots.
    day_number = float((stock_date - pd.Timestamp("2026-01-01")).days)

    return pd.DataFrame(
        [
            [
                "FAKE_REPLACE_ME - CRDS-1001",
                "FAKE_REPLACE_ME - CPTY_ALPHA",
                "FAKE_REPLACE_ME - BOOK_A",
                "FAKE_REPLACE_ME - EURUSD Forward",
                "USD",
                2_500_000.0 + (day_number * 1_000.0),
                41_250.0 + (day_number * 125.0),
            ],
            [
                "FAKE_REPLACE_ME - CRDS-1002",
                "FAKE_REPLACE_ME - CPTY_BETA",
                "FAKE_REPLACE_ME - BOOK_C",
                "FAKE_REPLACE_ME - CDX IG",
                "USD",
                -5_000_000.0 - (day_number * 2_000.0),
                -83_500.0 - (day_number * 250.0),
            ],
            [
                "FAKE_REPLACE_ME - CRDS-1003",
                "FAKE_REPLACE_ME - CPTY_GAMMA",
                "FAKE_REPLACE_ME - BOOK_NOT_MAPPED",
                "FAKE_REPLACE_ME - UK Gilt",
                "GBP",
                1_000_000.0 + (day_number * 500.0),
                12_700.0 + (day_number * 75.0),
            ],
        ],
        columns=list(STOCK_COLUMNS),
    )


_FAKE_STOCK_ADAPTER = build_stock_adapter(stock=_fake_stock_source)


def get_stock(stock_date: object) -> pd.DataFrame:
    """Return validated fake Stock data for the selected date.

    Replace ``_fake_stock_source`` with the site's real implementation at the
    composition boundary; callers keep the same exact schema.
    """

    return _FAKE_STOCK_ADAPTER.get_stock(stock_date)


# Retain the business-facing name from the requested external connector.
GetStock = get_stock


__all__ = [
    "GetStock",
    "STOCK_COLUMNS",
    "STOCK_NUMERIC_COLUMNS",
    "STOCK_TEXT_COLUMNS",
    "StockConnectorAdapter",
    "StockSource",
    "build_stock_adapter",
    "get_stock",
    "normalize_stock_date",
    "validate_stock_frame",
]
