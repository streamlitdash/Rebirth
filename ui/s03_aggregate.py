"""Risk data preparation, filtering, aggregation, and key helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from core.s01_schema import PORTFOLIO_FIELDS
from .s02_constants import (
    ALT_GROUPS,
    BASE_GROUPS,
    BREAKDOWN_DEFAULTS,
    CREDIT_MEASURE_KEYS,
    CREDIT_MEASURES,
    CROSS_GAMMA_SOURCE_SPLIT,
    DEFAULT_UNDERLYING_SORT_METRIC,
    DEFAULT_VIEW_DIMENSION,
    DIMENSION_LABELS,
    FILTER_COLUMNS,
    IR_GREEK_FAMILIES,
    METRIC_BREAKDOWNS,
    META_COLUMNS,
    NUMERIC_COLUMNS,
    PRODUCT_LABELS,
    PRODUCT_ORDER,
    REQUIRED_INPUT_COLUMNS,
    RISK_TYPE_ORDER,
    ROW_KEY_COLUMNS,
    SPLIT_ORDER,
    TOP_EXPOSURE_LABELS,
    UNDERLYING_SORT_METRICS,
    VIEW_DIMENSIONS,
    XGAMMA_SOURCE_RISK_GREEKS,
)


def tenor_sort_key(value: object) -> tuple[int, float, str]:
    """Sort common tenor labels without discarding unfamiliar but valid values."""
    label = str(value).strip()
    upper = label.upper()
    if upper == "SPOT":
        return (-2, 0.0, upper)
    if upper in {"ON", "O/N"}:
        return (-1, 0.0, upper)
    if upper in {"", "N/A", "NA", "UNSPECIFIED"}:
        return (2, float("inf"), upper)
    # Demo fixtures and some upstream systems prefix tenor labels with a source
    # marker. Parse a standard tenor token at the end while preserving the full
    # label shown to the user.
    match = re.search(r"(?:^|[\s\-_ /])(\d+(?:\.\d+)?)\s*([DMY])$", upper)
    if match:
        number = float(match.group(1))
        days = number * {"D": 1.0, "M": 30.4375, "Y": 365.25}[match.group(2)]
        return (0, days, upper)
    return (1, float("inf"), upper)


def _resolved_tenor_orders(
    frame: pd.DataFrame,
    *,
    tenor_column: str,
    order_column: str,
) -> pd.Series:
    """Preserve connector ranks and fill only genuinely unmatched labels.

    Connector order is authoritative within an Underlying. Missing values on
    duplicate rows inherit that label's supplied rank. Only a label with no
    supplied rank receives a deterministic fallback after the supplied range.
    A frame without an order column receives a deterministic UI-only order;
    normal manager snapshots always carry the market-owned order columns.
    """
    if order_column in frame:
        raw = frame[order_column]
        boolean = raw.map(lambda value: isinstance(value, (bool, np.bool_)))
        numeric = pd.to_numeric(raw, errors="coerce")
        blank = raw.isna() | raw.astype(str).str.strip().eq("")
        invalid = boolean | (~blank & numeric.isna())
        invalid |= numeric.notna() & (
            (numeric < 0)
            | ~np.isfinite(numeric)
            | ~np.isclose(numeric, np.round(numeric))
        )
        if invalid.any():
            rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"Column {order_column!r} must contain non-negative finite "
                "integer ranks; "
                f"invalid rows {rows}"
            )
        supplied = numeric.round().astype("Int64")
    else:
        supplied = pd.Series(pd.NA, index=frame.index, dtype="Int64")

    resolved = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    authority_columns = (
        ["source type", "underlying"]
        if "source type" in frame
        else ["risk type", "risk greek", "underlying"]
    )
    for authority, positions in frame.groupby(
        authority_columns, sort=False, dropna=False
    ).groups.items():
        group_index = pd.Index(positions)
        labels = frame.loc[group_index, tenor_column].astype(str)
        group_supplied = supplied.loc[group_index]
        rank_by_label: dict[str, int] = {}
        for label in labels.drop_duplicates():
            label_ranks = (
                group_supplied.loc[labels.eq(label)]
                .dropna()
                .astype(int)
                .drop_duplicates()
                .tolist()
            )
            if len(label_ranks) > 1:
                raise ValueError(
                    f"{order_column!r} has conflicting ranks for Underlying "
                    f"{authority} tenor {label!r}: "
                    f"{sorted(label_ranks)}"
                )
            if label_ranks:
                rank_by_label[label] = label_ranks[0]

        ranks_to_labels: dict[int, list[str]] = {}
        for label, rank in rank_by_label.items():
            ranks_to_labels.setdefault(rank, []).append(label)
        collisions = {
            rank: sorted(values)
            for rank, values in ranks_to_labels.items()
            if len(values) > 1
        }
        if collisions:
            raise ValueError(
                f"{order_column!r} must identify one tenor per rank within "
                f"{authority!r}; collisions {collisions}"
            )

        missing_labels = sorted(
            set(labels) - set(rank_by_label),
            key=tenor_sort_key,
        )
        next_rank = max(rank_by_label.values(), default=-1) + 1
        used_ranks = set(rank_by_label.values())
        for label in missing_labels:
            while next_rank in used_ranks:
                next_rank += 1
            rank_by_label[label] = next_rank
            used_ranks.add(next_rank)
            next_rank += 1
        resolved.loc[group_index] = labels.map(rank_by_label).astype("Int64")
    return resolved


def tenor_axis_order(
    frame: pd.DataFrame,
    tenor_column: str,
    order_column: str,
) -> tuple[list[str], bool]:
    """Resolve one selected axis without inventing a global connector order.

    Each Underlying can legitimately publish a different rank vocabulary. A
    selected multi-underlying view therefore uses the modal supplied rank for
    each label (smallest rank breaks ties) and reports ambiguity to the caller.
    """
    if frame.empty or tenor_column not in frame:
        return [], False
    labels = frame[tenor_column].astype("string").str.strip()
    valid = labels.notna() & labels.ne("")
    labels = labels.loc[valid].astype(str)
    if labels.empty:
        return [], False
    if order_column not in frame:
        return sorted(labels.drop_duplicates(), key=tenor_sort_key), False
    orders = pd.to_numeric(frame.loc[valid, order_column], errors="coerce")
    finite = orders.notna() & np.isfinite(orders)
    if not finite.any():
        return sorted(labels.drop_duplicates(), key=tenor_sort_key), False

    chosen: dict[str, float] = {}
    ambiguous = bool((~finite).any())
    for label in labels.drop_duplicates():
        label_orders = orders.loc[labels.eq(label) & finite]
        if label_orders.empty:
            continue
        counts = label_orders.value_counts()
        largest = counts.max()
        candidates = sorted(float(value) for value in counts[counts.eq(largest)].index)
        chosen[label] = candidates[0]
        ambiguous |= len(counts) > 1

    rank_labels: dict[float, list[str]] = {}
    for label, rank in chosen.items():
        rank_labels.setdefault(rank, []).append(label)
    ambiguous |= any(len(values) > 1 for values in rank_labels.values())
    with_rank = sorted(
        chosen,
        key=lambda label: (chosen[label], tenor_sort_key(label), label),
    )
    without_rank = sorted(
        set(labels) - set(chosen),
        key=tenor_sort_key,
    )
    return [*with_rank, *without_rank], ambiguous


def _assert_metric_breakdown(
    frame: pd.DataFrame,
    metric: str,
    *,
    label: str,
) -> None:
    """Fail when an authoritative total and its two components disagree."""
    expo_column, hedges_column = METRIC_BREAKDOWNS[metric]
    total = frame[metric]
    expo = frame[expo_column]
    hedges = frame[hedges_column]
    total_missing = total.isna()
    component_missing = expo.isna() | hedges.isna()
    invalid_missing = (total_missing & (expo.notna() | hedges.notna())) | (
        total.notna() & component_missing
    )
    comparable = total.notna() & expo.notna() & hedges.notna()
    mismatch = pd.Series(False, index=frame.index)
    mismatch.loc[comparable] = ~np.isclose(
        total.loc[comparable],
        expo.loc[comparable] + hedges.loc[comparable],
        rtol=1e-9,
        atol=1e-9,
    )
    invalid = invalid_missing | mismatch
    if invalid.any():
        rows = frame.index[invalid].tolist()[:5]
        raise ValueError(
            f"{label}: {metric} must equal {expo_column} + {hedges_column}; "
            f"invalid rows {rows}"
        )


def _derive_and_validate_breakdowns(frame: pd.DataFrame) -> None:
    """Complete one missing component without changing authoritative totals.

    When neither component is supplied, Product partitioning assigns a Hedges
    row wholly to Hedges and every XVA row wholly to the legacy ``expo``
    component column.
    """
    hedge_product = (
        frame["product"].str.strip().str.casefold().isin(("hedge", "hedges"))
    )
    for metric in ("risk", "drisk", "pl"):
        expo_column, hedges_column = METRIC_BREAKDOWNS[metric]
        total = frame[metric]
        expo = frame[expo_column].copy()
        hedges = frame[hedges_column].copy()
        available_total = total.notna()

        both_missing = available_total & expo.isna() & hedges.isna()
        expo.loc[both_missing & ~hedge_product] = total.loc[
            both_missing & ~hedge_product
        ]
        hedges.loc[both_missing & ~hedge_product] = 0.0
        expo.loc[both_missing & hedge_product] = 0.0
        hedges.loc[both_missing & hedge_product] = total.loc[
            both_missing & hedge_product
        ]

        only_expo_missing = available_total & expo.isna() & hedges.notna()
        expo.loc[only_expo_missing] = (
            total.loc[only_expo_missing] - hedges.loc[only_expo_missing]
        )
        only_hedges_missing = available_total & expo.notna() & hedges.isna()
        hedges.loc[only_hedges_missing] = (
            total.loc[only_hedges_missing] - expo.loc[only_hedges_missing]
        )

        frame[expo_column] = expo
        frame[hedges_column] = hedges
        _assert_metric_breakdown(frame, metric, label="Dashboard input")


def prepare_risk_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize a caller-owned DataFrame for the dashboard."""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")
    if data.empty:
        raise ValueError("data must contain at least one row")

    frame = data.copy()
    canonical_to_internal = {
        **{
            field.external_name.strip().casefold(): field.key
            for field in PORTFOLIO_FIELDS
        },
        "tenor swap": "tenor swap",
        "tenor option": "tenor option",
        "tenor swap order": "tenor swap order",
        "tenor option order": "tenor option order",
    }
    for measure, key in CREDIT_MEASURE_KEYS.items():
        for metric in ("risk", "drisk"):
            canonical = f"{metric} {key}"
            canonical_to_internal[canonical] = canonical
            canonical_to_internal[f"{metric} {measure.casefold()}"] = canonical
    frame.columns = [
        canonical_to_internal.get(
            str(column).strip().casefold(), str(column).strip().casefold()
        )
        for column in frame.columns
    ]
    duplicate_columns = frame.columns[frame.columns.duplicated()].unique().tolist()
    if duplicate_columns:
        raise ValueError(f"Duplicate columns after normalization: {duplicate_columns}")

    if "display bucket" not in frame:
        frame["display bucket"] = "Other"
    if "region" not in frame:
        frame["region"] = ""
    if "split" not in frame:
        frame["split"] = "Risk"
    if "promotion reason" not in frame:
        frame["promotion reason"] = ""
    if "promotion score" not in frame:
        frame["promotion score"] = 0.0

    missing = [column for column in REQUIRED_INPUT_COLUMNS if column not in frame]
    if missing:
        raise ValueError(f"Missing required Risk columns: {missing}")

    # Open and Current/OFFICIAL must be supplied explicitly by the real market
    # source. Rows with unavailable market data should carry NaN in both fields;
    # Cube derives Move only where both authoritative quotes are available.
    if "move" not in frame:
        frame["move"] = np.nan

    for column in ["risk type", *BASE_GROUPS, "product"]:
        if column == "region":
            frame[column] = frame[column].fillna("").astype(str)
        else:
            frame[column] = frame[column].fillna("Unspecified").astype(str)
    product_labels = frame["product"].str.strip().str.casefold()
    invalid_product = ~product_labels.isin(PRODUCT_LABELS)
    if invalid_product.any():
        bad_rows = frame.index[invalid_product].tolist()[:5]
        allowed = ", ".join(repr(value) for value in PRODUCT_LABELS.values())
        raise ValueError(
            f"Column 'product' must contain only {allowed}; invalid rows {bad_rows}"
        )
    frame["product"] = product_labels.map(PRODUCT_LABELS)
    for column in FILTER_COLUMNS:
        if column not in frame:
            frame[column] = "Unspecified"
        else:
            frame[column] = frame[column].fillna("Unspecified").astype(str)
    for column in META_COLUMNS:
        frame[column] = frame[column].fillna("").astype(str)

    for column in BREAKDOWN_DEFAULTS:
        if column not in frame:
            frame[column] = np.nan

    optional_credit_columns = [
        f"{metric} {key}"
        for key in CREDIT_MEASURE_KEYS.values()
        for metric in ("risk", "drisk")
        if f"{metric} {key}" in frame
    ]
    nullable_numeric = {
        "drisk",
        "risk expo",
        "risk hedges",
        "drisk expo",
        "drisk hedges",
        "open",
        "current",
        "move",
        "pl",
        "pl expo",
        "pl hedges",
        *optional_credit_columns,
    }
    for column in {*NUMERIC_COLUMNS, *optional_credit_columns}:
        if frame[column].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError(f"Column {column!r} must not contain booleans")
        converted = pd.to_numeric(frame[column], errors="coerce")
        blank = frame[column].isna() | frame[column].astype(str).str.strip().eq("")
        invalid = (~blank & converted.isna()) | (
            converted.notna() & ~np.isfinite(converted)
        )
        if column not in nullable_numeric:
            invalid |= converted.isna()
        if invalid.any():
            bad_rows = frame.index[invalid].tolist()[:5]
            raise ValueError(
                f"Column {column!r} contains missing, non-numeric, or non-finite values at rows {bad_rows}"
            )
        frame[column] = converted.astype(float)

    available_quotes = frame["open"].notna() & frame["current"].notna()
    frame.loc[available_quotes, "move"] = (
        frame.loc[available_quotes, "current"] - frame.loc[available_quotes, "open"]
    )
    _derive_and_validate_breakdowns(frame)

    frame["tenor swap order"] = _resolved_tenor_orders(
        frame,
        tenor_column="tenor swap",
        order_column="tenor swap order",
    )
    frame["tenor option order"] = _resolved_tenor_orders(
        frame,
        tenor_column="tenor option",
        order_column="tenor option order",
    )
    optional_status_columns = [
        column
        for column in ("market available", "market data status")
        if column in frame
    ]
    return frame[
        [
            *(["source type"] if "source type" in frame else []),
            "risk type",
            *ALT_GROUPS,
            *VIEW_DIMENSIONS,
            *META_COLUMNS,
            *NUMERIC_COLUMNS,
            *optional_credit_columns,
            *optional_status_columns,
            "tenor swap order",
            "tenor option order",
        ]
    ]


