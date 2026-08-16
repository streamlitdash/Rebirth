"""Shared filter authority for Validate P&L and Histo P&L."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .s02_constants import FILTER_DIMENSION_FIELDS


PL_EXPLORER_FILTER_COLUMNS = tuple(
    field.external_name for field in FILTER_DIMENSION_FIELDS
)
PL_EXPLORER_FILTER_IDS = {
    field.key: f"pnl-explorer-{field.key}-filter" for field in FILTER_DIMENSION_FIELDS
}
PL_EXPLORER_EXCLUDE_ID = "pnl-explorer-filter-exclude-selected"


def pl_explorer_filter_map(
    values: Sequence[Sequence[object] | None],
) -> dict[str, list[str]]:
    """Normalize the five Explorer selectors to canonical external columns."""

    if len(values) != len(FILTER_DIMENSION_FIELDS):
        raise ValueError(
            f"Expected {len(FILTER_DIMENSION_FIELDS)} P&L Explorer filters; "
            f"found {len(values)}"
        )
    return {
        field.external_name: [
            text for value in (selected or []) if (text := str(value).strip())
        ]
        for field, selected in zip(FILTER_DIMENSION_FIELDS, values, strict=True)
    }


def pl_explorer_filter_options(
    *frames: pd.DataFrame,
) -> dict[str, list[dict[str, str]]]:
    """Return a case-insensitively deduplicated union of available dimensions."""

    options: dict[str, list[dict[str, str]]] = {}
    for column in PL_EXPLORER_FILTER_COLUMNS:
        values_by_fold: dict[str, str] = {}
        for frame in frames:
            if not isinstance(frame, pd.DataFrame) or column not in frame:
                continue
            for raw in frame[column].dropna().tolist():
                value = str(raw).strip()
                if value:
                    values_by_fold.setdefault(value.casefold(), value)
        values = sorted(values_by_fold.values(), key=str.casefold)
        options[column] = [{"label": value, "value": value} for value in values]
    return options


def apply_pl_explorer_filters(
    frame: pd.DataFrame,
    selections: Mapping[str, Sequence[object] | None] | None,
    *,
    exclude_selected: bool = False,
) -> pd.DataFrame:
    """Apply OR within a selector and include-AND/exclude-OR across selectors."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("P&L Explorer source must be a pandas DataFrame")
    normalized = {
        str(column): {
            text.casefold() for value in (values or []) if (text := str(value).strip())
        }
        for column, values in (selections or {}).items()
    }
    populated = {column: values for column, values in normalized.items() if values}
    if not populated or frame.empty:
        return frame.copy(deep=True)
    missing = sorted(column for column in populated if column not in frame)
    if missing:
        raise ValueError(f"P&L Explorer source is missing filter columns: {missing}")

    if exclude_selected:
        matched = pd.Series(False, index=frame.index)
        for column, selected in populated.items():
            values = frame[column].astype("string").str.strip().str.casefold()
            matched |= values.isin(selected).fillna(False)
        keep = ~matched
    else:
        keep = pd.Series(True, index=frame.index)
        for column, selected in populated.items():
            values = frame[column].astype("string").str.strip().str.casefold()
            keep &= values.isin(selected).fillna(False)
    return frame.loc[keep].copy(deep=True)


__all__ = [
    "PL_EXPLORER_EXCLUDE_ID",
    "PL_EXPLORER_FILTER_COLUMNS",
    "PL_EXPLORER_FILTER_IDS",
    "apply_pl_explorer_filters",
    "pl_explorer_filter_map",
    "pl_explorer_filter_options",
]
