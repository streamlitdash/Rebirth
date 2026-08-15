"""Pure PL-send mapping, aggregation, adjustment, and export rules.

This module deliberately performs no application, connector, or filesystem I/O
other than reading explicitly supplied CSV paths.  Dash callbacks and production
senders can therefore share one fail-closed financial data contract.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TypeAlias

import numpy as np
import pandas as pd

from core.s01_schema import (
    PL_ADJUSTMENT_METADATA_COLUMNS,
    PL_SIGNOFF_COLUMN,
    PORTFOLIO_COLUMN,
    PORTFOLIO_MAPPED_COLUMN,
    PORTFOLIO_METADATA_COLUMNS,
)


RISK_TYPE = "Risk Type"
RISK_GREEK = "Risk Greek"
PORTFOLIO = PORTFOLIO_COLUMN
SIGNOFF_GROUP = PL_SIGNOFF_COLUMN
CONCERTO_FIELD = "ConcertoField"
PL = "PL"
ADJUSTMENT = "Adjustment"
MARKET_DATE = "Market Date"
RECORD_TYPE = "Record Type"

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
HISTO_TYPE = "Histo"
PREDICTED_TYPE = "Predicted"
HISTORY_FILE_COLUMNS = (PORTFOLIO, CONCERTO_FIELD, PL)
PL_HISTORY_COLUMNS = (
    MARKET_DATE,
    HISTORY_TYPE,
    PORTFOLIO,
    CONCERTO_FIELD,
    PL,
)
PL_HISTORY_KEY = (MARKET_DATE, HISTORY_TYPE, PORTFOLIO, CONCERTO_FIELD)

_HISTORY_YEAR_PATTERN = re.compile(r"\d{4}")
_HISTORY_DATE_PATTERN = re.compile(r"(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])")
_HISTORY_FILES = {
    "histo.csv": HISTO_TYPE,
    "predicted.csv": PREDICTED_TYPE,
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


def _legacy_pl_history(source: FrameSource) -> pd.DataFrame:
    """Promote the legacy one-file actual history contract into the paired shape."""
    history = load_historical_pl(source)
    history.insert(1, HISTORY_TYPE, HISTO_TYPE)
    return history[list(PL_HISTORY_COLUMNS)]


def _history_directory_entries(directory: Path, *, label: str) -> list[Path]:
    """Return a stable directory listing with storage errors in the PL domain."""
    try:
        return sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise PLSendValidationError(f"Could not inspect {label}: {exc}") from exc


def _history_leaf_date(year: str, month_day: str) -> str:
    """Validate and normalize one ``YYYY/MM-DD`` history partition."""
    if not _HISTORY_YEAR_PATTERN.fullmatch(year):
        raise PLSendValidationError(
            f"P&L history year directory must be YYYY; found {year!r}"
        )
    if not _HISTORY_DATE_PATTERN.fullmatch(month_day):
        raise PLSendValidationError(
            "P&L history date directory must be MM-DD below its year; "
            f"found {year}/{month_day}"
        )
    try:
        return datetime.strptime(f"{year}-{month_day}", "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise PLSendValidationError(
            f"P&L history date directory is not a valid date: {year}/{month_day}"
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
        [PORTFOLIO, CONCERTO_FIELD],
        label=label,
    )
    frame = _normalise_pl(frame, label=label)
    duplicate_keys = frame.duplicated([PORTFOLIO, CONCERTO_FIELD], keep=False)
    if duplicate_keys.any():
        keys = (
            frame.loc[duplicate_keys, [PORTFOLIO, CONCERTO_FIELD]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise PLSendValidationError(
            f"{label} contains duplicate Portfolio + ConcertoField keys: {keys}"
        )
    frame.insert(0, HISTORY_TYPE, history_type)
    frame.insert(0, MARKET_DATE, market_date)
    return frame[list(PL_HISTORY_COLUMNS)]


def _load_pl_history_uncached(source: FrameSource) -> pd.DataFrame:
    """Load paired actual/predicted P&L from ``YYYY/MM-DD`` partitions.

    A directory source must contain only ``YYYY/MM-DD`` leaf directories. Every
    leaf must contain exactly ``histo.csv`` and ``predicted.csv``; their exact
    schema is ``Portfolio, ConcertoField, PL`` because the market date and series
    identity are authoritative in the path. A legacy CSV/DataFrame with
    :data:`HISTORICAL_PL_COLUMNS` remains readable as actual-only history.
    """
    if isinstance(source, pd.DataFrame):
        return _legacy_pl_history(source)
    if not isinstance(source, (str, Path)):
        raise TypeError(
            "P&L history must be a pandas DataFrame, CSV path, or directory"
        )

    root = Path(source)
    if not root.exists() or not root.is_dir():
        return _legacy_pl_history(root)

    year_entries = _history_directory_entries(root, label=f"P&L history root {root}")
    if not year_entries:
        raise PLSendValidationError(f"P&L history root is empty: {root}")

    partitions: list[pd.DataFrame] = []
    for year_directory in year_entries:
        year = year_directory.name
        if not year_directory.is_dir() or not _HISTORY_YEAR_PATTERN.fullmatch(year):
            raise PLSendValidationError(
                "P&L history root may contain only YYYY directories; "
                f"found {year_directory}"
            )
        date_entries = _history_directory_entries(
            year_directory,
            label=f"P&L history year {year_directory}",
        )
        if not date_entries:
            raise PLSendValidationError(
                f"P&L history year directory is empty: {year_directory}"
            )
        for date_directory in date_entries:
            if not date_directory.is_dir():
                raise PLSendValidationError(
                    "P&L history year directories may contain only MM-DD "
                    f"directories; found {date_directory}"
                )
            market_date = _history_leaf_date(year, date_directory.name)
            file_entries = _history_directory_entries(
                date_directory,
                label=f"P&L history date {date_directory}",
            )
            names = {entry.name for entry in file_entries if entry.is_file()}
            unexpected = [entry.name for entry in file_entries if not entry.is_file()]
            unexpected.extend(sorted(names - set(_HISTORY_FILES)))
            missing = sorted(set(_HISTORY_FILES) - names)
            if missing or unexpected:
                details: list[str] = []
                if missing:
                    details.append(f"missing {missing}")
                if unexpected:
                    details.append(f"unexpected {sorted(unexpected)}")
                raise PLSendValidationError(
                    f"P&L history date {year}/{date_directory.name} must contain "
                    "exactly histo.csv and predicted.csv; " + "; ".join(details)
                )
            for filename, history_type in _HISTORY_FILES.items():
                partitions.append(
                    _load_history_leaf_file(
                        date_directory / filename,
                        market_date=market_date,
                        history_type=history_type,
                    )
                )

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
            "P&L history contains duplicate Market Date + P&L Type + Portfolio + "
            f"ConcertoField keys: {keys}"
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

    frame = _normalise_text_columns(
        frame,
        [RISK_TYPE, RISK_GREEK, PORTFOLIO, SIGNOFF_GROUP],
        label=label,
    )
    frame = _normalise_market_dates(frame, label=label)
    frame = _normalise_pl(frame, label=label)
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


def build_saved_pl_frame(
    raw_pl: pd.DataFrame,
    mapping: FrameSource,
    portfolio_governance: FrameSource,
    adjustment_rows: pd.DataFrame | None = None,
    *,
    include_adjustments: bool = True,
) -> pd.DataFrame:
    """Return every raw P&L row plus separately flagged governed adjustments."""
    if not isinstance(raw_pl, pd.DataFrame):
        raise TypeError("raw saved P&L must be a pandas DataFrame")
    mapped_mask = _portfolio_mapped_mask(raw_pl, label="raw saved P&L")
    unmapped_mask = ~mapped_mask
    mapped_input = raw_pl.loc[mapped_mask].copy()
    raw, _, governed_portfolios = _mapped_raw_rows(
        mapped_input,
        mapping,
        portfolio_governance,
        label="raw saved P&L",
    )
    unmapped = raw_pl.loc[unmapped_mask].copy(deep=True)
    if not unmapped.empty:
        unmapped[CONCERTO_FIELD] = pd.NA
        raw = pd.concat([raw, unmapped], ignore_index=True, sort=False)
    raw[ADJUSTMENT] = False
    raw[RECORD_TYPE] = "Unadjusted"

    original_columns = [
        column
        for column in raw_pl.columns
        if column not in {CONCERTO_FIELD, ADJUSTMENT, RECORD_TYPE}
    ]
    output_columns = [
        *[column for column in original_columns if column in raw],
        CONCERTO_FIELD,
        ADJUSTMENT,
        RECORD_TYPE,
    ]
    base_output = raw.reindex(columns=output_columns)
    if not include_adjustments or adjustment_rows is None or adjustment_rows.empty:
        return base_output.reset_index(drop=True)

    adjustments = collapse_pl_send_rows(
        adjustment_rows,
        mapping,
        portfolio_governance,
        require_adjustment=True,
    )
    for metadata_column in PL_ADJUSTMENT_METADATA_COLUMNS:
        if metadata_column in output_columns and metadata_column in governed_portfolios:
            metadata = governed_portfolios[[PORTFOLIO, metadata_column]].rename(
                columns={metadata_column: f"_Governed {metadata_column}"}
            )
            adjustments = adjustments.merge(
                metadata,
                on=PORTFOLIO,
                how="left",
                validate="many_to_one",
            )
            adjustments[metadata_column] = adjustments.pop(
                f"_Governed {metadata_column}"
            )
    if PORTFOLIO_MAPPED_COLUMN in output_columns:
        adjustments[PORTFOLIO_MAPPED_COLUMN] = True
    adjustments[RECORD_TYPE] = "Adjustment"
    adjustment_output = adjustments.reindex(columns=output_columns)
    return pd.concat(
        [base_output, adjustment_output],
        ignore_index=True,
        sort=False,
    )


__all__ = [
    "ADJUSTMENT",
    "ADJUSTMENT_KEY",
    "HISTORICAL_PL_COLUMNS",
    "HISTORICAL_PL_KEY",
    "HISTORY_FILE_COLUMNS",
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
    "PL_HISTORY_KEY",
    "PORTFOLIO",
    "PREDICTED_TYPE",
    "RECORD_TYPE",
    "RISK_GREEK",
    "RISK_TYPE",
    "SIGNOFF_GROUP",
    "apply_adjustment_overlay",
    "build_pl_send_base",
    "build_saved_pl_frame",
    "collapse_pl_send_rows",
    "empty_pl_send_frame",
    "load_plsend_mapping",
    "load_historical_pl",
    "load_pl_history",
    "load_portfolio_governance",
    "normalize_market_date",
    "normalize_pl_send_rows",
    "validate_pl_send_rows",
]