def apply_filters(
    data: pd.DataFrame,
    risk_types: Sequence[str] | None,
    splits: Sequence[str] | None,
    dimension_filters: Mapping[str, Sequence[str] | None] | None,
    *,
    exclude_selected: bool = False,
) -> pd.DataFrame:
    """Apply Risk-local filters with OR-within and AND-across semantics.

    Populated dimension selections include their values by default.  In
    exclusion mode every populated set instead removes its selected values;
    empty selections remain unrestricted in either mode.  Risk Type and Split
    retain their existing inclusion semantics because they are navigation and
    sourced-risk controls rather than reporting-dimension filters.
    """
    mask = pd.Series(True, index=data.index)
    if risk_types:
        mask &= data["risk type"].isin(risk_types)
    if splits:
        mask &= data["split"].isin(splits)
    selected_dimensions = dict(dimension_filters or {})
    unknown_dimensions = sorted(set(selected_dimensions) - set(FILTER_COLUMNS))
    if unknown_dimensions:
        raise ValueError(f"Unknown reporting-dimension filters: {unknown_dimensions}")
    for column in FILTER_COLUMNS:
        selected = selected_dimensions.get(column)
        if isinstance(selected, (str, bytes)):
            raise TypeError(
                f"Reporting-dimension filter {column!r} must be a sequence of values"
            )
        if selected:
            selected_values = {
                str(value).strip().casefold()
                for value in selected
                if str(value).strip()
            }
            if not selected_values:
                continue
            values = data[column].astype("string").str.strip().str.casefold()
            matches = values.isin(selected_values).fillna(False)
            mask &= ~matches if exclude_selected else matches
    frame = data.loc[mask].copy()
    frame["abs pl"] = frame["pl"].abs()
    frame["rows"] = 1
    return frame


