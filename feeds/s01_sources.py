"""Site-owned connector boundary with explicit fake-CSV placeholders.

Cube reads the numbered files in its module-relative ``data`` directory. Every
reporting dimension in those files is marked ``FAKE_REPLACE_ME`` so placeholder
data cannot be mistaken for production.

Those files sit behind small public connector functions. The dated Risk Checker
function owns both readiness and inventory files. Recovered private connector
code is preserved as clearly delimited, comment-only ``REAL`` blocks immediately
beside the active CSV fallback in this file and the matching adapter module. To
connect real systems, uncomment a ``REAL`` block and comment its adjacent active
CSV fallback/return as instructed by the marker.
Keep its parameters and documented return columns unchanged; the common pipeline
will continue to own validation, joins, P&L formulas, aggregation, readiness-date
transitions, and transactional last-good-snapshot behavior.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from adapters.s06_new_positions import (
    CASHFLOW as NEW_POSITION_CASHFLOW,
    ROW_TYPE as NEW_POSITION_ROW_TYPE,
    get_new_positions as get_new_position_blotter,
)
from adapters.s07_cross_gamma import get_cross_gamma as get_cross_gamma_matrix
from core.s01_schema import (
    PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
    PORTFOLIO_FIELD_BY_KEY,
    TENOR_COLUMNS,
    TENOR_ORDER_COLUMNS,
)
from core.s02_pipeline import (
    CREDIT_MEASURE_COLUMNS,
    CURRENT,
    DIRECT_PL_INPUT_COLUMNS,
    LIVE,
    MARKET_STATUS,
    OFFICIAL,
    NEW_POSITION_CASH_FLOW_CLASSIFICATION,
    PL,
    PORTFOLIO,
    PRODUCT_SPECS_BY_SOURCE_TYPE,
    REGION,
    RISK_GREEK,
    RISK_OVERLAY_COLUMNS,
    RISK_TYPE,
    SOURCE_TYPE,
    ProductConnectorAdapter,
    RiskRefreshManager,
)
from core.s11_risk_archive import COLOSSUS_COLUMNS

# === REAL PRODUCT IMPORTS (COMMENTED OUT) ====================================
# Uncomment these only after uncommenting the recovered builders in the named
# adapter modules. The active CSV registration is at get_product_connector_adapters.
# from adapters.s02_ir import (
#     build_ir_basis_adapter,
#     build_ir_bond_adapter,
#     build_ir_delta_adapter,
#     build_ir_deltavega_adapter,
#     build_ir_inflation_adapter,
#     build_ir_inflationvega_adapter,
#     build_ir_xccy_adapter,
# )
# from adapters.s03_fx import (
#     build_fx_delta_adapter,
#     build_fx_gamma_adapter,
#     build_fx_vega_adapter,
# )
# from adapters.s04_credit import build_credit_delta_adapter
# No private Commodity builder was present in the recovered source.
#
# Recovered dependencies for get_risk_checker/get_portfolio_config:
# import colossus
# import mrx
# import numpy as np
# from awacs_poc import configmanager as cm
# from pandas.tseries.offsets import BDay
# colossus_connection = colossus.connect("PROD")
#
# Recovered original import inventory (reference only; do not uncomment this
# whole list). Most names were unused by the recovered connector bodies, but
# they are kept inline so moving away from the old archive files loses nothing:
# import asyncio
# import boto3
# import concerto
# import credentials_wrapper as cw
# import dataframe_image as dfi
# import datetime as dt
# import io
# import json
# import mailer
# import pathlib
# import re
# import requests
# import streamlit as st
# import string
# import time as time_time
# import warnings
# import xva_rpmlib.ver as rple
# from base64 import b64encode
# from collections import Counter
# from cryptdl2 import pypdl
# from cryptdl2.pypdl import pdl_read, gnp_exec
# from dataclasses import dataclass
# from datetime import *
# from functools import reduce
# from IPython.display import Image, display
# from numpy import *
# from PIL import Image as PilImage
# from st_aggrid import (
#     Aggrid,
#     ColumnsAutoSizeMode,
#     DataReturnMode,
#     GridOptionsBuilder,
#     GridUpdateMode,
#     JsCode,
# )
# from typing import Dict, List, Literal, Tuple
# from xva.boli.local import *
# from xva.boli.local import bkeu_functions, gcd
# from xva.boli.local.utils import dates
# from xva.rpmlib.data.attributes import DataAttributes
# from xva.rpmlib.mercury_xva_api import MercuryXvaApi
# from xva.rpmlib.pal.pal_credit_data import PalCreditData
# from xva.rpmlib.pal.pal_enums import PalServers, Xvascope, xvacode
# === END REAL PRODUCT IMPORTS ================================================


FAKE_DATA_DIRECTORY = Path(__file__).resolve().parent.parent / "data"
FAKE_CSV_FILES = {
    "risk_readiness": FAKE_DATA_DIRECTORY / "s01_readiness.csv",
    "risk_checker": FAKE_DATA_DIRECTORY / "s02_checker.csv",
    "risk": FAKE_DATA_DIRECTORY / "s03_risk.csv",
    "market_open": FAKE_DATA_DIRECTORY / "s04_open.csv",
    "market_status": FAKE_DATA_DIRECTORY / "s05_current.csv",
    "portfolio_config": FAKE_DATA_DIRECTORY / "s06_portfolios.csv",
    "risk_thresholds": FAKE_DATA_DIRECTORY / "s07_thresholds.csv",
    "reported_underlyings": FAKE_DATA_DIRECTORY / "s09_reported.csv",
}

_SOURCE_TYPE = "Source Type"
_FAKE_NOTICE = "FAKE_REPLACE_ME"
_FAKE_CSV_SCHEMAS = {
    "risk_readiness": ("Risk Type", "Risk Greek", "Age"),
    "risk_checker": ("Risk Type", "Risk Greek", "MMMFile", "Product"),
    "risk": (
        _SOURCE_TYPE,
        "Underlying",
        *TENOR_COLUMNS,
        "Portfolio",
        "Group",
        "Risk",
        "dRisk",
        *CREDIT_MEASURE_COLUMNS,
    ),
    "market_open": (
        _SOURCE_TYPE,
        "Underlying",
        *TENOR_COLUMNS,
        *TENOR_ORDER_COLUMNS,
        "Open",
    ),
    "market_status": (
        _SOURCE_TYPE,
        "Underlying",
        *TENOR_COLUMNS,
        *TENOR_ORDER_COLUMNS,
        "Current",
    ),
    "portfolio_config": PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
    "risk_thresholds": ("Risk Type", "Risk Greek", "PL", "Risk", "dRisk"),
    "reported_underlyings": (
        "Risk Type",
        "Risk Greek",
        "Underlying",
        "Reported Underlying",
    ),
}


class FakeCsvConnectorError(RuntimeError):
    """Raised when an explicit fake connector CSV is missing or malformed."""


@lru_cache(maxsize=32)
def _load_fake_csv(
    dataset: str,
    path_text: str,
    modified_ns: int,
    size: int,
) -> pd.DataFrame:
    """Parse one immutable file revision; callers always receive a copy."""

    del modified_ns, size  # Their values intentionally form the cache key.
    path = Path(path_text)
    expected_columns = _FAKE_CSV_SCHEMAS[dataset]
    try:
        frame = pd.read_csv(
            path,
            dtype="string",
            encoding="utf-8-sig",
            keep_default_na=False,
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise FakeCsvConnectorError(
            f"Could not read fake connector file {path}: {exc}"
        ) from exc
    actual_columns = tuple(str(column).strip() for column in frame.columns)
    if actual_columns != expected_columns:
        raise FakeCsvConnectorError(
            f"Fake connector file {path.name} must have columns "
            f"{list(expected_columns)} in that order; found {list(actual_columns)}."
        )
    if frame.empty:
        raise FakeCsvConnectorError(f"Fake connector file {path.name} has no rows.")
    frame.columns = list(expected_columns)
    if _SOURCE_TYPE in expected_columns:
        _validate_source_coverage(frame, _SOURCE_TYPE, dataset)
    return frame


def _fake_csv_revision(dataset: str) -> tuple[Path, str, int, int]:
    """Return one file revision key without copying or parsing its contents."""

    path = FAKE_CSV_FILES[dataset]
    try:
        stat = path.stat()
    except OSError as exc:
        raise FakeCsvConnectorError(
            f"Fake connector file is missing: {path}. Restore it or replace the "
            f"{dataset!r} loader in feeds.s01_sources.py with a real function."
        ) from exc
    return path, str(path.resolve()), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=1024)
def _load_fake_source_partition(
    dataset: str,
    path_text: str,
    modified_ns: int,
    size: int,
    source_type: str,
    underlying: str | None,
) -> pd.DataFrame:
    """Cache one immutable-by-convention source/Underlying file partition."""

    frame = _load_fake_csv(dataset, path_text, modified_ns, size)
    scoped = frame.loc[frame[_SOURCE_TYPE].eq(source_type)]
    if underlying is not None:
        scoped = scoped.loc[scoped["Underlying"].eq(underlying)]
    return scoped.reset_index(drop=True)


def _read_fake_csv(dataset: str) -> pd.DataFrame:
    """Read one fixed placeholder file, reusing only the same file revision."""

    _path, path_text, modified_ns, size = _fake_csv_revision(dataset)
    frame = _load_fake_csv(dataset, path_text, modified_ns, size)
    return frame.copy(deep=True)


def _normalized_date(value: pd.Timestamp, *, parameter: str) -> pd.Timestamp:
    """Validate a connector date even though static placeholder rows are reused."""
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"{parameter} must not be blank or NaT")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _market_status(value: object) -> str:
    """Reject ambiguous status routing before a connector reads any source."""
    if value not in {LIVE, OFFICIAL}:
        raise ValueError("market_status must be exactly 'Live' or 'OFFICIAL'")
    return str(value)


def get_market_state(
    market_date: pd.Timestamp,
    *,
    trading_timezone: str = "Europe/London",
    now: datetime | pd.Timestamp | None = None,
) -> str:
    """Resolve the one authoritative Live/OFFICIAL source for a market date.

    Replace this function body with the real market-status service. The refresh
    manager calls it once per refresh, validates the exact returned value, and
    passes that same value to every per-Underlying Open and Current connector.
    The checked-in fake implementation follows the desk's 22:00 trading-time
    cutoff: an earlier date is OFFICIAL, while today's date becomes OFFICIAL at
    22:00 in the configured timezone. ``now`` exists only for deterministic
    fixture tests; production callers leave it unset.
    """

    selected_date = _normalized_date(market_date, parameter="market_date")
    zone = ZoneInfo(trading_timezone)
    trading_now = pd.Timestamp(datetime.now(zone) if now is None else now)
    if trading_now.tzinfo is None:
        trading_now = trading_now.tz_localize(zone)
    else:
        trading_now = trading_now.tz_convert(zone)
    trading_today = pd.Timestamp(trading_now.date())
    if selected_date < trading_today:
        return OFFICIAL
    if selected_date == trading_today and trading_now.hour >= 22:
        return OFFICIAL
    return LIVE


def _source_spec(source_type: str):
    try:
        return PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    except KeyError as exc:
        raise ValueError(f"Unknown source_type {source_type!r}") from exc


def _validate_source_coverage(frame: pd.DataFrame, column: str, dataset: str) -> None:
    expected = set(PRODUCT_SPECS_BY_SOURCE_TYPE)
    actual = set(frame[column].astype(str))
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise FakeCsvConnectorError(
            f"{FAKE_CSV_FILES[dataset].name} source coverage is invalid; "
            f"missing={missing}, extra={extra}."
        )


def _require_fake_notice(
    frame: pd.DataFrame,
    columns: list[str],
    *,
    dataset: str,
) -> None:
    """Keep placeholder warnings visible in every reporting dimension."""
    for column in columns:
        values = frame[column].astype(str)
        invalid = ~values.str.contains(_FAKE_NOTICE, regex=False)
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise FakeCsvConnectorError(
                f"{FAKE_CSV_FILES[dataset].name} column {column!r} must contain "
                f"{_FAKE_NOTICE!r}; invalid rows {rows}. Replace the loader code, "
                "not the warning, when connecting real data."
            )


def _source_rows(
    dataset: str,
    source_type: str,
    output_columns: list[str],
    *,
    underlying: str | None = None,
    allow_empty: bool = False,
) -> pd.DataFrame:
    _path, path_text, modified_ns, size = _fake_csv_revision(dataset)
    partition = _load_fake_source_partition(
        dataset,
        path_text,
        modified_ns,
        size,
        source_type,
        underlying,
    )
    if partition.empty and not allow_empty:
        raise FakeCsvConnectorError(
            f"{FAKE_CSV_FILES[dataset].name} has no rows for {source_type!r}."
        )
    # The cached partition is never exposed: connector callers receive only a
    # narrow defensive copy, so one call cannot mutate a later call's result.
    return partition.loc[:, output_columns].copy().reset_index(drop=True)


def get_risk_checker(
    checker_date: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(risk_readiness_df, risk_checker_df)`` from one dated call.

    The readiness frame contains ``Risk Type``, ``Risk Greek``, and ``Age``.
    Missing known pairs are completed by the pipeline with Age 0. The inventory
    frame contains ``Risk Type``, ``Risk Greek``, ``MMMFile``, and ``Product``.
    A real implementation should fetch both atomically for ``checker_date``.
    """

    # === REAL RISK CHECKER (COMMENTED OUT) ===================================
    # SWITCH (1/2): uncomment this REAL block.
    # SWITCH (2/2): comment the adjacent ACTIVE CSV FALLBACK block below.
    # _normalized_date(checker_date, parameter="checker_date")
    # try:
    #     view = mrx.MRXView(r"mrx/static/age.tsv")
    #     view += ("Current Date", checker_date.strftime("%Y/%m/%d"))
    #     data = view.fetch(verify=False)
    #     data = data.rename(columns={"Age": "Aged", "Risk Type": "Risk All"})
    #
    #     hpce_files = [
    #         "MICRO_XTARGET_RT_PAL.DAT",
    #         "MICRO_XTARGET_RT_PAL.DAT",
    #         "MICRO_XTARGET_PT_ADJ.DAT",
    #         "MICRO_PLATO_LON.DAT",
    #         "HPCE_RPO_XVA_STOCK.DAT",
    #         "HPCE_CRD_XVA_STOCK.DAT",
    #         "HPCE_CMD_XVA_STOCK.DAT",
    #         "HPCE_RPO_XVA_CR_SP01_SPLIT_PARENT_TENOR.DAT",
    #         "HPCE_CRD_XVA_CR_SP01_SPLIT_PARENT_TENOR.DAT",
    #         "RHO_HAT_DETAILED_FAST_MANAGEMENT.DAT",
    #         "PAL_CMD_XVA_CR_SP01_SPLIT_PARENT_TENOR.DAT",
    #     ]
    #
    #     data["Product"] = np.where(
    #         data["MRX File"].str.contains("PAL", na=False), "XVA", "Hedges"
    #     )
    #     data["Age"] = np.where(data["Aged"] == "Aged", 1, 0)
    #     data = data[~data["MRX File"].isin(hpce_files)]
    #
    #     data_mapping = {
    #         "CO Delta Cash (GeAR)": ("Commo", "Delta"),
    #         "CO Vega ATM": ("Commo", "Vega"),
    #         "Credit Spread Delta (GeAR)": ("Credit", "Delta"),
    #         "Credit Option Vega": ("Credit", "Vega"),
    #         "FX Exposure (Ext.) (GeAR)": ("FX", "Delta"),
    #         "FX Gamma Ccy1 (Soho)": ("FX", "Gamma"),
    #         "FX Vega (Soho) (GeAR)": ("FX", "Vega"),
    #         "IR Delta (GeAR)": ("IR", "Delta"),
    #         "IR Gamma +10 (GeAR)": ("IR", "Gamma"),
    #         "IR Gamma -10 (GeAR)": ("IR", "Gamma"),
    #         "IR Vega (GeAR)": ("IR", "DeltaVega"),
    #         "INF ZC Delta (GeAR)": ("IR", "Inflation"),
    #         "INF ZC Spread (GeAR)": ("IR", "Inflation"),
    #         "INF ZC SABRAP Vega": ("IR", "InflationVega"),
    #         "IR Basis Spread (GeAR)": ("IR", "Basis"),
    #         "Bond Spread (GeAR)": ("IR", "Bond"),
    #     }
    #     mapping = pd.DataFrame(
    #         [[key, *values] for key, values in data_mapping.items()],
    #         columns=["Risk All", "Risk Type", "Risk Greek"],
    #     )
    #     data = pd.merge(data, mapping, on="Risk All", how="left")
    #     checker = data.loc[
    #         data["Age"] != 0,
    #         ["Risk Type", "Risk Greek", "MRX File", "Product"],
    #     ].copy()
    #     readiness = (
    #         data[["Risk Type", "Risk Greek", "Age"]]
    #         .groupby(["Risk Type", "Risk Greek"], as_index=False)
    #         .sum()
    #     )
    #     basis_age = readiness.loc[
    #         readiness["Risk Greek"] == "Basis", "Age"
    #     ].sum()
    #     readiness = pd.concat(
    #         [
    #             readiness,
    #             pd.DataFrame(
    #                 {
    #                     "Risk Type": ["IR", "IR"],
    #                     "Risk Greek": ["XCCY", "XCCYVega"],
    #                     "Age": [basis_age, basis_age],
    #                 }
    #             ),
    #         ],
    #         ignore_index=True,
    #     )
    #     readiness.to_csv("data/s01_readiness.csv", index=False)
    #     checker.to_csv("data/s02_checker.csv", index=False)
    # except Exception:
    #     readiness = pd.read_csv("data/s01_readiness.csv")
    #     checker = pd.read_csv("data/s02_checker.csv")
    # # Contract shim: current Cube calls this column MMMFile.
    # checker = checker.rename(columns={"MRX File": "MMMFile"})
    # return readiness.copy(), checker.copy()
    # === END REAL RISK CHECKER ===============================================

    # === ACTIVE CSV FALLBACK (COMMENT OUT WHEN REAL IS ENABLED) =============
    _normalized_date(checker_date, parameter="checker_date")
    readiness = _read_fake_csv("risk_readiness")
    checker = _read_fake_csv("risk_checker")
    return readiness.copy(), checker.copy()


