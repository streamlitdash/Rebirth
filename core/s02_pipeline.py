"""Reusable pandas pipeline for risk, market move, P&L, and dashboard data.

The runnable app performs data access only through ``feeds.s01_sources.py``.
Functions in this module validate injected DataFrames and calculate results; they
must remain independent of CSV, database, and API implementation details.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from numbers import Real
from pathlib import Path
from threading import Lock, RLock
from typing import Callable, Literal, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from core.s01_schema import (
    PORTFOLIO_COLUMN,
    PORTFOLIO_CONFIG_COLUMNS,
    PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
    PORTFOLIO_FIELDS,
    PORTFOLIO_FIELD_BY_KEY,
    PORTFOLIO_MAPPED_COLUMN,
    PORTFOLIO_METADATA_COLUMNS,
    PORTFOLIO_OPTIONAL_METADATA_COLUMNS,
    PORTFOLIO_POSITION_COLUMNS,
    PORTFOLIO_REPORTING_COLUMNS,
    TENOR_COLUMNS,
    TENOR_OPTION,
    TENOR_OPTION_ORDER,
    TENOR_ORDER_COLUMNS,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
    UNMAPPED_VALUE,
)
from core.s03_search import SearchCatalog, SearchResult, build_search_catalog
from core.s07_reporting import (
    REPORTED_UNDERLYING,
    attach_reported_underlying,
    load_reported_underlying_mapping,
)


LOGGER = logging.getLogger(__name__)

RISK_TYPE = "Risk Type"
SOURCE_TYPE = "Source Type"
RISK_GREEK = "Risk Greek"
SPLIT = "Split"
UNDERLYING = "Underlying"
PORTFOLIO = PORTFOLIO_COLUMN
RISK = "Risk"
DRISK = "dRisk"
OPEN = "Open"
CURRENT = "Current"
LIVE = "Live"
OFFICIAL = "OFFICIAL"
MARKET_STATUS = "Market Status"
MARKET_DATE = "Market Date"
MARKET_MOVE = "Move"
MARKET_AVAILABLE = "Market Available"
MARKET_DATA_STATUS = "Market Data Status"
PL = "PL"
PRODUCT = PORTFOLIO_FIELD_BY_KEY["product"].external_name
PRODUCT_LABELS = {
    value.casefold(): value
    for value in PORTFOLIO_FIELD_BY_KEY["product"].allowed_values
}
CANONICAL_PRODUCTS = frozenset(PRODUCT_LABELS.values())
ACTIVITY = PORTFOLIO_FIELD_BY_KEY["activity"].external_name
SIGNOFF_GROUP = PORTFOLIO_FIELD_BY_KEY["signoffgroup"].external_name
CATEGORY = PORTFOLIO_FIELD_BY_KEY["category"].external_name
SUBCATEGORY = PORTFOLIO_FIELD_BY_KEY["subcategory"].external_name
PORTFOLIO_MAPPED = PORTFOLIO_MAPPED_COLUMN
GROUP = "Group"
REGION = "Region"
DISPLAY_BUCKET = "Display Bucket"
PROMOTION_REASON = "Promotion Reason"
PROMOTION_SCORE = "Promotion Score"
RISK_THRESHOLD = "Risk Threshold"
DRISK_THRESHOLD = "dRisk Threshold"
PL_THRESHOLD = "PL Threshold"
RISK_DATE = "Risk Date"
AGE = "Age"
MMM_FILE = "MMMFile"
EFFECTIVE_RISK_DATE = "Effective Risk Date"
FORCE_RISK = "Force Risk"
CHECKER_DATE = "Checker Date"
SUGGESTED_RISK_DATE = "Suggested Risk Date"
AGE_DEFAULTED = "Age Defaulted"

CREDIT_MEASURES = ("SP01", "PSP01", "PM01", "PM01P", "Theta", "JTD")
CREDIT_MEASURE_COLUMNS = tuple(
    f"{metric} {measure}" for measure in CREDIT_MEASURES for metric in (RISK, DRISK)
)

MarketUnit = Literal["pips", "outright", "vol_points", "bp"]
PlFormula = Literal["absolute", "percentage", "taylor_gamma"]
FrameSource = pd.DataFrame | Callable[[], pd.DataFrame] | None
DataFrameSource = pd.DataFrame | str | Path
GovernanceSource = DataFrameSource | Callable[[], pd.DataFrame]
PortfolioConfigSource = DataFrameSource | Callable[[pd.Timestamp], pd.DataFrame]
ProductSources = Mapping[str, Mapping[str, FrameSource]]
RiskCheckerResult = tuple[pd.DataFrame, pd.DataFrame]
MarketStatusResolver = Callable[[pd.Timestamp], str]
RiskOverlayLoader = Callable[[pd.Timestamp], pd.DataFrame]
RISK_OVERLAY_COLUMNS = (
    SOURCE_TYPE,
    UNDERLYING,
    TENOR_SWAP,
    TENOR_OPTION,
    PORTFOLIO,
    GROUP,
    RISK,
)
FrameName = Literal[
    "risk_status",
    "risk_checker",
    "combined_pl",
    "market_frame",
    "dashboard_frame",
    "unmapped_frame",
]


class ProductMarketConnector(Protocol):
    """One source-bound, one-Underlying market connector."""

    def __call__(
        self,
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame: ...


class GenericMarketConnector(Protocol):
    """Generic market connector whose first argument selects the source."""

    def __call__(
        self,
        source_type: str,
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame: ...


class RefreshInProgressError(RuntimeError):
    """Raised when a refresh writer already owns the manager's refresh gate."""