PROMOTION_KEYS = [
    "risk type",
    "risk greek",
    "reported underlying",
]


def recompute_filtered_promotion(data: pd.DataFrame) -> pd.DataFrame:
    """Recalculate promotion from only the already-filtered position rows."""
    if data.empty:
        return data.copy()

    # Remove the global snapshot classification before calculating the
    # browser session's filtered classification.
    base = data.drop(
        columns=["display bucket", "promotion reason", "promotion score"],
        errors="ignore",
    )

    # The threshold is repeated on position rows. Take it once; do not sum it.
    summary = base.groupby(
        PROMOTION_KEYS,
        as_index=False,
        dropna=False,
        sort=False,
    ).agg(
        {
            "risk": lambda values: values.sum(min_count=1),
            "drisk": lambda values: values.sum(min_count=1),
            "pl": lambda values: values.sum(min_count=1),
            "risk threshold": "first",
            "drisk threshold": "first",
            "pl threshold": "first",
        }
    )

    risk_ratio = summary["risk"].abs() / summary["risk threshold"]
    drisk_ratio = summary["drisk"].abs() / summary["drisk threshold"]
    pl_ratio = summary["pl"].abs() / summary["pl threshold"]

    summary["promotion score"] = pd.concat(
        [risk_ratio, drisk_ratio, pl_ratio],
        axis=1,
    ).max(axis=1)

    summary["promotion reason"] = [
        ", ".join(
            label
            for label, breached in (
                ("Big Risk", risk_value >= 1.0),
                ("Big dRisk", drisk_value >= 1.0),
                ("Big PL", pl_value >= 1.0),
            )
            if breached
        )
        for risk_value, drisk_value, pl_value in zip(
            risk_ratio,
            drisk_ratio,
            pl_ratio,
        )
    ]

    summary["display bucket"] = np.where(
        summary["promotion reason"].ne(""),
        summary["reported underlying"],
        "Other",
    )

    promotion = summary[
        PROMOTION_KEYS + ["display bucket", "promotion reason", "promotion score"]
    ]

    return base.merge(
        promotion,
        on=PROMOTION_KEYS,
        how="left",
        validate="many_to_one",
    )


