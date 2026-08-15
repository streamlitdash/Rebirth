"""Site-owned connector boundary with explicit fake-CSV placeholders.

Cube reads the numbered files in its module-relative ``data`` directory. Every
reporting dimension in those files is marked ``FAKE_REPLACE_ME`` so placeholder
data cannot be mistaken for production.

Those files sit behind small public connector functions. The dated Risk Checker
function owns both readiness and inventory files. To connect real systems,
replace the marked body of each public loader below.
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

from core.s01_schema import (
    PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
    PORTFOLIO_FIELD_BY_KEY,
    TENOR_COLUMNS,
    TENOR_ORDER_COLUMNS,
)
from core.s06_cashflow import INTRADAY_CASHFLOW_COLUMNS

from core.s02_pipeline import (
    CREDIT_MEASURE_COLUMNS,
    CURRENT,
    LIVE,
    MARKET_STATUS,
    OFFICIAL,
    PRODUCT_SPECS_BY_SOURCE_TYPE,
    REGION,
    RISK_OVERLAY_COLUMNS,
    ProductConnectorAdapter,
    RiskRefreshManager,
)


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
) -> str:
    """Resolve the one authoritative Live/OFFICIAL source for a market date.

    Replace this function body with the real market-status service. The refresh
    manager calls it once per refresh, validates the exact returned value, and
    passes that same value to every per-Underlying Open and Current connector.
    The checked-in fake implementation uses the configured trading calendar day
    only so the runnable example has deterministic routing semantics.
    """

    selected_date = _normalized_date(market_date, parameter="market_date")
    zone = ZoneInfo(trading_timezone)
    trading_today = pd.Timestamp(datetime.now(zone).date())
    return LIVE if selected_date == trading_today else OFFICIAL


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
    # REPLACE THESE FAKE CSV READS WITH YOUR ONE REAL RISK-CHECKER FUNCTION.
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
    # REPLACE THIS FAKE CSV READ WITH YOUR REAL RISK/DRISK CONNECTOR.
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


def get_new_positions(market_date: pd.Timestamp) -> pd.DataFrame:
    """Return fixture intraday new-position target-risk rows.

    The deterministic demo has no invented positions, so an exact header-only
    frame is the truthful fixture.  A production replacement returns the same
    seven canonical fields and the pipeline supplies ``Split = New Position``.
    """

    _normalized_date(market_date, parameter="market_date")
    return pd.DataFrame(columns=list(RISK_OVERLAY_COLUMNS))


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
    # REPLACE THIS FAKE CSV READ WITH YOUR REAL OPEN-MARKET CONNECTOR.
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
    # REPLACE THIS FAKE CSV READ WITH YOUR REAL LIVE/OFFICIAL MARKET CONNECTOR.
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
    _normalized_date(portfolio_date, parameter="portfolio_date")
    # REPLACE THIS FAKE CSV READ WITH YOUR REAL PORTFOLIO CONFIG CONNECTOR.
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
    # REPLACE THIS FAKE CSV READ WITH YOUR REAL RISK-THRESHOLD CONNECTOR.
    return _read_fake_csv("risk_thresholds").copy()


def get_reported_underlyings() -> pd.DataFrame:
    """Return the cross-product raw-to-reporting Underlying map.

    Real replacement contract: return exactly ``Risk Type``, ``Risk Greek``,
    ``Underlying``, and ``Reported Underlying``. The first three columns form a
    unique source key; multiple source keys may share one reported target.
    """

    # REPLACE THIS FAKE CSV READ WITH YOUR REAL REPORTING-MAPPING CONNECTOR.
    frame = _read_fake_csv("reported_underlyings")
    _require_fake_notice(
        frame,
        ["Underlying", "Reported Underlying"],
        dataset="reported_underlyings",
    )
    return frame.copy()


def get_intraday_cashflows(cashflow_date: pd.Timestamp) -> pd.DataFrame:
    """Return demo cashflows for the new page through its own connector.

    Replace only this function body with the real cashflow API. The canonical
    validator in ``core/s06_cashflow.py`` rejects aliases or malformed values.
    """
    selected = _normalized_date(cashflow_date, parameter="cashflow_date")
    timestamp = selected.tz_localize("UTC")
    rows = [
        (
            "CF-001",
            timestamp + pd.Timedelta(hours=8, minutes=15),
            "BOOK_A",
            "SOG_ALPHA",
            "USD",
            "Settlement",
            1_250_000.0,
            "Sent",
        ),
        (
            "CF-002",
            timestamp + pd.Timedelta(hours=9, minutes=40),
            "BOOK_B",
            "SOG_ALPHA",
            "GBP",
            "Coupon",
            -275_500.0,
            "Pending",
        ),
        (
            "CF-003",
            timestamp + pd.Timedelta(hours=11, minutes=5),
            "BOOK_C",
            "SOG_BETA",
            "EUR",
            "Premium",
            540_000.0,
            "Confirmed",
        ),
        (
            "CF-004",
            timestamp + pd.Timedelta(hours=13, minutes=20),
            "BOOK_D",
            "SOG_BETA",
            "JPY",
            "Settlement",
            -82_000_000.0,
            "Pending",
        ),
    ]
    return pd.DataFrame(
        [
            {
                "Cashflow ID": f"FAKE_REPLACE_ME-{cashflow_id}",
                "Cashflow Time": cashflow_time,
                "Value Date": selected + pd.offsets.BDay(1),
                "Portfolio": f"FAKE_REPLACE_ME - {portfolio}",
                "SignoffGroup": f"FAKE_REPLACE_ME - {signoff_group}",
                "Currency": currency,
                "Cashflow Type": cashflow_type,
                "Amount": amount,
                "Status": status,
            }
            for cashflow_id, cashflow_time, portfolio, signoff_group, currency, cashflow_type, amount, status in rows
        ],
        columns=list(INTRADAY_CASHFLOW_COLUMNS),
    )


def send_sog_pl(frame: pd.DataFrame) -> None:
    """Reject external SOG delivery while the fixture boundary is active."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("send_sog_pl expects a pandas DataFrame")
    raise RuntimeError(
        "External SOG delivery is disabled in fixture mode; "
        "replace feeds.s01_sources.send_sog_pl for an authorized deployment"
    )


def send_portfolio_pl(frame: pd.DataFrame) -> None:
    """Reject external Portfolio delivery while the fixture boundary is active."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("send_portfolio_pl expects a pandas DataFrame")
    raise RuntimeError(
        "External Portfolio delivery is disabled in fixture mode; "
        "replace feeds.s01_sources.send_portfolio_pl for an authorized deployment"
    )


def get_product_connector_adapters() -> Mapping[str, ProductConnectorAdapter]:
    """Return per-source real adapters when APIs cannot share generic loaders.

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
    return RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        reported_underlyings=get_reported_underlyings,
        risk_checker_loader=get_risk_checker,
        market_status_resolver=resolve_market_state,
        risk_loader=get_risk,
        cross_gamma_loader=get_cross_gamma_risk,
        new_position_loader=get_new_positions,
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
    "get_market_open",
    "get_market_state",
    "get_market_status",
    "get_intraday_cashflows",
    "get_new_positions",
    "get_portfolio_config",
    "get_product_connector_adapters",
    "get_reported_underlyings",
    "get_risk",
    "get_risk_checker",
    "get_risk_thresholds",
    "send_portfolio_pl",
    "send_sog_pl",
]