class StaleRefreshError(RuntimeError):
    """Raised when a caller attempts to refresh from an obsolete revision."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            "refresh revision is stale: "
            f"expected {expected_revision}, current revision is {actual_revision}"
        )


class ProductionIntegrationError(RuntimeError):
    """Raised when a required real-data integration has not been configured."""


@dataclass(frozen=True)
class AxisSpec:
    """One market-owned categorical axis and its authoritative rank column."""

    column: str
    order_column: str


SWAP_AXIS = AxisSpec(TENOR_SWAP, TENOR_SWAP_ORDER)
OPTION_AXIS = AxisSpec(TENOR_OPTION, TENOR_OPTION_ORDER)


@dataclass(frozen=True)
class ProductSpec:
    key: str
    source_type: str
    risk_type: str
    risk_greek: str
    axes: tuple[AxisSpec, ...]
    market_unit: MarketUnit
    pl_formula: PlFormula
    gamma_move_scale: float = 1.0
    gamma_risk_step: float = 1.0

    @property
    def tenor_columns(self) -> list[str]:
        return [axis.column for axis in self.axes]

    @property
    def tenor_order_columns(self) -> list[str]:
        return [axis.order_column for axis in self.axes]

    @property
    def market_keys(self) -> list[str]:
        return [RISK_TYPE, RISK_GREEK, UNDERLYING, *self.tenor_columns]


PRODUCT_SPECS: dict[str, ProductSpec] = {
    "fxdelta": ProductSpec(
        "fxdelta", "fx/delta", "FX", "Delta", (), "pips", "percentage"
    ),
    "fxgamma": ProductSpec(
        "fxgamma", "fx/gamma", "FX", "Gamma", (), "outright", "taylor_gamma"
    ),
    "fxvega": ProductSpec(
        "fxvega", "fx/vega", "FX", "Vega", (SWAP_AXIS,), "vol_points", "absolute"
    ),
    "irdelta": ProductSpec(
        "irdelta", "ir/delta", "IR", "Delta", (SWAP_AXIS,), "bp", "absolute"
    ),
    "irgamma": ProductSpec(
        "irgamma",
        "ir/gamma",
        "IR",
        "Gamma",
        (SWAP_AXIS,),
        "bp",
        "taylor_gamma",
        10_000.0,
        10.0,
    ),
    "irdeltavega": ProductSpec(
        "irdeltavega",
        "ir/deltavega",
        "IR",
        "DeltaVega",
        (SWAP_AXIS, OPTION_AXIS),
        "bp",
        "percentage",
    ),
    "xccy": ProductSpec(
        "xccy", "ir/xccy", "IR", "XCCY", (SWAP_AXIS,), "bp", "absolute"
    ),
    "xccyvega": ProductSpec(
        "xccyvega",
        "ir/xccyvega",
        "IR",
        "XCCYVega",
        (SWAP_AXIS, OPTION_AXIS),
        "bp",
        "percentage",
    ),
    "inflation": ProductSpec(
        "inflation",
        "ir/inflation",
        "IR",
        "Inflation",
        (SWAP_AXIS,),
        "bp",
        "absolute",
    ),
    "inflationvega": ProductSpec(
        "inflationvega",
        "ir/inflationvega",
        "IR",
        "InflationVega",
        (SWAP_AXIS, OPTION_AXIS),
        "bp",
        "percentage",
    ),
    "basis": ProductSpec(
        "basis", "ir/basis", "IR", "Basis", (SWAP_AXIS,), "bp", "absolute"
    ),
    "bond": ProductSpec(
        "bond", "ir/bond", "IR", "Bond", (SWAP_AXIS,), "bp", "absolute"
    ),
    "creditdelta": ProductSpec(
        "creditdelta",
        "credit/delta",
        "Credit",
        "Delta",
        (SWAP_AXIS,),
        "bp",
        "absolute",
    ),
    "creditvega": ProductSpec(
        "creditvega",
        "credit/vega",
        "Credit",
        "Vega",
        (SWAP_AXIS,),
        "bp",
        "absolute",
    ),
    "commodelta": ProductSpec(
        "commodelta",
        "commo/delta",
        "Commo",
        "Delta",
        (SWAP_AXIS,),
        "outright",
        "percentage",
    ),
    "commovega": ProductSpec(
        "commovega",
        "commo/vega",
        "Commo",
        "Vega",
        (SWAP_AXIS,),
        "vol_points",
        "absolute",
    ),
}


def _validate_multiplier(value: object, *, label: str) -> float:
    """Return a finite real multiplier while rejecting bool and string coercion."""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise TypeError(f"{label} must be a non-boolean real number")
    multiplier = float(value)
    if not np.isfinite(multiplier):
        raise ValueError(f"{label} must be finite")
    return multiplier


def _validate_multipliers(
    multipliers: Mapping[str, float] | None,
) -> dict[str, float]:
    """Validate product multiplier keys and values at a public API boundary."""
    if multipliers is None:
        return {}
    if not isinstance(multipliers, Mapping):
        raise TypeError("multipliers must be a mapping of product keys to numbers")
    unknown_keys = sorted(
        set(multipliers) - set(PRODUCT_SPECS), key=lambda key: str(key)
    )
    if unknown_keys:
        raise ValueError(f"unknown product multiplier keys: {unknown_keys}")
    return {
        key: _validate_multiplier(value, label=f"multiplier for {key!r}")
        for key, value in multipliers.items()
    }


@dataclass(frozen=True)
class ProductConnectorAdapter:
    """Product-specific connector hooks for sources with different APIs/shapes.

    Each callable is already bound to its product/source, so FX delta can call a
    completely different service from credit detail while the validated output
    contract below remains common.

    REAL CONNECTOR INTEGRATION POINT
    --------------------------------
    Construct one adapter per source type and pass the mapping to
    ``RiskRefreshManager(connector_adapters=...)``. Every callable must return a
    ``pandas.DataFrame``; returning ``None``, dictionaries, or lists is rejected.

    ``risk(risk_date)``
        ``risk_date`` is a normalized, timezone-naive ``pandas.Timestamp``.
        Return one authoritative position row per product key with columns
        ``Underlying``, the source's required tenor columns, ``Portfolio``,
        connector-owned ``Group``, ``Risk``, and ``dRisk``. ``Group`` passes
        through unchanged: the framework does not classify, normalize, or
        restrict its values. ``Risk Type`` and ``Risk Greek`` may be supplied
        and are checked when present. Credit sources may also return the
        optional ``Risk/ dRisk`` measure columns listed in
        ``CREDIT_MEASURE_COLUMNS``.

    ``market_open(market_date, underlying, *, market_status)``
        ``market_date`` is independent of the risk date. Return unique market
        keys plus numeric ``Open`` and the applicable market-owned tenor-order
        columns. ``underlying`` is one member of the ordered, unique scope from
        validated Risk. The framework calls once per Underlying and concatenates
        only after every call succeeds. An empty
        DataFrame with the correct columns is allowed and means that the opening
        leg is unavailable. ``market_status`` is supplied by the manager and is
        exactly ``Live`` or ``OFFICIAL``; connectors must not infer it again.

    ``market_status(market_date, underlying, *, market_status)``
        Return the same market keys plus numeric ``Current``. ``Market Status`` may
        be supplied, but if present it must exactly match the explicit
        ``market_status`` argument. An empty, correctly shaped frame is allowed
        and is surfaced as unavailable market data, never as zero.

    The normalized market keys are ``Risk Type``, ``Risk Greek``, ``Underlying``,
    and the tenor columns declared by the corresponding ``ProductSpec``. The
    validators below enforce uniqueness, numeric types, finite values, and the
    source-specific identity before anything reaches aggregation or P&L.
    """

    risk: Callable[[pd.Timestamp], pd.DataFrame]
    # Market connectors receive the independently selected market/view date,
    # not the product's (potentially aged) risk date.
    market_open: ProductMarketConnector
    market_status: ProductMarketConnector


PRODUCT_SPECS_BY_SOURCE_TYPE = {
    spec.source_type: spec for spec in PRODUCT_SPECS.values()
}
if len(PRODUCT_SPECS_BY_SOURCE_TYPE) != len(PRODUCT_SPECS):
    raise RuntimeError("Product catalogue Source Type values must be unique")
_PRODUCT_PAIRS = {(spec.risk_type, spec.risk_greek) for spec in PRODUCT_SPECS.values()}
if len(_PRODUCT_PAIRS) != len(PRODUCT_SPECS):
    raise RuntimeError("Product catalogue Risk Type/Risk Greek pairs must be unique")


def _load_frame(
    source: FrameSource,
    *,
    label: str,
    allow_empty: bool = False,
) -> pd.DataFrame:
    if source is None:
        raise ProductionIntegrationError(
            f"{label} requires an explicit real connector DataFrame or callable"
        )
    if isinstance(source, pd.DataFrame):
        frame = source
    elif callable(source):
        frame = source()
    else:
        raise TypeError(
            "A source must be a pandas DataFrame or a zero-argument callable"
        )
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Data source functions must return a pandas DataFrame")
    if frame.empty and not allow_empty:
        raise ValueError("Data source returned an empty DataFrame")
    # Product adapters own any source-specific renaming. The shared boundary is
    # intentionally strict so one column name has one meaning everywhere.
    return frame.copy()


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def _coerce_numeric(
    frame: pd.DataFrame, columns: list[str], label: str
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if result[column].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError(f"{label} column {column!r} must not contain booleans")
        converted = pd.to_numeric(result[column], errors="coerce")
        invalid = converted.isna() | ~np.isfinite(converted)
        if invalid.any():
            rows = result.index[invalid].tolist()[:5]
            raise ValueError(
                f"{label} column {column!r} has missing, non-numeric, or non-finite values at rows {rows}"
            )
        result[column] = converted.astype(float)
    return result


def _validate_market_tenor_orders(
    frame: pd.DataFrame,
    spec: ProductSpec,
    label: str,
) -> pd.DataFrame:
    """Validate market-owned axis order without making it part of quote identity."""
    result = frame.copy()
    for axis in spec.axes:
        tenor_column = axis.column
        order_column = axis.order_column
        _require_columns(result, [order_column], label)
        boolean = result[order_column].map(
            lambda value: isinstance(value, (bool, np.bool_))
        )
        numeric = pd.to_numeric(result[order_column], errors="coerce")
        invalid = boolean | numeric.isna() | ~np.isfinite(numeric)
        invalid |= numeric.lt(0) | numeric.mod(1).ne(0)
        if invalid.any():
            rows = result.index[invalid].tolist()[:5]
            raise ValueError(
                f"{label} column {order_column!r} must contain non-negative "
                f"integer market orders at rows {rows}"
            )
        result[order_column] = numeric.astype("Int64")
        # Within one Source Type (fixed by ProductSpec), each Underlying has one
        # authority for a tenor label and one tenor label at each order position.
        tenor_to_order = result.groupby([UNDERLYING, tenor_column], dropna=False)[
            order_column
        ].nunique(dropna=False)
        if tenor_to_order.gt(1).any():
            raise ValueError(
                f"{label} has conflicting {order_column!r} values per "
                f"Source Type + Underlying + {tenor_column}"
            )
        order_to_tenor = result.groupby([UNDERLYING, order_column], dropna=False)[
            tenor_column
        ].nunique(dropna=False)
        if order_to_tenor.gt(1).any():
            raise ValueError(
                f"{label} maps more than one {tenor_column!r} to the same "
                f"{order_column!r} per Source Type + Underlying"
            )
    return result


def _require_nonblank(
    frame: pd.DataFrame, columns: list[str], label: str
) -> pd.DataFrame:
    """Reject null/blank join keys and normalize their surrounding whitespace."""
    result = frame.copy()
    for column in columns:
        missing = result[column].isna()
        normalized = result[column].astype("string").str.strip()
        invalid = missing | normalized.eq("")
        if invalid.any():
            rows = result.index[invalid].tolist()[:5]
            raise ValueError(
                f"{label} column {column!r} has null or blank keys at rows {rows}"
            )
        result[column] = normalized.astype(str)
    return result


def _enforce_product(
    frame: pd.DataFrame, spec: ProductSpec, label: str
) -> pd.DataFrame:
    result = frame.copy()
    if (
        RISK_TYPE in result
        and not result[RISK_TYPE].dropna().astype(str).eq(spec.risk_type).all()
    ):
        raise ValueError(f"{label} contains a Risk Type other than {spec.risk_type!r}")
    if (
        RISK_GREEK in result
        and not result[RISK_GREEK].dropna().astype(str).eq(spec.risk_greek).all()
    ):
        raise ValueError(
            f"{label} contains a Risk Greek other than {spec.risk_greek!r}"
        )
    result[RISK_TYPE] = spec.risk_type
    result[RISK_GREEK] = spec.risk_greek
    return result


def _as_timestamp(value: date | datetime | str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("date values must not be NaT or blank")
    if timestamp.tzinfo is not None:
        # Preserve the caller's stated calendar day. Production connectors should
        # normalize to their agreed trading timezone before calling this function.
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def checker_date_for(
    market_date: date | datetime | str | pd.Timestamp,
) -> pd.Timestamp:
    """Return the one authoritative prior-business-day checker date."""

    return _as_timestamp(market_date) - pd.offsets.BDay(1)


def risk_date_for(
    checker_date: date | datetime | str | pd.Timestamp,
    age: int,
) -> pd.Timestamp:
    """Apply checker Age to the already-derived checker date."""

    if isinstance(age, (bool, np.bool_)) or not isinstance(age, Real):
        raise TypeError("Age must be a non-negative integer")
    selected_age = float(age)
    if (
        not np.isfinite(selected_age)
        or selected_age < 0
        or not selected_age.is_integer()
    ):
        raise ValueError("Age must be a non-negative integer")
    return _as_timestamp(checker_date) - pd.offsets.BDay(int(selected_age))


def get_risk(
    risk_date: date | datetime | str | pd.Timestamp,
    source_type: str,
) -> pd.DataFrame:
    """Fail-closed generic risk connector boundary.

    ``risk_date`` is the effective T-1/T-2 (or forced) business date and
    ``source_type`` is one of the 16 source contracts, for example ``fx/delta``.
    Return a ``pandas.DataFrame`` containing ``Underlying``, ``Portfolio``,
    opaque connector-owned ``Group``, numeric authoritative ``Risk`` and
    ``dRisk``, plus every tenor column required by that source's
    ``ProductSpec``. ``Risk Type`` and ``Risk Greek`` are optional inputs
    because the common validator adds and enforces them. Credit connectors may
    additionally return columns named by ``CREDIT_MEASURE_COLUMNS``. Rows must
    be unique by underlying, applicable tenors, and portfolio.
    """
    try:
        spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    except KeyError as exc:
        raise ValueError(f"Unknown Source Type {source_type!r}") from exc
    normalized_date = _as_timestamp(risk_date)
    required = [UNDERLYING, *spec.tenor_columns, PORTFOLIO, GROUP, RISK, DRISK]
    # APP DATA ACCESS IS CENTRALIZED IN feeds.s01_sources.get_risk.
    # Direct-library integration shape (comments only, never fallback data):
    # records = risk_client.fetch(source_type=source_type, risk_date=normalized_date)
    # return pd.DataFrame(records)
    raise ProductionIntegrationError(
        f"No real risk connector is configured for {source_type!r} on "
        f"{normalized_date.date()}. Implement get_risk() or inject risk_loader=...; "
        f"required columns are {required}."
    )


def get_market_open(
    source_type: str,
    market_date: date | datetime | str | pd.Timestamp,
    underlying: str,
    *,
    market_status: str,
) -> pd.DataFrame:
    """Fail-closed generic opening-market connector boundary.

    Return one row per source-specific market key for ``market_date`` with a
    finite numeric ``Open``. Required keys are ``Underlying`` plus the tenor
    columns declared by the source's ``ProductSpec``. ``Risk Type`` and
    ``Risk Greek`` may be supplied and will be checked. A genuinely unavailable
    market leg may be represented by an empty DataFrame with the correct schema;
    it must never be replaced with zero-valued quotes. ``market_status`` is the
    manager-selected ``Live`` or ``OFFICIAL`` source and must be used rather than
    independently inferred by the connector.
    """
    try:
        spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    except KeyError as exc:
        raise ValueError(f"Unknown Source Type {source_type!r}") from exc
    selected_date = _as_timestamp(market_date)
    if not isinstance(underlying, str) or not underlying.strip():
        raise ValueError("underlying must be nonblank text")
    selected_status = _require_market_status(market_status)
    required = [UNDERLYING, *spec.tenor_columns, *spec.tenor_order_columns, OPEN]
    # APP DATA ACCESS IS CENTRALIZED IN feeds.s01_sources.get_market_open.
    # Direct-library integration shape (comments only, never fallback data):
    # records = market_client.fetch_open(source_type=source_type, date=selected_date)
    # return pd.DataFrame(records)
    raise ProductionIntegrationError(
        f"No real opening-market connector is configured for {source_type!r} on "
        f"{selected_date.date()}. Implement get_market_open() or inject "
        "market_open_loader=...; the loader also receives the ordered Risk "
        f"Underlying {underlying!r} and Market Status "
        f"{selected_status!r}; required columns are {required}."
    )


def _require_market_status(value: object) -> str:
    """Validate the exact connector routing value before any source I/O."""
    if value not in {LIVE, OFFICIAL}:
        raise ValueError("market_status must be exactly 'Live' or 'OFFICIAL'")
    return str(value)


def get_market_status(
    source_type: str,
    market_date: date | datetime | str | pd.Timestamp,
    underlying: str,
    *,
    market_status: str,
) -> pd.DataFrame:
    """Fail-closed generic current/official market connector boundary.

    ``source_type`` is a source contract and ``market_date`` is the selected view
    date. Return unique market keys plus finite numeric ``Current``. The caller also
    supplies the authoritative ``market_status`` (exactly ``Live`` or
    ``OFFICIAL``), which selects the real upstream source. A returned ``Market
    Status`` column is optional and, when present, must match that input.
    """
    try:
        spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    except KeyError as exc:
        raise ValueError(f"Unknown Source Type {source_type!r}") from exc
    selected_date = _as_timestamp(market_date)
    if not isinstance(underlying, str) or not underlying.strip():
        raise ValueError("underlying must be nonblank text")
    selected_status = _require_market_status(market_status)
    required = [UNDERLYING, *spec.tenor_columns, *spec.tenor_order_columns, CURRENT]
    # APP DATA ACCESS IS CENTRALIZED IN feeds.s01_sources.get_market_status.
    # Direct-library integration shape (comments only, never fallback data):
    # if selected_status == LIVE:
    #     records = market_client.fetch_live(source_type=source_type, date=selected_date)
    # else:
    #     records = market_client.fetch_official(source_type=source_type, date=selected_date)
    # frame = pd.DataFrame(records)
    # frame[MARKET_STATUS] = selected_status
    # return frame
    raise ProductionIntegrationError(
        f"No real {selected_status} market connector is configured for "
        f"{source_type!r} on {selected_date.date()}. Implement get_market_status() "
        f"or inject market_status_loader=...; required columns are {required} "
        f"and optional {MARKET_STATUS!r} must equal {selected_status!r}; the "
        f"loader also receives Risk Underlying {underlying!r}."
    )


def get_product_risk(
    spec: ProductSpec,
    risk_date: date | datetime | str | pd.Timestamp,
    source: FrameSource = None,
) -> pd.DataFrame:
    """Validate one connector snapshot with connector-owned Group, Risk, and dRisk.

    PRODUCTION INTEGRATION POINT: ``source`` may be the connector DataFrame or a
    zero-argument callable already bound to this product and risk date. Prefer a
    ``ProductConnectorAdapter.risk`` when constructing the refresh manager.
    """
    if source is None:
        raise ProductionIntegrationError(
            f"{spec.key} risk requires a real connector source; provide source=... "
            "or configure ProductConnectorAdapter.risk"
        )
    _as_timestamp(risk_date)
    frame = _enforce_product(
        _load_frame(source, label=f"{spec.key} risk"),
        spec,
        f"{spec.key} risk",
    )
    key_columns = [RISK_TYPE, RISK_GREEK, UNDERLYING, *spec.tenor_columns, PORTFOLIO]
    required = [*key_columns, GROUP, RISK, DRISK]
    _require_columns(frame, required, f"{spec.key} risk")
    frame = _require_nonblank(frame, key_columns, f"{spec.key} risk")
    frame = _coerce_numeric(frame, [RISK, DRISK], f"{spec.key} risk")
    credit_measure_columns = [
        column for column in CREDIT_MEASURE_COLUMNS if column in frame
    ]
    if credit_measure_columns and spec.risk_type != "Credit":
        raise ValueError(
            f"{spec.key} risk contains Credit measure columns outside the Credit family"
        )
    if spec.risk_type == "Credit":
        for measure in CREDIT_MEASURES:
            risk_measure = f"{RISK} {measure}"
            drisk_measure = f"{DRISK} {measure}"
            supplied = (risk_measure in frame, drisk_measure in frame)
            if supplied[0] != supplied[1]:
                raise ValueError(
                    f"{spec.key} optional Credit measure {measure!r} must supply "
                    f"both {risk_measure!r} and {drisk_measure!r}, or omit both"
                )
    if credit_measure_columns:
        frame = _coerce_numeric(
            frame,
            credit_measure_columns,
            f"{spec.key} optional Credit measures",
        )
    if REGION in frame and spec.risk_type != "Credit":
        raise ValueError(
            f"{spec.key} risk contains {REGION!r} outside the Credit family"
        )
    position_keys = [*key_columns, *([REGION] if REGION in frame else [])]
    if frame.duplicated(position_keys).any():
        raise ValueError(f"{spec.key} risk has duplicate position keys")
    frame[SPLIT] = "Risk"
    columns = [
        RISK_TYPE,
        RISK_GREEK,
        SPLIT,
        UNDERLYING,
        *spec.tenor_columns,
        PORTFOLIO,
        GROUP,
        *([REGION] if REGION in frame else []),
        RISK,
        DRISK,
        *credit_measure_columns,
    ]
    return frame[columns].copy()


def get_product_market_open(
    spec: ProductSpec,
    market_date: date | datetime | str | pd.Timestamp,
    source: FrameSource,
) -> pd.DataFrame:
    """Validate one product's opening-market connector result.

    PRODUCTION INTEGRATION POINT: pass a date-bound ``source`` here for direct
    use, or inject ``ProductConnectorAdapter.market_open`` into the manager.
    """
    if source is None:
        raise ProductionIntegrationError(
            f"{spec.key} market open requires a real connector source; provide "
            "source=... or configure ProductConnectorAdapter.market_open"
        )
    _as_timestamp(market_date)
    columns = [*spec.market_keys, *spec.tenor_order_columns, OPEN]
    raw_frame = _load_frame(
        source,
        label=f"{spec.key} market open",
        allow_empty=True,
    )
    frame = _enforce_product(raw_frame, spec, f"{spec.key} market open")
    _require_columns(frame, columns, f"{spec.key} market open")
    if frame.empty:
        return frame[columns].copy()
    frame = _require_nonblank(frame, spec.market_keys, f"{spec.key} market open")
    frame = _coerce_numeric(frame, [OPEN], f"{spec.key} market open")
    frame = _validate_market_tenor_orders(frame, spec, f"{spec.key} market open")
    if frame.duplicated(spec.market_keys).any():
        raise ValueError(f"{spec.key} market open has duplicate join keys")
    return frame[columns].copy()


def get_product_market_status(
    spec: ProductSpec,
    market_date: date | datetime | str | pd.Timestamp,
    source: FrameSource,
    *,
    market_status: str,
) -> pd.DataFrame:
    """Validate the Live or OFFICIAL market leg.

    PRODUCTION INTEGRATION POINT: pass a date-bound ``source`` here for direct
    use, or inject ``ProductConnectorAdapter.market_status`` into the manager.
    """
    if source is None:
        raise ProductionIntegrationError(
            f"{spec.key} market status requires a real connector source; provide "
            "source=... or configure ProductConnectorAdapter.market_status"
        )
    _as_timestamp(market_date)
    selected_status = _require_market_status(market_status)
    columns = [
        *spec.market_keys,
        *spec.tenor_order_columns,
        CURRENT,
        MARKET_STATUS,
    ]
    raw_frame = _load_frame(
        source,
        label=f"{spec.key} market status",
        allow_empty=True,
    )
    frame = _enforce_product(raw_frame, spec, f"{spec.key} market status")
    status_was_supplied = MARKET_STATUS in frame
    if not status_was_supplied:
        frame[MARKET_STATUS] = selected_status
    _require_columns(frame, columns, f"{spec.key} current market")
    if frame.empty:
        return frame[columns].copy()
    frame = _require_nonblank(frame, spec.market_keys, f"{spec.key} market status")
    frame = _coerce_numeric(frame, [CURRENT], f"{spec.key} current market")
    frame = _validate_market_tenor_orders(frame, spec, f"{spec.key} market status")
    if status_was_supplied:
        supplied_status = frame[MARKET_STATUS]
        blank_status = supplied_status.isna() | supplied_status.astype(
            "string"
        ).str.strip().eq("")
        if blank_status.any():
            rows = frame.index[blank_status].tolist()[:5]
            raise ValueError(
                f"{spec.key} market status column {MARKET_STATUS!r} "
                f"has null or blank values at rows {rows}"
            )
        exact_status = supplied_status.map(
            lambda value: isinstance(value, str) and value == selected_status
        )
        if not exact_status.all():
            raise ValueError(
                f"{spec.key} market status must be exactly {selected_status!r} "
                "on every supplied row"
            )
    elif (
        not frame[MARKET_STATUS].eq(selected_status).all()
    ):  # pragma: no cover - defensive
        raise ValueError(
            f"{spec.key} generated market status must be exactly {selected_status!r}"
        )
    if frame.duplicated(spec.market_keys).any():
        raise ValueError(f"{spec.key} current market has duplicate join keys")
    return frame[columns].copy()


def _merge_validated_market_legs(
    spec: ProductSpec,
    market_open: pd.DataFrame,
    market_status: pd.DataFrame,
    *,
    selected_status: str,
) -> pd.DataFrame:
    """Merge quote legs and reconcile their shared market-owned axis authority."""
    selected_status = _require_market_status(selected_status)
    for axis in spec.axes:
        tenor_column = axis.column
        order_column = axis.order_column
        open_authority = market_open[
            [UNDERLYING, tenor_column, order_column]
        ].drop_duplicates()
        status_authority = market_status[
            [UNDERLYING, tenor_column, order_column]
        ].drop_duplicates()
        authority = open_authority.merge(
            status_authority,
            on=[UNDERLYING, tenor_column],
            how="inner",
            suffixes=("_open", "_status"),
            validate="one_to_one",
        )
        open_order = f"{order_column}_open"
        status_order = f"{order_column}_status"
        if not authority[open_order].eq(authority[status_order]).all():
            raise ValueError(
                f"{spec.key} market Open and Status disagree on {order_column!r} "
                f"per Source Type + Underlying + {tenor_column}"
            )

    market = market_open.merge(
        market_status,
        on=spec.market_keys,
        how="outer",
        validate="one_to_one",
        indicator=True,
        suffixes=("_open", "_status"),
    )
    # Order fields describe display authority, not quote identity.  Compare the
    # two legs above, then coalesce them after joining only on canonical keys.
    for order_column in spec.tenor_order_columns:
        open_order = f"{order_column}_open"
        status_order = f"{order_column}_status"
        market[order_column] = (
            market[open_order].combine_first(market[status_order]).astype("Int64")
        )
        market = market.drop(columns=[open_order, status_order])
    # Validate the coalesced union as well. Two disjoint legs must not be able
    # to assign the same order to different labels for one Underlying.
    market = _validate_market_tenor_orders(market, spec, f"{spec.key} merged market")
    # Status is routing metadata for the complete MarketBook, not merely a
    # property of rows returned by the Current leg. Open-only rows therefore
    # still state which source was selected for the missing Current quote.
    market[MARKET_STATUS] = selected_status
    market[MARKET_AVAILABLE] = (
        market["_merge"].eq("both") & market[OPEN].notna() & market[CURRENT].notna()
    )
    market[MARKET_DATA_STATUS] = np.select(
        [
            market[MARKET_AVAILABLE],
            market[OPEN].isna() & market[CURRENT].isna(),
            market[OPEN].isna(),
            market[CURRENT].isna(),
        ],
        [
            "Available",
            "Missing Open and Current (Live/OFFICIAL)",
            "Missing Open",
            "Missing Current (Live/OFFICIAL)",
        ],
        default="Incomplete market data",
    )
    market = market.drop(columns="_merge")
    market[MARKET_MOVE] = market[CURRENT] - market[OPEN]
    return market


def get_product_market(
    spec: ProductSpec,
    market_date: date | datetime | str | pd.Timestamp,
    open_source: FrameSource,
    status_source: FrameSource,
    *,
    market_status: str,
) -> pd.DataFrame:
    """Outer-join validated opening and Live/OFFICIAL connector legs.

    PRODUCTION INTEGRATION POINT: ``open_source`` and ``status_source`` accept
    connector DataFrames or zero-argument, date-bound callables. In the managed
    path they are populated from the configured adapter/generic loaders.
    """
    selected_date = _as_timestamp(market_date)
    selected_status = _require_market_status(market_status)
    market_open = get_product_market_open(spec, selected_date, open_source)
    market_current = get_product_market_status(
        spec,
        selected_date,
        status_source,
        market_status=selected_status,
    )
    return _merge_validated_market_legs(
        spec,
        market_open,
        market_current,
        selected_status=selected_status,
    )


def _raw_market_move(frame: pd.DataFrame) -> pd.Series:
    """Return the unscaled market move used by all P&L strategies."""
    return frame[CURRENT] - frame[OPEN]


def _pnl_move(spec: ProductSpec, frame: pd.DataFrame) -> pd.Series:
    """Apply the product's configured absolute or percentage move convention."""
    raw_move = _raw_market_move(frame)
    if spec.pl_formula == "percentage":
        nonzero_open = frame[OPEN].where(frame[OPEN].ne(0.0))
        return raw_move / nonzero_open
    return raw_move