def filter_ir_family(
    data: pd.DataFrame, risk_type: str | None, family: str | None
) -> pd.DataFrame:
    """Restrict IR rows to the selected ordinary or XGamma family."""
    if risk_type != "IR":
        return data
    allowed = IR_GREEK_FAMILIES.get(str(family or "delta"), IR_GREEK_FAMILIES["delta"])
    return data.loc[data["risk greek"].isin(allowed)]


def credit_measure_column(metric: str, measure: str) -> str:
    """Return the optional canonical connector column for a credit measure."""
    normalized_measure = measure if measure in CREDIT_MEASURES else CREDIT_MEASURES[0]
    if metric not in {"risk", "drisk"}:
        raise ValueError("Credit measure columns are only defined for Risk and dRisk")
    return f"{metric} {CREDIT_MEASURE_KEYS[normalized_measure]}"


def _credit_cross_gamma_source_mask(frame: pd.DataFrame) -> pd.Series:
    """Identify generic Credit Cross Gamma sensitivities, not output risk."""

    required = {"risk greek", "split"}
    if not required.issubset(frame.columns):
        return pd.Series(False, index=frame.index, dtype=bool)
    mask = frame["risk greek"].isin(XGAMMA_SOURCE_RISK_GREEKS) & frame["split"].eq(
        CROSS_GAMMA_SOURCE_SPLIT
    )
    if "risk type" in frame:
        mask &= frame["risk type"].eq("Credit")
    return mask


def credit_measure_available(frame: pd.DataFrame, measure: str) -> bool:
    """Whether at least one complete metric is available for a Credit measure.

    Credit Cross Gamma source sensitivities deliberately have generic Risk and
    no connector-measure values.  They are excluded from connector completeness
    so their intentional blanks do not disable a complete ordinary Credit
    measure.  :func:`credit_measure_values` overlays generic Risk only for those
    exact source rows and leaves their connector columns untouched.
    """

    connector_rows = frame.loc[~_credit_cross_gamma_source_mask(frame)]
    return not connector_rows.empty and any(
        (column := credit_measure_column(metric, measure)) in frame
        and connector_rows[column].notna().all()
        for metric in ("risk", "drisk")
    )


def credit_measure_values(
    frame: pd.DataFrame,
    metric: str,
    measure: str,
    *,
    connector_complete: bool | None = None,
) -> pd.Series:
    """Read a complete connector measure or return explicit unavailability.

    A column with any missing row is not safe to aggregate: returning its
    populated subset would display a plausible but incomplete total. Callers
    rendering nested scopes can pass the completeness decision from the full
    filtered frame so every row uses the same connector-availability decision.
    """
    if metric == "pl":
        return frame["pl"].astype(float)
    source_mask = _credit_cross_gamma_source_mask(frame)
    connector_mask = ~source_mask
    column = credit_measure_column(metric, measure)
    locally_complete = (
        connector_mask.any()
        and column in frame
        and frame.loc[connector_mask, column].notna().all()
    )
    use_connector = locally_complete and connector_complete is not False
    selected = pd.Series(np.nan, index=frame.index, dtype=float)
    if use_connector:
        selected.loc[connector_mask] = frame.loc[connector_mask, column].astype(float)
    if metric == "risk":
        selected.loc[source_mask] = frame.loc[source_mask, "risk"].astype(float)
    return selected


