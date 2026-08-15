"""Pure Stock-to-Portfolio mapping at the governed Portfolio grain."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from core.s01_schema import (
    PORTFOLIO_COLUMN,
    PORTFOLIO_FIELDS,
    PORTFOLIO_MAPPED_COLUMN,
    PORTFOLIO_METADATA_COLUMNS,
)
from core.s02_pipeline import merge_config


STOCK_TEXT_COLUMNS = (
    "CRDS",
    "CPTY",
    "Portfolio",
    "Instrument",
    "Currency",
)
STOCK_NUMERIC_COLUMNS = ("Quantity", "Market Value")
STOCK_COLUMNS = (*STOCK_TEXT_COLUMNS, *STOCK_NUMERIC_COLUMNS)
STOCK_IDENTITY_COLUMNS = STOCK_TEXT_COLUMNS
MAPPED_STOCK_COLUMNS = (
    *STOCK_COLUMNS,
    *PORTFOLIO_METADATA_COLUMNS,
    PORTFOLIO_MAPPED_COLUMN,
)
PRIOR_QUANTITY_COLUMN = "Prior Quantity"
CURRENT_QUANTITY_COLUMN = "Current Quantity"
QUANTITY_CHANGE_COLUMN = "Quantity Change"
PRIOR_MARKET_VALUE_COLUMN = "Prior Market Value"
CURRENT_MARKET_VALUE_COLUMN = "Current Market Value"
MARKET_VALUE_CHANGE_COLUMN = "Market Value Change"
STOCK_CHANGE_COLUMN = "Stock Change"
STOCK_COMPARISON_NUMERIC_COLUMNS = (
    PRIOR_QUANTITY_COLUMN,
    CURRENT_QUANTITY_COLUMN,
    QUANTITY_CHANGE_COLUMN,
    PRIOR_MARKET_VALUE_COLUMN,
    CURRENT_MARKET_VALUE_COLUMN,
    MARKET_VALUE_CHANGE_COLUMN,
)
STOCK_COMPARISON_COLUMNS = (
    *STOCK_IDENTITY_COLUMNS,
    *STOCK_COMPARISON_NUMERIC_COLUMNS,
    STOCK_CHANGE_COLUMN,
)
MAPPED_STOCK_COMPARISON_COLUMNS = (
    *STOCK_COMPARISON_COLUMNS,
    *PORTFOLIO_METADATA_COLUMNS,
    PORTFOLIO_MAPPED_COLUMN,
)
STOCK_FILTER_COLUMN_BY_KEY = {
    "portfolio": PORTFOLIO_COLUMN,
    **{
        field.key: field.external_name
        for field in PORTFOLIO_FIELDS
        if "filter_dimension" in field.roles
    },
}


def validate_stock_frame(value: object, *, label: str = "Stock") -> pd.DataFrame:
    """Validate and copy the exact core-owned Stock schema."""

    if not isinstance(value, pd.DataFrame):
        raise TypeError(f"{label} must return a pandas DataFrame")
    actual = tuple(value.columns)
    if actual != STOCK_COLUMNS:
        raise ValueError(
            f"{label} columns must be exactly {list(STOCK_COLUMNS)} in that order; "
            f"found {list(actual)}"
        )
    frame = value.copy()
    for column in STOCK_TEXT_COLUMNS:
        values = frame[column]
        valid = values.map(lambda item: isinstance(item, str) and bool(item.strip()))
        if not valid.all():
            rows = frame.index[~valid].tolist()[:5]
            raise ValueError(
                f"{label} column {column!r} must contain nonblank text at rows {rows}"
            )
        frame[column] = values.astype("string").str.strip()

    for column in STOCK_NUMERIC_COLUMNS:
        values = frame[column]
        boolean = values.map(lambda item: isinstance(item, (bool, np.bool_)))
        numeric = pd.to_numeric(values, errors="coerce")
        invalid = boolean | numeric.isna() | ~np.isfinite(numeric)
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"{label} column {column!r} must contain finite numbers at rows {rows}"
            )
        frame[column] = numeric
    return frame


def map_stock_portfolios(
    stock: pd.DataFrame,
    portfolio_config: pd.DataFrame | str | Path,
) -> pd.DataFrame:
    """Attach authoritative Portfolio metadata without dropping unmapped Stock.

    ``merge_config`` validates that the mapping has one row per ``Portfolio`` and
    performs the canonical left ``many_to_one`` join. Unmapped Stock rows are
    retained with ``Portfolio Mapped=False`` and governed metadata set to
    ``Unmapped``.
    """

    validated_stock = validate_stock_frame(stock)
    mapped = merge_config(validated_stock, portfolio_config)
    return mapped[list(MAPPED_STOCK_COLUMNS)].copy()


def _reject_duplicate_stock_identity(frame: pd.DataFrame, *, label: str) -> None:
    duplicate = frame.duplicated(list(STOCK_IDENTITY_COLUMNS), keep=False)
    if not duplicate.any():
        return
    examples = (
        frame.loc[duplicate, list(STOCK_IDENTITY_COLUMNS)]
        .drop_duplicates()
        .head(5)
        .astype(str)
        .agg(" / ".join, axis=1)
        .tolist()
    )
    raise ValueError(
        f"{label} contains duplicate Stock identities on "
        f"{list(STOCK_IDENTITY_COLUMNS)}: {examples}"
    )


def compare_stock_snapshots(
    current_stock: pd.DataFrame,
    prior_stock: pd.DataFrame,
) -> pd.DataFrame:
    """Outer-compare two Stock snapshots at one explicit position grain.

    The five text fields are the temporary Stock identity until the real site
    connector supplies a narrower governed key. Duplicates are rejected rather
    than aggregated because summing them could conceal a source-grain defect.
    Missing legs remain unavailable in the displayed prior/current columns;
    changes treat an absent leg as zero and are labelled Added or Removed so
    that convention is visible to the user.
    """

    current = validate_stock_frame(current_stock, label="Current Stock")
    prior = validate_stock_frame(prior_stock, label="Prior Stock")
    _reject_duplicate_stock_identity(current, label="Current Stock")
    _reject_duplicate_stock_identity(prior, label="Prior Stock")

    current_leg = current.rename(
        columns={
            "Quantity": CURRENT_QUANTITY_COLUMN,
            "Market Value": CURRENT_MARKET_VALUE_COLUMN,
        }
    )
    prior_leg = prior.rename(
        columns={
            "Quantity": PRIOR_QUANTITY_COLUMN,
            "Market Value": PRIOR_MARKET_VALUE_COLUMN,
        }
    )
    comparison = current_leg.merge(
        prior_leg,
        how="outer",
        on=list(STOCK_IDENTITY_COLUMNS),
        sort=False,
        validate="one_to_one",
        indicator=True,
    )
    comparison[QUANTITY_CHANGE_COLUMN] = comparison[CURRENT_QUANTITY_COLUMN].fillna(
        0.0
    ) - comparison[PRIOR_QUANTITY_COLUMN].fillna(0.0)
    comparison[MARKET_VALUE_CHANGE_COLUMN] = comparison[
        CURRENT_MARKET_VALUE_COLUMN
    ].fillna(0.0) - comparison[PRIOR_MARKET_VALUE_COLUMN].fillna(0.0)

    both = comparison["_merge"].eq("both")
    unchanged = (
        both
        & comparison[CURRENT_QUANTITY_COLUMN].eq(comparison[PRIOR_QUANTITY_COLUMN])
        & comparison[CURRENT_MARKET_VALUE_COLUMN].eq(
            comparison[PRIOR_MARKET_VALUE_COLUMN]
        )
    )
    comparison[STOCK_CHANGE_COLUMN] = np.select(
        [
            comparison["_merge"].eq("left_only"),
            comparison["_merge"].eq("right_only"),
            unchanged,
        ],
        ["Added", "Removed", "Unchanged"],
        default="Changed",
    )
    return comparison[list(STOCK_COMPARISON_COLUMNS)].copy()


def map_stock_comparison_portfolios(
    current_stock: pd.DataFrame,
    prior_stock: pd.DataFrame,
    portfolio_config: pd.DataFrame | str | Path,
) -> pd.DataFrame:
    """Compare Stock snapshots, then attach one authoritative Portfolio map."""

    comparison = compare_stock_snapshots(current_stock, prior_stock)
    mapped = merge_config(comparison, portfolio_config)
    return mapped[list(MAPPED_STOCK_COMPARISON_COLUMNS)].copy()


def filter_stock_comparison(
    mapped_stock: pd.DataFrame,
    dimension_filters: dict[str, list[str] | tuple[str, ...] | None] | None,
    *,
    exclude_selected: bool = False,
) -> pd.DataFrame:
    """Apply Stock-local OR-within and AND-across reporting filters.

    When ``exclude_selected`` is true, each populated set removes its selected
    values instead. Empty selections remain unrestricted in either mode.
    """

    if not isinstance(mapped_stock, pd.DataFrame):
        raise TypeError("mapped_stock must be a pandas DataFrame")
    missing = [
        column
        for column in MAPPED_STOCK_COMPARISON_COLUMNS
        if column not in mapped_stock
    ]
    if missing:
        raise ValueError(f"mapped_stock is missing required columns: {missing}")
    selected = dict(dimension_filters or {})
    unknown = sorted(set(selected) - set(STOCK_FILTER_COLUMN_BY_KEY))
    if unknown:
        raise ValueError(f"Unknown Stock reporting-dimension filters: {unknown}")

    mask = pd.Series(True, index=mapped_stock.index)
    for key, column in STOCK_FILTER_COLUMN_BY_KEY.items():
        raw_values = selected.get(key)
        if raw_values is None:
            continue
        if isinstance(raw_values, (str, bytes)):
            raise TypeError(f"Stock filter {key!r} must be a sequence of values")
        values = [str(value) for value in raw_values if value is not None]
        if values:
            matches = mapped_stock[column].astype(str).isin(values)
            mask &= ~matches if exclude_selected else matches
    return mapped_stock.loc[mask, list(MAPPED_STOCK_COMPARISON_COLUMNS)].copy()


__all__ = [
    "CURRENT_MARKET_VALUE_COLUMN",
    "CURRENT_QUANTITY_COLUMN",
    "MARKET_VALUE_CHANGE_COLUMN",
    "MAPPED_STOCK_COLUMNS",
    "MAPPED_STOCK_COMPARISON_COLUMNS",
    "PRIOR_MARKET_VALUE_COLUMN",
    "PRIOR_QUANTITY_COLUMN",
    "QUANTITY_CHANGE_COLUMN",
    "STOCK_CHANGE_COLUMN",
    "STOCK_COLUMNS",
    "STOCK_COMPARISON_COLUMNS",
    "STOCK_COMPARISON_NUMERIC_COLUMNS",
    "STOCK_FILTER_COLUMN_BY_KEY",
    "STOCK_IDENTITY_COLUMNS",
    "STOCK_NUMERIC_COLUMNS",
    "STOCK_TEXT_COLUMNS",
    "compare_stock_snapshots",
    "filter_stock_comparison",
    "map_stock_comparison_portfolios",
    "map_stock_portfolios",
    "validate_stock_frame",
]