def get_product_pl(
    spec: ProductSpec,
    risk_date: date | datetime | str | pd.Timestamp,
    risk_source: FrameSource = None,
    open_source: FrameSource = None,
    status_source: FrameSource = None,
    multiplier: float = 1.0,
    validated_risk: pd.DataFrame | None = None,
    validated_market: pd.DataFrame | None = None,
    *,
    market_date: date | datetime | str | pd.Timestamp,
    market_status: str,
) -> pd.DataFrame:
    """Join authoritative risk to market and apply the product P&L contract.

    PRODUCTION INTEGRATION POINT: direct callers inject ``risk_source``,
    ``open_source``, and ``status_source``; managed callers should configure the
    corresponding ``RiskRefreshManager`` loaders/adapters once. ``validated_risk``
    is for the manager's already-validated cache and is not a second connector.
    """
    selected_market_status = _require_market_status(market_status)
    validated_multiplier = _validate_multiplier(
        multiplier, label=f"multiplier for {spec.key!r}"
    )
    if validated_risk is not None:
        if risk_source is not None:
            raise ValueError("validated_risk cannot be combined with a raw risk source")
        risk = validated_risk.copy()
    else:
        risk = get_product_risk(spec, risk_date, risk_source)
    if validated_market is not None:
        if open_source is not None or status_source is not None:
            raise ValueError(
                "validated_market cannot be combined with raw market sources"
            )
        market = validated_market.copy()
    else:
        market = get_product_market(
            spec,
            market_date,
            open_source,
            status_source,
            market_status=selected_market_status,
        )
    result = risk.merge(
        market,
        on=spec.market_keys,
        how="left",
        validate="many_to_one",
        indicator="_market_merge",
    )
    no_market_row = result["_market_merge"].ne("both")
    result[MARKET_AVAILABLE] = result[MARKET_AVAILABLE].fillna(False).astype(bool)
    result.loc[no_market_row, MARKET_DATA_STATUS] = "No matching market row"
    result[MARKET_DATA_STATUS] = result[MARKET_DATA_STATUS].fillna(
        "No matching market row"
    )
    result = result.drop(columns="_market_merge")
    sort_columns = [UNDERLYING, *spec.tenor_order_columns, PORTFOLIO]
    result = result.sort_values(
        sort_columns,
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)
    pnl_move = _pnl_move(spec, result)
    invalid_percentage_open = (
        spec.pl_formula == "percentage" and result[OPEN].eq(0.0).any()
    )
    if invalid_percentage_open:
        zero_open = result[OPEN].eq(0.0)
        result.loc[zero_open, MARKET_DATA_STATUS] = (
            "Open is zero; percentage P&L unavailable"
        )
    if spec.pl_formula != "taylor_gamma":
        result[PL] = result[RISK] * pnl_move * validated_multiplier
        result.loc[~result[MARKET_AVAILABLE], PL] = np.nan
        result[SPLIT] = "Risk"
        return result

    # Keep the Taylor P&L on the sourced Gamma/Risk position. The generated
    # Delta/Gamma row represents delta exposure only and therefore has no P&L.
    sourced = result.copy()
    sourced[SPLIT] = "Risk"
    raw_move = _raw_market_move(result)
    # Product metadata owns quote scaling and the development step. This keeps
    # the calculation generic and avoids product-name branches in the engine.
    taylor_move = raw_move * spec.gamma_move_scale
    developed_risk = result[RISK] * taylor_move / spec.gamma_risk_step
    sourced[PL] = 0.5 * developed_risk * taylor_move * validated_multiplier
    sourced.loc[~sourced[MARKET_AVAILABLE], PL] = np.nan

    # A developed Delta exists only when both market legs exist. Retain the
    # authoritative sourced Gamma row when market is missing, but do not emit a
    # placeholder derived row whose exposure cannot actually be calculated.
    derived = result.loc[result[MARKET_AVAILABLE]].copy()
    derived_developed_risk = developed_risk.loc[derived.index]
    derived[RISK_GREEK] = "Delta"
    derived[SPLIT] = "Gamma"
    derived[RISK] = derived_developed_risk
    # The derived Delta is a point-in-time development of Gamma.  There is no
    # connector-sourced prior-day Delta here, so manufacturing dRisk would be
    # misleading.  The sourced Gamma/Risk row above retains authoritative dRisk.
    derived[DRISK] = np.nan
    derived[PL] = 0.0
    combined = pd.concat([sourced, derived], ignore_index=True, sort=False)
    combined["__split_order__"] = combined[SPLIT].map({"Risk": 0, "Gamma": 1})
    combined = combined.sort_values(
        [UNDERLYING, *spec.tenor_order_columns, PORTFOLIO, "__split_order__"],
        kind="stable",
        na_position="last",
    )
    return combined.drop(columns="__split_order__").reset_index(drop=True)


def _with_dashboard_tenors(frame: pd.DataFrame, spec: ProductSpec) -> pd.DataFrame:
    result = frame.copy()
    if TENOR_SWAP not in result:
        result[TENOR_SWAP] = "Spot" if spec.key == "fxdelta" else "N/A"
    if TENOR_OPTION not in result:
        result[TENOR_OPTION] = "N/A"
    # Authority is supplied by market connectors. Risk-only unmatched rows keep
    # nullable orders so the presentation layer can apply a documented fallback
    # without pretending that Risk owned the ordering.
    for order_column in TENOR_ORDER_COLUMNS:
        if order_column not in result:
            result[order_column] = pd.Series(pd.NA, index=result.index, dtype="Int64")
        else:
            result[order_column] = pd.to_numeric(
                result[order_column], errors="raise"
            ).astype("Int64")
    return result


