"""Pure PL-send mapping, aggregation, adjustment, and export rules.

This module deliberately performs no application, connector, or filesystem I/O
other than reading explicitly supplied CSV paths.  Dash callbacks and production
senders can therefore share one fail-closed financial data contract.
"""

from __future__ import annotations

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
    "FrameSource",
    "MAPPING_COLUMNS",
    "MARKET_DATE",
    "PL",
    "CONCERTO_FIELD",
    "PLSendValidationError",
    "PL_SEND_COLUMNS",
    "PL_SEND_KEY",
    "PORTFOLIO",
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
    "load_portfolio_governance",
    "normalize_market_date",
    "normalize_pl_send_rows",
    "validate_pl_send_rows",
]
