"""Validated reporting identities applied after raw market P&L calculation.

``Underlying`` is the connector-owned market identity.  It must remain
unchanged while Risk is joined to Open/Current and while P&L is calculated.
``Reported Underlying`` is a separate presentation identity that may combine
several raw Underlyings only after those calculations are complete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Collection

import pandas as pd


RISK_TYPE = "Risk Type"
RISK_GREEK = "Risk Greek"
UNDERLYING = "Underlying"
REPORTED_UNDERLYING = "Reported Underlying"
REPORTED_UNDERLYING_KEY = (RISK_TYPE, RISK_GREEK, UNDERLYING)
REPORTED_UNDERLYING_COLUMNS = (
    *REPORTED_UNDERLYING_KEY,
    REPORTED_UNDERLYING,
)


def _empty_mapping() -> pd.DataFrame:
    return pd.DataFrame(columns=list(REPORTED_UNDERLYING_COLUMNS), dtype="string")


def load_reported_underlying_mapping(
    source: pd.DataFrame | str | Path | None,
    *,
    allowed_pairs: Collection[tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """Load and validate one exact, non-recursive reporting map.

    The source key is ``Risk Type + Risk Greek + Underlying``.  It must be
    unique, but many source keys may deliberately share one reported target.
    A header-only file is valid and means every row keeps its raw identity.
    """

    if source is None:
        return _empty_mapping()
    if isinstance(source, (str, Path)):
        try:
            frame = pd.read_csv(
                source,
                dtype="string",
                encoding="utf-8-sig",
                keep_default_na=False,
            )
        except (OSError, UnicodeError, pd.errors.ParserError) as exc:
            raise ValueError(
                f"Could not read Reported Underlying mapping {source}: {exc}"
            ) from exc
    elif isinstance(source, pd.DataFrame):
        frame = source.copy()
    else:
        raise TypeError(
            "Reported Underlying mapping must be a DataFrame, CSV path, or None"
        )

    actual_columns = list(frame.columns)
    expected_columns = list(REPORTED_UNDERLYING_COLUMNS)
    if actual_columns != expected_columns:
        raise ValueError(
            "Reported Underlying mapping columns must be exactly "
            f"{expected_columns} in that order; found {actual_columns}"
        )
    if frame.empty:
        return _empty_mapping()

    result = frame.copy()
    for column in REPORTED_UNDERLYING_COLUMNS:
        values = result[column]
        non_text = ~values.map(lambda value: isinstance(value, str))
        if non_text.any():
            rows = result.index[non_text].tolist()[:5]
            raise ValueError(
                f"Reported Underlying mapping column {column!r} must contain "
                f"text at rows {rows}"
            )
        result[column] = values.astype("string").str.strip()
        blank = result[column].isna() | result[column].eq("")
        if blank.any():
            rows = result.index[blank].tolist()[:5]
            raise ValueError(
                f"Reported Underlying mapping column {column!r} is blank at rows {rows}"
            )

    duplicate = result.duplicated(list(REPORTED_UNDERLYING_KEY), keep=False)
    if duplicate.any():
        records = (
            result.loc[duplicate, list(REPORTED_UNDERLYING_KEY)]
            .drop_duplicates()
            .head(5)
            .to_dict("records")
        )
        raise ValueError(
            "Reported Underlying mapping source keys must be unique; "
            f"duplicates={records}"
        )

    if allowed_pairs is not None:
        allowed = set(allowed_pairs)
        actual = set(
            result[[RISK_TYPE, RISK_GREEK]]
            .drop_duplicates()
            .itertuples(index=False, name=None)
        )
        unknown = sorted(actual - allowed)
        if unknown:
            raise ValueError(
                "Reported Underlying mapping contains unregistered Risk Type + "
                f"Risk Greek pairs: {unknown}"
            )

    return result.loc[:, expected_columns].reset_index(drop=True)


def attach_reported_underlying(
    frame: pd.DataFrame,
    mapping: pd.DataFrame | str | Path | None,
    *,
    allowed_pairs: Collection[tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """Attach a reporting identity without changing row order or raw values."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("reporting input must be a pandas DataFrame")
    missing = [
        column for column in REPORTED_UNDERLYING_KEY if column not in frame.columns
    ]
    if missing:
        raise ValueError(f"reporting input is missing required columns: {missing}")
    if REPORTED_UNDERLYING in frame.columns:
        raise ValueError(
            "reporting input already contains 'Reported Underlying'; apply the "
            "mapping exactly once after P&L"
        )

    validated = load_reported_underlying_mapping(
        mapping,
        allowed_pairs=allowed_pairs,
    )
    result = frame.copy()
    if validated.empty:
        reported = result[UNDERLYING].copy()
    else:
        lookup = validated.set_index(list(REPORTED_UNDERLYING_KEY))[REPORTED_UNDERLYING]
        row_keys = pd.MultiIndex.from_frame(
            result.loc[:, list(REPORTED_UNDERLYING_KEY)]
        )
        reported = pd.Series(
            lookup.reindex(row_keys).array,
            index=result.index,
            dtype="string",
        )
        reported = reported.fillna(result[UNDERLYING].astype("string"))

    insert_at = result.columns.get_loc(UNDERLYING) + 1
    result.insert(insert_at, REPORTED_UNDERLYING, reported)
    return result


__all__ = [
    "REPORTED_UNDERLYING",
    "REPORTED_UNDERLYING_COLUMNS",
    "REPORTED_UNDERLYING_KEY",
    "attach_reported_underlying",
    "load_reported_underlying_mapping",
]
