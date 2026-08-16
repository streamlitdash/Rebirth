"""Flat, atomic archives for one official Risk Explorer snapshot per date.

The archive deliberately stores the committed dashboard frame without turning
it into a hierarchy.  A reader can rebuild the Risk Explorer hierarchy at
display time.  Colossus P&L is stored at its separate, explicit four-key grain;
it is never copied across tenor or Product rows.
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

from core.s04_pl import (
    BOOK,
    COLOSSUS_TYPE,
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
SUCCESS_FILE_NAME = "_SUCCESS"
ARCHIVE_FILE_NAMES = (RISK_FILE_NAME, COLOSSUS_FILE_NAME, SUCCESS_FILE_NAME)
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
ARCHIVE_SCHEMA_VERSION = 1

_DATE_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])")
_PENDING_LEAF_PATTERN = re.compile(
    r"\.\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\.pending-.+"
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_LEGACY_HISTORY_FILE_NAMES = frozenset(("histo.csv", "predicted.csv"))
_OFFICIAL_HISTORY_FILE_NAMES = frozenset(ARCHIVE_FILE_NAMES)


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
    errors: tuple[str, ...]


ColossusLoader = Callable[[pd.Timestamp], pd.DataFrame]


@dataclass(frozen=True)
class RiskArchive:
    """One validated, completed daily archive."""

    market_date: str
    path: Path
    risk: pd.DataFrame
    colossus: pd.DataFrame


@dataclass(frozen=True)
class ArchiveResult:
    """Small scheduler-friendly outcome that does not include archived frames."""

    status: str
    reason: str
    market_date: str
    path: Path
    risk_rows: int = 0
    colossus_rows: int = 0

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


def _load_completed_leaf(leaf: Path) -> RiskArchive:
    if not leaf.exists() or not leaf.is_dir():
        raise RiskArchiveValidationError(f"Risk archive leaf does not exist: {leaf}")
    actual_entries = {path.name for path in leaf.iterdir()}
    expected_files = set(ARCHIVE_FILE_NAMES)
    if actual_entries != expected_files:
        missing = sorted(expected_files - actual_entries)
        extra = sorted(actual_entries - expected_files)
        raise RiskArchiveValidationError(
            f"Risk archive leaf {leaf} is incomplete or invalid; "
            f"missing={missing}, extra={extra}"
        )
    market_date = _completed_leaf_date(leaf)
    manifest = _read_manifest(leaf)
    if manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise RiskArchiveValidationError(
            f"Risk archive leaf {leaf} has an unsupported schema version"
        )
    if manifest.get("market_date") != market_date:
        raise RiskArchiveValidationError(
            f"Risk archive marker date does not match its leaf: {leaf}"
        )
    if manifest.get("market_status") != OFFICIAL:
        raise RiskArchiveValidationError(f"Risk archive marker is not OFFICIAL: {leaf}")
    if manifest.get("colossus_columns") != list(COLOSSUS_COLUMNS):
        raise RiskArchiveValidationError(
            f"Colossus archive columns do not match its completion marker: {leaf}"
        )
    for file_name in (RISK_FILE_NAME, COLOSSUS_FILE_NAME):
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
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise RiskArchiveValidationError(
            f"Could not read completed risk archive {leaf}: {exc}"
        ) from exc
    risk = validate_risk_archive_frame(risk)
    colossus = validate_colossus_frame(colossus)
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
    return RiskArchive(
        market_date=market_date,
        path=leaf,
        risk=risk,
        colossus=colossus,
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
        if {path.name for path in leaf.iterdir()} != set(ARCHIVE_FILE_NAMES):
            raise RiskArchiveValidationError(
                f"Completed risk archive leaf is incomplete or invalid: {leaf}"
            )
        manifest = _read_manifest(leaf)
        digests = manifest.get("sha256")
        risk_columns = manifest.get("risk_columns")
        row_counts = (manifest.get("risk_rows"), manifest.get("colossus_rows"))
        valid_marker = (
            manifest.get("schema_version") == ARCHIVE_SCHEMA_VERSION
            and manifest.get("market_date") == market_date
            and manifest.get("market_status") == OFFICIAL
            and isinstance(risk_columns, list)
            and all(
                isinstance(column, str) and bool(column.strip())
                for column in risk_columns
            )
            and set(RISK_PROJECTION_COLUMNS).issubset(risk_columns)
            and manifest.get("colossus_columns") == list(COLOSSUS_COLUMNS)
            and all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in row_counts
            )
            and isinstance(digests, dict)
            and all(
                isinstance(digests.get(file_name), str)
                and _SHA256_PATTERN.fullmatch(digests[file_name])
                for file_name in (RISK_FILE_NAME, COLOSSUS_FILE_NAME)
            )
        )
        if not valid_marker:
            raise RiskArchiveValidationError(
                f"Completed risk archive marker is invalid: {leaf / SUCCESS_FILE_NAME}"
            )
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
    )


def archive_official_snapshot(
    snapshot: OfficialSnapshot,
    colossus_loader: ColossusLoader,
    root: str | Path,
) -> ArchiveResult:
    """Atomically write one eligible committed snapshot and its Colossus P&L.

    Eligibility is deliberately narrow: the selected Market Date must be the
    manager's natural System Date, the committed source must be exactly
    ``OFFICIAL``, and the snapshot must not be a retained last-good revision
    carrying refresh errors.  Re-running a completed date is a no-op.
    """

    market_date = _normalize_date(snapshot.market_date, label="Market Date")
    system_date = _normalize_date(snapshot.system_date, label="System Date")
    leaf = archive_leaf_path(root, market_date)
    status = str(snapshot.market_status).strip()
    if market_date != system_date:
        return ArchiveResult(
            status="skipped",
            reason="Selected Market Date is not the current natural System Date.",
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
        refreshed_at = getattr(snapshot, "refreshed_at", None)
        refreshed_text = (
            refreshed_at.isoformat()
            if isinstance(refreshed_at, datetime)
            else str(refreshed_at or "")
        )
        manifest: dict[str, object] = {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
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


def project_archive_to_pl_history(archive: RiskArchive) -> pd.DataFrame:
    """Project one archive into the existing canonical Colossus/Predict grain.

    Predict is summed from position rows only after grouping to Risk Type +
    Risk Greek + Underlying + Product + Portfolio.  A partially missing PL
    group is omitted rather than treated as a partial or zero total.  Colossus
    receives Product only from the snapshot's strict Portfolio-to-Product
    authority; unknown or multi-Product portfolios fail closed.
    """

    market_date = _normalize_date(archive.market_date, label="Market Date")
    risk = validate_risk_archive_frame(archive.risk)
    colossus = validate_colossus_frame(archive.colossus)
    normalized_risk = _validate_text_columns(
        risk,
        (PORTFOLIO, UNDERLYING, RISK_TYPE, RISK_GREEK, PRODUCT),
        label="official Risk Explorer snapshot",
    )
    normalized_risk[PL] = _nullable_numeric(
        normalized_risk[PL],
        label="official Risk Explorer snapshot column 'PL'",
        allow_missing=True,
    )

    product_authority = normalized_risk[[PORTFOLIO, PRODUCT]].drop_duplicates()
    ambiguous = product_authority.duplicated(PORTFOLIO, keep=False)
    if ambiguous.any():
        keys = (
            product_authority.loc[ambiguous]
            .sort_values([PORTFOLIO, PRODUCT], kind="stable")
            .to_dict("records")
        )
        raise RiskArchiveValidationError(
            "Cannot project Colossus: Portfolio does not map to exactly one "
            f"Product in risk.csv: {keys}"
        )

    predict_keys = [RISK_TYPE, RISK_GREEK, UNDERLYING, PRODUCT, PORTFOLIO]
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
        .rename(columns={PORTFOLIO: BOOK})
    )
    predicted.insert(0, HISTORY_TYPE, PREDICT_TYPE)
    predicted.insert(0, MARKET_DATE, market_date)

    actual = colossus.merge(
        product_authority,
        on=PORTFOLIO,
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    unknown = actual["_merge"].ne("both")
    if unknown.any():
        portfolios = sorted(actual.loc[unknown, PORTFOLIO].drop_duplicates().tolist())
        raise RiskArchiveValidationError(
            "Cannot project Colossus because these Portfolios have no Product "
            f"authority in risk.csv: {portfolios}"
        )
    actual = actual.drop(columns="_merge").rename(columns={PORTFOLIO: BOOK})
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


__all__ = [
    "ARCHIVE_FILE_NAMES",
    "ArchiveResult",
    "COLOSSUS_COLUMNS",
    "COLOSSUS_FILE_NAME",
    "COLOSSUS_KEY",
    "ColossusLoader",
    "RISK_FILE_NAME",
    "RiskArchive",
    "RiskArchiveValidationError",
    "SUCCESS_FILE_NAME",
    "archive_from_manager",
    "archive_leaf_path",
    "archive_official_snapshot",
    "list_completed_market_dates",
    "load_risk_archive",
    "load_shared_pl_history",
    "project_archive_to_pl_history",
    "validate_colossus_frame",
    "validate_risk_archive_frame",
]