def apply_credit_measure(
    frame: pd.DataFrame,
    measure: str,
) -> pd.DataFrame:
    """Return a display-only Credit frame with selected Risk/drisk values.

    P&L and market columns are deliberately untouched. Component values keep
    the connector's XVA/Hedges proportions, so the three detail traces remain
    internally consistent without changing the caller-owned DataFrame.
    """
    scoped = frame.copy()
    for metric in ("risk", "drisk"):
        selected = credit_measure_values(scoped, metric, measure)
        original = scoped[metric].astype(float)
        for component in METRIC_BREAKDOWNS[metric]:
            ratio = scoped[component].astype(float).div(original.replace(0.0, np.nan))
            hedge_product = (
                scoped["product"].str.strip().str.casefold().isin(("hedge", "hedges"))
            )
            fallback_mask = (
                ~hedge_product if component.endswith("expo") else hedge_product
            )
            scoped[component] = selected * ratio.where(
                ratio.notna(), fallback_mask.astype(float)
            )
        scoped[metric] = selected
        _assert_metric_breakdown(scoped, metric, label="Credit measure")
    return scoped


def decimals_for(column: str | None) -> int:
    mapping = {
        "open": 4,
        "current": 4,
        "move": 4,
        "risk": 1,
        "drisk": 1,
        "risk hedges": 1,
        "drisk hedges": 1,
        "risk expo": 1,
        "drisk expo": 1,
    }
    return mapping.get(column, 0)


def format_number(
    value: float, decimals: int | None = None, column: str | None = None
) -> str:
    if pd.isna(value):
        return ""
    if decimals is None and column == "move":
        rendered = f"{float(value):,.6f}".rstrip("0").rstrip(".")
        return "0" if rendered == "-0" else rendered
    if decimals is None:
        decimals = decimals_for(column)
    return f"{float(value):,.{decimals}f}"


def number_sign_class(value: float) -> str:
    return "number-negative" if float(value) < 0 else "number-positive"


def selected_underlying_sort_metric(value: str | None) -> str:
    return value if value in UNDERLYING_SORT_METRICS else DEFAULT_UNDERLYING_SORT_METRIC


def _ordered_by_metric(
    frame: pd.DataFrame,
    column: str,
    metric: str,
) -> list[str]:
    ranked = frame.groupby(
        column,
        as_index=False,
        dropna=False,
        sort=False,
    )[metric].sum(min_count=1)
    ranked["_magnitude"] = ranked[metric].abs()
    ranked["_label"] = ranked[column].astype(str).str.casefold()
    ranked = ranked.sort_values(
        ["_magnitude", "_label"],
        ascending=[False, True],
        kind="stable",
        na_position="last",
    )
    return ranked[column].astype(str).tolist()


def ordered_unique(
    frame: pd.DataFrame,
    column: str,
    *,
    underlying_sort_metric: str | None = None,
) -> list[str]:
    if column == "label":
        values = frame[column].dropna().astype(str).unique().tolist()
        positions = {label: index for index, label in enumerate(TOP_EXPOSURE_LABELS)}
        return sorted(values, key=lambda value: (positions.get(value, 99), value))
    if column == "risk type":
        values = frame[column].dropna().astype(str).unique().tolist()
        return sorted(values, key=lambda value: (RISK_TYPE_ORDER.get(value, 99), value))
    if column == "split":
        values = frame[column].dropna().astype(str).unique().tolist()
        positions = {label: index for index, label in enumerate(SPLIT_ORDER)}
        return sorted(
            values,
            key=lambda value: (positions.get(value, len(positions)), value),
        )
    if column == "product":
        values = frame[column].dropna().astype(str).unique().tolist()
        return sorted(
            values,
            key=lambda value: (PRODUCT_ORDER.get(value, 99), value),
        )
    if (
        column == "risk greek"
        and "risk type" in frame
        and set(frame["risk type"].dropna().astype(str).unique()) == {"IR"}
    ):
        values = frame[column].dropna().astype(str).unique().tolist()
        ir_order = [greek for family in IR_GREEK_FAMILIES.values() for greek in family]
        positions = {greek: index for index, greek in enumerate(ir_order)}
        return sorted(values, key=lambda value: (positions.get(value, 99), value))
    if column in {"reported underlying", "underlying"}:
        metric = (
            selected_underlying_sort_metric(underlying_sort_metric)
            if underlying_sort_metric is not None
            else "risk"
        )
        return _ordered_by_metric(frame, column, metric)
    if column == "display bucket":
        promoted = (
            frame.loc[frame[column].ne("Other")]
            .groupby(column)["promotion score"]
            .max()
            .sort_values(ascending=False, kind="stable")
        )
        values = promoted.index.astype(str).tolist()
        if frame[column].eq("Other").any():
            values.append("Other")
        return values
    if column in {"tenor swap", "tenor option"}:
        order_column = {
            "tenor swap": "tenor swap order",
            "tenor option": "tenor option order",
        }[column]
        values, _ambiguous = tenor_axis_order(frame, column, order_column)
        return values
    return sorted(frame[column].dropna().astype(str).unique().tolist())


def selected_dimension(dimension: str | None) -> str:
    return dimension if dimension in VIEW_DIMENSIONS else DEFAULT_VIEW_DIMENSION


