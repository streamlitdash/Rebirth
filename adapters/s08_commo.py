"""Working Commodity Delta curve adapter example."""

# === REAL COMMODITY CONNECTOR: SOURCE BODY NOT RECOVERED =====================
# The retained originals referenced ``build_commo_delta_adapter`` and
# ``build_commo_vega_adapter`` but contained neither implementation. Nothing was
# invented. This strict adapter contract remains active over the CSV feed.
# === END COMMODITY RECOVERY MARKER ===========================================

from __future__ import annotations

import pandas as pd

from core.s01_schema import TENOR_SWAP, TENOR_SWAP_ORDER
from core.s02_pipeline import ProductConnectorAdapter
from .s01_common import MarketSource, RiskSource, exact_frame, market_frame


COMMO_DELTA_RISK = (
    "Underlying",
    TENOR_SWAP,
    "Portfolio",
    "Group",
    "Risk",
    "dRisk",
)
COMMO_DELTA_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
COMMO_DELTA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")


def build_commo_adapter(
    *,
    risk: RiskSource,
    open_market: MarketSource,
    current_market: MarketSource,
) -> ProductConnectorAdapter:
    """Bind personal Commodity Delta risk/Open/current functions."""

    def get_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
        return exact_frame(
            risk(risk_date), columns=COMMO_DELTA_RISK, label="Commodity Delta risk"
        )

    def get_open(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            open_market,
            market_date,
            underlying,
            market_status=market_status,
            columns=COMMO_DELTA_OPEN,
            label="Commodity Delta Open",
        )

    def get_current(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            current_market,
            market_date,
            underlying,
            market_status=market_status,
            columns=COMMO_DELTA_CURRENT,
            label="Commodity Delta current",
            attach_status=True,
        )

    return ProductConnectorAdapter(
        risk=get_risk,
        market_open=get_open,
        market_status=get_current,
    )


__all__ = [
    "COMMO_DELTA_CURRENT",
    "COMMO_DELTA_OPEN",
    "COMMO_DELTA_RISK",
    "build_commo_adapter",
]
