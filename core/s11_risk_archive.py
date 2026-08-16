"""Flat, atomic archives for one official Risk Explorer snapshot per date.

The archive deliberately stores the committed dashboard frame without turning
it into a hierarchy.  A reader can rebuild the Risk Explorer hierarchy at
display time.  Colossus P&L is stored at its separate, explicit four-key grain;
it is never copied across tenor or Product rows. Schema-v2 leaves also store
the coherent full MarketBook at unique raw quote grain for historical Quick
Market; schema-v1 archives without that optional history remain readable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from core.s01_schema import (
    TENOR_OPTION,
    TENOR_OPTION_ORDER,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
    UNMAPPED_VALUE,
)
from core.s02_pipeline import PRODUCT_SPECS_BY_SOURCE_TYPE, market_date_for
from core.s03_search import (
    CURRENT,
    MARKET_DATA_STATUS,
    MARKET_RESULT_COLUMNS,
    MARKET_STATUS,
    OPEN,
    SOURCE_TYPE,
)
from core.s04_pl import (
    ACTIVITY,
    CATEGORY,
    COLOSSUS_TYPE,
    HISTORY_MAPPING_STATUS,
    HISTORY_TYPE,
    MARKET_DATE,
    PL,
    PL_HISTORY_COLUMNS,
    PL_HISTORY_KEY,
    PREDICT_TYPE,
    PRODUCT,
    PLSendValidationError,
    RISK_GREEK,
    RISK_TYPE,
    SIGNOFF_GROUP,
    SUB_CATEGORY,
    UNDERLYING,
    load_legacy_pl_history_leaf,
    validate_pl_history_frame,
)


PORTFOLIO = "Portfolio"
RISK = "Risk"
DRISK = "dRisk"
OFFICIAL = "OFFICIAL"
RISK_FILE_NAME = "risk.csv"
COLOSSUS_FILE_NAME = "colossus.csv"
MARKET_FILE_NAME = "market.csv"
SUCCESS_FILE_NAME = "_SUCCESS"
BASE_ARCHIVE_FILE_NAMES = (RISK_FILE_NAME, COLOSSUS_FILE_NAME, SUCCESS_FILE_NAME)
ARCHIVE_FILE_NAMES = (
    RISK_FILE_NAME,
    COLOSSUS_FILE_NAME,
    MARKET_FILE_NAME,
    SUCCESS_FILE_NAME,
)
COLOSSUS_COLUMNS = (PORTFOLIO, UNDERLYING, RISK_TYPE, RISK_GREEK, PL)
COLOSSUS_KEY = COLOSSUS_COLUMNS[:-1]
RISK_PROJECTION_COLUMNS = (
    PORTFOLIO,
    UNDERLYING,
    RISK_TYPE,
    RISK_GREEK,
    PRODUCT,
    RISK,
    DRISK,
    PL,
)
MARKET_ARCHIVE_COLUMNS = tuple(MARKET_RESULT_COLUMNS)
MARKET_IDENTITY_COLUMNS = (
    SOURCE_TYPE,
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    TENOR_SWAP,
    TENOR_OPTION,
)
MARKET_HISTORY_COLUMNS = (
    MARKET_DATE,
    TENOR_SWAP,
    TENOR_OPTION,
    TENOR_SWAP_ORDER,
    TENOR_OPTION_ORDER,
    CURRENT,
)
PORTFOLIO_AUTHORITY_COLUMNS = (
    PORTFOLIO,
    SIGNOFF_GROUP,
    PRODUCT,
    ACTIVITY,
    CATEGORY,
    SUB_CATEGORY,
    HISTORY_MAPPING_STATUS,
)
MAPPED_HISTORY_VALUE = "Mapped"
ARCHIVE_SCHEMA_VERSION = 2
_SUPPORTED_ARCHIVE_SCHEMA_VERSIONS = frozenset((1, ARCHIVE_SCHEMA_VERSION))

_DATE_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])")
_PENDING_LEAF_PATTERN = re.compile(
    r"\.\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\.pending-.+"
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_LEGACY_HISTORY_FILE_NAMES = frozenset(("histo.csv", "predicted.csv"))
_OFFICIAL_HISTORY_FILE_NAMES = frozenset(BASE_ARCHIVE_FILE_NAMES)


class RiskArchiveValidationError(ValueError):
    """Raised when an archive request or completed leaf is not trustworthy."""


class OfficialSnapshot(Protocol):
    """The committed manager fields required by the archive boundary."""

    revision: int
    refreshed_at: datetime
    system_date: pd.Timestamp
    market_date: pd.Timestamp
    market_status: str
    dashboard_frame: pd.DataFrame
    market_frame: pd.DataFrame
    errors: tuple[str, ...]


ColossusLoader = Callable[[pd.Timestamp], pd.DataFrame]


@dataclass(frozen=True)
class RiskArchive:
    """One validated, completed daily archive."""

    market_date: str
    path: Path
    risk: pd.DataFrame
    colossus: pd.DataFrame
    market: pd.DataFrame | None = None


@dataclass(frozen=True)
class ArchiveResult:
    """Small scheduler-friendly outcome that does not include archived frames."""

    status: str
    reason: str
    market_date: str
    path: Path
    risk_rows: int = 0
    colossus_rows: int = 0
    market_rows: int = 0

    @property
    def archived(self) -> bool:
        return self.status == "archived"


def _normalize_date(value: object, *, label: str) -> str:
    if value is None or isinstance(value, (bool, np.bool_)):
        raise RiskArchiveValidationError(f"{label} must be a valid date")
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise RiskArchiveValidationError(f"{label} must be a valid date") from exc
    if pd.isna(timestamp):
        raise RiskArchiveValidationError(f"{label} must be a valid date")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp.date().isoformat()


def archive_leaf_path(root: str | Path, market_date: object) -> Path:
    """Return the authoritative flat ``YYYY-MM-DD`` leaf for one market date."""

    normalized = _normalize_date(market_date, label="Market Date")
    return Path(root).expanduser().resolve() / normalized


def _require_frame(value: object, *, label: str) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame):
        raise TypeError(f"{label} must return a pandas DataFrame")
    frame = value.copy(deep=True)
    duplicate_columns = frame.columns[frame.columns.duplicated()].tolist()
    if duplicate_columns:
        raise RiskArchiveValidationError(
            f"{label} contains duplicate columns: {duplicate_columns}"
        )
    if any(not isinstance(column, str) or not column.strip() for column in frame):
        raise RiskArchiveValidationError(
            f"{label} columns must be nonblank text labels"
        )
    return frame


def _validate_text_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    label: str,
) -> pd.DataFrame:
    result = frame.copy(deep=True)
    for column in columns:
        values = result[column]
        is_text = values.map(lambda value: isinstance(value, str)).astype(bool)
        invalid = values.isna() | ~is_text | values.astype("string").str.strip().eq("")
        if invalid.any():
            rows = result.index[invalid].tolist()[:5]
            raise RiskArchiveValidationError(
                f"{label} column {column!r} must contain nonblank text; "
                f"invalid rows {rows}"
            )
        result[column] = values.astype(str).str.strip()
    return result


def _nullable_numeric(
    values: pd.Series,
    *,
    label: str,
    allow_missing: bool,
) -> pd.Series:
    boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
    blank = values.isna() | values.astype("string").str.strip().eq("")
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = boolean | (~blank & numeric.isna())
    if not allow_missing:
        invalid |= blank | numeric.isna()
    invalid |= numeric.notna() & ~np.isfinite(numeric)
    if invalid.any():
        rows = values.index[invalid].tolist()[:5]
        qualifier = (
            "finite numbers or missing values" if allow_missing else "finite numbers"
        )
        raise RiskArchiveValidationError(
            f"{label} must contain {qualifier}; invalid rows {rows}"
        )
    return numeric.astype(float)


def validate_risk_archive_frame(value: object) -> pd.DataFrame:
    """Validate the minimum identities needed to retain and project Predict P&L."""

    frame = _require_frame(value, label="official Risk Explorer snapshot")
    if frame.empty:
        raise RiskArchiveValidationError(
            "official Risk Explorer snapshot must contain at least one row"
        )
    missing = [column for column in RISK_PROJECTION_COLUMNS if column not in frame]
    if missing:
        raise RiskArchiveValidationError(
            f"official Risk Explorer snapshot is missing required columns: {missing}"
        )
    normalized = _validate_text_columns(
        frame,
        (PORTFOLIO, UNDERLYING, RISK_TYPE, RISK_GREEK, PRODUCT),
        label="official Risk Explorer snapshot",
    )
    identity_columns = (PORTFOLIO, UNDERLYING, RISK_TYPE, RISK_GREEK, PRODUCT)
    frame.loc[:, list(identity_columns)] = normalized.loc[:, list(identity_columns)]
    frame[PL] = _nullable_numeric(
        frame[PL],
        label="official Risk Explorer snapshot column 'PL'",
        allow_missing=True,
    )
    frame[RISK] = _nullable_numeric(
        frame[RISK],
        label="official Risk Explorer snapshot column 'Risk'",
        allow_missing=False,
    )
    frame[DRISK] = _nullable_numeric(
        frame[DRISK],
        label="official Risk Explorer snapshot column 'dRisk'",
        allow_missing=True,
    )
    return frame


def validate_colossus_frame(value: object) -> pd.DataFrame:
    """Return strict Colossus P&L at Portfolio/Underlying/Risk pair grain."""

    frame = _require_frame(value, label="Colossus loader")
    actual = tuple(frame.columns)
    if actual != COLOSSUS_COLUMNS:
        raise RiskArchiveValidationError(
            "Colossus loader must return exactly these columns in order: "
            f"{list(COLOSSUS_COLUMNS)}; found {list(actual)}"
        )
    if frame.empty:
        raise RiskArchiveValidationError(
            "Colossus loader must return at least one official P&L row"
        )
    frame = _validate_text_columns(
        frame,
        COLOSSUS_KEY,
        label="Colossus loader",
    )
    frame[PL] = _nullable_numeric(
        frame[PL],
        label="Colossus loader column 'PL'",
        allow_missing=False,
    )
    duplicates = frame.duplicated(list(COLOSSUS_KEY), keep=False)
    if duplicates.any():
        keys = (
            frame.loc[duplicates, list(COLOSSUS_KEY)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise RiskArchiveValidationError(
            f"Colossus loader contains duplicate four-key rows: {keys}"
        )
    return frame.sort_values(list(COLOSSUS_KEY), kind="stable").reset_index(drop=True)


def _market_order_column(
    values: pd.Series,
    *,
    label: str,
) -> pd.Series:
    """Return nullable, non-negative integer connector-owned ranks."""

    boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
    blank = values.isna() | values.astype("string").str.strip().eq("")
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = boolean | (~blank & numeric.isna())
    invalid |= numeric.notna() & (
        ~np.isfinite(numeric) | numeric.lt(0) | numeric.mod(1).ne(0)
    )
    if invalid.any():
        rows = values.index[invalid].tolist()[:5]
        raise RiskArchiveValidationError(
            f"{label} must contain non-negative integer market orders or missing "
            f"values; invalid rows {rows}"
        )
    return numeric.astype("Int64")


def validate_market_archive_frame(
    value: object,
    *,
    market_date: object | None = None,
) -> pd.DataFrame:
    """Validate and canonicalize one daily full MarketBook archive.

    The persisted schema deliberately mirrors ``MARKET_RESULT_COLUMNS`` used by
    current Quick Market.  Input snapshots may contain additional manager-only
    fields, but the returned frame is always the exact closed archive schema at
    unique raw quote grain.  Portfolio and reporting fields are never accepted
    into the persisted projection.
    """

    frame = _require_frame(value, label="official MarketBook snapshot")
    if frame.empty:
        raise RiskArchiveValidationError(
            "official MarketBook snapshot must contain at least one quote"
        )
    missing = [column for column in MARKET_ARCHIVE_COLUMNS if column not in frame]
    if missing:
        raise RiskArchiveValidationError(
            f"official MarketBook snapshot is missing required columns: {missing}"
        )
    frame = frame.loc[:, list(MARKET_ARCHIVE_COLUMNS)].copy()
    frame = _validate_text_columns(
        frame,
        (
            SOURCE_TYPE,
            RISK_TYPE,
            RISK_GREEK,
            UNDERLYING,
            TENOR_SWAP,
            TENOR_OPTION,
            MARKET_STATUS,
            MARKET_DATA_STATUS,
        ),
        label="official MarketBook snapshot",
    )

    normalized_dates: list[str] = []
    for row, value_date in frame[MARKET_DATE].items():
        try:
            normalized_dates.append(
                _normalize_date(value_date, label=f"Market Date at row {row}")
            )
        except RiskArchiveValidationError as exc:
            raise RiskArchiveValidationError(
                f"official MarketBook snapshot contains an invalid Market Date: {exc}"
            ) from exc
    frame[MARKET_DATE] = normalized_dates
    expected_date = (
        _normalize_date(market_date, label="Market Date")
        if market_date is not None
        else None
    )
    unique_dates = frame[MARKET_DATE].drop_duplicates().tolist()
    if len(unique_dates) != 1:
        raise RiskArchiveValidationError(
            "official MarketBook snapshot must contain exactly one Market Date; "
            f"found {unique_dates}"
        )
    if expected_date is not None and unique_dates != [expected_date]:
        raise RiskArchiveValidationError(
            "official MarketBook snapshot Market Date does not match its archive "
            f"leaf: expected {expected_date}, found {unique_dates[0]}"
        )
    if not frame[MARKET_STATUS].eq(OFFICIAL).all():
        statuses = sorted(frame[MARKET_STATUS].drop_duplicates().tolist())
        raise RiskArchiveValidationError(
            "official MarketBook snapshot Market Status must be exactly "
            f"{OFFICIAL!r}; found {statuses}"
        )

    for column in (OPEN, CURRENT, "Move"):
        frame[column] = _nullable_numeric(
            frame[column],
            label=f"official MarketBook snapshot column {column!r}",
            allow_missing=True,
        )
    for column in (TENOR_SWAP_ORDER, TENOR_OPTION_ORDER):
        frame[column] = _market_order_column(
            frame[column],
            label=f"official MarketBook snapshot column {column!r}",
        )

    complete = frame[OPEN].notna() & frame[CURRENT].notna()
    inconsistent_availability = frame["Move"].notna().ne(complete)
    if inconsistent_availability.any():
        rows = frame.index[inconsistent_availability].tolist()[:5]
        raise RiskArchiveValidationError(
            "official MarketBook snapshot Move must be present exactly when Open "
            f"and Current are both present; invalid rows {rows}"
        )
    if complete.any():
        expected_move = frame.loc[complete, CURRENT] - frame.loc[complete, OPEN]
        inconsistent_move = ~np.isclose(
            frame.loc[complete, "Move"],
            expected_move,
            rtol=1e-10,
            atol=1e-12,
        )
        if inconsistent_move.any():
            rows = expected_move.index[inconsistent_move].tolist()[:5]
            raise RiskArchiveValidationError(
                "official MarketBook snapshot Move must equal Current minus Open; "
                f"invalid rows {rows}"
            )

    for source_type, source_rows in frame.groupby(
        SOURCE_TYPE, sort=False, observed=True, dropna=False
    ):
        try:
            spec = PRODUCT_SPECS_BY_SOURCE_TYPE[str(source_type)]
        except KeyError as exc:
            raise RiskArchiveValidationError(
                f"official MarketBook snapshot contains unknown Source Type "
                f"{source_type!r}"
            ) from exc
        wrong_pair = source_rows[RISK_TYPE].ne(spec.risk_type) | source_rows[
            RISK_GREEK
        ].ne(spec.risk_greek)
        if wrong_pair.any():
            rows = source_rows.index[wrong_pair].tolist()[:5]
            raise RiskArchiveValidationError(
                f"official MarketBook Source Type {source_type!r} must use Risk "
                f"Type={spec.risk_type!r}, Risk Greek={spec.risk_greek!r}; "
                f"invalid rows {rows}"
            )

        declared_axes = set(spec.tenor_columns)
        for tenor_column, order_column in (
            (TENOR_SWAP, TENOR_SWAP_ORDER),
            (TENOR_OPTION, TENOR_OPTION_ORDER),
        ):
            if tenor_column not in declared_axes:
                expected_tenor = (
                    "Spot"
                    if spec.key == "fxdelta" and tenor_column == TENOR_SWAP
                    else "N/A"
                )
                invalid_tenor = source_rows[tenor_column].ne(expected_tenor)
                invalid_order = source_rows[order_column].notna()
                invalid = invalid_tenor | invalid_order
                if invalid.any():
                    rows = source_rows.index[invalid].tolist()[:5]
                    raise RiskArchiveValidationError(
                        f"official MarketBook Source Type {source_type!r} does not "
                        f"declare {tenor_column!r}; expected {expected_tenor!r} and "
                        f"a missing {order_column!r} at rows {rows}"
                    )
                continue

            missing_order = source_rows[order_column].isna()
            if missing_order.any():
                rows = source_rows.index[missing_order].tolist()[:5]
                raise RiskArchiveValidationError(
                    f"official MarketBook Source Type {source_type!r} requires "
                    f"{order_column!r} at rows {rows}"
                )
            tenor_to_order = source_rows.groupby(
                [UNDERLYING, tenor_column], dropna=False, observed=True
            )[order_column].nunique(dropna=False)
            if tenor_to_order.gt(1).any():
                raise RiskArchiveValidationError(
                    f"official MarketBook has conflicting {order_column!r} values "
                    f"per Source Type + Underlying + {tenor_column}"
                )
            order_to_tenor = source_rows.groupby(
                [UNDERLYING, order_column], dropna=False, observed=True
            )[tenor_column].nunique(dropna=False)
            if order_to_tenor.gt(1).any():
                raise RiskArchiveValidationError(
                    f"official MarketBook maps more than one {tenor_column!r} to "
                    f"the same {order_column!r} per Source Type + Underlying"
                )

    duplicates = frame.duplicated(list(MARKET_IDENTITY_COLUMNS), keep=False)
    if duplicates.any():
        keys = (
            frame.loc[duplicates, list(MARKET_IDENTITY_COLUMNS)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise RiskArchiveValidationError(
            f"official MarketBook snapshot contains duplicate quote identities: {keys}"
        )

    return frame.sort_values(
        [
            SOURCE_TYPE,
            UNDERLYING,
            TENOR_SWAP_ORDER,
            TENOR_OPTION_ORDER,
            TENOR_SWAP,
            TENOR_OPTION,
        ],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        frame.to_csv(stream, index=False, lineterminator="\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(value: dict[str, object], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(leaf: Path) -> dict[str, object]:
    marker = leaf / SUCCESS_FILE_NAME
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RiskArchiveValidationError(
            f"Could not read completed archive marker {marker}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RiskArchiveValidationError(
            f"Completed archive marker {marker} must contain a JSON object"
        )
    return value


def _completed_leaf_date(leaf: Path) -> str:
    value = leaf.name
    if not _DATE_PATTERN.fullmatch(value):
        raise RiskArchiveValidationError(
            f"Risk archive leaf must use YYYY-MM-DD; found {value!r}"
        )
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise RiskArchiveValidationError(
            f"Risk archive leaf is not a valid date: {value}"
        ) from exc


def _completed_leaf_contract(
    leaf: Path,
    manifest: dict[str, object],
    market_date: str,
) -> tuple[str, ...]:
    """Validate completion metadata and return its exact expected file set."""

    schema_version = manifest.get("schema_version")
    if schema_version not in _SUPPORTED_ARCHIVE_SCHEMA_VERSIONS:
        raise RiskArchiveValidationError(
            f"Risk archive leaf {leaf} has an unsupported schema version"
        )
    if manifest.get("market_date") != market_date:
        raise RiskArchiveValidationError(
            f"Risk archive marker date does not match its leaf: {leaf}"
        )
    if manifest.get("market_status") != OFFICIAL:
        raise RiskArchiveValidationError(f"Risk archive marker is not OFFICIAL: {leaf}")
    risk_columns = manifest.get("risk_columns")
    if not (
        isinstance(risk_columns, list)
        and all(
            isinstance(column, str) and bool(column.strip()) for column in risk_columns
        )
        and set(RISK_PROJECTION_COLUMNS).issubset(risk_columns)
    ):
        raise RiskArchiveValidationError(
            f"Risk archive columns are invalid in its completion marker: {leaf}"
        )
    if manifest.get("colossus_columns") != list(COLOSSUS_COLUMNS):
        raise RiskArchiveValidationError(
            f"Colossus archive columns do not match its completion marker: {leaf}"
        )
    for field in ("risk_rows", "colossus_rows"):
        value = manifest.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise RiskArchiveValidationError(
                f"Risk archive row counts are invalid in its completion marker: {leaf}"
            )

    digests = manifest.get("sha256")
    if not isinstance(digests, dict):
        raise RiskArchiveValidationError(
            f"Risk archive digests are invalid in its completion marker: {leaf}"
        )
    for file_name in (RISK_FILE_NAME, COLOSSUS_FILE_NAME):
        digest = digests.get(file_name)
        if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
            raise RiskArchiveValidationError(
                f"Risk archive digests are invalid in its completion marker: {leaf}"
            )

    market_metadata = (
        manifest.get("market_rows"),
        manifest.get("market_columns"),
        digests.get(MARKET_FILE_NAME),
    )
    has_any_market_metadata = any(value is not None for value in market_metadata)
    has_complete_market_metadata = (
        isinstance(market_metadata[0], int)
        and not isinstance(market_metadata[0], bool)
        and market_metadata[0] > 0
        and market_metadata[1] == list(MARKET_ARCHIVE_COLUMNS)
        and isinstance(market_metadata[2], str)
        and bool(_SHA256_PATTERN.fullmatch(market_metadata[2]))
    )
    market_required = schema_version == ARCHIVE_SCHEMA_VERSION
    if schema_version == 1 and has_any_market_metadata:
        raise RiskArchiveValidationError(
            f"Schema version 1 archive must not declare market.csv: {leaf}"
        )
    if market_required and not has_complete_market_metadata:
        raise RiskArchiveValidationError(
            f"Market archive metadata is missing or invalid in completion marker: "
            f"{leaf}"
        )
    if has_any_market_metadata and not has_complete_market_metadata:
        raise RiskArchiveValidationError(
            f"Market archive metadata is incomplete in completion marker: {leaf}"
        )
    expected_files = (
        ARCHIVE_FILE_NAMES if has_complete_market_metadata else BASE_ARCHIVE_FILE_NAMES
    )
    actual_entries = {path.name for path in leaf.iterdir()}
    if actual_entries != set(expected_files):
        missing = sorted(set(expected_files) - actual_entries)
        extra = sorted(actual_entries - set(expected_files))
        raise RiskArchiveValidationError(
            f"Risk archive leaf {leaf} is incomplete or invalid; "
            f"missing={missing}, extra={extra}"
        )
    return expected_files


def _load_completed_leaf(leaf: Path) -> RiskArchive:
    if not leaf.exists() or not leaf.is_dir():
        raise RiskArchiveValidationError(f"Risk archive leaf does not exist: {leaf}")
    actual_entries = {path.name for path in leaf.iterdir()}
    if not set(BASE_ARCHIVE_FILE_NAMES).issubset(actual_entries):
        missing = sorted(set(BASE_ARCHIVE_FILE_NAMES) - actual_entries)
        raise RiskArchiveValidationError(
            f"Risk archive leaf {leaf} is incomplete or invalid; missing={missing}"
        )
    market_date = _completed_leaf_date(leaf)
    manifest = _read_manifest(leaf)
    expected_files = _completed_leaf_contract(leaf, manifest, market_date)
    data_file_names = [RISK_FILE_NAME, COLOSSUS_FILE_NAME]
    if MARKET_FILE_NAME in expected_files:
        data_file_names.append(MARKET_FILE_NAME)
    for file_name in data_file_names:
        expected_digest = (
            manifest.get("sha256", {}).get(file_name)
            if isinstance(manifest.get("sha256"), dict)
            else None
        )
        if expected_digest != _file_sha256(leaf / file_name):
            raise RiskArchiveValidationError(
                f"Risk archive file does not match its completion marker: "
                f"{leaf / file_name}"
            )
    try:
        risk = pd.read_csv(
            leaf / RISK_FILE_NAME,
            encoding="utf-8",
            keep_default_na=False,
            dtype="string",
        )
        colossus = pd.read_csv(
            leaf / COLOSSUS_FILE_NAME,
            encoding="utf-8",
            keep_default_na=False,
            dtype="string",
        )
        market = (
            pd.read_csv(
                leaf / MARKET_FILE_NAME,
                encoding="utf-8",
                keep_default_na=False,
                dtype="string",
            )
            if MARKET_FILE_NAME in expected_files
            else None
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise RiskArchiveValidationError(
            f"Could not read completed risk archive {leaf}: {exc}"
        ) from exc
    risk = validate_risk_archive_frame(risk)
    colossus = validate_colossus_frame(colossus)
    if market is not None:
        market = validate_market_archive_frame(market, market_date=market_date)
    expected_columns = manifest.get("risk_columns")
    if expected_columns != list(risk.columns):
        raise RiskArchiveValidationError(
            f"Risk archive columns do not match its completion marker: {leaf}"
        )
    if manifest.get("risk_rows") != len(risk) or manifest.get("colossus_rows") != len(
        colossus
    ):
        raise RiskArchiveValidationError(
            f"Risk archive row counts do not match its completion marker: {leaf}"
        )
    if market is not None and manifest.get("market_rows") != len(market):
        raise RiskArchiveValidationError(
            f"Market archive row count does not match its completion marker: {leaf}"
        )
    return RiskArchive(
        market_date=market_date,
        path=leaf,
        risk=risk,
        colossus=colossus,
        market=market,
    )


def load_risk_archive(root: str | Path, market_date: object) -> RiskArchive:
    """Load and validate one completed official daily archive."""

    return _load_completed_leaf(archive_leaf_path(root, market_date))


def list_completed_market_dates(root: str | Path) -> tuple[str, ...]:
    """Hide partial leaves and fail closed on any invalid completed marker."""

    directory = Path(root).expanduser().resolve()
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise RiskArchiveValidationError(
            f"Risk archive root must be a directory: {directory}"
        )
    dates: list[str] = []
    for leaf in directory.iterdir():
        if not leaf.is_dir() or not _DATE_PATTERN.fullmatch(leaf.name):
            continue
        if not (leaf / SUCCESS_FILE_NAME).is_file():
            continue
        market_date = _completed_leaf_date(leaf)
        manifest = _read_manifest(leaf)
        try:
            _completed_leaf_contract(leaf, manifest, market_date)
        except RiskArchiveValidationError as exc:
            raise RiskArchiveValidationError(
                f"Completed risk archive marker is invalid: {leaf / SUCCESS_FILE_NAME}"
            ) from exc
        dates.append(market_date)
    return tuple(sorted(set(dates)))


def _already_archived_result(archive: RiskArchive) -> ArchiveResult:
    return ArchiveResult(
        status="already_archived",
        reason="A completed official archive already exists for this Market Date.",
        market_date=archive.market_date,
        path=archive.path,
        risk_rows=len(archive.risk),
        colossus_rows=len(archive.colossus),
        market_rows=0 if archive.market is None else len(archive.market),
    )


def archive_official_snapshot(
    snapshot: OfficialSnapshot,
    colossus_loader: ColossusLoader,
    root: str | Path,
) -> ArchiveResult:
    """Atomically write one eligible committed snapshot and its Colossus P&L.

    Eligibility is deliberately narrow: the selected Market Date must be the
    manager's naturally resolved business Market Date, the committed source
    must be exactly ``OFFICIAL``, and the snapshot must not be a retained
    last-good revision carrying refresh errors. Re-running a completed date is
    a no-op.
    """

    market_date = _normalize_date(snapshot.market_date, label="Market Date")
    system_date = _normalize_date(snapshot.system_date, label="System Date")
    natural_market_date = market_date_for(system_date).date().isoformat()
    leaf = archive_leaf_path(root, market_date)
    status = str(snapshot.market_status).strip()
    if market_date != natural_market_date:
        return ArchiveResult(
            status="skipped",
            reason="Selected Market Date is not the current natural Market Date.",
            market_date=market_date,
            path=leaf,
        )
    if status != OFFICIAL:
        return ArchiveResult(
            status="skipped",
            reason="Market source is not OFFICIAL yet.",
            market_date=market_date,
            path=leaf,
        )
    if tuple(snapshot.errors):
        return ArchiveResult(
            status="skipped",
            reason="The committed snapshot reports refresh errors.",
            market_date=market_date,
            path=leaf,
        )
    if leaf.exists():
        return _already_archived_result(_load_completed_leaf(leaf))
    if not callable(colossus_loader):
        raise TypeError("colossus_loader must be callable")

    risk = validate_risk_archive_frame(snapshot.dashboard_frame)
    colossus = validate_colossus_frame(colossus_loader(pd.Timestamp(market_date)))
    raw_market = getattr(snapshot, "market_frame", None)
    market = (
        None
        if raw_market is None
        else validate_market_archive_frame(raw_market, market_date=market_date)
    )
    root_directory = leaf.parent
    root_directory.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{leaf.name}.pending-", dir=root_directory)
    )
    try:
        risk_path = temporary / RISK_FILE_NAME
        colossus_path = temporary / COLOSSUS_FILE_NAME
        _write_csv(risk, risk_path)
        _write_csv(colossus, colossus_path)
        market_path: Path | None = None
        if market is not None:
            market_path = temporary / MARKET_FILE_NAME
            _write_csv(market, market_path)
        refreshed_at = getattr(snapshot, "refreshed_at", None)
        refreshed_text = (
            refreshed_at.isoformat()
            if isinstance(refreshed_at, datetime)
            else str(refreshed_at or "")
        )
        manifest: dict[str, object] = {
            "schema_version": ARCHIVE_SCHEMA_VERSION if market is not None else 1,
            "market_date": market_date,
            "market_status": OFFICIAL,
            "revision": int(snapshot.revision),
            "refreshed_at": refreshed_text,
            "risk_rows": len(risk),
            "colossus_rows": len(colossus),
            "risk_columns": list(risk.columns),
            "colossus_columns": list(COLOSSUS_COLUMNS),
            "sha256": {
                RISK_FILE_NAME: _file_sha256(risk_path),
                COLOSSUS_FILE_NAME: _file_sha256(colossus_path),
            },
        }
        if market is not None and market_path is not None:
            manifest["market_rows"] = len(market)
            manifest["market_columns"] = list(MARKET_ARCHIVE_COLUMNS)
            manifest_sha256 = manifest["sha256"]
            if not isinstance(manifest_sha256, dict):  # pragma: no cover - local
                raise AssertionError("manifest sha256 must be a dictionary")
            manifest_sha256[MARKET_FILE_NAME] = _file_sha256(market_path)
        _write_json(manifest, temporary / SUCCESS_FILE_NAME)
        try:
            temporary.rename(leaf)
        except OSError:
            if leaf.exists():
                existing = _load_completed_leaf(leaf)
                return _already_archived_result(existing)
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return ArchiveResult(
        status="archived",
        reason="Official Risk Explorer and Colossus P&L archived.",
        market_date=market_date,
        path=leaf,
        risk_rows=len(risk),
        colossus_rows=len(colossus),
        market_rows=0 if market is None else len(market),
    )


def archive_from_manager(
    manager: object,
    colossus_loader: ColossusLoader,
    root: str | Path,
    *,
    refresh: bool = True,
) -> ArchiveResult:
    """Refresh once for a scheduled job, then archive that coherent snapshot."""

    if refresh:
        refresh_method = getattr(manager, "refresh", None)
        if not callable(refresh_method):
            raise TypeError("manager must expose a callable refresh method")
        snapshot = refresh_method(
            force_risk=True,
            force_pl=True,
            reason="scheduled_official_archive",
        )
    else:
        snapshot = getattr(manager, "snapshot")
    return archive_official_snapshot(snapshot, colossus_loader, root)


def build_history_portfolio_authority(risk: pd.DataFrame) -> pd.DataFrame:
    """Return one nonduplicating Portfolio authority for historical P&L.

    Colossus owns no Product or SignoffGroup.  Those two fields are authoritative
    only when the archived Predict snapshot has exactly one distinct
    ``(SignoffGroup, Product)`` pair for the Portfolio.  Ambiguous Portfolios are
    retained once and labelled ``Unmapped`` so callers can expose them without
    guessing or duplicating Colossus rows.  The remaining filter metadata is
    independently retained only when unique for that Portfolio.
    """

    validated = validate_risk_archive_frame(risk)
    required = (
        PORTFOLIO,
        SIGNOFF_GROUP,
        PRODUCT,
        ACTIVITY,
        CATEGORY,
        SUB_CATEGORY,
    )
    missing = [column for column in required if column not in validated]
    if missing:
        raise RiskArchiveValidationError(
            "official Risk Explorer snapshot is missing historical P&L authority "
            f"columns: {missing}"
        )
    normalized = _validate_text_columns(
        validated,
        required,
        label="official Risk Explorer snapshot",
    )
    portfolios = (
        normalized[[PORTFOLIO]]
        .drop_duplicates()
        .sort_values(PORTFOLIO, kind="stable")
        .reset_index(drop=True)
    )
    pairs = normalized[[PORTFOLIO, SIGNOFF_GROUP, PRODUCT]].drop_duplicates()
    pair_counts = pairs.groupby(PORTFOLIO, sort=False).size()
    valid_portfolios = set(pair_counts.loc[pair_counts.eq(1)].index.astype(str))
    unique_pairs = pairs.loc[pairs[PORTFOLIO].isin(valid_portfolios)]
    authority = portfolios.merge(
        unique_pairs,
        on=PORTFOLIO,
        how="left",
        validate="one_to_one",
    )
    mapped = authority[PORTFOLIO].isin(valid_portfolios)
    authority[HISTORY_MAPPING_STATUS] = np.where(
        mapped,
        MAPPED_HISTORY_VALUE,
        UNMAPPED_VALUE,
    )
    authority.loc[~mapped, [SIGNOFF_GROUP, PRODUCT]] = UNMAPPED_VALUE

    for column in (ACTIVITY, CATEGORY, SUB_CATEGORY):
        values = normalized[[PORTFOLIO, column]].drop_duplicates()
        counts = values.groupby(PORTFOLIO, sort=False).size()
        unique_portfolios = set(counts.loc[counts.eq(1)].index.astype(str))
        unique_values = values.loc[values[PORTFOLIO].isin(unique_portfolios)]
        authority = authority.merge(
            unique_values,
            on=PORTFOLIO,
            how="left",
            validate="one_to_one",
        )
        authority[column] = authority[column].fillna(UNMAPPED_VALUE)

    return authority.loc[:, list(PORTFOLIO_AUTHORITY_COLUMNS)].reset_index(drop=True)


def project_archive_to_pl_history(archive: RiskArchive) -> pd.DataFrame:
    """Project one archive into the existing canonical Colossus/Predict grain.

    Predict is summed from position rows only after grouping to SignoffGroup +
    Risk Type + Risk Greek + Underlying + Product + Portfolio. A partially
    missing PL group is omitted rather than treated as a partial or zero total.
    Colossus receives SignoffGroup and Product only from the strict archived
    Portfolio authority. Unknown or ambiguous Portfolios are retained once in
    the explicit Unmapped hierarchy instead of failing or being duplicated.
    """

    market_date = _normalize_date(archive.market_date, label="Market Date")
    risk = validate_risk_archive_frame(archive.risk)
    colossus = validate_colossus_frame(archive.colossus)
    authority_dimensions = (
        SIGNOFF_GROUP,
        ACTIVITY,
        CATEGORY,
        SUB_CATEGORY,
    )
    missing = [column for column in authority_dimensions if column not in risk]
    if missing:
        raise RiskArchiveValidationError(
            "official Risk Explorer snapshot is missing historical P&L authority "
            f"columns: {missing}"
        )
    normalized_risk = _validate_text_columns(
        risk,
        (
            PORTFOLIO,
            UNDERLYING,
            RISK_TYPE,
            RISK_GREEK,
            PRODUCT,
            SIGNOFF_GROUP,
            ACTIVITY,
            CATEGORY,
            SUB_CATEGORY,
        ),
        label="official Risk Explorer snapshot",
    )
    normalized_risk[PL] = _nullable_numeric(
        normalized_risk[PL],
        label="official Risk Explorer snapshot column 'PL'",
        allow_missing=True,
    )

    portfolio_authority = build_history_portfolio_authority(normalized_risk)
    predict_keys = [
        SIGNOFF_GROUP,
        RISK_TYPE,
        RISK_GREEK,
        UNDERLYING,
        PRODUCT,
        PORTFOLIO,
    ]
    predicted = (
        normalized_risk[predict_keys + [PL]]
        .groupby(
            predict_keys,
            as_index=False,
            sort=False,
            observed=True,
            dropna=False,
        )[PL]
        .agg(lambda values: values.sum(min_count=len(values)))
        .dropna(subset=[PL])
    )
    predicted = predicted.merge(
        portfolio_authority[[PORTFOLIO, ACTIVITY, CATEGORY, SUB_CATEGORY]],
        on=PORTFOLIO,
        how="left",
        validate="many_to_one",
    )
    predicted[HISTORY_MAPPING_STATUS] = MAPPED_HISTORY_VALUE
    predicted.insert(0, HISTORY_TYPE, PREDICT_TYPE)
    predicted.insert(0, MARKET_DATE, market_date)

    actual = colossus.merge(
        portfolio_authority,
        on=PORTFOLIO,
        how="left",
        validate="many_to_one",
    )
    authority_columns = (
        SIGNOFF_GROUP,
        PRODUCT,
        ACTIVITY,
        CATEGORY,
        SUB_CATEGORY,
        HISTORY_MAPPING_STATUS,
    )
    for column in authority_columns:
        actual[column] = actual[column].fillna(UNMAPPED_VALUE)
    actual.insert(0, HISTORY_TYPE, COLOSSUS_TYPE)
    actual.insert(0, MARKET_DATE, market_date)

    history = pd.concat(
        [
            actual[list(PL_HISTORY_COLUMNS)],
            predicted[list(PL_HISTORY_COLUMNS)],
        ],
        ignore_index=True,
    )
    duplicates = history.duplicated(list(PL_HISTORY_KEY), keep=False)
    if duplicates.any():
        keys = (
            history.loc[duplicates, list(PL_HISTORY_KEY)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise RiskArchiveValidationError(
            f"Projected P&L history contains duplicate hierarchy keys: {keys}"
        )
    return history.sort_values(list(PL_HISTORY_KEY), kind="stable").reset_index(
        drop=True
    )


def _leaf_fingerprint(leaf: Path) -> tuple[tuple[str, int, int], ...]:
    """Return a cheap immutable-leaf cache key without rereading large CSVs."""

    return tuple(
        (file_name, path.stat().st_size, path.stat().st_mtime_ns)
        for file_name in ARCHIVE_FILE_NAMES
        if (path := leaf / file_name).is_file()
    )


@lru_cache(maxsize=512)
def _project_completed_leaf_cached(
    leaf_text: str,
    fingerprint: tuple[tuple[str, int, int], ...],
) -> pd.DataFrame:
    """Validate/hash one immutable leaf once per worker and cache its projection."""

    del fingerprint
    return project_archive_to_pl_history(_load_completed_leaf(Path(leaf_text)))


def _legacy_leaf_fingerprint(leaf: Path) -> tuple[tuple[str, int, int], ...]:
    """Fingerprint one immutable checked-in legacy date leaf."""

    try:
        fingerprint = []
        for file_name in sorted(_LEGACY_HISTORY_FILE_NAMES):
            stat = (leaf / file_name).stat()
            fingerprint.append((file_name, stat.st_size, stat.st_mtime_ns))
        return tuple(fingerprint)
    except OSError as exc:
        raise RiskArchiveValidationError(
            f"Could not inspect legacy P&L history leaf {leaf}: {exc}"
        ) from exc


@lru_cache(maxsize=512)
def _load_legacy_leaf_cached(
    leaf_text: str,
    fingerprint: tuple[tuple[str, int, int], ...],
) -> pd.DataFrame:
    """Parse one unchanged legacy date leaf at most once per worker."""

    del fingerprint
    try:
        return load_legacy_pl_history_leaf(Path(leaf_text))
    except PLSendValidationError as exc:
        raise RiskArchiveValidationError(str(exc)) from exc


def load_shared_pl_history(root: str | Path) -> pd.DataFrame:
    """Load one ``data/histo`` root containing legacy and official dates.

    Legacy demo leaves contain the old ``histo.csv``/``predicted.csv`` pair.
    Completed official leaves contain the sole Predict authority ``risk.csv``
    plus ``colossus.csv``. A date leaf may use exactly one contract. Partial
    official leaves without ``_SUCCESS`` are hidden; completed leaves are
    validated against their manifest before any rows are returned.
    """

    directory = Path(root).expanduser().resolve()
    if not directory.exists():
        return pd.DataFrame(columns=list(PL_HISTORY_COLUMNS))
    if not directory.is_dir():
        raise RiskArchiveValidationError(
            f"Shared P&L history root must be a directory: {directory}"
        )

    frames: list[pd.DataFrame] = []
    try:
        leaf_entries = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise RiskArchiveValidationError(
            f"Could not inspect shared P&L history root {directory}: {exc}"
        ) from exc
    for leaf in leaf_entries:
        if _PENDING_LEAF_PATTERN.fullmatch(leaf.name):
            continue
        if not leaf.is_dir() or not _DATE_PATTERN.fullmatch(leaf.name):
            raise RiskArchiveValidationError(
                "Shared P&L history root may contain only YYYY-MM-DD leaves; "
                f"found {leaf}"
            )
        market_date = _completed_leaf_date(leaf)
        try:
            entries = tuple(leaf.iterdir())
        except OSError as exc:
            raise RiskArchiveValidationError(
                f"Could not inspect shared P&L history leaf {leaf}: {exc}"
            ) from exc
        names = {path.name for path in entries}
        legacy_artifacts = names & _LEGACY_HISTORY_FILE_NAMES
        official_artifacts = names & _OFFICIAL_HISTORY_FILE_NAMES
        if legacy_artifacts and official_artifacts:
            raise RiskArchiveValidationError(
                f"P&L history date {market_date} mixes legacy and official files"
            )
        if official_artifacts:
            if SUCCESS_FILE_NAME not in names:
                continue
            projected = _project_completed_leaf_cached(
                str(leaf),
                _leaf_fingerprint(leaf),
            )
            frames.append(projected.copy(deep=True))
            continue
        legacy = _load_legacy_leaf_cached(
            str(leaf),
            _legacy_leaf_fingerprint(leaf),
        )
        frames.append(legacy.copy(deep=True))

    if not frames:
        return pd.DataFrame(columns=list(PL_HISTORY_COLUMNS))
    try:
        history = validate_pl_history_frame(pd.concat(frames, ignore_index=True))
    except PLSendValidationError as exc:
        raise RiskArchiveValidationError(str(exc)) from exc
    duplicates = history.duplicated(list(PL_HISTORY_KEY), keep=False)
    if duplicates.any():
        raise RiskArchiveValidationError(
            "Shared P&L history contains duplicate date/type/hierarchy keys"
        )
    return history


def _market_leaf_fingerprint(leaf: Path) -> tuple[tuple[str, int, int], ...]:
    """Fingerprint only files needed to establish historical market authority."""

    names = sorted(
        {MARKET_FILE_NAME, SUCCESS_FILE_NAME} | set(_LEGACY_HISTORY_FILE_NAMES)
    )
    try:
        return tuple(
            (file_name, path.stat().st_size, path.stat().st_mtime_ns)
            for file_name in names
            if (path := leaf / file_name).is_file()
        )
    except OSError as exc:
        raise RiskArchiveValidationError(
            f"Could not inspect historical MarketBook leaf {leaf}: {exc}"
        ) from exc


@lru_cache(maxsize=4096)
def _load_market_identity_leaf_cached(
    leaf_text: str,
    fingerprint: tuple[tuple[str, int, int], ...],
    risk_type: str,
    risk_greek: str,
    underlying: str,
) -> pd.DataFrame:
    """Validate one unchanged market file and cache only one raw identity."""

    del fingerprint
    leaf = Path(leaf_text)
    market_date = _completed_leaf_date(leaf)
    names = {path.name for path in leaf.iterdir()}
    if SUCCESS_FILE_NAME in names:
        manifest = _read_manifest(leaf)
        expected_files = _completed_leaf_contract(leaf, manifest, market_date)
        if MARKET_FILE_NAME not in expected_files:
            return pd.DataFrame(columns=list(MARKET_ARCHIVE_COLUMNS))
        digests = manifest.get("sha256")
        expected_digest = (
            digests.get(MARKET_FILE_NAME) if isinstance(digests, dict) else None
        )
        if expected_digest != _file_sha256(leaf / MARKET_FILE_NAME):
            raise RiskArchiveValidationError(
                "Historical MarketBook does not match its completion marker: "
                f"{leaf / MARKET_FILE_NAME}"
            )
    else:
        missing_legacy = sorted(_LEGACY_HISTORY_FILE_NAMES - names)
        official_artifacts = names & _OFFICIAL_HISTORY_FILE_NAMES
        if official_artifacts:
            # A partial official write is never historical authority, even if a
            # market file happened to reach the target directory independently.
            return pd.DataFrame(columns=list(MARKET_ARCHIVE_COLUMNS))
        if missing_legacy:
            raise RiskArchiveValidationError(
                f"Historical MarketBook date {market_date} is not a completed "
                f"legacy P&L leaf; missing={missing_legacy}"
            )
        if MARKET_FILE_NAME not in names:
            return pd.DataFrame(columns=list(MARKET_ARCHIVE_COLUMNS))
    try:
        market = pd.read_csv(
            leaf / MARKET_FILE_NAME,
            encoding="utf-8",
            keep_default_na=False,
            dtype="string",
        )
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise RiskArchiveValidationError(
            f"Could not read historical MarketBook {leaf / MARKET_FILE_NAME}: {exc}"
        ) from exc
    market = validate_market_archive_frame(market, market_date=market_date)
    return market.loc[
        market[RISK_TYPE].eq(risk_type)
        & market[RISK_GREEK].eq(risk_greek)
        & market[UNDERLYING].eq(underlying)
    ].reset_index(drop=True)


def _identity_argument(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RiskArchiveValidationError(f"{label} must be nonblank text")
    return value.strip()


def load_market_history_for_identity(
    root: str | Path,
    risk_type: str,
    risk_greek: str,
    underlying: str,
) -> pd.DataFrame:
    """Return one raw Quick Market identity across completed archive dates.

    Selection is the structured Risk Type/Risk Greek/raw Underlying triple,
    never a parsed display label.  Every stored quote cell is retained at its
    connector-owned tenor grain; no Portfolio join, aggregation, or weighting
    occurs.  Callers can therefore select one explicit tenor cell for a daily
    series, or render the complete historical curve/surface for each date.
    """

    selected_risk_type = _identity_argument(risk_type, label=RISK_TYPE)
    selected_risk_greek = _identity_argument(risk_greek, label=RISK_GREEK)
    selected_underlying = _identity_argument(underlying, label=UNDERLYING)
    directory = Path(root).expanduser().resolve()
    if not directory.exists():
        return pd.DataFrame(columns=list(MARKET_HISTORY_COLUMNS))
    if not directory.is_dir():
        raise RiskArchiveValidationError(
            f"Historical MarketBook root must be a directory: {directory}"
        )

    frames: list[pd.DataFrame] = []
    try:
        leaves = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise RiskArchiveValidationError(
            f"Could not inspect historical MarketBook root {directory}: {exc}"
        ) from exc
    for leaf in leaves:
        if _PENDING_LEAF_PATTERN.fullmatch(leaf.name):
            continue
        if not leaf.is_dir() or not _DATE_PATTERN.fullmatch(leaf.name):
            raise RiskArchiveValidationError(
                "Historical MarketBook root may contain only YYYY-MM-DD leaves; "
                f"found {leaf}"
            )
        selected = _load_market_identity_leaf_cached(
            str(leaf),
            _market_leaf_fingerprint(leaf),
            selected_risk_type,
            selected_risk_greek,
            selected_underlying,
        ).copy(deep=True)
        if not selected.empty:
            frames.append(selected)

    if not frames:
        return pd.DataFrame(columns=list(MARKET_HISTORY_COLUMNS))
    selected_history = pd.concat(frames, ignore_index=True, sort=False)
    source_types = selected_history[SOURCE_TYPE].drop_duplicates().tolist()
    if len(source_types) != 1:
        raise RiskArchiveValidationError(
            "Historical MarketBook identity resolves to multiple Source Types: "
            f"{source_types}"
        )
    duplicates = selected_history.duplicated(
        [MARKET_DATE, TENOR_SWAP, TENOR_OPTION], keep=False
    )
    if duplicates.any():
        keys = (
            selected_history.loc[duplicates, [MARKET_DATE, TENOR_SWAP, TENOR_OPTION]]
            .drop_duplicates()
            .to_dict("records")
        )
        raise RiskArchiveValidationError(
            f"Historical MarketBook identity contains duplicate daily quote cells: "
            f"{keys}"
        )
    return (
        selected_history.loc[:, list(MARKET_HISTORY_COLUMNS)]
        .sort_values(
            [
                MARKET_DATE,
                TENOR_SWAP_ORDER,
                TENOR_OPTION_ORDER,
                TENOR_SWAP,
                TENOR_OPTION,
            ],
            kind="stable",
            na_position="last",
        )
        .reset_index(drop=True)
    )


def build_market_history_loader(
    root: str | Path,
) -> Callable[[str, str, str], pd.DataFrame]:
    """Bind a shared history root for dependency injection into Quick Market."""

    resolved_root = Path(root).expanduser().resolve()

    def load(risk_type: str, risk_greek: str, underlying: str) -> pd.DataFrame:
        return load_market_history_for_identity(
            resolved_root,
            risk_type,
            risk_greek,
            underlying,
        )

    return load


__all__ = [
    "ARCHIVE_FILE_NAMES",
    "ARCHIVE_SCHEMA_VERSION",
    "ArchiveResult",
    "BASE_ARCHIVE_FILE_NAMES",
    "COLOSSUS_COLUMNS",
    "COLOSSUS_FILE_NAME",
    "COLOSSUS_KEY",
    "ColossusLoader",
    "MARKET_ARCHIVE_COLUMNS",
    "MARKET_FILE_NAME",
    "MARKET_HISTORY_COLUMNS",
    "MARKET_IDENTITY_COLUMNS",
    "MAPPED_HISTORY_VALUE",
    "RISK_FILE_NAME",
    "PORTFOLIO_AUTHORITY_COLUMNS",
    "RiskArchive",
    "RiskArchiveValidationError",
    "SUCCESS_FILE_NAME",
    "archive_from_manager",
    "archive_leaf_path",
    "archive_official_snapshot",
    "build_history_portfolio_authority",
    "build_market_history_loader",
    "list_completed_market_dates",
    "load_risk_archive",
    "load_market_history_for_identity",
    "load_shared_pl_history",
    "project_archive_to_pl_history",
    "validate_colossus_frame",
    "validate_market_archive_frame",
    "validate_risk_archive_frame",
]