def get_risk(risk_date: pd.Timestamp, source_type: str) -> pd.DataFrame:
    """Return fake Risk/dRisk rows for one source and requested risk date.

    Real replacement contract: use both parameters and return ``Underlying``,
    ``Portfolio``, ``Group``, ``Risk``, ``dRisk``, plus the source's required
    tenor fields. Credit may additionally return connector-owned ``Region``.
    Connector Group, Region, Risk, and dRisk remain authoritative.
    """
    # REAL risk bodies are comment-preserved in adapters/s02_ir.py,
    # adapters/s03_fx.py, and adapters/s04_credit.py. Switch them only in
    # get_product_connector_adapters below. ACTIVE CSV FALLBACK follows.
    _normalized_date(risk_date, parameter="risk_date")
    spec = _source_spec(source_type)
    output_columns = [
        "Underlying",
        *spec.tenor_columns,
        "Portfolio",
        "Group",
        "Risk",
        "dRisk",
    ]
    if spec.risk_type == "Credit":
        output_columns.extend(CREDIT_MEASURE_COLUMNS)
    frame = _source_rows("risk", source_type, output_columns)
    if spec.risk_type == "Credit":
        # The supplied Credit connector owns a Region dimension.  The public
        # fixture intentionally has no site taxonomy, so its deterministic and
        # truthful fallback is the connector-owned Group rather than an invented
        # geography.  A real Credit loader should return its authoritative
        # Region directly and remove this fixture-only derivation.
        frame.insert(frame.columns.get_loc("Group") + 1, REGION, frame["Group"])
    _require_fake_notice(
        frame,
        ["Underlying", *spec.tenor_columns, "Portfolio"],
        dataset="risk",
    )
    return frame