def build_risk_overlay(
    source: pd.DataFrame,
    *,
    split: str,
    market_frames: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Convert already-calculated target risks into normal Cube rows.

    ``Source Type`` selects an existing product contract, so overlays reuse the
    validated target product's identity and market shape.  Version one keeps
    P&L at zero: Cross Gamma/New Position are supplied target exposures, not a
    second independent P&L engine.
    """

    if not isinstance(source, pd.DataFrame):
        raise TypeError("risk overlay loader must return a pandas DataFrame")
    if not isinstance(split, str) or not split.strip():
        raise ValueError("risk overlay split must be nonblank text")
    _require_columns(source, list(RISK_OVERLAY_COLUMNS), f"{split} risk overlay")
    if source.empty:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for raw_source_type, source_rows in source.groupby(
        SOURCE_TYPE, sort=False, dropna=False
    ):
        source_type = str(raw_source_type)
        try:
            spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
        except KeyError as exc:
            raise ValueError(
                f"Unknown Source Type {source_type!r} in {split} risk overlay"
            ) from exc

        overlay = source_rows[
            [UNDERLYING, *spec.tenor_columns, PORTFOLIO, GROUP, RISK]
        ].copy()
        overlay = _require_nonblank(
            overlay,
            [UNDERLYING, *spec.tenor_columns, PORTFOLIO],
            f"{split} risk overlay",
        )
        overlay = _coerce_numeric(overlay, [RISK], f"{split} risk overlay")
        overlay[RISK_TYPE] = spec.risk_type
        overlay[RISK_GREEK] = spec.risk_greek
        overlay[SPLIT] = split.strip()
        overlay[DRISK] = np.nan

        market_columns = [
            *spec.market_keys,
            *spec.tenor_order_columns,
            OPEN,
            CURRENT,
            MARKET_STATUS,
            MARKET_MOVE,
            MARKET_AVAILABLE,
            MARKET_DATA_STATUS,
        ]
        market = market_frames.get(source_type)
        if market is None:
            market = pd.DataFrame(columns=market_columns)
        else:
            market = market[market_columns].copy()

        result = overlay.merge(
            market,
            on=spec.market_keys,
            how="left",
            validate="many_to_one",
            indicator="_market_merge",
        )
        no_market_row = result["_market_merge"].ne("both")
        result[MARKET_AVAILABLE] = result[MARKET_AVAILABLE].fillna(False).astype(bool)
        result.loc[no_market_row, MARKET_DATA_STATUS] = "No matching market row"
        result[MARKET_DATA_STATUS] = result[MARKET_DATA_STATUS].fillna(
            "No matching market row"
        )
        result = result.drop(columns="_market_merge")
        result[PL] = 0.0
        result[SOURCE_TYPE] = source_type
        result = _with_dashboard_tenors(result, spec)
        result = result.sort_values(
            [UNDERLYING, *spec.tenor_order_columns, PORTFOLIO],
            kind="stable",
            na_position="last",
        ).reset_index(drop=True)
        frames.append(result)

    return pd.concat(frames, ignore_index=True, sort=False)


def build_all_pl(
    product_sources: ProductSources | None = None,
    multipliers: Mapping[str, float] | None = None,
    risk_dates: Mapping[str, date | datetime | str | pd.Timestamp] | None = None,
    *,
    market_date: date | datetime | str | pd.Timestamp,
    market_status: str,
) -> pd.DataFrame:
    """Build validated P&L rows for every supported source contract.

    REAL CONNECTOR INTEGRATION POINT: for a one-shot build, populate
    ``product_sources[product_key]`` with ``risk``, ``open``, and ``status``
    DataFrames or date-bound callables, and supply explicit ``risk_dates``.
    Long-running applications should prefer ``RiskRefreshManager`` adapters.
    """
    if product_sources is None:
        raise ProductionIntegrationError(
            "build_all_pl requires real product_sources for all supported products"
        )
    if risk_dates is None:
        raise ProductionIntegrationError(
            "build_all_pl requires explicit risk_dates keyed by source type"
        )
    unknown_products = sorted(set(product_sources) - set(PRODUCT_SPECS))
    if unknown_products:
        raise ValueError(f"Unknown product source keys: {unknown_products}")
    missing_products = sorted(set(PRODUCT_SPECS) - set(product_sources))
    if missing_products:
        raise ProductionIntegrationError(
            f"Real connector sources are missing for products: {missing_products}"
        )
    selected_market_status = _require_market_status(market_status)
    validated_multipliers = _validate_multipliers(multipliers)
    selected_dates = dict(risk_dates)
    frames: list[pd.DataFrame] = []
    for key, spec in PRODUCT_SPECS.items():
        sources = product_sources[key]
        if not isinstance(sources, Mapping):
            raise TypeError(f"product_sources[{key!r}] must be a mapping")
        missing_legs = [
            leg
            for leg, available in (
                ("risk", sources.get("risk") is not None),
                ("open", sources.get("open") is not None),
                ("status", sources.get("status") is not None),
            )
            if not available
        ]
        if missing_legs:
            raise ProductionIntegrationError(
                f"product_sources[{key!r}] is missing real connector legs: "
                f"{missing_legs}"
            )
        if spec.source_type not in selected_dates:
            raise ValueError(f"No risk date supplied for {spec.source_type!r}")
        frame = get_product_pl(
            spec,
            selected_dates[spec.source_type],
            risk_source=sources.get("risk"),
            open_source=sources.get("open"),
            status_source=sources.get("status"),
            multiplier=validated_multipliers.get(key, 1.0),
            market_date=market_date,
            market_status=selected_market_status,
        )
        frame[SOURCE_TYPE] = spec.source_type
        frame[RISK_DATE] = _as_timestamp(selected_dates[spec.source_type])
        frames.append(_with_dashboard_tenors(frame, spec))
    return pd.concat(frames, ignore_index=True, sort=False)


def _load_governance_source(
    source: GovernanceSource,
    *,
    label: str,
) -> pd.DataFrame | str | Path:
    """Resolve a zero-argument loader only when a refresh actually needs it."""
    resolved = source() if callable(source) else source
    if not isinstance(resolved, (pd.DataFrame, str, Path)):
        raise TypeError(f"{label} loader must return a DataFrame or CSV path")
    return resolved


def load_config(source: DataFrameSource) -> pd.DataFrame:
    frame = (
        pd.read_csv(source, dtype={PORTFOLIO: "string"}, keep_default_na=False)
        if isinstance(source, (str, Path))
        else source
    )
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("config must be a DataFrame or CSV path")
    # Connector boundaries use one canonical vocabulary. Source-specific
    # renaming belongs inside the connector, never in the shared pipeline.
    result = frame.copy()
    required_columns = list(PORTFOLIO_CONFIG_REQUIRED_COLUMNS)
    _require_columns(result, required_columns, "config")
    for column in PORTFOLIO_OPTIONAL_METADATA_COLUMNS:
        if column not in result:
            result[column] = next(
                field.default_value
                for field in PORTFOLIO_FIELDS
                if field.external_name == column
            )
    columns = list(PORTFOLIO_CONFIG_COLUMNS)
    result = _require_nonblank(result, columns, "config")
    product_labels = result[PRODUCT].str.casefold()
    invalid_product = ~product_labels.isin(PRODUCT_LABELS)
    if invalid_product.any():
        rows = result.index[invalid_product].tolist()[:5]
        raise ValueError(
            f"config Product must contain only 'XVA' or 'Hedges'; invalid rows {rows}"
        )
    result[PRODUCT] = product_labels.map(PRODUCT_LABELS)
    reserved_columns = [
        column for column in PORTFOLIO_METADATA_COLUMNS if column != PRODUCT
    ]
    reserved = result[reserved_columns].apply(
        lambda column: column.str.casefold().eq(UNMAPPED_VALUE.casefold())
    )
    if reserved.any().any():
        raise ValueError("config metadata must not use the reserved value 'Unmapped'")
    if result.duplicated(PORTFOLIO).any():
        duplicates = (
            result.loc[result.duplicated(PORTFOLIO, keep=False), PORTFOLIO]
            .unique()
            .tolist()
        )
        raise ValueError(f"config contains duplicate portfolios: {duplicates}")
    return result[columns].copy()


def _load_portfolio_config(
    source: PortfolioConfigSource,
    portfolio_date: pd.Timestamp,
) -> pd.DataFrame:
    """Call the dated Portfolio connector, then validate its canonical frame."""
    resolved = source(portfolio_date) if callable(source) else source
    if not isinstance(resolved, (pd.DataFrame, str, Path)):
        raise TypeError("portfolio config loader must return a DataFrame or CSV path")
    return load_config(resolved)


def load_thresholds(source: GovernanceSource) -> pd.DataFrame:
    source = _load_governance_source(source, label="thresholds")
    frame = pd.read_csv(source) if isinstance(source, (str, Path)) else source
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("thresholds must be a DataFrame or CSV path")
    result = frame.copy()
    columns = [RISK_TYPE, RISK_GREEK, RISK_THRESHOLD, DRISK_THRESHOLD, PL_THRESHOLD]
    metric_columns = [RISK_TYPE, RISK_GREEK, PL, RISK, DRISK]
    if list(result.columns) != metric_columns:
        raise ValueError(
            f"risk thresholds columns must be exactly {metric_columns} in that "
            f"order; found {list(result.columns)}"
        )
    # The external file mirrors metric names. Explicit internal suffixes keep
    # those governance values distinct when they are joined to position data.
    result = result.rename(
        columns={RISK: RISK_THRESHOLD, DRISK: DRISK_THRESHOLD, PL: PL_THRESHOLD}
    )
    result = _require_nonblank(result, [RISK_TYPE, RISK_GREEK], "risk thresholds")
    if result.duplicated([RISK_TYPE, RISK_GREEK]).any():
        raise ValueError(
            "risk thresholds must contain unique Risk Type + Risk Greek rows"
        )
    result = _coerce_numeric(
        result, [RISK_THRESHOLD, DRISK_THRESHOLD, PL_THRESHOLD], "risk thresholds"
    )
    if (result[[RISK_THRESHOLD, DRISK_THRESHOLD, PL_THRESHOLD]] <= 0).any().any():
        raise ValueError("risk thresholds must be greater than zero")
    return result[columns].copy()


def load_reported_underlyings(
    source: GovernanceSource | None,
) -> pd.DataFrame:
    """Resolve and validate the optional cross-product reporting map lazily."""

    if source is None:
        return load_reported_underlying_mapping(
            None,
            allowed_pairs=_PRODUCT_PAIRS,
        )
    resolved = _load_governance_source(source, label="Reported Underlying mapping")
    return load_reported_underlying_mapping(
        resolved,
        allowed_pairs=_PRODUCT_PAIRS,
    )


def _apply_validated_thresholds(
    frame: pd.DataFrame,
    threshold_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Apply thresholds at the governed reporting-underlying grain."""
    result = frame.copy()
    _require_columns(result, [GROUP], "configured P&L")
    if REPORTED_UNDERLYING not in result:
        result[REPORTED_UNDERLYING] = result[UNDERLYING]
    required_pairs = result[[RISK_TYPE, RISK_GREEK]].drop_duplicates()
    missing = required_pairs.merge(
        threshold_frame[[RISK_TYPE, RISK_GREEK]],
        on=[RISK_TYPE, RISK_GREEK],
        how="left",
        indicator=True,
    )
    if missing["_merge"].ne("both").any():
        records = missing.loc[
            missing["_merge"].ne("both"), [RISK_TYPE, RISK_GREEK]
        ].to_dict("records")
        raise ValueError(
            f"risk thresholds are missing Risk Type + Risk Greek rows: {records}"
        )

    keys = [RISK_TYPE, RISK_GREEK, REPORTED_UNDERLYING]
    _require_columns(result, [PORTFOLIO_MAPPED], "configured P&L")
    mapped = result.loc[result[PORTFOLIO_MAPPED].eq(True)]
    aggregate = mapped.groupby(keys, as_index=False)[[RISK, DRISK, PL]].sum(min_count=1)
    aggregate = aggregate.merge(
        threshold_frame, on=[RISK_TYPE, RISK_GREEK], how="left", validate="many_to_one"
    )
    risk_ratio = aggregate[RISK].abs() / aggregate[RISK_THRESHOLD]
    drisk_ratio = aggregate[DRISK].abs() / aggregate[DRISK_THRESHOLD]
    pl_ratio = aggregate[PL].abs() / aggregate[PL_THRESHOLD]
    aggregate[PROMOTION_SCORE] = pd.concat(
        [risk_ratio, drisk_ratio, pl_ratio], axis=1
    ).max(axis=1)
    aggregate[PROMOTION_REASON] = [
        ", ".join(
            reason
            for reason, breached in (
                ("Big Risk", risk_value >= 1.0),
                ("Big dRisk", drisk_value >= 1.0),
                ("Big PL", pl_value >= 1.0),
            )
            if breached
        )
        for risk_value, drisk_value, pl_value in zip(risk_ratio, drisk_ratio, pl_ratio)
    ]
    aggregate[DISPLAY_BUCKET] = np.where(
        aggregate[PROMOTION_REASON].ne(""),
        aggregate[REPORTED_UNDERLYING],
        "Other",
    )
    enrichment = aggregate[
        keys
        + [
            DISPLAY_BUCKET,
            PROMOTION_REASON,
            PROMOTION_SCORE,
            RISK_THRESHOLD,
            DRISK_THRESHOLD,
            PL_THRESHOLD,
        ]
    ]
    result = result.merge(enrichment, on=keys, how="left", validate="many_to_one")
    result[DISPLAY_BUCKET] = result[DISPLAY_BUCKET].fillna("Other")
    result[PROMOTION_REASON] = result[PROMOTION_REASON].fillna("")
    result[PROMOTION_SCORE] = result[PROMOTION_SCORE].fillna(0.0)
    return result


def evaluate_promotions(
    frame: pd.DataFrame,
    threshold_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Re-evaluate promotion on a position subset using validated thresholds.

    This is the supplied pure boundary used when the UI wants promotion to
    follow an already-filtered position set.  External threshold files still go
    through :func:`apply_thresholds`; this function deliberately requires the
    internal ``... Threshold`` columns so the two contracts cannot be confused.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("promotion input must be a pandas DataFrame")
    if not isinstance(threshold_frame, pd.DataFrame):
        raise TypeError("validated thresholds must be a pandas DataFrame")
    required_threshold_columns = [
        RISK_TYPE,
        RISK_GREEK,
        RISK_THRESHOLD,
        DRISK_THRESHOLD,
        PL_THRESHOLD,
    ]
    _require_columns(
        threshold_frame,
        required_threshold_columns,
        "validated thresholds",
    )
    return _apply_validated_thresholds(frame, threshold_frame)


def apply_thresholds(
    frame: pd.DataFrame,
    thresholds: pd.DataFrame | str | Path,
) -> pd.DataFrame:
    """Validate external thresholds once, then apply exposure promotion rules."""

    return _apply_validated_thresholds(frame, load_thresholds(thresholds))


def _merge_validated_config(
    pl_frame: pd.DataFrame,
    validated_config: pd.DataFrame,
) -> pd.DataFrame:
    """Join one already-validated internal Portfolio mapping."""

    result = pl_frame.copy()
    result = _require_nonblank(result, [PORTFOLIO], "P&L")
    overlap = sorted(
        set(result.columns) & set((*PORTFOLIO_METADATA_COLUMNS, PORTFOLIO_MAPPED))
    )
    if overlap:
        raise ValueError(
            f"P&L already contains portfolio-config-owned columns: {overlap}"
        )
    result = result.merge(
        validated_config,
        on=PORTFOLIO,
        how="left",
        validate="many_to_one",
        indicator="_config_merge",
    )
    unmapped = result["_config_merge"].ne("both")
    result[PORTFOLIO_MAPPED] = ~unmapped
    result.loc[unmapped, list(PORTFOLIO_METADATA_COLUMNS)] = UNMAPPED_VALUE
    return result.drop(columns="_config_merge")


def merge_config(
    pl_frame: pd.DataFrame, config: pd.DataFrame | str | Path
) -> pd.DataFrame:
    """Validate an external Portfolio mapping once, then join it to P&L."""

    return _merge_validated_config(pl_frame, load_config(config))


def to_dashboard_frame(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(
        frame,
        [
            SOURCE_TYPE,
            RISK_TYPE,
            RISK_GREEK,
            SPLIT,
            *PORTFOLIO_METADATA_COLUMNS,
            PORTFOLIO_MAPPED,
            DISPLAY_BUCKET,
            GROUP,
            UNDERLYING,
            REPORTED_UNDERLYING,
            *TENOR_COLUMNS,
            *TENOR_ORDER_COLUMNS,
            PORTFOLIO,
            RISK,
            DRISK,
            OPEN,
            CURRENT,
            PL,
            MARKET_MOVE,
            MARKET_AVAILABLE,
            MARKET_DATA_STATUS,
            PROMOTION_REASON,
            PROMOTION_SCORE,
            RISK_THRESHOLD,
            DRISK_THRESHOLD,
            PL_THRESHOLD,
        ],
        "combined P&L",
    )
    columns = [
        SOURCE_TYPE,
        RISK_TYPE,
        RISK_GREEK,
        SPLIT,
        *PORTFOLIO_POSITION_COLUMNS,
        DISPLAY_BUCKET,
        GROUP,
        *([REGION] if REGION in frame else []),
        REPORTED_UNDERLYING,
        UNDERLYING,
        *TENOR_COLUMNS,
        *TENOR_ORDER_COLUMNS,
        PORTFOLIO,
        *PORTFOLIO_REPORTING_COLUMNS,
        PORTFOLIO_MAPPED,
        PROMOTION_REASON,
        PROMOTION_SCORE,
        RISK_THRESHOLD,
        DRISK_THRESHOLD,
        PL_THRESHOLD,
        RISK,
        DRISK,
        OPEN,
        CURRENT,
        PL,
        MARKET_MOVE,
        MARKET_AVAILABLE,
        MARKET_DATA_STATUS,
        *[column for column in CREDIT_MEASURE_COLUMNS if column in frame],
    ]
    return frame[columns].copy()


def _validate_dashboard_release(frame: pd.DataFrame) -> None:
    """Reject pipeline-owned invariant failures before an atomic cache commit.

    This is deliberately narrower than ``ui.s03_aggregate.prepare_risk_data``: the UI
    remains responsible for display normalization and derived breakdown columns.
    The pipeline owns the authoritative numeric values, canonical Product
    partition, market identity, and nonblank aggregation keys checked here.
    Keeping this guard in this module avoids a pipeline-to-UI import cycle.
    """
    if not isinstance(frame, pd.DataFrame):  # pragma: no cover - internal contract
        raise TypeError("dashboard release must be a pandas DataFrame")
    if frame.empty:
        raise ValueError("dashboard release must contain at least one mapped row")

    grouping_columns = [
        SOURCE_TYPE,
        RISK_TYPE,
        RISK_GREEK,
        SPLIT,
        *PORTFOLIO_POSITION_COLUMNS,
        DISPLAY_BUCKET,
        GROUP,
        REPORTED_UNDERLYING,
        UNDERLYING,
        *TENOR_COLUMNS,
        *PORTFOLIO_REPORTING_COLUMNS,
    ]
    threshold_columns = [RISK_THRESHOLD, DRISK_THRESHOLD, PL_THRESHOLD]
    numeric_columns = [
        RISK,
        DRISK,
        OPEN,
        CURRENT,
        PL,
        MARKET_MOVE,
        PROMOTION_SCORE,
        *threshold_columns,
    ]
    required_columns = [
        *grouping_columns,
        *TENOR_ORDER_COLUMNS,
        *numeric_columns,
        PORTFOLIO_MAPPED,
        MARKET_AVAILABLE,
        MARKET_DATA_STATUS,
    ]
    missing = [column for column in required_columns if column not in frame]
    if missing:
        raise ValueError(f"dashboard release is missing required columns: {missing}")
    duplicates = frame.columns[frame.columns.duplicated()].unique().tolist()
    if duplicates:
        raise ValueError(f"dashboard release contains duplicate columns: {duplicates}")

    for order_column in TENOR_ORDER_COLUMNS:
        values = frame[order_column]
        boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
        numeric = pd.to_numeric(values, errors="coerce")
        nonblank = values.notna() & values.astype("string").str.strip().ne("")
        invalid = boolean | (nonblank & numeric.isna())
        invalid |= numeric.notna() & (
            ~np.isfinite(numeric) | numeric.lt(0) | numeric.mod(1).ne(0)
        )
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"dashboard release {order_column!r} must be nullable or a "
                f"non-negative integer at rows {rows}"
            )

    # Group is opaque connector-owned metadata. Its column is structurally
    # required above, but the framework deliberately applies no value,
    # taxonomy, type, blank, or allow-list validation to it.
    validated_text_keys = [column for column in grouping_columns if column != GROUP]
    for column in validated_text_keys:
        values = frame[column]
        invalid = values.isna() | ~values.map(lambda value: isinstance(value, str))
        invalid |= values.astype("string").str.strip().eq("")
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"dashboard release column {column!r} has blank or non-text keys "
                f"at rows {rows}"
            )

    mapped_values = frame[PORTFOLIO_MAPPED]
    invalid_mapped = ~mapped_values.map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    if invalid_mapped.any():
        rows = frame.index[invalid_mapped].tolist()[:5]
        raise ValueError(
            f"dashboard release {PORTFOLIO_MAPPED!r} must be boolean at rows {rows}"
        )
    if not mapped_values.astype(bool).all():
        rows = frame.index[~mapped_values.astype(bool)].tolist()[:5]
        raise ValueError(
            f"dashboard release contains unmapped portfolio rows at {rows}"
        )

    invalid_product = ~frame[PRODUCT].isin(CANONICAL_PRODUCTS)
    if invalid_product.any():
        rows = frame.index[invalid_product].tolist()[:5]
        raise ValueError(
            "dashboard release Product must be exactly 'XVA' or 'Hedges'; "
            f"invalid rows {rows}"
        )
    # Product is the disjoint partition from which the UI derives each
    # Risk/dRisk/P&L XVA + Hedges breakdown. Canonical labels here are therefore
    # the aggregate identity that can be proven before UI-side derivation.

    optional_credit_columns = [
        column for column in CREDIT_MEASURE_COLUMNS if column in frame
    ]
    converted: dict[str, pd.Series] = {}
    for column in [*numeric_columns, *optional_credit_columns]:
        values = frame[column]
        boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
        numeric = pd.to_numeric(values, errors="coerce")
        blank = values.isna() | values.astype("string").str.strip().eq("")
        invalid = boolean | (~blank & numeric.isna())
        invalid |= numeric.notna() & ~np.isfinite(numeric)
        if column == RISK or column in threshold_columns:
            invalid |= numeric.isna()
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"dashboard release column {column!r} contains missing, non-numeric, "
                f"boolean, or non-finite values at rows {rows}"
            )
        converted[column] = numeric

    for column in threshold_columns:
        invalid_threshold = converted[column].le(0)
        if invalid_threshold.any():
            rows = frame.index[invalid_threshold].tolist()[:5]
            raise ValueError(
                f"dashboard release column {column!r} must be greater than zero "
                f"at rows {rows}"
            )

    availability = frame[MARKET_AVAILABLE]
    invalid_availability = ~availability.map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    if invalid_availability.any():
        rows = frame.index[invalid_availability].tolist()[:5]
        raise ValueError(
            f"dashboard release {MARKET_AVAILABLE!r} must be boolean at rows {rows}"
        )
    complete_quotes = converted[OPEN].notna() & converted[CURRENT].notna()
    availability_mismatch = availability.astype(bool).ne(complete_quotes)
    if availability_mismatch.any():
        rows = frame.index[availability_mismatch].tolist()[:5]
        raise ValueError(
            "dashboard release 'market available' contradicts Open/Current at rows "
            f"{rows}"
        )

    move_matches_quotes = pd.Series(
        np.isclose(
            converted[MARKET_MOVE].to_numpy(dtype=float, na_value=np.nan),
            (
                converted[CURRENT].to_numpy(dtype=float, na_value=np.nan)
                - converted[OPEN].to_numpy(dtype=float, na_value=np.nan)
            ),
            rtol=1e-12,
            atol=1e-12,
            equal_nan=False,
        ),
        index=frame.index,
    )
    invalid_move = complete_quotes & ~move_matches_quotes
    if invalid_move.any():
        rows = frame.index[invalid_move].tolist()[:5]
        raise ValueError(
            "dashboard release Move must equal Current - Open where quotes exist; "
            f"invalid rows {rows}"
        )

    market_status = frame[MARKET_DATA_STATUS]
    invalid_market_status = (
        market_status.isna()
        | ~market_status.map(lambda value: isinstance(value, str))
        | market_status.astype("string").str.strip().eq("")
    )
    if invalid_market_status.any():
        rows = frame.index[invalid_market_status].tolist()[:5]
        raise ValueError(
            f"dashboard release market status is blank or non-text at rows {rows}"
        )


def build_dashboard_dataframe(
    config: pd.DataFrame | str | Path,
    thresholds: pd.DataFrame | str | Path | None = None,
    product_sources: ProductSources | None = None,
    multipliers: Mapping[str, float] | None = None,
    risk_dates: Mapping[str, date | datetime | str | pd.Timestamp] | None = None,
    *,
    market_date: date | datetime | str | pd.Timestamp,
    market_status: str,
    reported_underlyings: pd.DataFrame | str | Path | None = None,
) -> pd.DataFrame:
    """Build a display-ready frame from connector, config, and threshold inputs.

    REAL CONNECTOR INTEGRATION POINT: supply ``product_sources``, ``risk_dates``,
    governed portfolio ``config``, and approved ``thresholds``.
    ``product_sources`` is keyed by ProductSpec key (for example ``fxdelta``),
    while ``risk_dates`` is keyed by source type (for example ``fx/delta``).
    For transactional refreshes and readiness transitions, construct a
    ``RiskRefreshManager`` instead of repeatedly calling this one-shot helper.
    """
    if thresholds is None:
        raise ProductionIntegrationError(
            "build_dashboard_dataframe requires an explicit real threshold source"
        )
    configured = merge_config(
        build_all_pl(
            product_sources,
            multipliers,
            risk_dates,
            market_date=market_date,
            market_status=market_status,
        ),
        config,
    )
    reported = attach_reported_underlying(
        configured,
        reported_underlyings,
        allowed_pairs=_PRODUCT_PAIRS,
    )
    enriched = apply_thresholds(reported, thresholds)
    return to_dashboard_frame(enriched.loc[enriched[PORTFOLIO_MAPPED].eq(True)])


@dataclass(frozen=True)
class RefreshSnapshot:
    revision: int
    refreshed_at: datetime
    last_attempt_at: datetime
    refresh_reason: str
    system_date: pd.Timestamp
    market_date: pd.Timestamp
    checker_date: pd.Timestamp
    market_status: str
    forced_view_date: pd.Timestamp | None
    risk_status: pd.DataFrame
    risk_checker: pd.DataFrame
    risk_checker_enabled: bool
    commodity_market_enabled: bool
    risk_dates: dict[str, pd.Timestamp]
    forced_dates: dict[str, pd.Timestamp]
    changed_source_types: tuple[str, ...]
    open_refreshed_source_types: tuple[str, ...]
    market_refreshed_source_types: tuple[str, ...]
    combined_pl: pd.DataFrame
    market_frame: pd.DataFrame
    dashboard_frame: pd.DataFrame
    unmapped_frame: pd.DataFrame
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ControlSnapshot:
    """Small committed view for refresh controls and the dated editor.

    Only the compact readiness frame is copied.  Large P&L, dashboard, market,
    unmapped, and checker-inventory frames remain untouched in the manager.
    """

    revision: int
    refreshed_at: datetime
    system_date: pd.Timestamp
    market_date: pd.Timestamp
    checker_date: pd.Timestamp
    market_status: str
    forced_view_date: pd.Timestamp | None
    risk_status: pd.DataFrame
    risk_checker_enabled: bool
    commodity_market_enabled: bool
    risk_dates: dict[str, pd.Timestamp]
    forced_dates: dict[str, pd.Timestamp]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class PLSnapshot:
    """Committed P&L view copied only when a PL workflow needs it."""

    revision: int
    market_date: pd.Timestamp
    combined_pl: pd.DataFrame


