"""Pure P&L-send mapping, aggregation, and adjustment rules.

This module deliberately performs no application, connector, or filesystem I/O
other than reading explicitly supplied CSV paths.  Dash callbacks and production
senders can therefore share one fail-closed financial data contract.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TypeAlias

import numpy as np
import pandas as pd

from core.s01_schema import (
    PL_SIGNOFF_COLUMN,
    PORTFOLIO_COLUMN,
    PORTFOLIO_MAPPED_COLUMN,
    PORTFOLIO_METADATA_COLUMNS,
    UNMAPPED_VALUE,
)
from core.s09_cross_gamma import (
    CROSS_GAMMA_SOURCE_SPLIT,
    XGAMMA_SOURCE_RISK_GREEKS,
)


RISK_TYPE = "Risk Type"
RISK_GREEK = "Risk Greek"
SPLIT = "Split"
PORTFOLIO = PORTFOLIO_COLUMN
SIGNOFF_GROUP = PL_SIGNOFF_COLUMN
CONCERTO_FIELD = "ConcertoField"
PL = "PL"
ADJUSTMENT = "Adjustment"
MARKET_DATE = "Market Date"
ACTIVITY = "Activity"
CATEGORY = "Category"
SUB_CATEGORY = "Sub Category"

MAPPING_COLUMNS = (RISK_TYPE, RISK_GREEK, CONCERTO_FIELD)
PL_SEND_COLUMNS = (
    MARKET_DATE,
    RISK_TYPE,
    RISK_GREEK,
    PORTFOLIO,
    SIGNOFF_GROUP,
    CONCERTO_FIELD,
    PL,
    ADJUSTMENT,
)
PL_SEND_KEY = (PORTFOLIO, CONCERTO_FIELD)
ADJUSTMENT_KEY = (MARKET_DATE, PORTFOLIO, CONCERTO_FIELD)
HISTORICAL_PL_COLUMNS = (MARKET_DATE, PORTFOLIO, CONCERTO_FIELD, PL)
HISTORICAL_PL_KEY = ADJUSTMENT_KEY
HISTORY_TYPE = "P&L Type"
COLOSSUS_TYPE = "Colossus"
PREDICT_TYPE = "Predict"
# Compatibility names for callers that still describe the backing file names.
# Their values deliberately remain the canonical, user-facing labels.
HISTO_TYPE = COLOSSUS_TYPE
PREDICTED_TYPE = PREDICT_TYPE
PL_HISTORY_TYPES = (COLOSSUS_TYPE, PREDICT_TYPE)
PL_HISTORY_PERIOD = "Period"
PL_HISTORY_PERIOD_START = "Start Date"
PL_HISTORY_PERIOD_END = "End Date"
PL_HISTORY_DAILY_PERIOD = "Daily (P)"
PL_HISTORY_WTD_PERIOD = "WTD"
PL_HISTORY_MTD_PERIOD = "MTD"
PL_HISTORY_YTD_PERIOD = "YTD"
PL_HISTORY_PERIODS = (
    PL_HISTORY_DAILY_PERIOD,
    PL_HISTORY_WTD_PERIOD,
    PL_HISTORY_MTD_PERIOD,
    PL_HISTORY_YTD_PERIOD,
)
UNDERLYING = "Underlying"
PRODUCT = "Product"
BOOK = "Book"
HISTORY_MAPPING_STATUS = "Mapping Status"
HISTORY_FILE_IDENTITY_COLUMNS = (
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    PRODUCT,
    BOOK,
)
HISTORY_FILE_COLUMNS = (*HISTORY_FILE_IDENTITY_COLUMNS, PL)
HISTORY_FILTER_COLUMNS = (
    ACTIVITY,
    SIGNOFF_GROUP,
    PORTFOLIO,
    CATEGORY,
    SUB_CATEGORY,
)
HISTORY_IDENTITY_COLUMNS = (
    SIGNOFF_GROUP,
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    PRODUCT,
    PORTFOLIO,
)
HISTORY_DIMENSION_COLUMNS = (
    ACTIVITY,
    SIGNOFF_GROUP,
    CATEGORY,
    SUB_CATEGORY,
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    PRODUCT,
    PORTFOLIO,
)
PL_HISTORY_COLUMNS = (
    MARKET_DATE,
    HISTORY_TYPE,
    *HISTORY_DIMENSION_COLUMNS,
    HISTORY_MAPPING_STATUS,
    PL,
)
PL_HISTORY_KEY = (MARKET_DATE, HISTORY_TYPE, *HISTORY_IDENTITY_COLUMNS)
PL_HISTORY_SERIES_COLUMNS = (MARKET_DATE, HISTORY_TYPE, PL)
PL_HISTORY_PERIOD_COLUMNS = (
    PL_HISTORY_PERIOD,
    PL_HISTORY_PERIOD_START,
    PL_HISTORY_PERIOD_END,
    HISTORY_TYPE,
    PL,
)

_HISTORY_DATE_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])")
_HISTORY_FILES = {
    "histo.csv": HISTO_TYPE,
    "predicted.csv": PREDICTED_TYPE,
}
_OPTIONAL_HISTORY_FILES = frozenset(("market.csv",))
_HISTORY_TYPE_ALIASES = {
    "actual": COLOSSUS_TYPE,
    "colossus": COLOSSUS_TYPE,
    "histo": COLOSSUS_TYPE,
    "historical": COLOSSUS_TYPE,
    "real": COLOSSUS_TYPE,
    "predict": PREDICT_TYPE,
    "predicted": PREDICT_TYPE,
}

FrameSource: TypeAlias = pd.DataFrame | str | Path


class PLSendValidationError(ValueError):
    """Raised when a PL-send mapping or row set violates its governed schema."""


def _read_frame(source: FrameSource, *, label: str) -> pd.DataFrame:
    if isinstance(source, (str, Path)):
        try:
            return pd.read_csv(
                source,
                dtype="string",
                encoding="utf-8-sig",
                keep_default_na=False,
            )
        except (OSError, UnicodeError, pd.errors.ParserError) as exc:
            raise PLSendValidationError(f"Could not read {label}: {exc}") from exc
    if not isinstance(source, pd.DataFrame):
        raise TypeError(f"{label} must be a pandas DataFrame or CSV path")
    return source.copy(deep=True)


def _require_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...] | list[str],
    *,
    label: str,
) -> None:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise PLSendValidationError(f"{label} is missing required columns: {missing}")


def _normalise_text_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...] | list[str],
    *,
    label: str,
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        values = result[column]
        is_text = values.map(lambda value: isinstance(value, str)).astype(bool)
        non_text = values.notna() & ~is_text
        blank = values.isna() | values.astype("string").str.strip().eq("")
        invalid = non_text | blank
        if invalid.any():
            rows = result.index[invalid].tolist()[:5]
            raise PLSendValidationError(
                f"{label} column {column!r} must contain nonblank text; invalid rows {rows}"
            )
        result[column] = values.astype(str).str.strip()
    return result


def normalize_market_date(value: object) -> str:
    """Return one date-only ISO value for use in adjustment identities."""
    if value is None or isinstance(value, (bool, np.bool_)) or str(value).strip() == "":
        raise PLSendValidationError("Market Date must be a valid date")
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise PLSendValidationError("Market Date must be a valid date") from exc
    if pd.isna(timestamp):
        raise PLSendValidationError("Market Date must be a valid date")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp.date().isoformat()


def _normalise_market_dates(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    result = frame.copy()
    values: list[str] = []
    for index, value in result[MARKET_DATE].items():
        try:
            values.append(normalize_market_date(value))
        except PLSendValidationError as exc:
            raise PLSendValidationError(
                f"{label} column {MARKET_DATE!r} is invalid at row {index}"
            ) from exc
    result[MARKET_DATE] = values
    return result


def _normalise_pl(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    result = frame.copy()
    values = result[PL]
    boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = boolean | numeric.isna() | ~np.isfinite(numeric)
    if invalid.any():
        rows = result.index[invalid].tolist()[:5]
        raise PLSendValidationError(
            f"{label} column {PL!r} must contain finite numbers; invalid rows {rows}"
        )
    result[PL] = numeric.astype(float)
    return result


def _normalise_adjustment(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    result = frame.copy()
    values = result[ADJUSTMENT]
    invalid = ~values.map(lambda value: isinstance(value, (bool, np.bool_)))
    if invalid.any():
        rows = result.index[invalid].tolist()[:5]
        raise PLSendValidationError(
            f"{label} column {ADJUSTMENT!r} must contain booleans; invalid rows {rows}"
        )
    result[ADJUSTMENT] = values.astype(bool)
    return result


def load_plsend_mapping(source: FrameSource) -> pd.DataFrame:
    """Load the governed one-to-one Risk Type/Greek-to-ConcertoField mapping."""
    frame = _read_frame(source, label="PLSEND mapping")
    actual_columns = tuple(str(column).strip() for column in frame.columns)
    if actual_columns != MAPPING_COLUMNS:
        raise PLSendValidationError(
            "PLSEND mapping must have exactly these columns in order: "
            f"{list(MAPPING_COLUMNS)}; found {list(actual_columns)}"
        )
    frame.columns = list(MAPPING_COLUMNS)
    frame = _normalise_text_columns(frame, MAPPING_COLUMNS, label="PLSEND mapping")
    if frame.empty:
        raise PLSendValidationError("PLSEND mapping must contain at least one row")

    duplicate_pairs = frame.duplicated([RISK_TYPE, RISK_GREEK], keep=False)
    if duplicate_pairs.any():
        records = (
            frame.loc[duplicate_pairs, [RISK_TYPE, RISK_GREEK]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"PLSEND mapping contains duplicate Risk Type + Risk Greek pairs: {records}"
        )
    duplicate_names = frame.duplicated(CONCERTO_FIELD, keep=False)
    if duplicate_names.any():
        names = sorted(frame.loc[duplicate_names, CONCERTO_FIELD].unique().tolist())
        raise PLSendValidationError(
            f"PLSEND mapping contains ConcertoField values assigned to multiple pairs: {names}"
        )
    return frame.reset_index(drop=True)


def load_historical_pl(source: FrameSource) -> pd.DataFrame:
    """Load one governed daily P&L value per Portfolio and ConcertoField."""
    frame = _read_frame(source, label="historical P&L")
    actual_columns = tuple(str(column).strip() for column in frame.columns)
    if actual_columns != HISTORICAL_PL_COLUMNS:
        raise PLSendValidationError(
            "historical P&L must have exactly these columns in order: "
            f"{list(HISTORICAL_PL_COLUMNS)}; found {list(actual_columns)}"
        )
    frame.columns = list(HISTORICAL_PL_COLUMNS)
    frame = _normalise_text_columns(
        frame,
        [PORTFOLIO, CONCERTO_FIELD],
        label="historical P&L",
    )
    frame = _normalise_market_dates(frame, label="historical P&L")
    frame = _normalise_pl(frame, label="historical P&L")

    duplicate_keys = frame.duplicated(list(HISTORICAL_PL_KEY), keep=False)
    if duplicate_keys.any():
        keys = (
            frame.loc[duplicate_keys, list(HISTORICAL_PL_KEY)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            "historical P&L contains duplicate Market Date + Portfolio + "
            f"ConcertoField keys: {keys}"
        )
    return frame.sort_values(list(HISTORICAL_PL_KEY), kind="stable").reset_index(
        drop=True
    )


def _history_directory_entries(directory: Path, *, label: str) -> list[Path]:
    """Return a stable directory listing with storage errors in the PL domain."""
    try:
        return sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise PLSendValidationError(f"Could not inspect {label}: {exc}") from exc


def _history_leaf_date(value: str) -> str:
    """Validate and normalize one flat ``YYYY-MM-DD`` history partition."""
    if not _HISTORY_DATE_PATTERN.fullmatch(value):
        raise PLSendValidationError(
            f"P&L history date directory must be YYYY-MM-DD; found {value!r}"
        )
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise PLSendValidationError(
            f"P&L history date directory is not a valid date: {value}"
        ) from exc


def _load_history_leaf_file(
    source: Path,
    *,
    market_date: str,
    history_type: str,
) -> pd.DataFrame:
    """Load one strictly shaped actual or predicted history partition."""
    label = f"P&L history file {source}"
    frame = _read_frame(source, label=label)
    actual_columns = tuple(str(column).strip() for column in frame.columns)
    if actual_columns != HISTORY_FILE_COLUMNS:
        raise PLSendValidationError(
            f"{label} must have exactly these columns in order: "
            f"{list(HISTORY_FILE_COLUMNS)}; found {list(actual_columns)}"
        )
    frame.columns = list(HISTORY_FILE_COLUMNS)
    frame = _normalise_text_columns(
        frame,
        list(HISTORY_FILE_IDENTITY_COLUMNS),
        label=label,
    )
    frame = _normalise_pl(frame, label=label)
    duplicate_keys = frame.duplicated(list(HISTORY_FILE_IDENTITY_COLUMNS), keep=False)
    if duplicate_keys.any():
        keys = (
            frame.loc[duplicate_keys, list(HISTORY_FILE_IDENTITY_COLUMNS)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"{label} contains duplicate history identity keys: {keys}"
        )
    frame = frame.rename(columns={BOOK: PORTFOLIO})
    for column in (ACTIVITY, SIGNOFF_GROUP, CATEGORY, SUB_CATEGORY):
        frame[column] = UNMAPPED_VALUE
    frame[HISTORY_MAPPING_STATUS] = UNMAPPED_VALUE
    frame.insert(0, HISTORY_TYPE, history_type)
    frame.insert(0, MARKET_DATE, market_date)
    return frame[list(PL_HISTORY_COLUMNS)]


def load_legacy_pl_history_leaf(source: str | Path) -> pd.DataFrame:
    """Load one strict legacy ``YYYY-MM-DD`` P&L history leaf."""

    leaf = Path(source)
    if not leaf.exists() or not leaf.is_dir():
        raise PLSendValidationError(
            f"legacy P&L history leaf must be an existing directory; found {leaf}"
        )
    market_date = _history_leaf_date(leaf.name)
    file_entries = _history_directory_entries(
        leaf,
        label=f"P&L history date {leaf}",
    )
    names = {entry.name for entry in file_entries if entry.is_file()}
    unexpected = [entry.name for entry in file_entries if not entry.is_file()]
    unexpected.extend(
        sorted(names - set(_HISTORY_FILES) - set(_OPTIONAL_HISTORY_FILES))
    )
    missing = sorted(set(_HISTORY_FILES) - names)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unexpected:
            details.append(f"unexpected {sorted(unexpected)}")
        raise PLSendValidationError(
            f"P&L history date {leaf.name} must contain "
            "histo.csv and predicted.csv, with optional market.csv; "
            + "; ".join(details)
        )
    partitions = [
        _load_history_leaf_file(
            leaf / filename,
            market_date=market_date,
            history_type=history_type,
        )
        for filename, history_type in _HISTORY_FILES.items()
    ]
    return pd.concat(partitions, ignore_index=True)


def _load_pl_history_uncached(source: FrameSource) -> pd.DataFrame:
    """Load paired actual/predicted P&L from ``YYYY-MM-DD`` partitions.

    A directory source must contain only ``YYYY-MM-DD`` leaf directories. Every
    leaf must contain ``histo.csv`` and ``predicted.csv`` and may also contain
    the independently validated historical Quick Market ``market.csv``. Their
    exact P&L leaf grain is Risk Type + Risk Greek + Underlying + Product +
    Book; Market Date and P&L Type are authoritative in the partition path and
    file name. Legacy rows have no archived portfolio-governance authority, so
    Activity, SignoffGroup, Category, and Sub Category are explicitly labelled
    ``Unmapped`` and Book is carried forward as Portfolio without guessing.

    The former Portfolio + ConcertoField shape cannot be promoted safely: it
    does not contain the requested hierarchy identities. ``load_historical_pl``
    remains available for callers that still own that legacy contract, while
    this paired-history reader fails closed on it.
    """
    if isinstance(source, pd.DataFrame):
        raise PLSendValidationError(
            "paired P&L history requires a YYYY-MM-DD directory with strict "
            "Risk Type, Risk Greek, Underlying, Product, Book, PL files"
        )
    if not isinstance(source, (str, Path)):
        raise TypeError(
            "P&L history must be a pandas DataFrame, CSV path, or directory"
        )

    root = Path(source)
    if not root.exists() or not root.is_dir():
        raise PLSendValidationError(
            f"paired P&L history must be an existing YYYY-MM-DD directory; found {root}"
        )

    date_entries = _history_directory_entries(root, label=f"P&L history root {root}")
    if not date_entries:
        raise PLSendValidationError(f"P&L history root is empty: {root}")

    partitions: list[pd.DataFrame] = []
    for date_directory in date_entries:
        if not date_directory.is_dir():
            raise PLSendValidationError(
                "P&L history root may contain only YYYY-MM-DD directories; "
                f"found {date_directory}"
            )
        _history_leaf_date(date_directory.name)
        partitions.append(load_legacy_pl_history_leaf(date_directory))

    if not partitions:
        raise PLSendValidationError(f"P&L history root has no date partitions: {root}")
    history = pd.concat(partitions, ignore_index=True)
    duplicate_keys = history.duplicated(list(PL_HISTORY_KEY), keep=False)
    if duplicate_keys.any():
        keys = (
            history.loc[duplicate_keys, list(PL_HISTORY_KEY)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"P&L history contains duplicate daily hierarchy keys: {keys}"
        )
    return history.sort_values(list(PL_HISTORY_KEY), kind="stable").reset_index(
        drop=True
    )


def _pl_history_directory_signature(
    root: Path,
) -> tuple[tuple[str, str, int, int], ...]:
    """Fingerprint one history tree so filter changes reuse parsed CSV rows."""

    try:
        entries = sorted(root.rglob("*"), key=lambda path: path.as_posix())
        signature = []
        for entry in entries:
            stat = entry.stat()
            signature.append(
                (
                    entry.relative_to(root).as_posix(),
                    "directory" if entry.is_dir() else "file",
                    int(stat.st_mtime_ns),
                    int(stat.st_size),
                )
            )
        return tuple(signature)
    except OSError as exc:
        raise PLSendValidationError(
            f"Could not inspect P&L history root {root}: {exc}"
        ) from exc


@lru_cache(maxsize=16)
def _cached_pl_history_directory(
    root_text: str,
    signature: tuple[tuple[str, str, int, int], ...],
) -> pd.DataFrame:
    """Parse one immutable directory signature at most once per process."""

    del signature  # Its immutable value is intentionally part of the cache key.
    return _load_pl_history_uncached(Path(root_text))


def load_pl_history(source: FrameSource) -> pd.DataFrame:
    """Load strict paired history, caching unchanged directory revisions."""

    if isinstance(source, (str, Path)):
        root = Path(source)
        if root.exists() and root.is_dir():
            resolved = root.resolve()
            signature = _pl_history_directory_signature(resolved)
            return _cached_pl_history_directory(str(resolved), signature).copy(
                deep=True
            )
    return _load_pl_history_uncached(source)


def _canonical_pl_history_type(value: object, *, label: str) -> str:
    """Return the canonical user-facing label for one compatible type name."""

    if not isinstance(value, str) or not value.strip():
        raise PLSendValidationError(f"{label} must contain nonblank text")
    normalized = value.strip().casefold()
    try:
        return _HISTORY_TYPE_ALIASES[normalized]
    except KeyError as exc:
        raise PLSendValidationError(
            f"{label} must be one of {list(PL_HISTORY_TYPES)}; found {value!r}"
        ) from exc


def normalize_pl_history_types(
    history_types: object | Sequence[object] | None = None,
) -> tuple[str, ...]:
    """Normalize selected type names to canonical Colossus/Predict labels.

    ``Histo``/``Actual``/``Real`` and ``Predicted`` remain accepted input
    aliases. Output ordering is always stable and canonical, independent of
    the caller's selection order.
    """

    if history_types is None:
        return PL_HISTORY_TYPES
    if isinstance(history_types, str):
        values = (history_types,)
    elif isinstance(history_types, Sequence):
        values = tuple(history_types)
    else:
        raise TypeError("history types must be text or a sequence of text values")
    selected = {
        _canonical_pl_history_type(value, label="P&L history type") for value in values
    }
    return tuple(
        history_type for history_type in PL_HISTORY_TYPES if history_type in selected
    )


def _normalize_pl_history_for_analysis(history: pd.DataFrame) -> pd.DataFrame:
    """Validate an in-memory strict-history frame for pure analysis helpers."""

    if not isinstance(history, pd.DataFrame):
        raise TypeError("P&L history must be a pandas DataFrame")
    _require_columns(history, list(PL_HISTORY_COLUMNS), label="P&L history")
    normalized = history.loc[:, list(PL_HISTORY_COLUMNS)].copy(deep=True)
    normalized = _normalise_market_dates(normalized, label="P&L history")
    normalized = _normalise_text_columns(
        normalized,
        [*HISTORY_DIMENSION_COLUMNS, HISTORY_MAPPING_STATUS],
        label="P&L history",
    )
    invalid_mapping_status = ~normalized[HISTORY_MAPPING_STATUS].isin(
        ("Mapped", UNMAPPED_VALUE)
    )
    if invalid_mapping_status.any():
        values = sorted(
            normalized.loc[invalid_mapping_status, HISTORY_MAPPING_STATUS]
            .drop_duplicates()
            .tolist()
        )
        raise PLSendValidationError(
            f"P&L history Mapping Status must be 'Mapped' or 'Unmapped'; found {values}"
        )
    normalized[HISTORY_TYPE] = [
        _canonical_pl_history_type(value, label="P&L history type")
        for value in normalized[HISTORY_TYPE]
    ]
    normalized = _normalise_pl(normalized, label="P&L history")
    duplicate_keys = normalized.duplicated(list(PL_HISTORY_KEY), keep=False)
    if duplicate_keys.any():
        keys = (
            normalized.loc[duplicate_keys, list(PL_HISTORY_KEY)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"P&L history contains duplicate daily hierarchy keys: {keys}"
        )
    return normalized.sort_values(list(PL_HISTORY_KEY), kind="stable").reset_index(
        drop=True
    )


def validate_pl_history_frame(history: pd.DataFrame) -> pd.DataFrame:
    """Validate one already-loaded canonical Colossus/Predict history frame."""

    return _normalize_pl_history_for_analysis(history)


def _normalize_pl_history_path(path: Sequence[object]) -> tuple[str, ...]:
    """Validate one ordered SignoffGroup-to-Portfolio hierarchy prefix."""

    if isinstance(path, (str, bytes)) or not isinstance(path, Sequence):
        raise TypeError("P&L history path must be a sequence")
    if len(path) > len(HISTORY_IDENTITY_COLUMNS):
        raise PLSendValidationError(
            "P&L history path cannot be deeper than SignoffGroup, Risk Type, "
            "Risk Greek, Underlying, Product, Portfolio"
        )
    normalized: list[str] = []
    for depth, value in enumerate(path):
        if value is None or isinstance(value, (bool, np.bool_)):
            raise PLSendValidationError(
                f"P&L history path value for {HISTORY_IDENTITY_COLUMNS[depth]!r} "
                "must be nonblank text"
            )
        text = str(value).strip()
        if not text:
            raise PLSendValidationError(
                f"P&L history path value for {HISTORY_IDENTITY_COLUMNS[depth]!r} "
                "must be nonblank text"
            )
        normalized.append(text)
    return tuple(normalized)


def _empty_pl_history_series() -> pd.DataFrame:
    return pd.DataFrame(
        {
            MARKET_DATE: pd.Series(dtype="string"),
            HISTORY_TYPE: pd.Series(dtype="string"),
            PL: pd.Series(dtype="float64"),
        }
    )


def _select_normalized_pl_history_series(
    history: pd.DataFrame,
    path: tuple[str, ...],
    history_types: tuple[str, ...],
) -> pd.DataFrame:
    """Aggregate a validated exact hierarchy prefix without filling its gaps."""

    if history.empty or not history_types:
        return _empty_pl_history_series()
    scoped = history.loc[history[HISTORY_TYPE].isin(history_types)]
    for column, value in zip(HISTORY_IDENTITY_COLUMNS[: len(path)], path, strict=True):
        scoped = scoped.loc[scoped[column].eq(value)]
    if scoped.empty:
        return _empty_pl_history_series()
    daily = scoped.groupby(
        [MARKET_DATE, HISTORY_TYPE],
        as_index=False,
        sort=False,
        observed=True,
    )[PL].sum(min_count=1)
    type_order = {
        history_type: index for index, history_type in enumerate(PL_HISTORY_TYPES)
    }
    daily["_P&L Type Order"] = daily[HISTORY_TYPE].map(type_order)
    daily = daily.sort_values(
        [MARKET_DATE, "_P&L Type Order"],
        kind="stable",
    ).drop(columns="_P&L Type Order")
    return daily.loc[:, list(PL_HISTORY_SERIES_COLUMNS)].reset_index(drop=True)


def select_pl_history_series(
    history: pd.DataFrame,
    path: Sequence[object] = (),
    history_types: object | Sequence[object] | None = None,
) -> pd.DataFrame:
    """Return observed daily P&L for one exact hierarchy prefix and type set.

    An empty path represents the total across all identities. The result has
    exactly one row per observed Market Date and P&L Type. Missing paths,
    dates, or types stay absent; this helper never inserts zero-valued rows.
    """

    normalized_history = _normalize_pl_history_for_analysis(history)
    normalized_path = _normalize_pl_history_path(path)
    normalized_types = normalize_pl_history_types(history_types)
    return _select_normalized_pl_history_series(
        normalized_history,
        normalized_path,
        normalized_types,
    )


def pl_history_period_bounds(as_of: object) -> dict[str, tuple[str, str]]:
    """Return inclusive Daily/WTD/MTD/YTD bounds ending on ``as_of``.

    WTD always starts on the calendar Monday containing ``as_of``. The helper
    intentionally does not snap to an observed date; absent dates must remain
    distinguishable from genuine zero P&L.
    """

    end = pd.Timestamp(normalize_market_date(as_of))
    starts = {
        PL_HISTORY_DAILY_PERIOD: end,
        PL_HISTORY_WTD_PERIOD: end - pd.Timedelta(days=end.weekday()),
        PL_HISTORY_MTD_PERIOD: end.replace(day=1),
        PL_HISTORY_YTD_PERIOD: end.replace(month=1, day=1),
    }
    end_text = end.date().isoformat()
    return {
        period: (starts[period].date().isoformat(), end_text)
        for period in PL_HISTORY_PERIODS
    }


def _empty_pl_history_period_values() -> pd.DataFrame:
    return pd.DataFrame(
        {
            PL_HISTORY_PERIOD: pd.Series(dtype="string"),
            PL_HISTORY_PERIOD_START: pd.Series(dtype="string"),
            PL_HISTORY_PERIOD_END: pd.Series(dtype="string"),
            HISTORY_TYPE: pd.Series(dtype="string"),
            PL: pd.Series(dtype="float64"),
        }
    )


def pl_history_period_values(
    history: pd.DataFrame,
    path: Sequence[object] = (),
    as_of: object = None,
) -> pd.DataFrame:
    """Summarize one hierarchy prefix at the governed reporting periods.

    With no explicit ``as_of``, every path uses the latest date in the full
    history frame, rather than silently falling back to that path's last
    observation. ``Daily (P)`` therefore contains only Predict on that exact
    date. WTD, MTD, and YTD contain each observed Colossus/Predict total in
    their inclusive window. Missing observations produce no row, never zero.
    """

    normalized_history = _normalize_pl_history_for_analysis(history)
    normalized_path = _normalize_pl_history_path(path)
    if normalized_history.empty:
        if as_of is not None:
            normalize_market_date(as_of)
        return _empty_pl_history_period_values()
    resolved_as_of = (
        normalize_market_date(as_of)
        if as_of is not None
        else str(normalized_history[MARKET_DATE].max())
    )
    series = _select_normalized_pl_history_series(
        normalized_history,
        normalized_path,
        PL_HISTORY_TYPES,
    )
    if series.empty:
        return _empty_pl_history_period_values()

    bounds = pl_history_period_bounds(resolved_as_of)
    rows: list[dict[str, object]] = []
    for period in PL_HISTORY_PERIODS:
        start_date, end_date = bounds[period]
        period_types = (
            (PREDICT_TYPE,) if period == PL_HISTORY_DAILY_PERIOD else PL_HISTORY_TYPES
        )
        window = series.loc[
            series[MARKET_DATE].between(start_date, end_date, inclusive="both")
            & series[HISTORY_TYPE].isin(period_types)
        ]
        for history_type in period_types:
            values = window.loc[window[HISTORY_TYPE].eq(history_type), PL]
            if values.empty:
                continue
            value = values.sum(min_count=1)
            if pd.isna(value):
                continue
            rows.append(
                {
                    PL_HISTORY_PERIOD: period,
                    PL_HISTORY_PERIOD_START: start_date,
                    PL_HISTORY_PERIOD_END: end_date,
                    HISTORY_TYPE: history_type,
                    PL: float(value),
                }
            )
    if not rows:
        return _empty_pl_history_period_values()
    return pd.DataFrame(rows, columns=list(PL_HISTORY_PERIOD_COLUMNS))


def load_portfolio_governance(source: FrameSource) -> pd.DataFrame:
    """Return governed Portfolio metadata with exactly one row per Portfolio."""
    frame = _read_frame(source, label="portfolio governance")
    _require_columns(frame, [PORTFOLIO, SIGNOFF_GROUP], label="portfolio governance")
    text_columns = [PORTFOLIO, SIGNOFF_GROUP]
    text_columns.extend(
        column
        for column in PORTFOLIO_METADATA_COLUMNS
        if column != SIGNOFF_GROUP and column in frame
    )
    frame = _normalise_text_columns(
        frame,
        text_columns,
        label="portfolio governance",
    )
    duplicate_portfolios = frame.duplicated(PORTFOLIO, keep=False)
    if duplicate_portfolios.any():
        portfolios = sorted(
            frame.loc[duplicate_portfolios, PORTFOLIO].unique().tolist()
        )
        raise PLSendValidationError(
            f"portfolio governance contains duplicate portfolios: {portfolios}"
        )
    return frame.reset_index(drop=True)


def _portfolio_mapped_mask(frame: pd.DataFrame, *, label: str) -> pd.Series:
    """Return the explicit config-merge state; business fields are never flags."""
    _require_columns(frame, [PORTFOLIO_MAPPED_COLUMN], label=label)
    values = frame[PORTFOLIO_MAPPED_COLUMN]
    invalid = ~values.map(lambda value: isinstance(value, (bool, np.bool_)))
    if invalid.any():
        rows = frame.index[invalid].tolist()[:5]
        raise PLSendValidationError(
            f"{label} column {PORTFOLIO_MAPPED_COLUMN!r} must contain booleans; "
            f"invalid rows {rows}"
        )
    return values.astype(bool)


def normalize_pl_send_rows(
    rows: pd.DataFrame,
    *,
    label: str = "PL-send rows",
) -> pd.DataFrame:
    """Normalize the structural PL-send schema without applying governance."""
    if not isinstance(rows, pd.DataFrame):
        raise TypeError(f"{label} must be a pandas DataFrame")
    frame = rows.copy(deep=True)
    _require_columns(frame, list(PL_SEND_COLUMNS), label=label)
    frame = _normalise_text_columns(
        frame,
        [RISK_TYPE, RISK_GREEK, PORTFOLIO, SIGNOFF_GROUP, CONCERTO_FIELD],
        label=label,
    )
    frame = _normalise_market_dates(frame, label=label)
    frame = _normalise_pl(frame, label=label)
    frame = _normalise_adjustment(frame, label=label)
    return frame


def _apply_mapping_governance(
    rows: pd.DataFrame,
    mapping: pd.DataFrame,
    *,
    label: str,
) -> pd.DataFrame:
    expected = mapping.rename(columns={CONCERTO_FIELD: "_Expected ConcertoField"})
    result = rows.merge(
        expected,
        on=[RISK_TYPE, RISK_GREEK],
        how="left",
        validate="many_to_one",
        indicator="_mapping_merge",
    )
    missing = result["_mapping_merge"].ne("both")
    if missing.any():
        pairs = (
            result.loc[missing, [RISK_TYPE, RISK_GREEK]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"{label} contains Risk Type + Risk Greek pairs missing from the PLSEND mapping: {pairs}"
        )
    mismatch = result[CONCERTO_FIELD].ne(result["_Expected ConcertoField"])
    if mismatch.any():
        rows_list = result.index[mismatch].tolist()[:5]
        raise PLSendValidationError(
            f"{label} contains ConcertoField values that contradict the governed mapping; "
            f"invalid rows {rows_list}"
        )
    return result.drop(columns=["_Expected ConcertoField", "_mapping_merge"])


def _apply_portfolio_governance(
    rows: pd.DataFrame,
    governance: pd.DataFrame,
    *,
    label: str,
) -> pd.DataFrame:
    expected = governance[[PORTFOLIO, SIGNOFF_GROUP]].rename(
        columns={SIGNOFF_GROUP: "_Expected SignoffGroup"}
    )
    result = rows.merge(
        expected,
        on=PORTFOLIO,
        how="left",
        validate="many_to_one",
        indicator="_portfolio_merge",
    )
    missing = result["_portfolio_merge"].ne("both")
    if missing.any():
        portfolios = sorted(result.loc[missing, PORTFOLIO].unique().tolist())
        raise PLSendValidationError(
            f"{label} contains portfolios missing from governance: {portfolios}"
        )
    mismatch = result[SIGNOFF_GROUP].ne(result["_Expected SignoffGroup"])
    if mismatch.any():
        rows_list = result.index[mismatch].tolist()[:5]
        raise PLSendValidationError(
            f"{label} contains SignoffGroup values that contradict portfolio governance; "
            f"invalid rows {rows_list}"
        )
    return result.drop(columns=["_Expected SignoffGroup", "_portfolio_merge"])


def validate_pl_send_rows(
    rows: pd.DataFrame,
    mapping: FrameSource,
    portfolio_governance: FrameSource,
    *,
    require_adjustment: bool | None = None,
    allow_duplicates: bool = False,
    label: str = "PL-send rows",
) -> pd.DataFrame:
    """Validate row identities against both governed mapping dimensions."""
    frame = normalize_pl_send_rows(rows, label=label)
    governed_mapping = load_plsend_mapping(mapping)
    governed_portfolios = load_portfolio_governance(portfolio_governance)
    frame = _apply_mapping_governance(frame, governed_mapping, label=label)
    frame = _apply_portfolio_governance(frame, governed_portfolios, label=label)

    if require_adjustment is not None:
        invalid = frame[ADJUSTMENT].ne(bool(require_adjustment))
        if invalid.any():
            rows_list = frame.index[invalid].tolist()[:5]
            raise PLSendValidationError(
                f"{label} must have Adjustment={bool(require_adjustment)}; "
                f"invalid rows {rows_list}"
            )
    if not allow_duplicates and frame.duplicated(list(ADJUSTMENT_KEY)).any():
        keys = (
            frame.loc[
                frame.duplicated(list(ADJUSTMENT_KEY), keep=False),
                list(ADJUSTMENT_KEY),
            ]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"{label} contains duplicate Market Date + Portfolio + ConcertoField keys: {keys}"
        )
    return frame


def _mapped_raw_rows(
    raw_pl: pd.DataFrame,
    mapping: FrameSource,
    portfolio_governance: FrameSource,
    *,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not isinstance(raw_pl, pd.DataFrame):
        raise TypeError(f"{label} must be a pandas DataFrame")
    frame = raw_pl.copy(deep=True)
    _require_columns(
        frame,
        [MARKET_DATE, RISK_TYPE, RISK_GREEK, PORTFOLIO, SIGNOFF_GROUP, PL],
        label=label,
    )
    frame = frame.loc[_portfolio_mapped_mask(frame, label=label)].copy()

    text_columns = [RISK_TYPE, RISK_GREEK, PORTFOLIO, SIGNOFF_GROUP]
    if SPLIT in frame:
        text_columns.append(SPLIT)
    frame = _normalise_text_columns(frame, text_columns, label=label)
    frame = _normalise_market_dates(frame, label=label)
    frame = _normalise_pl(frame, label=label)
    if SPLIT in frame:
        source_mask = frame[RISK_GREEK].isin(XGAMMA_SOURCE_RISK_GREEKS) & frame[
            SPLIT
        ].eq(CROSS_GAMMA_SOURCE_SPLIT)
        invalid_source = source_mask & frame[PL].ne(0.0)
        if invalid_source.any():
            rows = frame.index[invalid_source].tolist()[:5]
            raise PLSendValidationError(
                f"{label} Cross Gamma source-sensitivity rows must have PL=0; "
                f"invalid rows {rows}"
            )
        frame = frame.loc[~source_mask].copy()
    governed_mapping = load_plsend_mapping(mapping)
    governed_portfolios = load_portfolio_governance(portfolio_governance)

    supplied_names: pd.Series | None = None
    if CONCERTO_FIELD in frame:
        frame = _normalise_text_columns(frame, [CONCERTO_FIELD], label=label)
        supplied_names = frame[CONCERTO_FIELD].copy()
        frame = frame.drop(columns=CONCERTO_FIELD)
    frame = frame.merge(
        governed_mapping,
        on=[RISK_TYPE, RISK_GREEK],
        how="left",
        validate="many_to_one",
        indicator="_mapping_merge",
    )
    missing = frame["_mapping_merge"].ne("both")
    if missing.any():
        pairs = (
            frame.loc[missing, [RISK_TYPE, RISK_GREEK]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"{label} contains Risk Type + Risk Greek pairs missing from the PLSEND mapping: {pairs}"
        )
    frame = frame.drop(columns="_mapping_merge")
    if supplied_names is not None:
        supplied_names.index = frame.index
        mismatch = supplied_names.ne(frame[CONCERTO_FIELD])
        if mismatch.any():
            rows_list = frame.index[mismatch].tolist()[:5]
            raise PLSendValidationError(
                f"{label} contains ConcertoField values that contradict the governed mapping; "
                f"invalid rows {rows_list}"
            )
    frame = _apply_portfolio_governance(
        frame,
        governed_portfolios,
        label=label,
    )
    return frame.reset_index(drop=True), governed_mapping, governed_portfolios


def empty_pl_send_frame() -> pd.DataFrame:
    """Return an empty frame with the canonical UI/domain columns."""
    return pd.DataFrame(columns=list(PL_SEND_COLUMNS))


def build_pl_send_base(
    combined_pl: pd.DataFrame,
    mapping: FrameSource,
    portfolio_governance: FrameSource,
) -> pd.DataFrame:
    """Aggregate mapped raw P&L to one date/Portfolio/ConcertoField row."""
    frame, governed_mapping, governed_portfolios = _mapped_raw_rows(
        combined_pl,
        mapping,
        portfolio_governance,
        label="combined P&L",
    )
    if frame.empty:
        return empty_pl_send_frame()

    identity_columns = [RISK_TYPE, RISK_GREEK, SIGNOFF_GROUP]
    identity_counts = frame.groupby(list(ADJUSTMENT_KEY), dropna=False)[
        identity_columns
    ].nunique(dropna=False)
    if identity_counts.gt(1).any().any():
        raise PLSendValidationError(
            "combined P&L does not have a single governed identity for each "
            "Market Date + Portfolio + ConcertoField"
        )
    grouped = frame.groupby(list(ADJUSTMENT_KEY), as_index=False, dropna=False).agg(
        {
            RISK_TYPE: "first",
            RISK_GREEK: "first",
            SIGNOFF_GROUP: "first",
            PL: lambda values: values.sum(min_count=1),
        }
    )
    grouped[ADJUSTMENT] = False
    grouped = grouped[
        [
            MARKET_DATE,
            RISK_TYPE,
            RISK_GREEK,
            PORTFOLIO,
            SIGNOFF_GROUP,
            CONCERTO_FIELD,
            PL,
            ADJUSTMENT,
        ]
    ]
    validated = validate_pl_send_rows(
        grouped,
        governed_mapping,
        governed_portfolios,
        require_adjustment=False,
        label="aggregated PL-send base",
    )
    return validated.sort_values(list(ADJUSTMENT_KEY), kind="stable").reset_index(
        drop=True
    )


def collapse_pl_send_rows(
    rows: pd.DataFrame,
    mapping: FrameSource,
    portfolio_governance: FrameSource,
    *,
    require_adjustment: bool | None = None,
) -> pd.DataFrame:
    """Sum duplicate date/Portfolio/ConcertoField rows to one governed identity."""
    validated = validate_pl_send_rows(
        rows,
        mapping,
        portfolio_governance,
        require_adjustment=require_adjustment,
        allow_duplicates=True,
    )
    if validated.empty:
        return empty_pl_send_frame()

    identity_columns = [RISK_TYPE, RISK_GREEK, SIGNOFF_GROUP]
    identity_counts = validated.groupby(list(ADJUSTMENT_KEY), dropna=False)[
        identity_columns
    ].nunique(dropna=False)
    if identity_counts.gt(1).any().any():
        raise PLSendValidationError(
            "duplicate PL-send rows disagree on their governed identity"
        )
    collapsed = validated.groupby(
        list(ADJUSTMENT_KEY), as_index=False, dropna=False
    ).agg(
        {
            RISK_TYPE: "first",
            RISK_GREEK: "first",
            SIGNOFF_GROUP: "first",
            PL: lambda values: values.sum(min_count=1),
            ADJUSTMENT: "max",
        }
    )
    collapsed = collapsed[list(PL_SEND_COLUMNS)]
    collapsed = validate_pl_send_rows(
        collapsed,
        mapping,
        portfolio_governance,
        require_adjustment=require_adjustment,
    )
    return collapsed.sort_values(list(ADJUSTMENT_KEY), kind="stable").reset_index(
        drop=True
    )


def apply_adjustment_overlay(
    base_rows: pd.DataFrame,
    adjustment_rows: pd.DataFrame | None,
    mapping: FrameSource,
    portfolio_governance: FrameSource,
    *,
    include_adjustments: bool = True,
) -> pd.DataFrame:
    """Replace base rows using the date/Portfolio/ConcertoField adjustment key."""
    base = collapse_pl_send_rows(
        base_rows,
        mapping,
        portfolio_governance,
        require_adjustment=False,
    )
    if not include_adjustments:
        return base
    if adjustment_rows is None or adjustment_rows.empty:
        return base

    adjustments = collapse_pl_send_rows(
        adjustment_rows,
        mapping,
        portfolio_governance,
        require_adjustment=True,
    )
    base_index = pd.MultiIndex.from_frame(base[list(ADJUSTMENT_KEY)])
    adjustment_index = pd.MultiIndex.from_frame(adjustments[list(ADJUSTMENT_KEY)])
    retained = base.loc[~base_index.isin(adjustment_index)]
    effective = pd.concat([retained, adjustments], ignore_index=True, sort=False)
    effective = validate_pl_send_rows(
        effective,
        mapping,
        portfolio_governance,
    )
    return (
        effective[list(PL_SEND_COLUMNS)]
        .sort_values(list(ADJUSTMENT_KEY), kind="stable")
        .reset_index(drop=True)
    )


__all__ = [
    "ACTIVITY",
    "ADJUSTMENT",
    "ADJUSTMENT_KEY",
    "BOOK",
    "CATEGORY",
    "COLOSSUS_TYPE",
    "HISTORICAL_PL_COLUMNS",
    "HISTORICAL_PL_KEY",
    "HISTORY_FILE_COLUMNS",
    "HISTORY_FILE_IDENTITY_COLUMNS",
    "HISTORY_FILTER_COLUMNS",
    "HISTORY_DIMENSION_COLUMNS",
    "HISTORY_IDENTITY_COLUMNS",
    "HISTORY_MAPPING_STATUS",
    "HISTORY_TYPE",
    "HISTO_TYPE",
    "FrameSource",
    "MAPPING_COLUMNS",
    "MARKET_DATE",
    "PL",
    "CONCERTO_FIELD",
    "PLSendValidationError",
    "PL_SEND_COLUMNS",
    "PL_SEND_KEY",
    "PL_HISTORY_COLUMNS",
    "PL_HISTORY_DAILY_PERIOD",
    "PL_HISTORY_KEY",
    "PL_HISTORY_MTD_PERIOD",
    "PL_HISTORY_PERIOD",
    "PL_HISTORY_PERIOD_COLUMNS",
    "PL_HISTORY_PERIOD_END",
    "PL_HISTORY_PERIOD_START",
    "PL_HISTORY_PERIODS",
    "PL_HISTORY_SERIES_COLUMNS",
    "PL_HISTORY_TYPES",
    "PL_HISTORY_WTD_PERIOD",
    "PL_HISTORY_YTD_PERIOD",
    "PORTFOLIO",
    "PREDICT_TYPE",
    "PREDICTED_TYPE",
    "PRODUCT",
    "RISK_GREEK",
    "RISK_TYPE",
    "SIGNOFF_GROUP",
    "SUB_CATEGORY",
    "UNDERLYING",
    "apply_adjustment_overlay",
    "build_pl_send_base",
    "collapse_pl_send_rows",
    "empty_pl_send_frame",
    "load_plsend_mapping",
    "load_historical_pl",
    "load_legacy_pl_history_leaf",
    "load_pl_history",
    "load_portfolio_governance",
    "normalize_market_date",
    "normalize_pl_history_types",
    "normalize_pl_send_rows",
    "pl_history_period_bounds",
    "pl_history_period_values",
    "select_pl_history_series",
    "validate_pl_history_frame",
    "validate_pl_send_rows",
]