def get_cross_gamma_risk(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return fixture Cross Gamma target-risk rows.

    The supplied real implementation is intentionally not imported here because
    it depends on private credentials and libraries.  Replace this body with the
    site's function only in a production integration module; its exact output
    contract is ``RISK_OVERLAY_COLUMNS``.
    """

    _normalized_date(market_date, parameter="market_date")
    return pd.DataFrame(columns=list(RISK_OVERLAY_COLUMNS))


def get_cross_gamma_sensitivities(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return validated portfolio-level XGAMMA sensitivity matrix rows."""

    selected_date = _normalized_date(market_date, parameter="market_date")
    return get_cross_gamma_matrix(selected_date)


def get_new_positions(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return fixture intraday new-position target-risk rows.

    The deterministic demo has no invented positions, so an exact header-only
    frame is the truthful fixture.  A production replacement returns the same
    seven canonical fields and the pipeline supplies ``Split = New Trades``.
    """

    _normalized_date(market_date, parameter="market_date")
    return pd.DataFrame(columns=list(RISK_OVERLAY_COLUMNS))


def get_new_trades(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return the validated mixed MARKET/CASHFLOW New Trades blotter."""

    selected_date = _normalized_date(market_date, parameter="market_date")
    return get_new_position_blotter(selected_date)


def get_new_position_cash_flows(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return only validated CASHFLOW rows for the direct-P&L release path.

    The raw adapter's illustrative MARKET rows remain deliberately isolated
    until their traded-level and MarketBook calculation is implemented.  This
    explicit selection cannot reinterpret them as zero-risk P&L rows.
    """

    selected_date = _normalized_date(market_date, parameter="market_date")
    blotter = get_new_position_blotter(selected_date)
    cash_flows = blotter.loc[
        blotter[NEW_POSITION_ROW_TYPE].eq(NEW_POSITION_CASHFLOW),
        [RISK_TYPE, RISK_GREEK, PORTFOLIO, PL],
    ].copy()
    cash_flows.insert(
        0,
        SOURCE_TYPE,
        NEW_POSITION_CASH_FLOW_CLASSIFICATION.source_type,
    )
    return cash_flows.loc[:, list(DIRECT_PL_INPUT_COLUMNS)].reset_index(drop=True)


def get_market_open(
    source_type: str,
    market_date: pd.Timestamp,
    underlying: str,
    *,
    market_status: str,
) -> pd.DataFrame:
    """Return fake opening quotes for one source and requested market date.

    Real replacement contract: use the date and requested Underlying, returning
    numeric ``Open``, the source's tenor fields, and its applicable tenor-order
    authority. The manager supplies one Risk-derived ``underlying`` per call.
    Use the explicit ``market_status`` to select the Live or OFFICIAL dataset.
    """
    # REAL Open bodies live beside their adapter contracts. The ACTIVE CSV
    # FALLBACK below remains selected by get_product_connector_adapters.
    _normalized_date(market_date, parameter="market_date")
    _market_status(market_status)
    if not isinstance(underlying, str) or not underlying.strip():
        raise ValueError("underlying must be nonblank text")
    spec = _source_spec(source_type)
    output_columns = [
        "Underlying",
        *spec.tenor_columns,
        *spec.tenor_order_columns,
        "Open",
    ]
    frame = _source_rows(
        "market_open",
        source_type,
        output_columns,
        underlying=underlying.strip(),
        allow_empty=True,
    )
    _require_fake_notice(
        frame,
        ["Underlying", *spec.tenor_columns],
        dataset="market_open",
    )
    return frame


def get_market_status(
    source_type: str,
    market_date: pd.Timestamp,
    underlying: str,
    *,
    market_status: str,
) -> pd.DataFrame:
    """Return a fake numeric Live/OFFICIAL leg for the requested market date.

    Real replacement contract: use the date and requested Underlying, returning
    numeric ``Current``, the source's tenor fields, and the same applicable
    ``Tenor ... Order`` authority as Open. Product adapters call once per member
    of the ordered Risk-derived Underlying tuple.

    ``market_status`` is supplied by the manager as exactly ``Live`` or
    ``OFFICIAL``. A real connector uses it to choose the upstream source rather
    than comparing the date to its own clock.
    """
    # REAL Current bodies live beside their adapter contracts. The ACTIVE CSV
    # FALLBACK below remains selected by get_product_connector_adapters.
    _normalized_date(market_date, parameter="market_date")
    selected_status = _market_status(market_status)
    if not isinstance(underlying, str) or not underlying.strip():
        raise ValueError("underlying must be nonblank text")
    spec = _source_spec(source_type)
    output_columns = [
        "Underlying",
        *spec.tenor_columns,
        *spec.tenor_order_columns,
        CURRENT,
    ]
    frame = _source_rows(
        "market_status",
        source_type,
        output_columns,
        underlying=underlying.strip(),
        allow_empty=True,
    )
    _require_fake_notice(
        frame,
        ["Underlying", *spec.tenor_columns],
        dataset="market_status",
    )
    frame[MARKET_STATUS] = selected_status
    return frame


def get_portfolio_config(portfolio_date: pd.Timestamp) -> pd.DataFrame:
    """Return Portfolio mappings effective one business day before market date.

    Real replacement contract: return every required column from
    ``core.s01_schema.PORTFOLIO_CONFIG_REQUIRED_COLUMNS``. Optional registered
    fields such as ``Sub Category`` are preserved when supplied. The manager
    supplies ``market_date - BDay(1)`` as ``portfolio_date``.
    """

    # === REAL PORTFOLIO MAPPING (COMMENTED OUT) ==============================
    # SWITCH (1/2): uncomment this REAL block and the REAL imports above.
    # SWITCH (2/2): comment the adjacent ACTIVE CSV FALLBACK block below.
    # _normalized_date(portfolio_date, parameter="portfolio_date")
    # try:
    #     try:
    #         body = {
    #             "reportingCurrency": "EUR",
    #             "attributes": ["Ptf", "PtfName", "SignoffGroup"],
    #             "values": ["SteppedDailyPnL"],
    #             "filters": [
    #                 {"attributeName": "PnlType", "filters": ["Gross P&L"]}
    #             ],
    #             "fromDateKey": (portfolio_date - BDay(2)).strftime("%Y%m%d"),
    #             "toDateKey": portfolio_date.strftime("%Y%m%d"),
    #         }
    #         response = colossus_connection.raw_request(
    #             "POST",
    #             endpoint_uri="/v1/data-warehouse/reports/query",
    #             body=body,
    #         )
    #         sog_colossus = pd.DataFrame(
    #             [row["fields"] for row in response["results"][1:]],
    #             columns=response["results"][0]["fields"],
    #         ).drop(columns=["SteppedDailyPnL"]).rename(
    #             columns={"Ptf": "Portfolio", "PtfName": "Portfolio Name"}
    #         )
    #         sog_colossus.to_csv("data/s03_colossusbp.csv", index=False)
    #     except Exception:
    #         sog_colossus = pd.read_csv("data/s03_colossusbp.csv")
    #
    #     try:
    #         ptf_mapping = pd.DataFrame(cm.get("XVA.IM Optin.PnL.Ann.Ptf List"))
    #         ptf_mapping["Portfolio"] = (
    #             ptf_mapping["Portfolio"]
    #             .fillna("NA")
    #             .str.replace(" ", "", regex=True)
    #         )
    #         ptf_mapping.to_csv("data/s04_configbp.csv", index=False)
    #     except Exception:
    #         ptf_mapping = pd.read_csv("data/s04_configbp.csv")
    #
    #     try:
    #         view = mrx.MRXView("mrx/static/product.tsv")
    #         view += ("Current Date", portfolio_date.strftime("%Y/%m/%d"))
    #         product_data = view.fetch(verify=False)
    #         xva_products = [
    #             "CVA-NONRISKMANAGED",
    #             "CVA-RISKMANAGED",
    #             "FCVA",
    #             "FBVA",
    #             "COLVA",
    #         ]
    #         portfolio_product = product_data[["Portfolio", "Product"]].drop_duplicates()
    #         portfolio_xva = portfolio_product[
    #             portfolio_product["Product"].isin(xva_products)
    #         ].drop_duplicates(subset="Portfolio")
    #         portfolio_xva["Product"] = "XVA"
    #         all_portfolios = product_data["Portfolio"].drop_duplicates()
    #         portfolio_hedges = all_portfolios[
    #             ~all_portfolios.isin(portfolio_xva["Portfolio"])
    #         ].to_frame()
    #         portfolio_hedges["Product"] = "Hedges"
    #         productmap = pd.concat(
    #             [
    #                 portfolio_xva[["Portfolio", "Product"]],
    #                 portfolio_hedges[["Portfolio", "Product"]],
    #             ],
    #             ignore_index=True,
    #         )
    #         cit_sog = sog_colossus[
    #             sog_colossus["SignoffGroup"] == "CIT XVA"
    #         ].copy()
    #         cit_sog["Product"] = np.where(
    #             cit_sog["Portfolio Name"].str.contains("HED", na=False),
    #             "Hedges",
    #             "XVA",
    #         )
    #         productmap = pd.concat(
    #             [productmap, cit_sog[["Portfolio", "Product"]]],
    #             ignore_index=True,
    #         )
    #         productmap.to_csv("data/s05_productbp.csv", index=False)
    #     except Exception:
    #         productmap = pd.read_csv("data/s05_productbp.csv")
    #
    #     for source in (ptf_mapping, sog_colossus, productmap):
    #         source["Portfolio"] = (
    #             source["Portfolio"]
    #             .astype(str)
    #             .str.replace(",", "", regex=True)
    #             .str.replace(r"\.0$", "", regex=True)
    #         )
    #     frame = pd.merge(
    #         ptf_mapping, sog_colossus, on="Portfolio", how="outer"
    #     ).fillna("NA")
    #     cit_xva_rows = frame[frame["SignoffGroup"] == "CIT XVA"].copy()
    #     cit_xva_rows["Portfolio"] = cit_xva_rows["Portfolio Name"]
    #     frame = pd.concat([frame, cit_xva_rows], ignore_index=True)
    #     frame = pd.merge(
    #         frame, productmap, on="Portfolio", how="outer"
    #     ).fillna("NA")
    #     frame = frame.drop(columns=["Ptf Name"])[
    #         [
    #             "Portfolio",
    #             "Product",
    #             "SignoffGroup",
    #             "Activity",
    #             "Portfolio Name",
    #             "Category",
    #             "Sub Category",
    #         ]
    #     ]
    #     frame = frame[frame["Product"].isin(["XVA", "Hedges"])]
    #     frame.to_csv("data/s06_portfolios.csv", index=False)
    # except Exception:
    #     # The recovered implementation used its last successful local extracts.
    #     sog_colossus = pd.read_csv("data/s03_colossusbp.csv")
    #     ptf_mapping = pd.read_csv("data/s04_configbp.csv")
    #     productmap = pd.read_csv("data/s05_productbp.csv")
    #     for source in (ptf_mapping, sog_colossus, productmap):
    #         source["Portfolio"] = (
    #             source["Portfolio"]
    #             .astype(str)
    #             .str.replace(r"\.0$", "", regex=True)
    #         )
    #     frame = pd.merge(
    #         ptf_mapping, sog_colossus, on="Portfolio", how="outer"
    #     ).fillna("NA")
    #     cit_xva_rows = frame[frame["SignoffGroup"] == "CIT XVA"].copy()
    #     cit_xva_rows["Portfolio"] = cit_xva_rows["Portfolio Name"]
    #     frame = pd.concat([frame, cit_xva_rows], ignore_index=True)
    #     frame = pd.merge(
    #         frame, productmap, on="Portfolio", how="outer"
    #     ).fillna("NA")
    #     frame = frame.drop(columns=["Ptf Name"])[
    #         [
    #             "Portfolio",
    #             "Product",
    #             "SignoffGroup",
    #             "Activity",
    #             "Portfolio Name",
    #             "Category",
    #             "Sub Category",
    #         ]
    #     ]
    #     frame = frame[frame["Product"].isin(["XVA", "Hedges"])]
    # return frame.copy()
    # === END REAL PORTFOLIO MAPPING ==========================================

    # === ACTIVE CSV FALLBACK (COMMENT OUT WHEN REAL IS ENABLED) =============
    _normalized_date(portfolio_date, parameter="portfolio_date")
    frame = _read_fake_csv("portfolio_config")
    _require_fake_notice(
        frame,
        [
            column
            for column in PORTFOLIO_CONFIG_REQUIRED_COLUMNS
            if column != PORTFOLIO_FIELD_BY_KEY["product"].external_name
        ],
        dataset="portfolio_config",
    )
    return frame.copy()


def get_risk_thresholds() -> pd.DataFrame:
    """Return fake positive absolute PL/Risk/dRisk promotion limits.

    Real replacement contract: return one unique ``Risk Type`` + ``Risk Greek``
    row with positive absolute ``PL``, ``Risk``, and ``dRisk`` limits.
    """
    # === RECOVERED ORIGINAL THRESHOLDS (COMMENTED OUT) =======================
    # The original implementation was itself a CSV read; no private threshold
    # service body was present to restore.
    # return pd.read_csv("data/s07_thresholds.csv").copy()
    # === ACTIVE CSV FALLBACK ==================================================
    return _read_fake_csv("risk_thresholds").copy()


def get_reported_underlyings() -> pd.DataFrame:
    """Return the cross-product raw-to-reporting Underlying map.

    Real replacement contract: return exactly ``Risk Type``, ``Risk Greek``,
    ``Underlying``, and ``Reported Underlying``. The first three columns form a
    unique source key; multiple source keys may share one reported target.
    """

    # === RECOVERED ORIGINAL REPORTING MAP (COMMENTED OUT) ====================
    # The original implementation also read data/s09_reported.csv directly.
    # return pd.read_csv("data/s09_reported.csv", dtype="string")
    # === ACTIVE VALIDATED CSV FALLBACK =======================================
    frame = _read_fake_csv("reported_underlyings")
    _require_fake_notice(
        frame,
        ["Underlying", "Reported Underlying"],
        dataset="reported_underlyings",
    )
    return frame.copy()


def get_colossus_pl(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return official Colossus P&L at the archive's four-key grain.

    REAL CONNECTOR INTEGRATION POINT: replace this body with the site-owned
    Colossus function. It must return exactly ``Portfolio``, ``Underlying``,
    ``Risk Type``, ``Risk Greek``, and ``PL`` with one row per first four
    columns. The active fixture adapts the checked-in Histo P&L ``histo.csv``
    for the selected date; its Product field is governed separately by the
    official Risk Explorer snapshot.
    """

    selected_date = _normalized_date(market_date, parameter="market_date")
    source = (
        FAKE_DATA_DIRECTORY / "histo" / selected_date.date().isoformat() / "histo.csv"
    )
    expected = ("Risk Type", "Risk Greek", "Underlying", "Product", "Book", "PL")
    try:
        frame = pd.read_csv(
            source,
            dtype="string",
            encoding="utf-8-sig",
            keep_default_na=False,
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise FakeCsvConnectorError(
            f"Could not read fake Colossus P&L file {source}: {exc}"
        ) from exc
    actual = tuple(str(column).strip() for column in frame.columns)
    if actual != expected:
        raise FakeCsvConnectorError(
            f"Fake Colossus P&L file {source} must have columns "
            f"{list(expected)} in that order; found {list(actual)}"
        )
    frame.columns = list(expected)
    result = frame.rename(columns={"Book": PORTFOLIO})
    return result[list(COLOSSUS_COLUMNS)].copy()


def send_sog_pl(frame: pd.DataFrame) -> None:
    """Reject external SOG delivery while the fixture boundary is active."""
    # === RECOVERED ORIGINAL SENDER (COMMENTED OUT) ===========================
    # The recovered module declared this endpoint but did not contain a caller
    # or an authenticated transport implementation:
    # submit_endpoint = "/api/svc/predict/submitPredictByPortfolio"
    # SWITCH (1/2): uncomment this REAL block.
    # SWITCH (2/2): comment the adjacent ACTIVE FIXTURE REJECTION below.
    # if not isinstance(frame, pd.DataFrame):
    #     raise TypeError("send_sog_pl expects a pandas DataFrame")
    # print("success", flush=True)
    # return
    # === ACTIVE FIXTURE REJECTION ============================================
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("send_sog_pl expects a pandas DataFrame")
    raise RuntimeError(
        "External SOG delivery is disabled in fixture mode; "
        "replace feeds.s01_sources.send_sog_pl for an authorized deployment"
    )


def send_portfolio_pl(frame: pd.DataFrame) -> None:
    """Reject external Portfolio delivery while the fixture boundary is active."""
    # === RECOVERED ORIGINAL SENDER (COMMENTED OUT) ===========================
    # SWITCH (1/2): uncomment this REAL block.
    # SWITCH (2/2): comment the adjacent ACTIVE FIXTURE REJECTION below.
    # if not isinstance(frame, pd.DataFrame):
    #     raise TypeError("send_portfolio_pl expects a pandas DataFrame")
    # print("success", flush=True)
    # return
    # === ACTIVE FIXTURE REJECTION ============================================
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("send_portfolio_pl expects a pandas DataFrame")
    raise RuntimeError(
        "External Portfolio delivery is disabled in fixture mode; "
        "replace feeds.s01_sources.send_portfolio_pl for an authorized deployment"
    )


def _get_csv_product_connector_adapters() -> Mapping[str, ProductConnectorAdapter]:
    """Build the per-source views over the active fake CSV loaders.

    Every source gets its own bound callable. Market callables receive the
    ordered, unique Underlyings from validated Risk and intentionally fetch them
    one at a time before returning one all-or-nothing frame. Replace any one
    callable with that function's real API implementation without changing the
    other products.
    """
    adapters: dict[str, ProductConnectorAdapter] = {}
    for source_type in PRODUCT_SPECS_BY_SOURCE_TYPE:

        def risk(
            risk_date: pd.Timestamp, *, _source: str = source_type
        ) -> pd.DataFrame:
            return get_risk(risk_date, _source)

        def market_open(
            market_date: pd.Timestamp,
            underlying: str,
            *,
            market_status: str,
            _source: str = source_type,
        ) -> pd.DataFrame:
            return get_market_open(
                _source, market_date, underlying, market_status=market_status
            )

        def market_status_connector(
            market_date: pd.Timestamp,
            underlying: str,
            *,
            market_status: str,
            _source: str = source_type,
        ) -> pd.DataFrame:
            return get_market_status(
                _source, market_date, underlying, market_status=market_status
            )

        risk.__name__ = f"get_{source_type.replace('/', '_')}_risk"
        market_open.__name__ = f"get_{source_type.replace('/', '_')}_market_open"
        market_status_connector.__name__ = (
            f"get_{source_type.replace('/', '_')}_market_status"
        )
        adapters[source_type] = ProductConnectorAdapter(
            risk=risk,
            market_open=market_open,
            market_status=market_status_connector,
        )
    return adapters


def get_product_connector_adapters() -> Mapping[str, ProductConnectorAdapter]:
    """Select the comment-only REAL adapters or the active CSV fallback."""

    # === REAL PRODUCT REGISTRATION (COMMENTED OUT) ===========================
    # SWITCH (1/2): uncomment the imports near the top of this file and this
    # block after uncommenting the matching builders in adapters/*.py.
    # SWITCH (2/2): comment the one-line ACTIVE CSV FALLBACK return below.
    # adapters = dict(_get_csv_product_connector_adapters())
    # adapters["credit/delta"] = build_credit_delta_adapter()
    # # The original registration referenced this, but no builder body was recovered:
    # # adapters["credit/vega"] = build_credit_vega_adapter()  # unavailable
    #
    # # No private Commodity body was recovered. Keep its CSV entry active.
    # # adapters["commo/delta"] = build_commo_delta_adapter()  # unavailable
    # # adapters["commo/vega"] = build_commo_vega_adapter()  # unavailable
    #
    # adapters["fx/delta"] = build_fx_delta_adapter()
    # adapters["fx/gamma"] = build_fx_gamma_adapter()
    # adapters["fx/vega"] = build_fx_vega_adapter()
    #
    # adapters["ir/delta"], adapters["ir/gamma"] = build_ir_delta_adapter()
    # adapters["ir/xccy"] = build_ir_xccy_adapter()
    # # Listed by the recovered module, but no builder body was recovered:
    # # adapters["ir/xccyvega"] = build_ir_xccyvega_adapter()  # unavailable
    # adapters["ir/basis"] = build_ir_basis_adapter()
    # adapters["ir/inflation"] = build_ir_inflation_adapter()
    # adapters["ir/bond"] = build_ir_bond_adapter()
    # adapters["ir/deltavega"] = build_ir_deltavega_adapter()
    # adapters["ir/inflationvega"] = build_ir_inflationvega_adapter()
    # return adapters
    # === END REAL PRODUCT REGISTRATION =======================================

    # === ACTIVE CSV FALLBACK (COMMENT OUT WHEN REAL IS ENABLED) =============
    return _get_csv_product_connector_adapters()


def build_production_refresh_manager(
    *, stage_delays: Mapping[str, float] | None = None
) -> RiskRefreshManager:
    """Compose Cube from the explicit connector functions over fake datasets.

    Every loader is passed by reference.  Constructing the WSGI app performs no
    source I/O; the browser-triggered initial refresh calls and validates every
    boundary after the refresh shell is visible.
    """
    trading_timezone = (
        os.getenv("CUBE_MARKET_TIMEZONE", "Europe/London").strip() or "Europe/London"
    )

    def resolve_market_state(market_date: pd.Timestamp) -> str:
        return get_market_state(
            market_date,
            trading_timezone=trading_timezone,
        )

    resolve_market_state.__name__ = "get_market_state"
    # ACTIVE RUNTIME REGISTRATION: get_product_connector_adapters performs the
    # explicit, adjacent REAL-versus-CSV switch documented in that function.
    return RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        reported_underlyings=get_reported_underlyings,
        risk_checker_loader=get_risk_checker,
        market_status_resolver=resolve_market_state,
        risk_loader=get_risk,
        cross_gamma_matrix_loader=get_cross_gamma_sensitivities,
        new_trades_loader=get_new_trades,
        market_open_loader=get_market_open,
        market_status_loader=get_market_status,
        connector_adapters=get_product_connector_adapters(),
        stage_delays=stage_delays,
        trading_timezone=trading_timezone,
    )


__all__ = [
    "FAKE_CSV_FILES",
    "FAKE_DATA_DIRECTORY",
    "FakeCsvConnectorError",
    "build_production_refresh_manager",
    "get_cross_gamma_risk",
    "get_cross_gamma_sensitivities",
    "get_colossus_pl",
    "get_market_open",
    "get_market_state",
    "get_market_status",
    "get_new_positions",
    "get_new_position_cash_flows",
    "get_new_trades",
    "get_portfolio_config",
    "get_product_connector_adapters",
    "get_reported_underlyings",
    "get_risk",
    "get_risk_checker",
    "get_risk_thresholds",
    "send_portfolio_pl",
    "send_sog_pl",
]
