"""Validated date/portfolio CSV storage for active P&L adjustments.

The repository has one layout and no migration branch::

    adjustments/<YYYY-MM-DD>/<safe-portfolio-name>--<hash>.csv

Every file contains exactly one Portfolio.  A save replaces complete portfolio
files atomically and preserves files for portfolios that were not part of the
save request.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable

import numpy as np
import pandas as pd

from core.s04_pl import (
    ADJUSTMENT,
    ADJUSTMENT_KEY,
    PL_SEND_COLUMNS,
    PORTFOLIO,
    PLSendValidationError,
    empty_pl_send_frame,
    normalize_market_date,
    normalize_pl_send_rows,
)


BASE_REVISION = "Base Revision"
SAVED_AT_UTC = "Saved At UTC"
ADJUSTMENT_ID = "Adjustment ID"
PERSISTED_ADJUSTMENT_COLUMNS = (
    *PL_SEND_COLUMNS,
    BASE_REVISION,
    SAVED_AT_UTC,
    ADJUSTMENT_ID,
)
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_STEM_LENGTH = 80


class AdjustmentPersistenceError(RuntimeError):
    """Raised when adjustment data cannot be safely validated or persisted."""


def _normalise_revision(value: object) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise AdjustmentPersistenceError("Base Revision must be a nonnegative integer")
    try:
        revision = int(value)
        exact = float(value) == float(revision)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AdjustmentPersistenceError(
            "Base Revision must be a nonnegative integer"
        ) from exc
    if revision < 0 or not exact:
        raise AdjustmentPersistenceError("Base Revision must be a nonnegative integer")
    return revision


def _normalise_saved_at(value: datetime | str | None) -> str:
    if value is None:
        parsed = pd.Timestamp.now(tz="UTC")
    else:
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise AdjustmentPersistenceError(
                "Saved At UTC must be a valid timestamp"
            ) from exc
        if pd.isna(parsed):
            raise AdjustmentPersistenceError("Saved At UTC must be a valid timestamp")
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize(timezone.utc)
        else:
            parsed = parsed.tz_convert(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def empty_persisted_adjustments() -> pd.DataFrame:
    """Return an empty frame with the exact persisted schema."""
    return pd.DataFrame(columns=list(PERSISTED_ADJUSTMENT_COLUMNS))


def _portfolio_filename(portfolio: object) -> str:
    """Create a readable, bounded and collision-resistant portfolio filename."""
    if not isinstance(portfolio, str) or not portfolio.strip():
        raise AdjustmentPersistenceError("Portfolio must be nonblank text")
    canonical = portfolio.strip()
    ascii_name = (
        unicodedata.normalize("NFKD", canonical)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    stem = _UNSAFE_FILENAME.sub("_", ascii_name).strip("._-")
    stem = stem[:_MAX_STEM_LENGTH].rstrip("._-") or "portfolio"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{stem}--{digest}.csv"


class LocalCsvAdjustmentRepository:
    """Read and upsert adjustment files under ``date/portfolio`` folders."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self._lock = RLock()

    def path_for_date(self, market_date: object) -> Path:
        return self.directory / normalize_market_date(market_date)

    def path_for_portfolio(self, market_date: object, portfolio: object) -> Path:
        return self.path_for_date(market_date) / _portfolio_filename(portfolio)

    def _read_file(self, path: Path, normalized_date: str) -> pd.DataFrame:
        try:
            raw = pd.read_csv(
                path,
                dtype="string",
                encoding="utf-8-sig",
                keep_default_na=False,
            )
        except (OSError, UnicodeError, pd.errors.ParserError) as exc:
            raise AdjustmentPersistenceError(
                f"Could not read adjustment file {path.name}: {exc}"
            ) from exc

        actual = tuple(str(column).strip() for column in raw.columns)
        if actual != PERSISTED_ADJUSTMENT_COLUMNS:
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} must have exactly columns "
                f"{list(PERSISTED_ADJUSTMENT_COLUMNS)}; found {list(actual)}"
            )
        if raw.empty:
            raise AdjustmentPersistenceError(
                f"Portfolio adjustment file {path.name} must contain at least one row"
            )
        raw.columns = list(PERSISTED_ADJUSTMENT_COLUMNS)

        adjustment = raw[ADJUSTMENT].astype(str).str.strip().str.casefold()
        if not adjustment.eq("true").all():
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} contains a non-true Adjustment value"
            )
        raw[ADJUSTMENT] = True
        try:
            core = normalize_pl_send_rows(
                raw[list(PL_SEND_COLUMNS)], label=f"adjustment file {path.name}"
            )
        except (TypeError, PLSendValidationError) as exc:
            raise AdjustmentPersistenceError(str(exc)) from exc
        if core[ADJUSTMENT_KEY[0]].ne(normalized_date).any():
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} contains a different Market Date"
            )
        if core.duplicated(list(ADJUSTMENT_KEY)).any():
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} contains duplicate adjustment keys"
            )

        portfolios = core[PORTFOLIO].drop_duplicates().tolist()
        if len(portfolios) != 1:
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} must contain exactly one Portfolio"
            )
        expected_name = _portfolio_filename(portfolios[0])
        if path.name != expected_name:
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} must be named {expected_name!r}"
            )

        revisions = pd.to_numeric(raw[BASE_REVISION], errors="coerce")
        invalid_revision = (
            revisions.isna()
            | ~np.isfinite(revisions)
            | revisions.lt(0)
            | revisions.mod(1).ne(0)
        )
        if invalid_revision.any():
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} contains an invalid Base Revision"
            )
        timestamps = pd.to_datetime(raw[SAVED_AT_UTC], errors="coerce", utc=True)
        if timestamps.isna().any():
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} contains an invalid Saved At UTC"
            )
        identifiers = raw[ADJUSTMENT_ID].astype(str).str.strip()
        if identifiers.eq("").any() or identifiers.duplicated().any():
            raise AdjustmentPersistenceError(
                f"Adjustment file {path.name} contains blank or duplicate Adjustment IDs"
            )

        result = raw.copy()
        for column in PL_SEND_COLUMNS:
            result[column] = core[column]
        result[BASE_REVISION] = revisions.astype(int)
        result[SAVED_AT_UTC] = raw[SAVED_AT_UTC].astype(str)
        result[ADJUSTMENT_ID] = identifiers
        return result[list(PERSISTED_ADJUSTMENT_COLUMNS)].reset_index(drop=True)

    def _load_unlocked(self, normalized_date: str) -> pd.DataFrame:
        date_directory = self.directory / normalized_date
        if date_directory.exists() and not date_directory.is_dir():
            raise AdjustmentPersistenceError(
                f"Adjustment date path {date_directory} must be a directory"
            )
        if not date_directory.exists():
            return empty_persisted_adjustments()

        frames = [
            self._read_file(path, normalized_date)
            for path in sorted(date_directory.glob("*.csv"), key=lambda item: item.name)
        ]
        if not frames:
            return empty_persisted_adjustments()
        result = pd.concat(frames, ignore_index=True, sort=False)
        if result.duplicated(list(ADJUSTMENT_KEY)).any():
            raise AdjustmentPersistenceError(
                f"Adjustment date {normalized_date} contains duplicate adjustment keys"
            )
        if result[ADJUSTMENT_ID].duplicated().any():
            raise AdjustmentPersistenceError(
                f"Adjustment date {normalized_date} contains duplicate Adjustment IDs"
            )
        return (
            result[list(PERSISTED_ADJUSTMENT_COLUMNS)]
            .sort_values(list(ADJUSTMENT_KEY), kind="stable")
            .reset_index(drop=True)
        )

    def save(
        self,
        market_date: object,
        rows: pd.DataFrame,
        *,
        base_revision: object,
        saved_at: datetime | str | None = None,
        replace_portfolios: Iterable[str] | None = None,
    ) -> Path:
        """Atomically replace the requested Portfolio files.

        By default only Portfolios represented in ``rows`` are replaced.
        ``replace_portfolios`` may additionally name Portfolios whose saved
        adjustment should be removed when no corresponding row is supplied.
        Unrelated Portfolio files are never rewritten.
        """
        normalized_date = normalize_market_date(market_date)
        revision = _normalise_revision(base_revision)
        timestamp = _normalise_saved_at(saved_at)
        candidate = empty_pl_send_frame() if rows is None else rows
        try:
            normalized = normalize_pl_send_rows(
                candidate, label="persisted adjustments"
            )
        except (TypeError, PLSendValidationError) as exc:
            raise AdjustmentPersistenceError(str(exc)) from exc
        if not normalized.empty:
            if normalized[ADJUSTMENT_KEY[0]].ne(normalized_date).any():
                raise AdjustmentPersistenceError(
                    "persisted adjustments must match the requested Market Date"
                )
            if not normalized[ADJUSTMENT].eq(True).all():
                raise AdjustmentPersistenceError(
                    "persisted adjustments must all have Adjustment=True"
                )
            if normalized.duplicated(list(ADJUSTMENT_KEY)).any():
                raise AdjustmentPersistenceError(
                    "persisted adjustments must be unique by Market Date + "
                    "Portfolio + ConcertoField"
                )

        incoming = normalized[list(PL_SEND_COLUMNS)].copy()
        incoming[BASE_REVISION] = revision
        incoming[SAVED_AT_UTC] = timestamp
        incoming[ADJUSTMENT_ID] = [uuid.uuid4().hex for _ in range(len(incoming))]
        incoming = incoming[list(PERSISTED_ADJUSTMENT_COLUMNS)]

        incoming_portfolios = set(incoming[PORTFOLIO].astype(str).tolist())
        if replace_portfolios is None:
            target_portfolios = set(incoming_portfolios)
        else:
            target_portfolios = set()
            for value in replace_portfolios:
                if not isinstance(value, str) or not value.strip():
                    raise AdjustmentPersistenceError(
                        "replace_portfolios must contain nonblank Portfolio names"
                    )
                target_portfolios.add(value.strip())
            unexpected = incoming_portfolios - target_portfolios
            if unexpected:
                raise AdjustmentPersistenceError(
                    "persisted adjustments contain Portfolio values outside "
                    f"replace_portfolios: {sorted(unexpected)}"
                )

        date_directory = self.path_for_date(normalized_date)
        with self._lock:
            date_directory.mkdir(parents=True, exist_ok=True)
            destinations = {
                self.path_for_portfolio(normalized_date, portfolio): group.reset_index(
                    drop=True
                )
                for portfolio, group in incoming.groupby(
                    PORTFOLIO, sort=False, dropna=False
                )
            }
            target_paths = {
                self.path_for_portfolio(normalized_date, portfolio)
                for portfolio in target_portfolios
            }

            # Reject a stale editor attempting to overwrite data saved against a
            # newer committed snapshot. The UI separately verifies equality with
            # the current manager revision before calling this boundary.
            for path in sorted(target_paths, key=lambda item: item.name):
                if not path.is_file():
                    continue
                existing = self._read_file(path, normalized_date)
                if int(existing[BASE_REVISION].max()) > revision:
                    raise AdjustmentPersistenceError(
                        f"Base Revision {revision} is older than saved Portfolio "
                        f"{existing[PORTFOLIO].iloc[0]!r}"
                    )

            temporary_files: list[tuple[Path, Path]] = []
            backup_files: list[tuple[Path, Path]] = []
            try:
                for destination, portfolio_rows in destinations.items():
                    temporary = destination.with_name(
                        f".{destination.name}.{uuid.uuid4().hex}.tmp"
                    )
                    with temporary.open("w", encoding="utf-8", newline="") as handle:
                        portfolio_rows.to_csv(
                            handle,
                            index=False,
                            lineterminator="\n",
                            float_format="%.17g",
                        )
                        handle.flush()
                        os.fsync(handle.fileno())
                    temporary_files.append((temporary, destination))

                # Move every affected old file aside before publishing any new
                # file. If a later replace fails, the complete previous state is
                # restored while the repository lock still excludes readers.
                for destination in sorted(target_paths, key=lambda item: item.name):
                    if not destination.exists():
                        continue
                    backup = destination.with_name(
                        f".{destination.name}.{uuid.uuid4().hex}.bak"
                    )
                    os.replace(destination, backup)
                    backup_files.append((backup, destination))
                for temporary, destination in temporary_files:
                    os.replace(temporary, destination)
                for backup, _destination in backup_files:
                    backup.unlink(missing_ok=True)
            except OSError as exc:
                for destination in target_paths:
                    if destination.exists():
                        destination.unlink(missing_ok=True)
                for backup, destination in reversed(backup_files):
                    if backup.exists():
                        os.replace(backup, destination)
                raise AdjustmentPersistenceError(
                    f"Could not save adjustments for {normalized_date}: {exc}"
                ) from exc
            finally:
                for temporary, _destination in temporary_files:
                    temporary.unlink(missing_ok=True)
                for backup, _destination in backup_files:
                    backup.unlink(missing_ok=True)
        return date_directory

    def load(self, market_date: object) -> pd.DataFrame:
        normalized_date = normalize_market_date(market_date)
        with self._lock:
            return self._load_unlocked(normalized_date)


__all__ = [
    "ADJUSTMENT_ID",
    "AdjustmentPersistenceError",
    "BASE_REVISION",
    "LocalCsvAdjustmentRepository",
    "PERSISTED_ADJUSTMENT_COLUMNS",
    "SAVED_AT_UTC",
    "empty_persisted_adjustments",
]
