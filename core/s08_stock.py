"""Pure Stock-to-Portfolio mapping at the governed Portfolio grain."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable, Sequence
from typing import Final

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
STOCK_PROMOTION_BUCKET_COLUMN: Final = "Promotion Bucket"
STOCK_TEMPORARY_GROUP_COLUMN: Final = "Group (Temporary Fixture)"
STOCK_PROMOTION_THRESHOLD_DEFAULT: Final = 50_000.0
STOCK_HIERARCHY_COLUMNS: Final = (
    "Activity",
    STOCK_PROMOTION_BUCKET_COLUMN,
    STOCK_TEMPORARY_GROUP_COLUMN,
    "CPTY",
    "CRDS",
)
STOCK_PROMOTION_IDENTITY_COLUMNS: Final = tuple(
    column
    for column in STOCK_HIERARCHY_COLUMNS
    if column != STOCK_PROMOTION_BUCKET_COLUMN
)
STOCK_HIERARCHY_DEPTH_COLUMN: Final = "Hierarchy Depth"
STOCK_HIERARCHY_LEVEL_COLUMN: Final = "Hierarchy Level"
STOCK_HIERARCHY_LABEL_COLUMN: Final = "Hierarchy Label"
STOCK_HIERARCHY_PATH_COLUMN: Final = "Hierarchy Path"
STOCK_HIERARCHY_PARENT_PATH_COLUMN: Final = "Hierarchy Parent Path"
STOCK_HIERARCHY_POSITION_COUNT_COLUMN: Final = "Position Count"
STOCK_HIERARCHY_LEAF_COLUMN: Final = "Hierarchy Leaf"
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


def normalize_stock_promotion_threshold(value: object) -> float:
    """Return one finite, non-negative Stock promotion threshold.

    ``None`` represents the UI default so clearing the provisional selector
    cannot accidentally turn promotion into an unbounded or missing rule.
    Boolean values are rejected explicitly because Python otherwise treats
    them as the numbers zero and one.
    """

    if value is None:
        return STOCK_PROMOTION_THRESHOLD_DEFAULT
    if isinstance(value, (bool, np.bool_)):
        raise TypeError("Stock promotion threshold must be a finite number")
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Stock promotion threshold must be a finite number") from exc
    if not np.isfinite(threshold):
        raise ValueError("Stock promotion threshold must be a finite number")
    if threshold < 0:
        raise ValueError("Stock promotion threshold must be non-negative")
    return threshold


def prepare_stock_hierarchy(
    mapped_stock: pd.DataFrame,
    promotion_threshold: object = STOCK_PROMOTION_THRESHOLD_DEFAULT,
) -> pd.DataFrame:
    """Add transparent provisional grouping and promotion fields.

    Activity continues to come from the authoritative Portfolio mapping.  The
    requested Stock ``Group`` is not present in the temporary GetStock
    contract, so it is deliberately exposed as ``Group (Temporary Fixture)``
    and deterministically groups by Currency.  This makes the temporary rule
    visible instead of inventing an unexplained financial identity.

    Promotion is evaluated at the displayed Stock-name identity using the
    absolute net *current* market value across the already-filtered rows. Rows
    exactly at the threshold are promoted; removed identities with no current
    leg fall into ``Other``. This keeps one displayed name in one bucket even
    when it is represented by several Portfolio or Instrument rows.
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

    threshold = normalize_stock_promotion_threshold(promotion_threshold)
    prepared = mapped_stock[list(MAPPED_STOCK_COMPARISON_COLUMNS)].copy()
    prepared[STOCK_TEMPORARY_GROUP_COLUMN] = "Temporary currency group · " + prepared[
        "Currency"
    ].astype(str)
    prepared["_promotion_current_market_value"] = pd.to_numeric(
        prepared[CURRENT_MARKET_VALUE_COLUMN], errors="coerce"
    )
    promotion_market_value = prepared.groupby(
        list(STOCK_PROMOTION_IDENTITY_COLUMNS),
        dropna=False,
        sort=False,
    )["_promotion_current_market_value"].transform(
        lambda values: values.sum(min_count=1)
    )
    promoted = promotion_market_value.notna() & promotion_market_value.abs().ge(
        threshold
    )
    prepared[STOCK_PROMOTION_BUCKET_COLUMN] = np.where(
        promoted,
        "Promoted",
        "Other",
    )
    prepared.drop(columns="_promotion_current_market_value", inplace=True)
    return prepared


