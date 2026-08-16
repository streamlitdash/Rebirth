"""Strict Cross Gamma sensitivity adapter with deterministic Credit fixtures."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np
import pandas as pd

from core.s02_pipeline import GROUP, PORTFOLIO, RISK_GREEK
from core.s09_cross_gamma import (
    CROSS_GAMMA_COLUMNS,
    CROSS_GAMMA_SENSITIVITY,
    INPUT_RISK_GREEK,
    INPUT_RISK_TYPE,
    INPUT_TENOR_OPTION,
    INPUT_TENOR_SWAP,
    INPUT_UNDERLYING,
    OUTPUT_RISK_GREEK,
    OUTPUT_RISK_TYPE,
    OUTPUT_TENOR_OPTION,
    OUTPUT_TENOR_SWAP,
    OUTPUT_UNDERLYING,
    XGAMMA_RISK_GREEK,
    XGAMMA_VEGA_RISK_GREEK,
    validate_cross_gamma_rows,
)


class CrossGammaSource(Protocol):
    """Site-owned portfolio sensitivity matrix source."""

    def __call__(self, market_date: pd.Timestamp) -> pd.DataFrame: ...


CrossGammaLoader = Callable[[pd.Timestamp], pd.DataFrame]


def _normalized_date(value: object) -> pd.Timestamp:
    if value is None or isinstance(value, (bool, np.bool_)):
        raise TypeError("market_date must be a date-like value")
    try:
        selected = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("market_date must be a valid scalar date") from exc
    if pd.isna(selected):
        raise ValueError("market_date must be a valid scalar date")
    if selected.tzinfo is not None:
        selected = selected.tz_localize(None)
    return selected.normalize()


def build_cross_gamma_adapter(*, sensitivities: CrossGammaSource) -> CrossGammaLoader:
    """Bind a personal portfolio sensitivity source to the strict raw contract."""

    if not callable(sensitivities):
        raise TypeError("sensitivities must be callable")

    def get_cross_gamma(market_date: pd.Timestamp) -> pd.DataFrame:
        selected_date = _normalized_date(market_date)
        return validate_cross_gamma_rows(sensitivities(selected_date))

    return get_cross_gamma


def _fake_cross_gamma(_market_date: pd.Timestamp) -> pd.DataFrame:
    """Return fake Credit cells exercising source release and output summation."""

    return pd.DataFrame(
        [
            {
                PORTFOLIO: "FAKE_REPLACE_ME - BOOK_A",
                GROUP: "Index",
                INPUT_RISK_TYPE: "Credit",
                INPUT_RISK_GREEK: "Delta",
                RISK_GREEK: XGAMMA_RISK_GREEK,
                INPUT_UNDERLYING: "FAKE_REPLACE_ME - CDX IG",
                INPUT_TENOR_SWAP: "FAKE_REPLACE_ME - 1Y",
                INPUT_TENOR_OPTION: "",
                OUTPUT_RISK_TYPE: "Credit",
                OUTPUT_RISK_GREEK: "Delta",
                OUTPUT_UNDERLYING: "FAKE_REPLACE_ME - Ford CDS",
                OUTPUT_TENOR_SWAP: "FAKE_REPLACE_ME - 5Y",
                OUTPUT_TENOR_OPTION: "",
                CROSS_GAMMA_SENSITIVITY: 12_500.0,
            },
            {
                PORTFOLIO: "FAKE_REPLACE_ME - BOOK_A",
                GROUP: "Index",
                INPUT_RISK_TYPE: "Credit",
                INPUT_RISK_GREEK: "Delta",
                RISK_GREEK: XGAMMA_RISK_GREEK,
                INPUT_UNDERLYING: "FAKE_REPLACE_ME - iTraxx Main",
                INPUT_TENOR_SWAP: "FAKE_REPLACE_ME - 3Y",
                INPUT_TENOR_OPTION: "",
                OUTPUT_RISK_TYPE: "Credit",
                OUTPUT_RISK_GREEK: "Delta",
                OUTPUT_UNDERLYING: "FAKE_REPLACE_ME - Ford CDS",
                OUTPUT_TENOR_SWAP: "FAKE_REPLACE_ME - 5Y",
                OUTPUT_TENOR_OPTION: "",
                CROSS_GAMMA_SENSITIVITY: -7_500.0,
            },
            {
                PORTFOLIO: "FAKE_REPLACE_ME - BOOK_C",
                GROUP: "Single Name",
                INPUT_RISK_TYPE: "Credit",
                INPUT_RISK_GREEK: "Vega",
                RISK_GREEK: XGAMMA_VEGA_RISK_GREEK,
                INPUT_UNDERLYING: "FAKE_REPLACE_ME - Ford CDS Vol",
                INPUT_TENOR_SWAP: "FAKE_REPLACE_ME - 3M",
                INPUT_TENOR_OPTION: "",
                OUTPUT_RISK_TYPE: "Credit",
                OUTPUT_RISK_GREEK: "Delta",
                OUTPUT_UNDERLYING: "FAKE_REPLACE_ME - CDX IG",
                OUTPUT_TENOR_SWAP: "FAKE_REPLACE_ME - 5Y",
                OUTPUT_TENOR_OPTION: "",
                CROSS_GAMMA_SENSITIVITY: 4_000.0,
            },
        ],
        columns=list(CROSS_GAMMA_COLUMNS),
    )


_DEFAULT_ADAPTER = build_cross_gamma_adapter(sensitivities=_fake_cross_gamma)


def get_cross_gamma(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return deterministic fake Credit Cross Gamma sensitivity rows."""

    return _DEFAULT_ADAPTER(market_date)


# Compatibility with the business connector spelling used in design notes.
GetCrossGamma = get_cross_gamma


__all__ = [
    "CROSS_GAMMA_COLUMNS",
    "CROSS_GAMMA_SENSITIVITY",
    "CrossGammaLoader",
    "CrossGammaSource",
    "GetCrossGamma",
    "INPUT_RISK_GREEK",
    "INPUT_RISK_TYPE",
    "INPUT_TENOR_OPTION",
    "INPUT_TENOR_SWAP",
    "INPUT_UNDERLYING",
    "OUTPUT_RISK_GREEK",
    "OUTPUT_RISK_TYPE",
    "OUTPUT_TENOR_OPTION",
    "OUTPUT_TENOR_SWAP",
    "OUTPUT_UNDERLYING",
    "build_cross_gamma_adapter",
    "get_cross_gamma",
]