@dataclass(frozen=True)
class FrameRead:
    """One defensive frame read tied to the metadata of the same revision."""

    revision: int
    market_date: pd.Timestamp
    checker_date: pd.Timestamp
    risk_checker_enabled: bool
    frame: pd.DataFrame


@dataclass(frozen=True)
class RefreshProgressSnapshot:
    """Non-sensitive, independently readable progress for one refresh attempt."""

    attempt_id: str | None
    function_name: str | None
    source_type: str | None
    underlying: str | None
    product_label: str | None
    product_index: int
    product_total: int
    hold_seconds: float
    stage: str
    current: int
    total: int
    message: str
    running: bool
    error: str | None
    started_at: datetime | None
    updated_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class RefreshHealthSnapshot:
    """Small immutable refresh health view that never copies cached DataFrames."""

    revision: int
    refreshed_at: datetime | None
    last_attempt_at: datetime | None
    active_error_count: int


def _callable_name(callback: object) -> str:
    """Return a stable callable name without rendering bound arguments or data."""
    name = getattr(callback, "__name__", None)
    return str(name) if name else type(callback).__name__


def _product_progress_label(spec: ProductSpec) -> str:
    """Return a concise human-facing label without changing connector contracts."""
    risk_type = "Commodity" if spec.risk_type == "Commo" else spec.risk_type
    greek = {
        "DeltaVega": "Delta Vega",
        "XCCYVega": "XCCY Vega",
        "InflationVega": "Inflation Vega",
    }.get(spec.risk_greek, spec.risk_greek)
    return f"{risk_type} {greek}"


def _safe_failure_location(error: BaseException) -> str:
    """Describe only the final code location, never exception text or arguments."""
    traceback = error.__traceback__
    if traceback is None:
        return "unknown"
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    filename = Path(traceback.tb_frame.f_code.co_filename).name
    function_name = traceback.tb_frame.f_code.co_name
    safe_filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename) or "unknown"
    safe_function = re.sub(r"[^A-Za-z0-9_.<>-]", "_", function_name) or "unknown"
    return f"{safe_filename}:{traceback.tb_lineno}:{safe_function}"


