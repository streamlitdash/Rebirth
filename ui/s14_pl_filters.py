"""Pure helpers for the single authoritative P&L-page filter set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from .s02_constants import FILTER_DIMENSION_FIELDS


PL_FILTER_IDS = {
    field.key: f"pnl-{field.key}-filter" for field in FILTER_DIMENSION_FIELDS
}
PL_FILTER_EXCLUDE_ID = "pnl-filter-exclude-selected"


def pl_external_filter_map(
    values: Sequence[Sequence[object] | None],
) -> dict[str, list[str]]:
    """Normalize the five P&L selectors to canonical external columns."""

    if len(values) != len(FILTER_DIMENSION_FIELDS):
        raise ValueError(
            f"Expected {len(FILTER_DIMENSION_FIELDS)} P&L filters; found {len(values)}"
        )
    return {
        field.external_name: [
            text for value in (selected or []) if (text := str(value).strip())
        ]
        for field, selected in zip(FILTER_DIMENSION_FIELDS, values, strict=True)
    }


def apply_pl_filters(
    frame: pd.DataFrame,
    selections: Mapping[str, Sequence[object] | None] | None,
    *,
    exclude_selected: bool = False,
) -> pd.DataFrame:
    """Apply OR within a selector and include-AND/exclude-OR across selectors."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("P&L filter source must be a pandas DataFrame")
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
        raise ValueError(f"P&L filter source is missing filter columns: {missing}")

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
    "PL_FILTER_EXCLUDE_ID",
    "PL_FILTER_IDS",
    "apply_pl_filters",
    "pl_external_filter_map",
]