def _stock_hierarchy_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    """Aggregate one hierarchy scope without losing absent comparison legs."""

    prior_quantity = float(frame[PRIOR_QUANTITY_COLUMN].fillna(0.0).sum())
    current_quantity = float(frame[CURRENT_QUANTITY_COLUMN].fillna(0.0).sum())
    prior_market_value = float(frame[PRIOR_MARKET_VALUE_COLUMN].fillna(0.0).sum())
    current_market_value = float(frame[CURRENT_MARKET_VALUE_COLUMN].fillna(0.0).sum())
    return {
        STOCK_HIERARCHY_POSITION_COUNT_COLUMN: int(len(frame)),
        PRIOR_QUANTITY_COLUMN: prior_quantity,
        CURRENT_QUANTITY_COLUMN: current_quantity,
        QUANTITY_CHANGE_COLUMN: current_quantity - prior_quantity,
        PRIOR_MARKET_VALUE_COLUMN: prior_market_value,
        CURRENT_MARKET_VALUE_COLUMN: current_market_value,
        MARKET_VALUE_CHANGE_COLUMN: current_market_value - prior_market_value,
    }


def _ordered_stock_hierarchy_children(
    scope: pd.DataFrame,
    level: str,
) -> list[tuple[str, pd.DataFrame]]:
    """Rank visible children by absolute net current Stock, descending."""

    string_values = scope[level].astype(str)
    children: list[tuple[str, pd.DataFrame, float]] = []
    for value in string_values.loc[scope[level].notna()].drop_duplicates().tolist():
        child = scope.loc[string_values.eq(value)]
        current_stock = child[CURRENT_MARKET_VALUE_COLUMN].sum(min_count=1)
        magnitude = 0.0 if pd.isna(current_stock) else abs(float(current_stock))
        children.append((str(value), child, magnitude))
    children.sort(key=lambda item: (-item[2], item[0].casefold()))
    return [(value, child) for value, child, _magnitude in children]


def summarize_stock_hierarchy(
    mapped_stock: pd.DataFrame,
    promotion_threshold: object = STOCK_PROMOTION_THRESHOLD_DEFAULT,
) -> pd.DataFrame:
    """Return deterministic totals for the provisional Stock hierarchy.

    The returned paths are tuples so callers can reconstruct the exact parent
    tree without parsing display labels. Source comparison rows remain
    unchanged; this summary is a separate reporting projection.
    """

    prepared = prepare_stock_hierarchy(mapped_stock, promotion_threshold)
    columns = (
        STOCK_HIERARCHY_DEPTH_COLUMN,
        STOCK_HIERARCHY_LEVEL_COLUMN,
        STOCK_HIERARCHY_LABEL_COLUMN,
        STOCK_HIERARCHY_PATH_COLUMN,
        STOCK_HIERARCHY_PARENT_PATH_COLUMN,
        STOCK_HIERARCHY_POSITION_COUNT_COLUMN,
        *STOCK_COMPARISON_NUMERIC_COLUMNS,
        STOCK_HIERARCHY_LEAF_COLUMN,
    )
    if prepared.empty:
        return pd.DataFrame(columns=list(columns))

    records: list[dict[str, object]] = []

    def append_scope(
        scope: pd.DataFrame,
        *,
        depth: int,
        level: str,
        label: str,
        path: tuple[str, ...],
    ) -> None:
        records.append(
            {
                STOCK_HIERARCHY_DEPTH_COLUMN: depth,
                STOCK_HIERARCHY_LEVEL_COLUMN: level,
                STOCK_HIERARCHY_LABEL_COLUMN: label,
                STOCK_HIERARCHY_PATH_COLUMN: path,
                STOCK_HIERARCHY_PARENT_PATH_COLUMN: path[:-1],
                **_stock_hierarchy_metrics(scope),
                STOCK_HIERARCHY_LEAF_COLUMN: depth == len(STOCK_HIERARCHY_COLUMNS),
            }
        )

    append_scope(prepared, depth=0, level="Total", label="TOTAL", path=())

    def walk(scope: pd.DataFrame, depth: int, path: tuple[str, ...]) -> None:
        if depth >= len(STOCK_HIERARCHY_COLUMNS):
            return
        level = STOCK_HIERARCHY_COLUMNS[depth]
        for value, child in _ordered_stock_hierarchy_children(scope, level):
            child_path = (*path, value)
            append_scope(
                child,
                depth=depth + 1,
                level=level,
                label=value,
                path=child_path,
            )
            walk(child, depth + 1, child_path)

    walk(prepared, 0, ())
    return pd.DataFrame.from_records(records, columns=list(columns))