def hierarchy_groups(dimension: str | None) -> list[str]:
    return [*ALT_GROUPS, selected_dimension(dimension)]


def dimension_title(dimension: str | None) -> str:
    return DIMENSION_LABELS[selected_dimension(dimension)]


def row_key(context: dict[str, str]) -> str:
    payload = {
        column: str(context[column]) for column in ROW_KEY_COLUMNS if column in context
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parse_row_key(key: str | None) -> dict[str, str]:
    if not key:
        return {}
    try:
        parsed = json.loads(key)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict) or not set(parsed).issubset(ROW_KEY_COLUMNS):
        return {}
    return {str(column): str(value) for column, value in parsed.items()}


def triggered_pattern_click(
    triggered_id: dict[str, Any],
    *id_value_pairs: tuple[list[dict[str, Any]] | None, list[int | None] | None],
) -> int:
    """Return the click count for the exact pattern component that triggered."""
    for component_ids, click_values in id_value_pairs:
        for component_id, click_value in zip(component_ids or [], click_values or []):
            if component_id == triggered_id:
                return int(click_value or 0)
    return 0


def frame_for_context(frame: pd.DataFrame, context: dict[str, str]) -> pd.DataFrame:
    scoped = frame
    for column, value in context.items():
        if column not in ROW_KEY_COLUMNS or column not in scoped:
            return scoped.iloc[0:0]
        scoped = scoped[scoped[column].astype(str) == str(value)]
    return scoped


def hierarchical_market_value(frame: pd.DataFrame, column: str) -> float:
    """Roll up one market quote without portfolio or tenor-grid weighting.

    Exact duplicate quotes from XVA/Hedges or other portfolio rows count once.
    Option-tenor quotes are then averaged within each swap tenor, swap-tenor
    averages within each underlying, and underlying averages at broader scopes.
    The same calculation therefore returns the actual deduplicated quote at the
    finest level and equal-weight child averages at its parents.
    """
    if column not in {"open", "current", "move"}:
        raise ValueError(f"Unsupported market column: {column}")
    if frame.empty:
        return float("nan")

    identity = [
        "risk type",
        "risk greek",
        "underlying",
        "tenor swap",
        "tenor option",
    ]
    quotes = frame[[*identity, column]].drop_duplicates()
    option_quotes = (
        quotes.groupby(
            ["underlying", "tenor swap", "tenor option"],
            dropna=False,
        )[column]
        .mean()
        .rename("option quote")
    )
    swap_averages = option_quotes.groupby(
        level=["underlying", "tenor swap"],
        dropna=False,
    ).mean()
    underlying_averages = swap_averages.groupby(level="underlying", dropna=False).mean()
    return float(underlying_averages.mean())


def average_move(frame: pd.DataFrame) -> float:
    """Return the hierarchy-aware market Move for a scoped frame."""
    return hierarchical_market_value(frame, "move")


_AGGREGATE_SUM_COLUMNS = (
    "risk",
    "risk expo",
    "risk hedges",
    "drisk",
    "drisk expo",
    "drisk hedges",
    "pl",
    "pl expo",
    "pl hedges",
)
_MARKET_COLUMNS = ("open", "current", "move")
_MARKET_QUOTE_IDENTITY = (
    "risk type",
    "risk greek",
    "underlying",
    "tenor swap",
    "tenor option",
)


def _assert_aggregate_breakdowns(
    metrics: Mapping[str, float],
    *,
    label: str,
) -> None:
    """Validate three scalar identities without allocating a DataFrame.

    Input rows are validated at the connector boundary. Hierarchy sums retain
    those identities, but this inexpensive guard keeps ``aggregate_values`` and
    the hierarchy index safe for direct callers too.
    """
    for metric in ("risk", "drisk", "pl"):
        expo_column, hedges_column = METRIC_BREAKDOWNS[metric]
        total = metrics[metric]
        expo = metrics[expo_column]
        hedges = metrics[hedges_column]
        total_missing = bool(pd.isna(total))
        expo_missing = bool(pd.isna(expo))
        hedges_missing = bool(pd.isna(hedges))
        invalid_missing = (total_missing and not (expo_missing and hedges_missing)) or (
            not total_missing and (expo_missing or hedges_missing)
        )
        mismatch = (
            not total_missing
            and not expo_missing
            and not hedges_missing
            and not bool(np.isclose(total, expo + hedges, rtol=1e-9, atol=1e-9))
        )
        if invalid_missing or mismatch:
            raise ValueError(
                f"{label}: {metric} must equal {expo_column} + {hedges_column}; "
                "invalid rows [0]"
            )