class RiskRefreshManager:
    """Transactional refresh cache with non-blocking last-good-snapshot reads.

    REAL CONNECTOR INTEGRATION POINT: supply the real portfolio ``config``,
    approved ``thresholds``, optional ``reported_underlyings``, combined
    ``risk_checker_loader``, and product connectors here. Prefer
    ``connector_adapters`` when product APIs or schemas differ; generic loaders
    cover any source types not present in that mapping. Construction fails
    closed if any required source is uncovered.
    """

    def __init__(
        self,
        config: PortfolioConfigSource,
        *,
        thresholds: GovernanceSource | None = None,
        reported_underlyings: GovernanceSource | None = None,
        # PRODUCTION INTEGRATION POINT: one call returns readiness then inventory.
        risk_checker_loader: Callable[[pd.Timestamp], RiskCheckerResult],
        # PRODUCTION INTEGRATION POINT: called exactly once for each refresh view.
        market_status_resolver: MarketStatusResolver,
        # PRODUCTION INTEGRATION POINT: generic fallbacks for uncovered products.
        risk_loader: Callable[[pd.Timestamp, str], pd.DataFrame] | None = None,
        cross_gamma_loader: RiskOverlayLoader | None = None,
        new_position_loader: RiskOverlayLoader | None = None,
        market_open_loader: GenericMarketConnector | None = None,
        market_status_loader: GenericMarketConnector | None = None,
        # PRODUCTION INTEGRATION POINT: preferred per-source connector mapping.
        connector_adapters: Mapping[str, ProductConnectorAdapter] | None = None,
        multipliers: Mapping[str, float] | None = None,
        stage_delays: Mapping[str, float] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        trading_timezone: str = "UTC",
        max_history_days: int = 3650,
    ) -> None:
        # Source-type keys here are the connector contracts (for example
        # ``fx/delta``), not dashboard product keys (for example ``fxdelta``).
        adapters = dict(connector_adapters or {})
        unknown_adapters = sorted(set(adapters) - set(PRODUCT_SPECS_BY_SOURCE_TYPE))
        if unknown_adapters:
            raise ValueError(
                f"unknown connector adapter source types: {unknown_adapters}"
            )
        for source_type, adapter in adapters.items():
            if not isinstance(adapter, ProductConnectorAdapter):
                raise TypeError(
                    f"connector adapter for {source_type!r} must be a "
                    "ProductConnectorAdapter"
                )
            invalid_hooks = [
                hook
                for hook in ("risk", "market_open", "market_status")
                if not callable(getattr(adapter, hook, None))
            ]
            if invalid_hooks:
                raise TypeError(
                    f"connector adapter for {source_type!r} has non-callable hooks: "
                    f"{invalid_hooks}"
                )
        if thresholds is None:
            raise ProductionIntegrationError(
                "RiskRefreshManager requires an explicit approved threshold source"
            )
        if not callable(risk_checker_loader):
            raise TypeError("risk_checker_loader must be callable")
        if not callable(market_status_resolver):
            raise TypeError("market_status_resolver must be callable")
        uncovered = set(PRODUCT_SPECS_BY_SOURCE_TYPE) - set(adapters)
        generic_loaders = {
            "risk_loader": risk_loader,
            "market_open_loader": market_open_loader,
            "market_status_loader": market_status_loader,
        }
        missing_generic = [
            name for name, loader in generic_loaders.items() if loader is None
        ]
        if uncovered and missing_generic:
            raise ProductionIntegrationError(
                "Source types without ProductConnectorAdapter coverage require all "
                f"three generic loaders; uncovered={sorted(uncovered)}, "
                f"missing={missing_generic}"
            )
        invalid_generic = [
            name
            for name, loader in generic_loaders.items()
            if loader is not None and not callable(loader)
        ]
        if invalid_generic:
            raise TypeError(
                f"generic connector loaders must be callable: {invalid_generic}"
            )
        invalid_overlay_loaders = [
            name
            for name, loader in (
                ("cross_gamma_loader", cross_gamma_loader),
                ("new_position_loader", new_position_loader),
            )
            if loader is not None and not callable(loader)
        ]
        if invalid_overlay_loaders:
            raise TypeError(
                f"risk overlay loaders must be callable: {invalid_overlay_loaders}"
            )
        self._config_source = config
        self._threshold_source = thresholds
        self._reported_underlying_source = reported_underlyings
        # Callable governance boundaries are intentionally lazy. Production
        # passes a dated Portfolio connector and a zero-argument threshold
        # connector, so constructing the WSGI app performs no source I/O before
        # the browser sees the refresh hero.
        # Existing DataFrame/path callers retain their fail-fast validation.
        self._config = None if callable(config) else load_config(config)
        self._thresholds = None if callable(thresholds) else load_thresholds(thresholds)
        if callable(reported_underlyings):
            self._reported_underlyings = None
        else:
            self._reported_underlyings = load_reported_underlyings(reported_underlyings)
        self._risk_checker_loader = risk_checker_loader
        self._market_status_resolver = market_status_resolver
        self._risk_loader = risk_loader or get_risk
        self._cross_gamma_loader = cross_gamma_loader
        self._new_position_loader = new_position_loader
        self._market_open_loader = market_open_loader or get_market_open
        self._market_status_loader = market_status_loader or get_market_status
        self._connector_adapters = adapters
        self._multipliers = _validate_multipliers(multipliers)
        configured_delays = dict(stage_delays or {})
        unknown_delays = sorted(set(configured_delays) - {"risk_product"})
        if unknown_delays:
            raise ValueError(
                "Only the operator-visible 'risk_product' progress hold is "
                f"supported; unknown stage delays: {unknown_delays}"
            )
        raw_risk_product_delay = configured_delays.get("risk_product", 0.0)
        if isinstance(raw_risk_product_delay, (bool, np.bool_)) or not isinstance(
            raw_risk_product_delay, Real
        ):
            raise TypeError("stage delay 'risk_product' must be a real number")
        risk_product_delay = float(raw_risk_product_delay)
        if not np.isfinite(risk_product_delay):
            raise ValueError("stage delay 'risk_product' must be finite")
        if risk_product_delay < 0:
            raise ValueError("stage delays must be zero or greater")
        self._stage_delays = {"risk_product": risk_product_delay}
        if int(max_history_days) <= 0:
            raise ValueError("max_history_days must be greater than zero")
        self._max_history_days = int(max_history_days)
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if not isinstance(trading_timezone, str) or not trading_timezone.strip():
            raise TypeError("trading_timezone must be a nonblank IANA timezone name")
        self._trading_timezone_name = trading_timezone.strip()
        try:
            self._trading_timezone = ZoneInfo(self._trading_timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"unknown IANA trading timezone {self._trading_timezone_name!r}"
            ) from exc
        # A refresh can be long-running, but only one writer may calculate at a
        # time. Committed state has its own short-held lock so readers can keep
        # using the last successful snapshot while that calculation is running.
        self._refresh_lock = Lock()
        self._state_lock = RLock()
        self._progress_lock = RLock()
        self._progress = RefreshProgressSnapshot(
            attempt_id=None,
            function_name=None,
            source_type=None,
            underlying=None,
            product_label=None,
            product_index=0,
            product_total=0,
            hold_seconds=0.0,
            stage="idle",
            current=0,
            total=0,
            message="No refresh has run.",
            running=False,
            error=None,
            started_at=None,
            updated_at=None,
            finished_at=None,
        )
        self._risk_frames: dict[str, pd.DataFrame] = {}
        self._market_open_frames: dict[str, pd.DataFrame] = {}
        self._market_status_frames: dict[str, pd.DataFrame] = {}
        self._market_frames: dict[str, pd.DataFrame] = {}
        self._pl_frames: dict[str, pd.DataFrame] = {}
        self._overlay_frames: dict[str, pd.DataFrame] = {}
        self._risk_dates: dict[str, pd.Timestamp] = {}
        self._market_date: pd.Timestamp | None = None
        self._search_catalog: SearchCatalog | None = None
        self._snapshot: RefreshSnapshot | None = None

    @property
    def snapshot(self) -> RefreshSnapshot:
        with self._state_lock:
            if self._snapshot is None:
                raise RuntimeError("RiskRefreshManager has not been refreshed yet")
            committed = self._snapshot
        return self._copy_snapshot(committed)

    @property
    def control_snapshot(self) -> ControlSnapshot:
        """Return control metadata while copying only the readiness frame."""
        with self._state_lock:
            if self._snapshot is None:
                raise RuntimeError("RiskRefreshManager has not been refreshed yet")
            committed = self._snapshot
        return ControlSnapshot(
            revision=committed.revision,
            refreshed_at=committed.refreshed_at,
            system_date=committed.system_date,
            market_date=committed.market_date,
            checker_date=committed.checker_date,
            market_status=committed.market_status,
            forced_view_date=committed.forced_view_date,
            risk_status=committed.risk_status.copy(deep=True),
            risk_checker_enabled=committed.risk_checker_enabled,
            commodity_market_enabled=committed.commodity_market_enabled,
            risk_dates=dict(committed.risk_dates),
            forced_dates=dict(committed.forced_dates),
            errors=committed.errors,
        )

    @property
    def pl_snapshot(self) -> PLSnapshot:
        """Return the one large frame required by the PL workflow."""
        with self._state_lock:
            if self._snapshot is None:
                raise RuntimeError("RiskRefreshManager has not been refreshed yet")
            committed = self._snapshot
        return PLSnapshot(
            revision=committed.revision,
            market_date=committed.market_date,
            combined_pl=committed.combined_pl.copy(deep=True),
        )

    def read_frame(self, name: FrameName) -> FrameRead:
        """Defensively copy exactly one named committed frame.

        The metadata is captured from the same immutable snapshot reference, so
        callers can reject stale UI work without first copying every other frame.
        """
        frame_names = {
            "risk_status",
            "risk_checker",
            "combined_pl",
            "market_frame",
            "dashboard_frame",
            "unmapped_frame",
        }
        if name not in frame_names:
            raise ValueError(f"unknown committed frame {name!r}")
        with self._state_lock:
            if self._snapshot is None:
                raise RuntimeError("RiskRefreshManager has not been refreshed yet")
            committed = self._snapshot
            frame = getattr(committed, name)
        return FrameRead(
            revision=committed.revision,
            market_date=committed.market_date,
            checker_date=committed.checker_date,
            risk_checker_enabled=committed.risk_checker_enabled,
            frame=frame.copy(deep=True),
        )

    def combine_udl_options(
        self,
        *,
        identity_mode: str = "reported",
    ) -> tuple[str, ...]:
        """Return exact Quick Risk identities for the selected authority."""
        with self._state_lock:
            catalog = self._search_catalog
        if catalog is None:
            raise RuntimeError("RiskRefreshManager has not been refreshed yet")
        return catalog.combine_udl_options(identity_mode=identity_mode)

    def market_udl_options(self) -> tuple[str, ...]:
        """Return identities from the complete committed MarketBook."""
        with self._state_lock:
            catalog = self._search_catalog
        if catalog is None:
            raise RuntimeError("RiskRefreshManager has not been refreshed yet")
        return catalog.market_udl_options()

    def search_market_udl_options(
        self,
        search_value: str | None,
        *,
        limit: int = 100,
        include: str | None = None,
    ) -> tuple[str, ...]:
        """Return bounded full-MarketBook identity choices."""
        with self._state_lock:
            catalog = self._search_catalog
        if catalog is None:
            raise RuntimeError("RiskRefreshManager has not been refreshed yet")
        return catalog.search_market_udl_options(
            search_value, limit=limit, include=include
        )

    def search_combine_udl_options(
        self,
        search_value: str | None,
        *,
        identity_mode: str = "reported",
        limit: int = 100,
        include: str | None = None,
    ) -> tuple[str, ...]:
        """Return bounded current-revision dropdown choices without connector I/O."""
        with self._state_lock:
            catalog = self._search_catalog
        if catalog is None:
            raise RuntimeError("RiskRefreshManager has not been refreshed yet")
        return catalog.search_combine_udl_options(
            search_value,
            identity_mode=identity_mode,
            limit=limit,
            include=include,
        )

    def pivot_market_exact(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = (UNDERLYING, *TENOR_COLUMNS),
        limit: int | None = None,
    ) -> SearchResult:
        """Pivot one exact identity from the complete MarketBook."""
        with self._state_lock:
            catalog = self._search_catalog
        if catalog is None:
            raise RuntimeError("RiskRefreshManager has not been refreshed yet")
        return catalog.pivot_market_exact(
            combine_udl, index_columns=index_columns, limit=limit
        )

    def pivot_combined(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = (UNDERLYING, *TENOR_COLUMNS),
        limit: int = 500,
        identity_mode: str = "reported",
        risk_filters: Mapping[str, Sequence[str] | None] | None = None,
        exclude_selected: bool = False,
    ) -> SearchResult:
        """Build one exact-selection pivot from the current committed catalog."""
        with self._state_lock:
            catalog = self._search_catalog
        if catalog is None:
            raise RuntimeError("RiskRefreshManager has not been refreshed yet")
        return catalog.pivot_combined(
            combine_udl,
            index_columns=index_columns,
            limit=limit,
            identity_mode=identity_mode,
            risk_filters=risk_filters,
            exclude_selected=exclude_selected,
        )

    def pivot_combined_hierarchy(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = (UNDERLYING, *TENOR_COLUMNS),
        leaf_limit: int = 500,
        identity_mode: str = "reported",
        risk_filters: Mapping[str, Sequence[str] | None] | None = None,
        exclude_selected: bool = False,
    ) -> SearchResult:
        """Return all independently aggregated prefix levels for one identity."""
        with self._state_lock:
            catalog = self._search_catalog
        if catalog is None:
            raise RuntimeError("RiskRefreshManager has not been refreshed yet")
        return catalog.pivot_combined_hierarchy(
            combine_udl,
            index_columns=index_columns,
            leaf_limit=leaf_limit,
            identity_mode=identity_mode,
            risk_filters=risk_filters,
            exclude_selected=exclude_selected,
        )

    @property
    def progress(self) -> RefreshProgressSnapshot:
        """Return progress without waiting for the transactional refresh lock."""
        with self._progress_lock:
            return replace(self._progress)

    @property
    def health(self) -> RefreshHealthSnapshot:
        """Return lightweight committed health without copying any cached frame."""
        with self._state_lock:
            snapshot = self._snapshot
            if snapshot is None:
                return RefreshHealthSnapshot(
                    revision=0,
                    refreshed_at=None,
                    last_attempt_at=None,
                    active_error_count=0,
                )
            return RefreshHealthSnapshot(
                revision=snapshot.revision,
                refreshed_at=snapshot.refreshed_at,
                last_attempt_at=snapshot.last_attempt_at,
                active_error_count=len(snapshot.errors),
            )

    def _start_progress(
        self,
        started_at: datetime,
        *,
        function_name: str = "RiskRefreshManager.refresh",
        stage: str = "starting",
        message: str = "Starting refresh.",
    ) -> None:
        with self._progress_lock:
            self._progress = RefreshProgressSnapshot(
                attempt_id=uuid.uuid4().hex,
                function_name=function_name,
                source_type=None,
                underlying=None,
                product_label=None,
                product_index=0,
                product_total=0,
                hold_seconds=0.0,
                stage=stage,
                current=0,
                total=1,
                message=message,
                running=True,
                error=None,
                started_at=started_at,
                updated_at=started_at,
                finished_at=None,
            )

    def _set_progress_total(self, total: int) -> None:
        with self._progress_lock:
            planned_total = max(
                int(total), self._progress.current, self._progress.total
            )
            self._progress = replace(
                self._progress,
                total=planned_total,
                updated_at=datetime.now(timezone.utc),
            )

    def _progress_step(
        self,
        function_name: str,
        stage: str,
        *,
        source_type: str | None = None,
        underlying: str | None = None,
        product_label: str | None = None,
        product_index: int = 0,
        product_total: int = 0,
        hold_seconds: float = 0.0,
        message: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._progress_lock:
            current = self._progress.current + 1
            self._progress = replace(
                self._progress,
                function_name=function_name,
                source_type=source_type,
                underlying=underlying,
                product_label=product_label,
                product_index=max(0, int(product_index)),
                product_total=max(0, int(product_total)),
                hold_seconds=max(0.0, float(hold_seconds)),
                stage=stage,
                current=current,
                total=max(self._progress.total, current),
                message=message,
                running=True,
                error=None,
                updated_at=now,
                finished_at=None,
            )

    def _progress_activity(
        self,
        function_name: str,
        stage: str,
        *,
        source_type: str | None = None,
        underlying: str | None = None,
        product_label: str | None = None,
        product_index: int = 0,
        product_total: int = 0,
        hold_seconds: float = 0.0,
        message: str,
    ) -> None:
        """Report real non-work-unit activity without changing current/total."""
        now = datetime.now(timezone.utc)
        with self._progress_lock:
            self._progress = replace(
                self._progress,
                function_name=function_name,
                source_type=source_type,
                underlying=underlying,
                product_label=product_label,
                product_index=max(0, int(product_index)),
                product_total=max(0, int(product_total)),
                hold_seconds=max(0.0, float(hold_seconds)),
                stage=stage,
                message=message,
                running=True,
                error=None,
                updated_at=now,
                finished_at=None,
            )

    def _finish_progress(
        self,
        *,
        error: str | None = None,
        failed_function_name: str | None = None,
        failed_source_type: str | None = None,
        failed_underlying: str | None = None,
        failed_product_label: str | None = None,
        failed_product_index: int = 0,
        failed_product_total: int = 0,
        failed_hold_seconds: float = 0.0,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._progress_lock:
            self._progress = replace(
                self._progress,
                function_name=(
                    failed_function_name if error else self._progress.function_name
                ),
                source_type=(
                    failed_source_type if error else self._progress.source_type
                ),
                underlying=(failed_underlying if error else self._progress.underlying),
                product_label=(
                    failed_product_label if error else self._progress.product_label
                ),
                product_index=(
                    max(0, int(failed_product_index))
                    if error
                    else self._progress.product_index
                ),
                product_total=(
                    max(0, int(failed_product_total))
                    if error
                    else self._progress.product_total
                ),
                hold_seconds=(
                    max(0.0, float(failed_hold_seconds))
                    if error
                    else self._progress.hold_seconds
                ),
                stage="error" if error else "complete",
                current=(
                    self._progress.current
                    if error
                    else max(self._progress.current, self._progress.total)
                ),
                message=error or "Refresh complete.",
                running=False,
                error=error,
                updated_at=now,
                finished_at=now,
            )

    @staticmethod
    def _copy_snapshot(snapshot: RefreshSnapshot) -> RefreshSnapshot:
        """Return a defensive copy so callers cannot mutate the committed cache."""
        return replace(
            snapshot,
            risk_status=snapshot.risk_status.copy(deep=True),
            risk_checker=snapshot.risk_checker.copy(deep=True),
            risk_dates=dict(snapshot.risk_dates),
            forced_dates=dict(snapshot.forced_dates),
            combined_pl=snapshot.combined_pl.copy(deep=True),
            market_frame=snapshot.market_frame.copy(deep=True),
            dashboard_frame=snapshot.dashboard_frame.copy(deep=True),
            unmapped_frame=snapshot.unmapped_frame.copy(deep=True),
        )

    @property
    def stage_delays(self) -> dict[str, float]:
        """Return configured operator-visible refresh-stage delays."""
        return dict(self._stage_delays)

    def _now(self) -> datetime:
        """Return one timezone-aware UTC clock reading for refresh metadata."""
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return a datetime")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _system_date(self, attempted_at: datetime) -> pd.Timestamp:
        """Return the refresh calendar date in the configured trading timezone."""

        return pd.Timestamp(attempted_at.astimezone(self._trading_timezone).date())

    def _resolve_market_status(self, market_date: pd.Timestamp) -> str:
        """Call the one authoritative status boundary once and validate its result."""

        self._progress_activity(
            _callable_name(self._market_status_resolver),
            "market_status",
            message=f"Resolving the market source for {market_date.date()}.",
        )
        return _require_market_status(self._market_status_resolver(market_date))

    @staticmethod
    def _validate_risk_readiness(raw_status: object) -> pd.DataFrame:
        """Validate pair readiness and synthesize absent catalogue pairs at Age 0."""
        if not isinstance(raw_status, pd.DataFrame):
            raise TypeError("risk_checker_loader readiness result must be a DataFrame")
        required = [RISK_TYPE, RISK_GREEK, AGE]
        if list(raw_status.columns) != required:
            raise ValueError(
                f"risk readiness columns must be exactly {required} in that order; "
                f"found {list(raw_status.columns)}"
            )
        status = raw_status.copy()
        status = _require_nonblank(status, [RISK_TYPE, RISK_GREEK], "risk readiness")
        if status[AGE].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError("risk readiness Age must not contain booleans")
        age = pd.to_numeric(status[AGE], errors="coerce")
        invalid_age = age.isna() | ~np.isfinite(age) | age.lt(0) | age.mod(1).ne(0)
        if invalid_age.any():
            rows = status.index[invalid_age].tolist()[:5]
            raise ValueError(
                "risk readiness Age must contain only non-negative integers; "
                f"invalid rows {rows}"
            )
        status[AGE] = age.astype("int64")
        pair_columns = [RISK_TYPE, RISK_GREEK]
        if status.duplicated(pair_columns).any():
            raise ValueError(
                "risk readiness contains duplicate Risk Type/Risk Greek pairs"
            )

        catalogue = pd.DataFrame(
            [
                {
                    SOURCE_TYPE: spec.source_type,
                    RISK_TYPE: spec.risk_type,
                    RISK_GREEK: spec.risk_greek,
                }
                for spec in PRODUCT_SPECS.values()
            ]
        )
        known_pairs = set(catalogue[pair_columns].itertuples(index=False, name=None))
        supplied_pairs = set(status[pair_columns].itertuples(index=False, name=None))
        unknown_pairs = sorted(supplied_pairs - known_pairs)
        if unknown_pairs:
            raise ValueError(
                "risk readiness contains unknown Risk Type/Risk Greek pairs; "
                f"unknown={unknown_pairs}"
            )

        completed = catalogue.merge(
            status,
            on=pair_columns,
            how="left",
            validate="one_to_one",
        )
        completed[AGE_DEFAULTED] = completed[AGE].isna()
        completed[AGE] = completed[AGE].fillna(0).astype("int64")
        return completed[[SOURCE_TYPE, RISK_TYPE, RISK_GREEK, AGE, AGE_DEFAULTED]]

    @staticmethod
    def _validate_risk_checker(raw_checker: object) -> pd.DataFrame:
        """Validate the second DataFrame returned by the checker connector."""
        if not isinstance(raw_checker, pd.DataFrame):
            raise TypeError("risk_checker_loader inventory result must be a DataFrame")
        required = [RISK_TYPE, RISK_GREEK, MMM_FILE, PRODUCT]
        if list(raw_checker.columns) != required:
            raise ValueError(
                f"risk checker columns must be exactly {required} in that order; "
                f"found {list(raw_checker.columns)}"
            )
        checker = raw_checker.copy()
        checker = _require_nonblank(checker[required].copy(), required, "risk checker")
        invalid_suffix = ~checker[MMM_FILE].str.casefold().str.endswith(".mmm")
        if invalid_suffix.any():
            rows = checker.index[invalid_suffix].tolist()[:5]
            raise ValueError(
                f"risk checker MMMFile values must end in '.mmm'; invalid rows {rows}"
            )
        if checker.duplicated(required).any():
            raise ValueError("risk checker contains duplicate inventory rows")
        expected_pairs = {
            (spec.risk_type, spec.risk_greek) for spec in PRODUCT_SPECS.values()
        }
        actual_pairs = set(
            checker[[RISK_TYPE, RISK_GREEK]].itertuples(index=False, name=None)
        )
        extra_pairs = sorted(actual_pairs - expected_pairs)
        if extra_pairs:
            raise ValueError(
                "risk checker contains unknown Risk Type/Risk Greek pairs; "
                f"unknown={extra_pairs}"
            )
        invalid_product = ~checker[PRODUCT].isin(CANONICAL_PRODUCTS)
        if invalid_product.any():
            rows = checker.index[invalid_product].tolist()[:5]
            raise ValueError(
                "risk checker Product must be exactly 'XVA' or 'Hedges'; "
                f"invalid rows {rows}"
            )
        return checker.sort_values(required, kind="stable").reset_index(drop=True)

    def _load_risk_checker(self, checker_date: pd.Timestamp) -> RiskCheckerResult:
        """Load readiness and MMM inventory atomically from one dated connector."""
        self._progress_step(
            _callable_name(self._risk_checker_loader),
            "readiness",
            message="Loading risk readiness and checker inventory.",
        )
        result = self._risk_checker_loader(checker_date)
        if not isinstance(result, tuple) or len(result) != 2:
            raise TypeError(
                "risk_checker_loader must return exactly "
                "(risk_readiness_df, risk_checker_df)"
            )
        readiness, checker = result
        return (
            self._validate_risk_readiness(readiness),
            self._validate_risk_checker(checker),
        )

    def _wait_for_stage(self, stage: str, *, has_snapshot: bool) -> None:
        """Apply an optional configured pause after the initial snapshot."""
        delay = self._stage_delays.get(stage, 0.0)
        if has_snapshot and delay > 0:
            sleep_name = (
                "time.sleep"
                if self._sleep is time.sleep
                else _callable_name(self._sleep)
            )
            self._progress_activity(
                sleep_name,
                f"{stage}_delay",
                message=f"Configured {stage} progress hold.",
            )
            self._sleep(delay)

    def _load_product_risk(
        self, spec: ProductSpec, risk_date: pd.Timestamp
    ) -> pd.DataFrame:
        # PRODUCTION INTEGRATION POINT: a per-source adapter wins; the generic
        # ``risk_loader(date, source_type)`` handles only uncovered source types.
        adapter = self._connector_adapters.get(spec.source_type)
        connector = adapter.risk if adapter is not None else self._risk_loader
        self._progress_step(
            _callable_name(connector),
            "risk",
            source_type=spec.source_type,
            message="Loading connector risk.",
        )
        if adapter is not None:
            return adapter.risk(risk_date)
        return self._risk_loader(risk_date, spec.source_type)

    def _load_product_market_open(
        self,
        spec: ProductSpec,
        market_date: pd.Timestamp,
        underlyings: tuple[str, ...],
        *,
        market_status: str,
    ) -> pd.DataFrame:
        # PRODUCTION INTEGRATION POINT: adapter and generic loader receive the
        # market/view date, never the possibly aged product risk date.
        adapter = self._connector_adapters.get(spec.source_type)
        connector = (
            adapter.market_open if adapter is not None else self._market_open_loader
        )
        selected_status = _require_market_status(market_status)
        frames: list[pd.DataFrame] = []
        for index, underlying in enumerate(underlyings, start=1):
            self._progress_activity(
                _callable_name(connector),
                "market_open",
                source_type=spec.source_type,
                underlying=underlying,
                product_index=index,
                product_total=len(underlyings),
                message=f"Loading Open for {underlying}.",
            )
            try:
                frame = (
                    adapter.market_open(
                        market_date, underlying, market_status=selected_status
                    )
                    if adapter is not None
                    else self._market_open_loader(
                        spec.source_type,
                        market_date,
                        underlying,
                        market_status=selected_status,
                    )
                )
            except Exception:
                self._progress_activity(
                    _callable_name(connector),
                    "market_open",
                    source_type=spec.source_type,
                    underlying=underlying,
                    message="Opening market connector failed.",
                )
                raise
            if not isinstance(frame, pd.DataFrame):
                raise TypeError("market Open connector must return a pandas DataFrame")
            frames.append(frame)
        return pd.concat(frames, ignore_index=True, sort=False)

    def _load_product_market_status(
        self,
        spec: ProductSpec,
        market_date: pd.Timestamp,
        underlyings: tuple[str, ...],
        *,
        market_status: str,
    ) -> pd.DataFrame:
        # PRODUCTION INTEGRATION POINT: the injected callable selects Live for
        # today and OFFICIAL for prior views, then returns the normalized leg.
        adapter = self._connector_adapters.get(spec.source_type)
        connector = (
            adapter.market_status if adapter is not None else self._market_status_loader
        )
        selected_status = _require_market_status(market_status)
        frames: list[pd.DataFrame] = []
        for index, underlying in enumerate(underlyings, start=1):
            self._progress_activity(
                _callable_name(connector),
                "market_status",
                source_type=spec.source_type,
                underlying=underlying,
                product_index=index,
                product_total=len(underlyings),
                message=f"Loading {selected_status} for {underlying}.",
            )
            try:
                frame = (
                    adapter.market_status(
                        market_date, underlying, market_status=selected_status
                    )
                    if adapter is not None
                    else self._market_status_loader(
                        spec.source_type,
                        market_date,
                        underlying,
                        market_status=selected_status,
                    )
                )
            except Exception:
                self._progress_activity(
                    _callable_name(connector),
                    "market_status",
                    source_type=spec.source_type,
                    underlying=underlying,
                    message="Current market connector failed.",
                )
                raise
            if not isinstance(frame, pd.DataFrame):
                raise TypeError(
                    "current market connector must return a pandas DataFrame"
                )
            frames.append(frame)
        return pd.concat(frames, ignore_index=True, sort=False)

    @staticmethod
    def _disabled_market_sources(
        spec: ProductSpec,
        risk_frame: pd.DataFrame,
        *,
        market_status: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return zero quote legs without calling disabled Commodity connectors."""
        keys = risk_frame[spec.market_keys].drop_duplicates().reset_index(drop=True)
        for axis in spec.axes:
            tenor_column = axis.column
            order_column = axis.order_column
            keys[order_column] = keys.groupby(UNDERLYING, sort=False)[
                tenor_column
            ].transform(
                lambda values: pd.Series(
                    pd.factorize(values, sort=False)[0],
                    index=values.index,
                    dtype="Int64",
                )
            )
        market_open = keys.copy()
        market_open[OPEN] = 0.0
        market_status_frame = keys.copy()
        market_status_frame[CURRENT] = 0.0
        market_status_frame[MARKET_STATUS] = _require_market_status(market_status)
        return market_open, market_status_frame

    @staticmethod
    def _risk_underlyings(risk_frame: pd.DataFrame) -> tuple[str, ...]:
        """Return stable connector scope from an already validated Risk frame."""
        return tuple(risk_frame[UNDERLYING].drop_duplicates().tolist())

    @staticmethod
    def _reject_unrequested_market_underlyings(
        frame: pd.DataFrame,
        requested: tuple[str, ...],
        *,
        label: str,
    ) -> None:
        extras = sorted(set(frame[UNDERLYING]) - set(requested))
        if extras:
            raise ValueError(
                f"{label} returned Underlying values outside validated Risk scope: "
                f"{extras[:5]}"
            )

    @staticmethod
    def _release_pl_views(
        pl_frames: Mapping[str, pd.DataFrame],
        config: pd.DataFrame,
        thresholds: pd.DataFrame,
        reported_underlyings: pd.DataFrame,
        overlay_frames: Mapping[str, pd.DataFrame] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Apply reporting governance and return enriched, dashboard, unmapped."""
        missing = set(PRODUCT_SPECS_BY_SOURCE_TYPE) - set(pl_frames)
        if missing:
            raise RuntimeError(f"P&L cache is missing product types: {sorted(missing)}")
        release_frames = [
            pl_frames[spec.source_type] for spec in PRODUCT_SPECS.values()
        ]
        release_frames.extend(
            frame for frame in (overlay_frames or {}).values() if not frame.empty
        )
        combined = pd.concat(release_frames, ignore_index=True, sort=False)
        configured = _merge_validated_config(combined, config)
        reported = attach_reported_underlying(
            configured,
            reported_underlyings,
            allowed_pairs=_PRODUCT_PAIRS,
        )
        enriched = _apply_validated_thresholds(reported, thresholds)
        mapped = enriched.loc[enriched[PORTFOLIO_MAPPED].eq(True)].copy()
        unmapped = enriched.loc[enriched[PORTFOLIO_MAPPED].eq(False)].copy()
        dashboard = to_dashboard_frame(mapped)
        _validate_dashboard_release(dashboard)
        return enriched, dashboard, unmapped

    @staticmethod
    def _build_snapshot_search_catalog(
        *,
        revision: int,
        risk_frames: Mapping[str, pd.DataFrame],
        market_frames: Mapping[str, pd.DataFrame],
        dashboard: pd.DataFrame,
        risk_dates: Mapping[str, pd.Timestamp],
        market_date: pd.Timestamp,
        market_status: str,
    ) -> SearchCatalog:
        """Build Risk search from Risk/P&L and Market search from full quotes."""
        _require_market_status(market_status)
        return build_search_catalog(
            revision=revision,
            risk_frames=risk_frames,
            market_frames=market_frames,
            risk_pivot_frame=dashboard,
            risk_dates=risk_dates,
            market_date=market_date,
        )

    @staticmethod
    def _combined_market_frame(
        market_frames: Mapping[str, pd.DataFrame],
        market_date: pd.Timestamp,
    ) -> pd.DataFrame:
        """Return an isolated full MarketBook with all market-only tenors."""

        rows: list[pd.DataFrame] = []
        for spec in PRODUCT_SPECS.values():
            if spec.source_type not in market_frames:
                raise RuntimeError(f"full MarketBook is missing {spec.source_type!r}")
            frame = _with_dashboard_tenors(market_frames[spec.source_type], spec)
            frame[SOURCE_TYPE] = spec.source_type
            frame[MARKET_DATE] = market_date
            frame = frame.sort_values(
                [
                    UNDERLYING,
                    *spec.tenor_order_columns,
                    *spec.tenor_columns,
                ],
                kind="stable",
                na_position="last",
            ).reset_index(drop=True)
            rows.append(frame)
        return pd.concat(rows, ignore_index=True, sort=False)

    def _commit_snapshot(self, snapshot: RefreshSnapshot) -> None:
        """Commit metadata-only state without holding readers for calculation."""
        with self._state_lock:
            self._snapshot = snapshot

    def _commit_full_snapshot(
        self,
        snapshot: RefreshSnapshot,
        *,
        config: pd.DataFrame,
        thresholds: pd.DataFrame,
        reported_underlyings: pd.DataFrame,
        risk_frames: dict[str, pd.DataFrame],
        market_open_frames: dict[str, pd.DataFrame],
        market_status_frames: dict[str, pd.DataFrame],
        market_frames: dict[str, pd.DataFrame],
        pl_frames: dict[str, pd.DataFrame],
        overlay_frames: dict[str, pd.DataFrame],
        risk_dates: dict[str, pd.Timestamp],
        market_date: pd.Timestamp,
        search_catalog: SearchCatalog,
    ) -> None:
        """Atomically publish every newly calculated cache after all work succeeds."""
        with self._state_lock:
            self._config = config
            self._thresholds = thresholds
            self._reported_underlyings = reported_underlyings
            self._risk_frames = risk_frames
            self._market_open_frames = market_open_frames
            self._market_status_frames = market_status_frames
            self._market_frames = market_frames
            self._pl_frames = pl_frames
            self._overlay_frames = overlay_frames
            self._risk_dates = risk_dates
            self._market_date = market_date
            self._search_catalog = search_catalog
            self._snapshot = snapshot

    def refresh_portfolios(
        self,
        *,
        reason: str = "portfolio mapping",
        expected_revision: int | None = None,
    ) -> RefreshSnapshot:
        """Reload only portfolio mapping and rebuild its dependent cached views.

        Risk, market, checker, and threshold connectors are deliberately not
        invoked. Portfolio and Reported Underlying mappings plus every derived
        table/search view are validated before one atomic commit; failures
        retain the last snapshot.
        """
        if not self._refresh_lock.acquire(blocking=False):
            raise RefreshInProgressError("a risk refresh is already in progress")
        try:
            attempted_at = self._now()
            with self._state_lock:
                base_snapshot = self._snapshot
                if base_snapshot is None:
                    raise RuntimeError(
                        "portfolio mapping cannot refresh before the initial snapshot"
                    )
                if expected_revision is not None:
                    if isinstance(expected_revision, bool) or not isinstance(
                        expected_revision, int
                    ):
                        raise TypeError(
                            "expected_revision must be a non-negative integer"
                        )
                    if expected_revision < 0:
                        raise ValueError(
                            "expected_revision must be a non-negative integer"
                        )
                    if expected_revision != base_snapshot.revision:
                        raise StaleRefreshError(
                            expected_revision, base_snapshot.revision
                        )
                base_thresholds = self._thresholds
                risk_frames = dict(self._risk_frames)
                market_open_frames = dict(self._market_open_frames)
                market_status_frames = dict(self._market_status_frames)
                market_frames = dict(self._market_frames)
                pl_frames = dict(self._pl_frames)
                overlay_frames = dict(self._overlay_frames)
                risk_dates = dict(self._risk_dates)
                market_date = self._market_date
            if base_thresholds is None or market_date is None:
                raise RuntimeError("the committed snapshot cache is incomplete")

            self._start_progress(
                attempted_at,
                function_name="RiskRefreshManager.refresh_portfolios",
                stage="portfolio_config",
                message="Starting portfolio mapping refresh.",
            )
            try:
                self._set_progress_total(4)
                config_function = (
                    _callable_name(self._config_source)
                    if callable(self._config_source)
                    else "load_config"
                )
                portfolio_date = base_snapshot.checker_date
                self._progress_step(
                    config_function,
                    "portfolio_config",
                    message=(f"Loading portfolio mapping for {portfolio_date.date()}."),
                )
                next_config = _load_portfolio_config(
                    self._config_source, portfolio_date
                )

                mapping_function = (
                    _callable_name(self._reported_underlying_source)
                    if callable(self._reported_underlying_source)
                    else "load_reported_underlyings"
                )
                self._progress_step(
                    mapping_function,
                    "portfolio_config",
                    message="Loading Reported Underlying mapping.",
                )
                next_reported_underlyings = load_reported_underlyings(
                    self._reported_underlying_source
                )

                self._progress_step(
                    "_release_pl_views",
                    "final",
                    message="Rebuilding mapping-dependent dashboard views.",
                )
                enriched, dashboard, unmapped = self._release_pl_views(
                    pl_frames,
                    next_config,
                    base_thresholds,
                    next_reported_underlyings,
                    overlay_frames,
                )
                revision = base_snapshot.revision + 1
                search_catalog = self._build_snapshot_search_catalog(
                    revision=revision,
                    risk_frames=risk_frames,
                    market_frames=market_frames,
                    dashboard=dashboard,
                    risk_dates=risk_dates,
                    market_date=market_date,
                    market_status=base_snapshot.market_status,
                )
                completed_at = self._now()
                snapshot = replace(
                    base_snapshot,
                    revision=revision,
                    refreshed_at=completed_at,
                    last_attempt_at=attempted_at,
                    refresh_reason=reason,
                    changed_source_types=(),
                    open_refreshed_source_types=(),
                    market_refreshed_source_types=(),
                    combined_pl=enriched,
                    dashboard_frame=dashboard,
                    unmapped_frame=unmapped,
                    errors=(),
                )
                self._progress_step(
                    "_commit_full_snapshot",
                    "commit",
                    message="Publishing the new portfolio mapping atomically.",
                )
                self._commit_full_snapshot(
                    snapshot,
                    config=next_config,
                    thresholds=base_thresholds,
                    reported_underlyings=next_reported_underlyings,
                    risk_frames=risk_frames,
                    market_open_frames=market_open_frames,
                    market_status_frames=market_status_frames,
                    market_frames=market_frames,
                    pl_frames=pl_frames,
                    overlay_frames=overlay_frames,
                    risk_dates=risk_dates,
                    market_date=market_date,
                    search_catalog=search_catalog,
                )
                self._finish_progress()
                return self._copy_snapshot(snapshot)
            except Exception as error:
                incident_id = uuid.uuid4().hex[:10]
                error_type = (
                    re.sub(r"[^A-Za-z0-9_.-]", "_", type(error).__name__) or "Exception"
                )
                LOGGER.exception(
                    "Portfolio mapping refresh failed; incident=%s type=%s location=%s",
                    incident_id,
                    error_type,
                    _safe_failure_location(error),
                )
                safe_error = f"Refresh failed (incident {incident_id})."
                failed_progress = self.progress
                message = (
                    f"{attempted_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | "
                    f"Refresh failed (incident {incident_id}); last successful data retained."
                )
                retained = replace(
                    base_snapshot,
                    last_attempt_at=attempted_at,
                    refresh_reason=reason,
                    changed_source_types=(),
                    open_refreshed_source_types=(),
                    market_refreshed_source_types=(),
                    errors=(message,),
                )
                self._set_progress_total(self.progress.current + 1)
                self._progress_step(
                    "_commit_snapshot",
                    "commit",
                    message="Retaining the last successful snapshot.",
                )
                self._commit_snapshot(retained)
                self._finish_progress(
                    error=safe_error,
                    failed_function_name=failed_progress.function_name,
                    failed_source_type=failed_progress.source_type,
                    failed_underlying=failed_progress.underlying,
                    failed_product_label=failed_progress.product_label,
                    failed_product_index=failed_progress.product_index,
                    failed_product_total=failed_progress.product_total,
                    failed_hold_seconds=failed_progress.hold_seconds,
                )
                return self._copy_snapshot(retained)
        finally:
            self._refresh_lock.release()

    def refresh(
        self,
        *,
        force_pl: bool = False,
        force_risk: bool = False,
        commodity_market_enabled: bool = False,
        risk_checker_enabled: bool | None = None,
        forced_dates: Mapping[str, date | datetime | str | pd.Timestamp] | None = None,
        view_date: date | datetime | str | pd.Timestamp | None = None,
        reason: str = "status",
        expected_revision: int | None = None,
    ) -> RefreshSnapshot:
        """Refresh dependencies and retain the last successful snapshot if anything fails."""
        if not self._refresh_lock.acquire(blocking=False):
            raise RefreshInProgressError("a risk refresh is already in progress")
        try:
            attempted_at = self._now()
            with self._state_lock:
                base_snapshot = self._snapshot
                actual_revision = 0 if base_snapshot is None else base_snapshot.revision
                if expected_revision is not None:
                    if isinstance(expected_revision, bool) or not isinstance(
                        expected_revision, int
                    ):
                        raise TypeError(
                            "expected_revision must be a non-negative integer"
                        )
                    if expected_revision < 0:
                        raise ValueError(
                            "expected_revision must be a non-negative integer"
                        )
                    if expected_revision != actual_revision:
                        raise StaleRefreshError(expected_revision, actual_revision)
                base_config = self._config
                base_thresholds = self._thresholds
                base_risk_frames = dict(self._risk_frames)
                base_market_open_frames = dict(self._market_open_frames)
                base_market_status_frames = dict(self._market_status_frames)
                base_market_frames = dict(self._market_frames)
                base_pl_frames = dict(self._pl_frames)
                base_overlay_frames = dict(self._overlay_frames)
                base_risk_dates = dict(self._risk_dates)
                base_market_date = self._market_date
            self._start_progress(attempted_at)
            try:
                if not isinstance(commodity_market_enabled, bool):
                    raise TypeError("commodity_market_enabled must be boolean")
                checker_enabled = (
                    True if risk_checker_enabled is None else risk_checker_enabled
                )
                if not isinstance(checker_enabled, bool):
                    raise TypeError("risk_checker_enabled must be boolean")
                system_date = self._system_date(attempted_at)
                forced_view_date = (
                    None if view_date in (None, "") else _as_timestamp(view_date)
                )
                market_date = forced_view_date or system_date
                if market_date > system_date:
                    raise ValueError("view date must not be in the future")
                if forced_view_date is not None and market_date.weekday() >= 5:
                    raise ValueError("view date must be a business day")
                if (system_date - market_date).days > self._max_history_days:
                    raise ValueError(
                        f"view date exceeds the {self._max_history_days}-day retention window"
                    )

                expected_market_status = self._resolve_market_status(market_date)

                checker_date = checker_date_for(market_date)
                if checker_enabled:
                    status, next_risk_checker = self._load_risk_checker(checker_date)
                else:
                    status = self._validate_risk_readiness(
                        pd.DataFrame(columns=[RISK_TYPE, RISK_GREEK, AGE])
                    )
                    next_risk_checker = pd.DataFrame(
                        columns=[RISK_TYPE, RISK_GREEK, MMM_FILE, PRODUCT]
                    )
                age_by_source = status.set_index(SOURCE_TYPE)[AGE].to_dict()
                view_dates = {
                    source_type: risk_date_for(checker_date, age)
                    for source_type, age in age_by_source.items()
                }
                requested_overrides = dict(forced_dates or {})
                unknown_overrides = sorted(
                    set(requested_overrides) - set(PRODUCT_SPECS_BY_SOURCE_TYPE)
                )
                if unknown_overrides:
                    raise ValueError(
                        f"unknown forced-date source types: {unknown_overrides}"
                    )
                overrides: dict[str, pd.Timestamp] = {}
                for source_type, value in requested_overrides.items():
                    if value in (None, ""):
                        continue
                    forced_date = _as_timestamp(value)
                    if forced_date > system_date:
                        raise ValueError(
                            f"forced date for {source_type} must not be in the future"
                        )
                    if forced_date.weekday() >= 5:
                        raise ValueError(
                            f"forced date for {source_type} must be a business day"
                        )
                    if forced_date > checker_date:
                        raise ValueError(
                            f"forced date for {source_type} must not be after "
                            f"checker date {checker_date.date()} for market date "
                            f"{market_date.date()}"
                        )
                    if (system_date - forced_date).days > self._max_history_days:
                        raise ValueError(
                            f"forced date for {source_type} exceeds the {self._max_history_days}-day retention window"
                        )
                    overrides[source_type] = forced_date
                # Per-source Force Risk is the final override after readiness and
                # the selected Today view have established the derived dates.
                next_dates = {
                    source_type: overrides.get(source_type, risk_date)
                    for source_type, risk_date in view_dates.items()
                }
                status[CHECKER_DATE] = checker_date
                status[SUGGESTED_RISK_DATE] = status[SOURCE_TYPE].map(view_dates)
                status[EFFECTIVE_RISK_DATE] = status[SOURCE_TYPE].map(next_dates)
                status[FORCE_RISK] = status[SOURCE_TYPE].isin(overrides)
                status[MARKET_DATE] = market_date
                status[MARKET_STATUS] = expected_market_status

                changed_source_types = {
                    source_type
                    for source_type, risk_date in next_dates.items()
                    if base_risk_dates.get(source_type) != risk_date
                }
                if base_snapshot is None or force_risk:
                    changed_source_types = set(PRODUCT_SPECS_BY_SOURCE_TYPE)

                checker_state_changed = (
                    base_snapshot is not None
                    and base_snapshot.risk_checker_enabled != checker_enabled
                )
                checker_data_changed = (
                    checker_enabled
                    and base_snapshot is not None
                    and not next_risk_checker.equals(base_snapshot.risk_checker)
                )
                commodity_state_changed = (
                    base_snapshot is not None
                    and base_snapshot.commodity_market_enabled
                    != commodity_market_enabled
                )
                market_date_changed = base_market_date != market_date
                market_context_changed = market_date_changed
                if base_snapshot is not None:
                    market_context_changed = (
                        market_context_changed
                        or base_snapshot.market_status != expected_market_status
                    )

                override_changed = (
                    base_snapshot is not None
                    and base_snapshot.forced_dates != overrides
                )
                forced_view_changed = (
                    base_snapshot is not None
                    and base_snapshot.forced_view_date != forced_view_date
                )
                if (
                    not changed_source_types
                    and not market_context_changed
                    and not force_pl
                    and not force_risk
                    and not override_changed
                    and not forced_view_changed
                    and not checker_state_changed
                    and not checker_data_changed
                    and not commodity_state_changed
                ):
                    self._set_progress_total(2)
                    snapshot = replace(
                        base_snapshot,
                        last_attempt_at=attempted_at,
                        refresh_reason=reason,
                        system_date=system_date,
                        market_date=market_date,
                        checker_date=checker_date,
                        forced_view_date=forced_view_date,
                        risk_status=status,
                        risk_checker=next_risk_checker,
                        risk_checker_enabled=checker_enabled,
                        changed_source_types=(),
                        open_refreshed_source_types=(),
                        market_refreshed_source_types=(),
                        errors=(),
                    )
                    self._progress_step(
                        "_commit_snapshot",
                        "commit",
                        message="Committing refresh metadata.",
                    )
                    self._commit_snapshot(snapshot)
                    self._finish_progress()
                    return self._copy_snapshot(snapshot)

                all_types = set(PRODUCT_SPECS_BY_SOURCE_TYPE)
                open_source_types = set(changed_source_types)
                if market_context_changed:
                    open_source_types = set(all_types)
                market_status_source_types = (
                    set(all_types) if force_pl else set(changed_source_types)
                )
                if market_context_changed:
                    market_status_source_types = set(all_types)
                if base_snapshot is None or force_risk:
                    open_source_types = set(all_types)
                    market_status_source_types = set(all_types)
                if commodity_state_changed:
                    commodity_types = {
                        source_type
                        for source_type, spec in PRODUCT_SPECS_BY_SOURCE_TYPE.items()
                        if spec.risk_type == "Commo"
                    }
                    open_source_types.update(commodity_types)
                    market_status_source_types.update(commodity_types)

                if (
                    not changed_source_types
                    and not open_source_types
                    and not market_status_source_types
                    and (
                        override_changed or forced_view_changed or checker_state_changed
                    )
                    and not checker_data_changed
                ):
                    self._set_progress_total(2)
                    snapshot = replace(
                        base_snapshot,
                        last_attempt_at=attempted_at,
                        refresh_reason=reason,
                        system_date=system_date,
                        market_date=market_date,
                        checker_date=checker_date,
                        forced_view_date=forced_view_date,
                        risk_status=status,
                        risk_checker=next_risk_checker,
                        risk_checker_enabled=checker_enabled,
                        commodity_market_enabled=commodity_market_enabled,
                        forced_dates=overrides,
                        changed_source_types=(),
                        open_refreshed_source_types=(),
                        market_refreshed_source_types=(),
                        errors=(),
                    )
                    self._progress_step(
                        "_commit_snapshot",
                        "commit",
                        message="Committing forced-date metadata.",
                    )
                    self._commit_snapshot(snapshot)
                    self._finish_progress()
                    return self._copy_snapshot(snapshot)

                recalculate_source_types = (
                    changed_source_types
                    | open_source_types
                    | market_status_source_types
                )
                planned_total = (
                    3
                    + 2 * len(changed_source_types)
                    + int(bool(open_source_types))
                    + int(bool(market_status_source_types))
                    + int(bool(recalculate_source_types))
                    + int(self._cross_gamma_loader is not None)
                    + int(self._new_position_loader is not None)
                )
                self._set_progress_total(planned_total)

                next_risk = {} if force_risk else base_risk_frames
                next_open = {} if force_risk else base_market_open_frames
                next_status = {} if force_risk else base_market_status_frames
                next_market = {} if force_risk else base_market_frames

                if changed_source_types:
                    self._wait_for_stage("risk", has_snapshot=base_snapshot is not None)
                risk_specs = [
                    spec
                    for spec in PRODUCT_SPECS.values()
                    if spec.source_type in changed_source_types
                ]
                # Hold every post-startup Risk/dRisk product call long enough
                # for its function name to be read.  That includes selective
                # readiness 1 -> 0 reloads and view-date changes, not only the
                # explicit Reload All Risk path.  A P&L refresh whose risk
                # dates did not change has no risk calls and therefore no hold.
                risk_product_delay = (
                    self._stage_delays["risk_product"]
                    if base_snapshot is not None
                    else 0.0
                )
                for product_index, spec in enumerate(risk_specs, start=1):
                    source_type = spec.source_type
                    risk_date = next_dates[source_type]
                    raw_risk = self._load_product_risk(spec, risk_date)
                    product_label = _product_progress_label(spec)
                    self._progress_step(
                        f"get_{spec.key}_risk",
                        "risk",
                        source_type=source_type,
                        product_label=product_label,
                        product_index=product_index,
                        product_total=len(risk_specs),
                        hold_seconds=risk_product_delay,
                        message=f"Loading and validating Risk/dRisk for {product_label}.",
                    )
                    next_risk[source_type] = get_product_risk(spec, risk_date, raw_risk)
                    if risk_product_delay > 0:
                        self._sleep(risk_product_delay)

                if open_source_types or market_status_source_types:
                    self._wait_for_stage(
                        "market", has_snapshot=base_snapshot is not None
                    )
                open_specs = [
                    spec
                    for spec in PRODUCT_SPECS.values()
                    if spec.source_type in open_source_types
                ]
                if open_specs:
                    self._progress_step(
                        "get_product_market_open",
                        "market_open",
                        message=f"Loading and validating {len(open_specs)} opening market snapshots.",
                    )
                for spec in open_specs:
                    source_type = spec.source_type
                    requested_underlyings = self._risk_underlyings(
                        next_risk[source_type]
                    )
                    if spec.risk_type == "Commo" and not commodity_market_enabled:
                        raw_open, _ = self._disabled_market_sources(
                            spec,
                            next_risk[source_type],
                            market_status=expected_market_status,
                        )
                    else:
                        raw_open = self._load_product_market_open(
                            spec,
                            market_date,
                            requested_underlyings,
                            market_status=expected_market_status,
                        )
                    try:
                        validated_open = get_product_market_open(
                            spec, market_date, raw_open
                        )
                        self._reject_unrequested_market_underlyings(
                            validated_open,
                            requested_underlyings,
                            label=f"{spec.key} market open",
                        )
                        next_open[source_type] = validated_open
                    except Exception:
                        self._progress_activity(
                            "get_product_market_open",
                            "market_open",
                            source_type=source_type,
                            message="Opening market validation failed.",
                        )
                        raise
                status_specs = [
                    spec
                    for spec in PRODUCT_SPECS.values()
                    if spec.source_type in market_status_source_types
                ]
                if status_specs:
                    self._progress_step(
                        "get_product_market_status",
                        "market_status",
                        message=(
                            f"Loading and validating {len(status_specs)} live or official "
                            "market snapshots."
                        ),
                    )
                for spec in status_specs:
                    source_type = spec.source_type
                    requested_underlyings = self._risk_underlyings(
                        next_risk[source_type]
                    )
                    if spec.risk_type == "Commo" and not commodity_market_enabled:
                        _, raw_status = self._disabled_market_sources(
                            spec,
                            next_risk[source_type],
                            market_status=expected_market_status,
                        )
                    else:
                        raw_status = self._load_product_market_status(
                            spec,
                            market_date,
                            requested_underlyings,
                            market_status=expected_market_status,
                        )
                    try:
                        validated_status = get_product_market_status(
                            spec,
                            market_date,
                            raw_status,
                            market_status=expected_market_status,
                        )
                        self._reject_unrequested_market_underlyings(
                            validated_status,
                            requested_underlyings,
                            label=f"{spec.key} market status",
                        )
                        next_status[source_type] = validated_status
                    except Exception:
                        self._progress_activity(
                            "get_product_market_status",
                            "market_status",
                            source_type=source_type,
                            message="Live or official market validation failed.",
                        )
                        raise

                market_merge_types = (
                    open_source_types
                    | market_status_source_types
                    | (set(PRODUCT_SPECS_BY_SOURCE_TYPE) - set(next_market))
                )
                for spec in PRODUCT_SPECS.values():
                    if spec.source_type not in market_merge_types:
                        continue
                    merged_market = _merge_validated_market_legs(
                        spec,
                        next_open[spec.source_type],
                        next_status[spec.source_type],
                        selected_status=expected_market_status,
                    )
                    if spec.risk_type == "Commo" and not commodity_market_enabled:
                        merged_market[MARKET_DATA_STATUS] = "Commodity market disabled"
                    next_market[spec.source_type] = merged_market

                # Recalculate only products whose risk or market status changed.
                # The previous successful product frames remain immutable until
                # the whole refresh succeeds, preserving transactional fallback.
                if recalculate_source_types:
                    self._wait_for_stage("pl", has_snapshot=base_snapshot is not None)
                next_pl = {} if force_risk else base_pl_frames
                pl_specs = [
                    spec
                    for spec in PRODUCT_SPECS.values()
                    if spec.source_type in recalculate_source_types
                    or spec.source_type not in next_pl
                ]
                if pl_specs:
                    self._progress_step(
                        "get_product_pl",
                        "pl",
                        message=f"Calculating P&L for {len(pl_specs)} products.",
                    )
                for key, spec in PRODUCT_SPECS.items():
                    source_type = spec.source_type
                    if (
                        source_type not in recalculate_source_types
                        and source_type in next_pl
                    ):
                        continue
                    try:
                        pl_frame = get_product_pl(
                            spec,
                            next_dates[source_type],
                            multiplier=self._multipliers.get(key, 1.0),
                            validated_risk=next_risk[source_type],
                            validated_market=next_market[source_type],
                            market_date=market_date,
                            market_status=expected_market_status,
                        )
                        if spec.risk_type == "Commo" and not commodity_market_enabled:
                            pl_frame[OPEN] = 0.0
                            pl_frame[CURRENT] = 0.0
                            pl_frame[MARKET_MOVE] = 0.0
                            pl_frame[PL] = 0.0
                            pl_frame[MARKET_AVAILABLE] = True
                            pl_frame[MARKET_DATA_STATUS] = "Commodity market disabled"
                    except Exception:
                        self._progress_activity(
                            "get_product_pl",
                            "pl",
                            source_type=source_type,
                            message="Product P&L calculation failed.",
                        )
                        raise
                    pl_frame[SOURCE_TYPE] = source_type
                    pl_frame[RISK_DATE] = next_dates[source_type]
                    pl_frame[MARKET_DATE] = market_date
                    next_pl[source_type] = _with_dashboard_tenors(pl_frame, spec)

                next_overlay_frames = dict(base_overlay_frames)
                for overlay_key, split, loader in (
                    ("cross_gamma", "Cross Gamma", self._cross_gamma_loader),
                    ("new_position", "New Position", self._new_position_loader),
                ):
                    if loader is None:
                        continue
                    self._progress_step(
                        _callable_name(loader),
                        "pl",
                        message=f"Loading developed risk for {split}.",
                    )
                    overlay = build_risk_overlay(
                        loader(market_date),
                        split=split,
                        market_frames=next_market,
                    )
                    if not overlay.empty:
                        overlay[RISK_DATE] = market_date
                        overlay[MARKET_DATE] = market_date
                    next_overlay_frames[overlay_key] = overlay

                self._progress_step(
                    "_commit_full_snapshot",
                    "final",
                    message="Combining, validating and atomically publishing the snapshot.",
                )
                if force_risk or market_date_changed or base_config is None:
                    portfolio_date = checker_date
                    next_config = _load_portfolio_config(
                        self._config_source, portfolio_date
                    )
                else:
                    next_config = base_config
                if force_risk or base_thresholds is None:
                    next_thresholds = load_thresholds(self._threshold_source)
                else:
                    next_thresholds = base_thresholds
                mapping_function = (
                    _callable_name(self._reported_underlying_source)
                    if callable(self._reported_underlying_source)
                    else "load_reported_underlyings"
                )
                self._progress_step(
                    mapping_function,
                    "reporting_mapping",
                    message="Loading Reported Underlying mapping.",
                )
                next_reported_underlyings = load_reported_underlyings(
                    self._reported_underlying_source
                )
                enriched, dashboard, unmapped = self._release_pl_views(
                    next_pl,
                    next_config,
                    next_thresholds,
                    next_reported_underlyings,
                    next_overlay_frames,
                )
                revision = 1 if base_snapshot is None else base_snapshot.revision + 1
                completed_at = self._now()
                snapshot = RefreshSnapshot(
                    revision=revision,
                    refreshed_at=completed_at,
                    last_attempt_at=attempted_at,
                    refresh_reason=reason,
                    system_date=system_date,
                    market_date=market_date,
                    checker_date=checker_date,
                    market_status=expected_market_status,
                    forced_view_date=forced_view_date,
                    risk_status=status,
                    risk_checker=next_risk_checker,
                    risk_checker_enabled=checker_enabled,
                    commodity_market_enabled=commodity_market_enabled,
                    risk_dates=next_dates,
                    forced_dates=overrides,
                    changed_source_types=tuple(sorted(changed_source_types)),
                    open_refreshed_source_types=tuple(sorted(open_source_types)),
                    market_refreshed_source_types=tuple(
                        sorted(market_status_source_types)
                    ),
                    combined_pl=enriched,
                    market_frame=self._combined_market_frame(next_market, market_date),
                    dashboard_frame=dashboard,
                    unmapped_frame=unmapped,
                    errors=(),
                )

                search_catalog = self._build_snapshot_search_catalog(
                    revision=revision,
                    risk_frames=next_risk,
                    market_frames=next_market,
                    dashboard=dashboard,
                    risk_dates=next_dates,
                    market_date=market_date,
                    market_status=expected_market_status,
                )

                self._commit_full_snapshot(
                    snapshot,
                    config=next_config,
                    thresholds=next_thresholds,
                    reported_underlyings=next_reported_underlyings,
                    risk_frames=next_risk,
                    market_open_frames=next_open,
                    market_status_frames=next_status,
                    market_frames=next_market,
                    pl_frames=next_pl,
                    overlay_frames=next_overlay_frames,
                    risk_dates=next_dates,
                    market_date=market_date,
                    search_catalog=search_catalog,
                )
                self._finish_progress()
                return self._copy_snapshot(snapshot)
            except Exception as error:
                incident_id = uuid.uuid4().hex[:10]
                error_type = (
                    re.sub(r"[^A-Za-z0-9_.-]", "_", type(error).__name__) or "Exception"
                )
                LOGGER.exception(
                    "Risk refresh failed; incident=%s type=%s location=%s",
                    incident_id,
                    error_type,
                    _safe_failure_location(error),
                )
                safe_error = f"Refresh failed (incident {incident_id})."
                failed_progress = self.progress
                if base_snapshot is None:
                    self._finish_progress(
                        error=safe_error,
                        failed_function_name=failed_progress.function_name,
                        failed_source_type=failed_progress.source_type,
                        failed_underlying=failed_progress.underlying,
                        failed_product_label=failed_progress.product_label,
                        failed_product_index=failed_progress.product_index,
                        failed_product_total=failed_progress.product_total,
                        failed_hold_seconds=failed_progress.hold_seconds,
                    )
                    raise
                message = (
                    f"{attempted_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | "
                    f"Refresh failed (incident {incident_id}); last successful data retained."
                )
                snapshot = replace(
                    base_snapshot,
                    last_attempt_at=attempted_at,
                    refresh_reason=reason,
                    changed_source_types=(),
                    open_refreshed_source_types=(),
                    market_refreshed_source_types=(),
                    errors=(message,),
                )
                self._set_progress_total(self.progress.current + 1)
                self._progress_step(
                    "_commit_snapshot",
                    "commit",
                    message="Retaining the last successful snapshot.",
                )
                self._commit_snapshot(snapshot)
                self._finish_progress(
                    error=safe_error,
                    failed_function_name=failed_progress.function_name,
                    failed_source_type=failed_progress.source_type,
                    failed_underlying=failed_progress.underlying,
                    failed_product_label=failed_progress.product_label,
                    failed_product_index=failed_progress.product_index,
                    failed_product_total=failed_progress.product_total,
                    failed_hold_seconds=failed_progress.hold_seconds,
                )
                return self._copy_snapshot(snapshot)
        finally:
            self._refresh_lock.release()


__all__ = [
    "AGE",
    "AGE_DEFAULTED",
    "CHECKER_DATE",
    "ControlSnapshot",
    "CREDIT_MEASURE_COLUMNS",
    "CREDIT_MEASURES",
    "CURRENT",
    "EFFECTIVE_RISK_DATE",
    "FORCE_RISK",
    "FrameName",
    "FrameRead",
    "LIVE",
    "MARKET_AVAILABLE",
    "MARKET_DATE",
    "MARKET_DATA_STATUS",
    "MARKET_STATUS",
    "MarketStatusResolver",
    "OFFICIAL",
    "PRODUCT_SPECS",
    "PRODUCT_SPECS_BY_SOURCE_TYPE",
    "PRODUCT",
    "PRODUCT_LABELS",
    "PORTFOLIO_MAPPED",
    "PLSnapshot",
    "ProductionIntegrationError",
    "ProductConnectorAdapter",
    "ProductSpec",
    "RefreshHealthSnapshot",
    "RefreshInProgressError",
    "RefreshProgressSnapshot",
    "RefreshSnapshot",
    "REPORTED_UNDERLYING",
    "REGION",
    "RISK_OVERLAY_COLUMNS",
    "RiskOverlayLoader",
    "RiskRefreshManager",
    "SearchResult",
    "StaleRefreshError",
    "SUBCATEGORY",
    "SUGGESTED_RISK_DATE",
    "TENOR_COLUMNS",
    "TENOR_OPTION",
    "TENOR_OPTION_ORDER",
    "TENOR_ORDER_COLUMNS",
    "TENOR_SWAP",
    "TENOR_SWAP_ORDER",
    "apply_thresholds",
    "build_all_pl",
    "build_risk_overlay",
    "build_dashboard_dataframe",
    "checker_date_for",
    "evaluate_promotions",
    "get_market_open",
    "get_market_status",
    "get_product_market",
    "get_product_market_open",
    "get_product_market_status",
    "get_product_pl",
    "get_product_risk",
    "get_risk",
    "load_config",
    "load_reported_underlyings",
    "load_thresholds",
    "merge_config",
    "risk_date_for",
    "to_dashboard_frame",
]