def summarize_visible_stock_hierarchy(
    mapped_stock: pd.DataFrame,
    promotion_threshold: object = STOCK_PROMOTION_THRESHOLD_DEFAULT,
    *,
    open_paths: Iterable[Sequence[str]] | None = None,
) -> pd.DataFrame:
    """Return only hierarchy rows visible under the supplied open paths.

    The total and Activity children are always present.  A deeper level is
    grouped only when its exact parent path is open, so a closed branch does
    not allocate, aggregate, or serialize any descendants.  This is the
    server-side projection used by the interactive Stock tree; the complete
    :func:`summarize_stock_hierarchy` remains available for offline exports and
    contract checks.
    """

    prepared = prepare_stock_hierarchy(mapped_stock, promotion_threshold)
    columns = (
        STOCK_HIERARCHY_DEPTH_COLUMN,
        STOCK_HIERARCHY_LEVEL_COLUMN,
        STOCK_HIERARCHY_LABEL_COLUMN,
        STOCK_HIERARCHY_PATH_COLUMN,
        STOCK_HIERARCHY_PARENT_PATH_COLUMN,
        STOCK_HIERARCHY_POSITION_COUNT_COLUMN,
        *STOCK_COMPARISON_NUMERIC_COLUMNS,
        STOCK_HIERARCHY_LEAF_COLUMN,
    )
    if prepared.empty:
        return pd.DataFrame(columns=list(columns))

    normalized_open_paths: set[tuple[str, ...]] = set()
    for raw_path in open_paths or ():
        if isinstance(raw_path, (str, bytes)):
            raise TypeError("Stock hierarchy paths must be sequences of labels")
        path = tuple(str(value) for value in raw_path)
        if not path or len(path) > len(STOCK_HIERARCHY_COLUMNS):
            continue
        if any(not value.strip() for value in path):
            continue
        normalized_open_paths.add(path)

    records: list[dict[str, object]] = []

    def append_scope(
        scope: pd.DataFrame,
        *,
        depth: int,
        level: str,
        label: str,
        path: tuple[str, ...],
    ) -> None:
        records.append(
            {
                STOCK_HIERARCHY_DEPTH_COLUMN: depth,
                STOCK_HIERARCHY_LEVEL_COLUMN: level,
                STOCK_HIERARCHY_LABEL_COLUMN: label,
                STOCK_HIERARCHY_PATH_COLUMN: path,
                STOCK_HIERARCHY_PARENT_PATH_COLUMN: path[:-1],
                **_stock_hierarchy_metrics(scope),
                STOCK_HIERARCHY_LEAF_COLUMN: depth == len(STOCK_HIERARCHY_COLUMNS),
            }
        )

    append_scope(prepared, depth=0, level="Total", label="TOTAL", path=())

    def append_visible_children(
        scope: pd.DataFrame,
        depth: int,
        path: tuple[str, ...],
    ) -> None:
        if depth >= len(STOCK_HIERARCHY_COLUMNS):
            return
        level = STOCK_HIERARCHY_COLUMNS[depth]
        for value, child in _ordered_stock_hierarchy_children(scope, level):
            child_path = (*path, value)
            append_scope(
                child,
                depth=depth + 1,
                level=level,
                label=value,
                path=child_path,
            )
            if child_path in normalized_open_paths:
                append_visible_children(child, depth + 1, child_path)

    append_visible_children(prepared, 0, ())
    return pd.DataFrame.from_records(records, columns=list(columns))


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
    "STOCK_HIERARCHY_COLUMNS",
    "STOCK_HIERARCHY_DEPTH_COLUMN",
    "STOCK_HIERARCHY_LABEL_COLUMN",
    "STOCK_HIERARCHY_LEAF_COLUMN",
    "STOCK_HIERARCHY_LEVEL_COLUMN",
    "STOCK_HIERARCHY_PARENT_PATH_COLUMN",
    "STOCK_HIERARCHY_PATH_COLUMN",
    "STOCK_HIERARCHY_POSITION_COUNT_COLUMN",
    "STOCK_IDENTITY_COLUMNS",
    "STOCK_NUMERIC_COLUMNS",
    "STOCK_PROMOTION_BUCKET_COLUMN",
    "STOCK_PROMOTION_IDENTITY_COLUMNS",
    "STOCK_PROMOTION_THRESHOLD_DEFAULT",
    "STOCK_TEMPORARY_GROUP_COLUMN",
    "STOCK_TEXT_COLUMNS",
    "compare_stock_snapshots",
    "filter_stock_comparison",
    "map_stock_comparison_portfolios",
    "map_stock_portfolios",
    "normalize_stock_promotion_threshold",
    "prepare_stock_hierarchy",
    "summarize_stock_hierarchy",
    "summarize_visible_stock_hierarchy",
    "validate_stock_frame",
]