def _factorize_rows(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[np.ndarray, pd.DataFrame]:
    """Factorize compound keys once while retaining pandas' NA equality."""
    keys = pd.MultiIndex.from_frame(frame.loc[:, list(columns)])
    codes, unique_keys = pd.factorize(keys, sort=False)
    unique_frame = unique_keys.to_frame(index=False)
    unique_frame.columns = list(columns)
    return codes.astype(np.intp, copy=False), unique_frame


class _MarketQuoteIndex:
    """Compact quote hierarchy shared by every visible table row."""

    def __init__(self, frame: pd.DataFrame, column: str) -> None:
        self._row_quote_codes = np.full(len(frame), -1, dtype=np.intp)
        valid = frame[column].notna().to_numpy()
        if not valid.any():
            self._quote_values = np.empty(0, dtype=float)
            self._quote_to_option = np.empty(0, dtype=np.intp)
            self._option_to_swap = np.empty(0, dtype=np.intp)
            self._swap_to_underlying = np.empty(0, dtype=np.intp)
            return

        quote_columns = [*_MARKET_QUOTE_IDENTITY, column]
        quote_codes, unique_quotes = _factorize_rows(
            frame.loc[valid, quote_columns],
            quote_columns,
        )
        self._row_quote_codes[valid] = quote_codes
        self._quote_values = unique_quotes[column].to_numpy(dtype=float, copy=True)

        option_columns = ["underlying", "tenor swap", "tenor option"]
        self._quote_to_option, unique_options = _factorize_rows(
            unique_quotes,
            option_columns,
        )
        swap_columns = ["underlying", "tenor swap"]
        self._option_to_swap, unique_swaps = _factorize_rows(
            unique_options,
            swap_columns,
        )
        self._swap_to_underlying, _ = _factorize_rows(
            unique_swaps,
            ["underlying"],
        )

    @staticmethod
    def _means_by_group(
        values: np.ndarray,
        group_codes: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        groups, inverse = np.unique(group_codes, return_inverse=True)
        counts = np.bincount(inverse)
        totals = np.bincount(inverse, weights=values)
        return groups, totals / counts

    def value(self, row_positions: np.ndarray) -> float:
        """Evaluate the existing option -> swap -> underlying mean hierarchy."""
        if not len(row_positions) or not len(self._quote_values):
            return float("nan")
        quote_codes = np.unique(self._row_quote_codes[row_positions])
        quote_codes = quote_codes[quote_codes >= 0]
        if not len(quote_codes):
            return float("nan")

        option_codes, option_means = self._means_by_group(
            self._quote_values[quote_codes],
            self._quote_to_option[quote_codes],
        )
        swap_codes, swap_means = self._means_by_group(
            option_means,
            self._option_to_swap[option_codes],
        )
        _, underlying_means = self._means_by_group(
            swap_means,
            self._swap_to_underlying[swap_codes],
        )
        return float(underlying_means.mean())


class HierarchyAggregationIndex:
    """Precomputed numeric and quote state for one hierarchy render.

    The previous renderer rebuilt three pandas grouping pipelines and three
    validation DataFrames for every visible node. This index factorizes quote
    identities once, stores numeric measures in contiguous arrays, and then
    evaluates each scoped node with small NumPy reductions. It is deliberately
    request-local: no caller-owned DataFrame or cross-user state is mutated.
    """

    _ROW_POSITION_BASE = "__cube_aggregation_row_position__"

    def __init__(self, frame: pd.DataFrame) -> None:
        row_column = self._ROW_POSITION_BASE
        while row_column in frame.columns:
            row_column = f"_{row_column}"
        self.row_position_column = row_column
        self.frame = frame.copy(deep=False)
        self.frame.insert(len(self.frame.columns), row_column, np.arange(len(frame)))
        self._sum_values = frame.loc[:, list(_AGGREGATE_SUM_COLUMNS)].to_numpy(
            dtype=float,
            copy=True,
        )
        self._market_indexes = {
            column: _MarketQuoteIndex(frame, column) for column in _MARKET_COLUMNS
        }

    def aggregate(
        self,
        scoped: pd.DataFrame,
        *,
        include_market: bool = True,
        validate_breakdowns: bool = True,
    ) -> dict[str, float]:
        """Aggregate a scope produced from :attr:`frame`."""
        if self.row_position_column not in scoped:
            raise ValueError(
                "The scoped frame does not belong to this aggregation index"
            )
        positions = scoped[self.row_position_column].to_numpy(dtype=np.intp, copy=False)
        values = self._sum_values[positions]
        if len(values):
            available = ~np.isnan(values)
            sums = np.nansum(values, axis=0)
            sums[~available.any(axis=0)] = np.nan
        else:
            sums = np.full(len(_AGGREGATE_SUM_COLUMNS), np.nan, dtype=float)
        metrics = {
            column: float(value) for column, value in zip(_AGGREGATE_SUM_COLUMNS, sums)
        }
        if include_market:
            metrics.update(
                {
                    column: self._market_indexes[column].value(positions)
                    for column in _MARKET_COLUMNS
                }
            )
        else:
            metrics.update({column: float("nan") for column in _MARKET_COLUMNS})
        metrics["rows"] = float(len(positions))
        if validate_breakdowns:
            _assert_aggregate_breakdowns(metrics, label="Hierarchy aggregation")
        return metrics


def aggregate_values(
    frame: pd.DataFrame,
    *,
    include_market: bool = True,
    validate_breakdowns: bool = True,
) -> dict[str, float]:
    """Aggregate an ad-hoc frame without building a reusable hierarchy index."""
    metrics = {
        str(column): float(value)
        for column, value in frame.loc[:, list(_AGGREGATE_SUM_COLUMNS)]
        .sum(min_count=1)
        .items()
    }
    if include_market:
        metrics.update(
            {
                "open": hierarchical_market_value(frame, "open"),
                "current": hierarchical_market_value(frame, "current"),
                "move": average_move(frame),
            }
        )
    else:
        metrics.update({column: float("nan") for column in _MARKET_COLUMNS})
    metrics["rows"] = float(len(frame))
    if validate_breakdowns:
        _assert_aggregate_breakdowns(metrics, label="Hierarchy aggregation")
    return metrics


def _is_semantic_underlying(context: dict[str, str]) -> bool:
    """Show market values only at a raw-market identity or its descendants.

    A reported parent can contain several raw curves, so averaging Open,
    Current, or Move there would manufacture a quote.  When a one-to-one
    reported/raw duplicate level is skipped, a narrower tenor, split, or
    reporting-dimension row is still an implicit descendant of that raw
    identity and may display its market values.
    """
    return "underlying" in context or any(
        column in context
        for column in ("tenor swap", "tenor option", "split", *VIEW_DIMENSIONS)
    )


def should_show_sum(column: str, context: dict[str, str]) -> bool:
    if column in {"move", "open", "current"}:
        return _is_semantic_underlying(context)
    if column == "pl" or column.startswith("pl "):
        return True
    if (
        column == "risk"
        or column.startswith("risk ")
        or column == "drisk"
        or column.startswith("drisk ")
    ):
        # TOTAL / Risk Type stay blank. Scoped connector values and their
        # XVA/Hedges breakdowns become valid at Risk Greek and remain visible
        # for every narrower descendant context.
        return "risk greek" in context
    return True


def display_metric(
    metrics: dict[str, float],
    column: str,
    context: dict[str, str],
) -> str:
    if not should_show_sum(column, context):
        return ""
    return format_number(metrics[column], column=column)


def default_open_rows(frame: pd.DataFrame, risk_type: str | None = None) -> list[str]:
    scoped = frame if risk_type is None else frame.loc[frame["risk type"].eq(risk_type)]
    return [
        row_key({"risk greek": greek}) for greek in ordered_unique(scoped, "risk greek")
    ]


def visible_tree_level(
    frame: pd.DataFrame,
    level: int,
    context: dict[str, str],
    groups: list[str] | None = None,
) -> int:
    """Skip hierarchy levels that do not apply to the current branch."""
    groups = BASE_GROUPS if groups is None else groups
    while level < len(groups):
        column = groups[level]
        promoted = context.get("display bucket")
        if promoted not in {None, "Other"} and column in {
            "reported underlying",
            "group",
        }:
            level += 1
            continue
        if column == "underlying":
            raw_values = set(frame[column].dropna().astype(str).unique())
            reported = context.get("reported underlying")
            duplicate_parent = (
                reported is not None
                and bool(raw_values)
                and raw_values == {str(reported)}
            )
            duplicate_promoted_bucket = (
                reported is None
                and promoted not in {None, "Other"}
                and bool(raw_values)
                and raw_values == {str(promoted)}
            )
            if duplicate_parent or duplicate_promoted_bucket:
                level += 1
                continue
        if column in {"tenor swap", "tenor option"}:
            values = set(frame[column].dropna().astype(str).unique())
            if not values or values.issubset({"", "N/A", "Spot", "Unspecified"}):
                level += 1
                continue
        break
    return level


def tree_scope(frame: pd.DataFrame, group_column: str, value: str) -> pd.DataFrame:
    """Scope one branch and keep promoted underlyings out of the Other branch."""
    scoped = frame_for_context(frame, {group_column: value})
    if group_column == "display bucket" and value == "Other":
        reporting_column = (
            "reported underlying" if "reported underlying" in frame else "underlying"
        )
        promoted = set(
            frame.loc[frame["display bucket"].ne("Other"), reporting_column]
            .dropna()
            .astype(str)
        )
        if promoted:
            scoped = scoped.loc[~scoped[reporting_column].astype(str).isin(promoted)]
    return scoped


def detail_frame(
    frame: pd.DataFrame, context: dict[str, str], metric: str
) -> pd.DataFrame:
    scoped = frame_for_context(frame, context).copy()
    if scoped.empty:
        return pd.DataFrame()
    if "rows" not in scoped:
        scoped["rows"] = 1
    identity_keys = [
        *(["source type"] if "source type" in scoped else []),
        "underlying",
    ]
    group_keys = [
        *identity_keys,
        "tenor swap",
        "tenor swap order",
        "tenor option",
        "tenor option order",
    ]
    quote_identity = ["risk type", "risk greek", *group_keys]
    quotes = (
        scoped.drop_duplicates(quote_identity + ["open", "current", "move"])
        .groupby(group_keys, as_index=False)
        .agg(open=("open", "mean"), current=("current", "mean"), move=("move", "mean"))
    )
    rows = scoped.groupby(group_keys, as_index=False).agg(rows=("rows", "sum"))
    grouped = rows.merge(quotes, on=group_keys, how="left", validate="one_to_one")
    if metric not in {"move", "open", "current"}:
        value_columns = [metric, *METRIC_BREAKDOWNS.get(metric, [])]
        values = scoped.groupby(group_keys, as_index=False)[value_columns].sum(
            min_count=1
        )
        if metric in {"risk", "drisk", "pl"}:
            _assert_metric_breakdown(
                values,
                metric,
                label="Detail aggregation",
            )
        grouped = grouped.merge(
            values, on=group_keys, how="left", validate="one_to_one"
        )
    return grouped.sort_values(
        [*identity_keys, "tenor swap order", "tenor option order"],
        kind="stable",
    )


__all__ = [
    "HierarchyAggregationIndex",
    "aggregate_values",
    "apply_credit_measure",
    "apply_filters",
    "average_move",
    "credit_measure_available",
    "credit_measure_column",
    "credit_measure_values",
    "decimals_for",
    "default_open_rows",
    "detail_frame",
    "dimension_title",
    "display_metric",
    "filter_ir_family",
    "format_number",
    "frame_for_context",
    "hierarchy_groups",
    "hierarchical_market_value",
    "number_sign_class",
    "ordered_unique",
    "parse_row_key",
    "prepare_risk_data",
    "recompute_filtered_promotion",
    "row_key",
    "selected_dimension",
    "selected_underlying_sort_metric",
    "should_show_sum",
    "tenor_axis_order",
    "tenor_sort_key",
    "tree_scope",
    "triggered_pattern_click",
    "visible_tree_level",
]
