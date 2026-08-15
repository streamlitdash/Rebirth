"""Immutable, exact-identity lookup catalog for one committed Cube refresh.

The catalog is deliberately separate from the public refresh snapshot.  Normal
dashboard reads therefore do not copy raw connector caches, while Quick Risk
and Quick Market remain connector-free operations against the last fully
committed revision. The user selects an exact bounded ``Combine Udl`` option;
no row-level free-text posting index is built. A future historical repository
can implement the same result shape; this module indexes only current state.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from core.s01_schema import (
    PORTFOLIO_COLUMN,
    PORTFOLIO_FIELDS,
    PORTFOLIO_METADATA_COLUMNS,
    TENOR_COLUMNS,
    TENOR_OPTION,
    TENOR_OPTION_ORDER,
    TENOR_ORDER_BY_COLUMN,
    TENOR_ORDER_COLUMNS,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
    UNSPECIFIED_VALUE,
)


SOURCE_TYPE = "Source Type"
RISK_TYPE = "Risk Type"
RISK_GREEK = "Risk Greek"
SPLIT = "Split"
UNDERLYING = "Underlying"
REPORTED_UNDERLYING = "Reported Underlying"
RISK_DATE = "Risk Date"
MARKET_DATE = "Market Date"
RISK = "Risk"
DRISK = "dRisk"
PL = "PL"
OPEN = "Open"
CURRENT = "Current"
MOVE = "Move"
COMBINE_UDL = "Combine Udl"
HIERARCHY_DEPTH = "__Hierarchy Depth__"
MARKET_STATUS = "Market Status"
MARKET_AVAILABLE = "Market Available"
MARKET_DATA_STATUS = "Market Data Status"
PORTFOLIO = PORTFOLIO_COLUMN
UNSPECIFIED = UNSPECIFIED_VALUE

# The pivot allowlist is intentionally ordered and closed.  The UI can reorder
# a selected subset, but arbitrary column names never reach ``groupby``.
DEFAULT_PIVOT_INDEX = (UNDERLYING, *TENOR_COLUMNS)
PIVOT_INDEX_COLUMNS = (
    SOURCE_TYPE,
    RISK_TYPE,
    RISK_GREEK,
    REPORTED_UNDERLYING,
    UNDERLYING,
    *TENOR_COLUMNS,
    PORTFOLIO,
    *PORTFOLIO_METADATA_COLUMNS,
)
GOVERNANCE_COLUMNS = (PORTFOLIO, *PORTFOLIO_METADATA_COLUMNS)
RISK_ONLY_INDEX_COLUMNS = (REPORTED_UNDERLYING, *GOVERNANCE_COLUMNS)
RISK_PIVOT_VALUE_COLUMNS = (RISK, DRISK, PL)
MARKET_PIVOT_VALUE_COLUMNS = (OPEN, CURRENT, MOVE)
COMBINED_PIVOT_VALUE_COLUMNS = (
    *RISK_PIVOT_VALUE_COLUMNS,
    *MARKET_PIVOT_VALUE_COLUMNS,
)

RISK_RESULT_COLUMNS = (
    SOURCE_TYPE,
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    *TENOR_COLUMNS,
    RISK_DATE,
    RISK,
    DRISK,
)
MARKET_RESULT_COLUMNS = (
    SOURCE_TYPE,
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    *TENOR_COLUMNS,
    *TENOR_ORDER_COLUMNS,
    MARKET_DATE,
    OPEN,
    CURRENT,
    MOVE,
    MARKET_STATUS,
    MARKET_DATA_STATUS,
)

_QUOTE_IDENTITY = (
    SOURCE_TYPE,
    RISK_TYPE,
    RISK_GREEK,
    UNDERLYING,
    *TENOR_COLUMNS,
)
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_MAX_QUERY_LENGTH = 256
_MAX_RESULT_LIMIT = 500
_COMBINE_UDL_SEPARATOR = " | "

QUICK_RISK_IDENTITY_MODE_COLUMNS = MappingProxyType(
    {
        "reported": REPORTED_UNDERLYING,
        "underlying": UNDERLYING,
    }
)

# These are filters over committed position rows, not pivot dimensions and not
# new connector queries.  Keep the registry-derived dimension list aligned with
# the dashboard controls while Split remains the sourced/developed-risk axis.
QUICK_RISK_FILTER_COLUMNS = (
    SPLIT,
    *(
        field.external_name
        for field in PORTFOLIO_FIELDS
        if "filter_dimension" in field.roles
    ),
)


def _quick_risk_identity_column(identity_mode: str = "reported") -> str:
    """Resolve the selected Quick Risk identity authority."""

    if not isinstance(identity_mode, str):
        raise TypeError("Quick Risk identity mode must be text")
    selected_mode = identity_mode.strip().casefold() or "reported"
    try:
        return QUICK_RISK_IDENTITY_MODE_COLUMNS[selected_mode]
    except KeyError as exc:
        raise ValueError(
            "Quick Risk identity mode must be 'reported' or 'underlying'"
        ) from exc


def _filter_risk_positions(
    frame: pd.DataFrame,
    positions: np.ndarray,
    risk_filters: Mapping[str, Sequence[str] | None] | None,
) -> np.ndarray:
    """Filter exact committed risk positions without changing row order."""

    selected_filters = dict(risk_filters or {})
    unknown = sorted(set(selected_filters) - set(QUICK_RISK_FILTER_COLUMNS))
    if unknown:
        raise ValueError(f"Unknown Quick Risk filters: {unknown}")
    if len(positions) == 0 or not selected_filters:
        return positions

    candidates = frame.iloc[positions]
    keep = np.ones(len(candidates), dtype=bool)
    for column in QUICK_RISK_FILTER_COLUMNS:
        raw_selected = selected_filters.get(column)
        if isinstance(raw_selected, (str, bytes)):
            raise TypeError(
                f"Quick Risk filter {column!r} must be a sequence of values"
            )
        selected = list(raw_selected or [])
        if selected:
            keep &= candidates[column].isin(selected).to_numpy()
    return positions[keep]


@dataclass(frozen=True)
class SearchResult:
    """Small defensive result from a single committed catalog revision."""

    revision: int
    frame: pd.DataFrame
    risk_dates: Mapping[str, pd.Timestamp]
    market_date: pd.Timestamp
    query: str
    total: int


def _normalised_parts(value: object) -> tuple[str, ...]:
    if value is None or pd.isna(value):
        return ()
    text = _CAMEL_BOUNDARY.sub(
        " ", unicodedata.normalize("NFKC", str(value))
    ).casefold()
    return tuple(part for part in _NON_ALPHANUMERIC.split(text) if part)


def _validate_limit(limit: int) -> int:
    if isinstance(limit, (bool, np.bool_)) or not isinstance(limit, (int, np.integer)):
        raise TypeError("search limit must be an integer")
    selected = int(limit)
    if selected < 1 or selected > _MAX_RESULT_LIMIT:
        raise ValueError(f"search limit must be between 1 and {_MAX_RESULT_LIMIT}")
    return selected


def _combine_udl_keys(
    frame: pd.DataFrame,
    *,
    underlying_column: str = UNDERLYING,
) -> pd.Series:
    """Build the exact dropdown key without tokenizing or parsing identities."""
    if frame.empty:
        return pd.Series(index=frame.index, dtype="string", name=COMBINE_UDL)
    components: list[pd.Series] = []
    for column in (RISK_TYPE, RISK_GREEK, underlying_column):
        values = frame[column].astype("string").fillna(UNSPECIFIED).str.strip()
        components.append(values.mask(values.eq(""), UNSPECIFIED))
    keys = components[0].str.cat(
        components[1:],
        sep=_COMBINE_UDL_SEPARATOR,
    )
    keys.name = COMBINE_UDL
    return keys


def _validate_combine_udl_components(
    frame: pd.DataFrame,
    *,
    frame_name: str,
    underlying_column: str = UNDERLYING,
) -> None:
    """Keep the three-field display identity injective and canonical."""
    if frame.empty:
        return
    for column in (RISK_TYPE, RISK_GREEK, underlying_column):
        raw_values = frame[column]
        values = raw_values.astype("string")
        stripped = values.str.strip()
        missing = raw_values.isna() | stripped.isna() | stripped.eq("")
        if missing.any():
            raise ValueError(
                f"{frame_name} {column!r} must not contain null or blank "
                "Combine Udl identity values"
            )
        noncanonical = values.ne(stripped).fillna(False)
        if noncanonical.any():
            bad_value = values.loc[noncanonical].iloc[0]
            raise ValueError(
                f"{frame_name} {column!r} value {bad_value!r} has leading or "
                "trailing whitespace"
            )
        contains_separator = values.str.contains(
            _COMBINE_UDL_SEPARATOR,
            regex=False,
            na=False,
        )
        if contains_separator.any():
            bad_value = values.loc[contains_separator].iloc[0]
            raise ValueError(
                f"{frame_name} {column!r} value {bad_value!r} contains the "
                f"reserved Combine Udl separator {_COMBINE_UDL_SEPARATOR!r}"
            )


def _build_exact_positions(
    frame: pd.DataFrame,
    *,
    underlying_column: str = UNDERLYING,
) -> Mapping[str, np.ndarray]:
    """Precompute immutable row positions for O(1) Combine-Udl selection."""
    if len(frame) > np.iinfo(np.int32).max:
        raise ValueError("search catalog exceeds the supported row count")
    keys = _combine_udl_keys(frame, underlying_column=underlying_column)
    positions: dict[str, np.ndarray] = {}
    for key, rows in keys.groupby(keys, sort=False, dropna=False).indices.items():
        compact = np.asarray(rows, dtype=np.int32)
        compact.flags.writeable = False
        positions[str(key)] = compact
    return MappingProxyType(positions)


def _dropdown_search_terms(search_value: str | None) -> tuple[str, ...]:
    """Normalize a dropdown search once; exact pivot selection stays unchanged."""
    if search_value is None:
        return ()
    if not isinstance(search_value, str):
        raise TypeError("Combine Udl search value must be text")
    cleaned = search_value.strip()
    if len(cleaned) > _MAX_QUERY_LENGTH:
        raise ValueError(
            f"Combine Udl search must be at most {_MAX_QUERY_LENGTH} characters"
        )
    return tuple(dict.fromkeys(_normalised_parts(cleaned)))


def _dropdown_search_label(value: str) -> str:
    """Compact punctuation so EUR/USD is discoverable by typing EURUSD."""
    return "".join(_normalised_parts(value))


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _as_nullable_order(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _dashboard_tenors(frame: pd.DataFrame, *, fx_delta: bool) -> pd.DataFrame:
    result = frame.copy()
    if TENOR_SWAP not in result:
        result[TENOR_SWAP] = "Spot" if fx_delta else "N/A"
    if TENOR_OPTION not in result:
        result[TENOR_OPTION] = "N/A"
    for column in TENOR_ORDER_COLUMNS:
        if column not in result:
            result[column] = pd.Series(pd.NA, index=result.index, dtype="Int64")
        else:
            result[column] = _as_nullable_order(result[column])
    return result


def _risk_catalog_frame(
    risk_frames: Mapping[str, pd.DataFrame],
    risk_dates: Mapping[str, pd.Timestamp],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source_type, source_frame in risk_frames.items():
        frame = _dashboard_tenors(
            source_frame,
            fx_delta=source_type == "fx/delta",
        )
        frame[SOURCE_TYPE] = source_type
        frame[RISK_DATE] = pd.Timestamp(risk_dates[source_type]).normalize()
        frames.append(frame)
    if not frames:
        return _empty_frame(RISK_RESULT_COLUMNS)
    combined = pd.concat(frames, ignore_index=True, sort=False)
    identity = list(RISK_RESULT_COLUMNS[:-2])
    # Quick Risk Search is quote-level rather than portfolio-level.  Aggregate
    # the authoritative positions once when publishing the catalog.
    combined = (
        combined.groupby(identity, as_index=False, dropna=False, sort=False)[
            [RISK, DRISK]
        ]
        .sum(min_count=1)
        .loc[:, list(RISK_RESULT_COLUMNS)]
    )
    return combined.sort_values(
        [SOURCE_TYPE, UNDERLYING, *TENOR_COLUMNS], kind="stable"
    ).reset_index(drop=True)


def _market_catalog_frame(
    market_frames: Mapping[str, pd.DataFrame],
    market_date: pd.Timestamp,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source_type, source_frame in market_frames.items():
        frame = _dashboard_tenors(
            source_frame,
            fx_delta=source_type == "fx/delta",
        )
        frame[SOURCE_TYPE] = source_type
        frame[MARKET_DATE] = pd.Timestamp(market_date).normalize()
        frames.append(frame)
    if not frames:
        return _empty_frame(MARKET_RESULT_COLUMNS)
    combined = pd.concat(frames, ignore_index=True, sort=False)
    return (
        combined.loc[:, list(MARKET_RESULT_COLUMNS)]
        .sort_values(
            [
                SOURCE_TYPE,
                UNDERLYING,
                *TENOR_ORDER_COLUMNS,
                *TENOR_COLUMNS,
            ],
            kind="stable",
            na_position="last",
        )
        .reset_index(drop=True)
    )


def _risk_pivot_catalog_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Keep canonical position grain for additive Risk/P&L pivots.

    This is deliberately built from the configured/enriched P&L frame rather
    than raw Risk connector frames: portfolio governance and PL are committed
    only after config and threshold validation have succeeded.
    """
    columns = list(
        dict.fromkeys(
            (
                *PIVOT_INDEX_COLUMNS,
                *QUICK_RISK_FILTER_COLUMNS,
                *TENOR_ORDER_COLUMNS,
                *RISK_PIVOT_VALUE_COLUMNS,
            )
        )
    )
    if frame is None or frame.empty:
        return _empty_frame(columns)
    result = _dashboard_tenors(frame, fx_delta=False)
    # Direct-library callers created before Split filtering supplied ordinary
    # sourced-risk rows without the column.  Their unambiguous default is Risk;
    # committed manager frames already carry the authoritative Split value.
    if SPLIT not in result:
        result[SPLIT] = "Risk"
    if REPORTED_UNDERLYING not in result:
        result[REPORTED_UNDERLYING] = result[UNDERLYING]
    required = [
        SOURCE_TYPE,
        RISK_TYPE,
        RISK_GREEK,
        UNDERLYING,
        PORTFOLIO,
        *RISK_PIVOT_VALUE_COLUMNS,
    ]
    missing = [column for column in required if column not in result]
    if missing:
        raise ValueError(f"risk pivot source is missing required columns: {missing}")
    for column in GOVERNANCE_COLUMNS:
        if column not in result:
            result[column] = UNSPECIFIED
        else:
            blank = result[column].isna() | result[column].astype(
                "string"
            ).str.strip().eq("")
            result.loc[blank, column] = UNSPECIFIED
    for column in RISK_PIVOT_VALUE_COLUMNS:
        values = result[column]
        boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
        converted = pd.to_numeric(values, errors="coerce")
        nonblank = values.notna() & values.astype("string").str.strip().ne("")
        invalid = boolean | (nonblank & converted.isna())
        invalid |= converted.notna() & ~np.isfinite(converted)
        if invalid.any():
            rows = result.index[invalid].tolist()[:5]
            raise ValueError(
                f"risk pivot source column {column!r} contains invalid numeric "
                f"values at rows {rows}"
            )
        result[column] = converted
    return result.loc[:, columns].reset_index(drop=True)


def _validate_pivot_index(index_columns: Sequence[str]) -> tuple[str, ...]:
    if isinstance(index_columns, (str, bytes)):
        raise TypeError("pivot index columns must be a sequence of column names")
    selected = tuple(index_columns)
    if not selected:
        raise ValueError("pivot index must contain at least one column")
    if any(not isinstance(column, str) for column in selected):
        raise TypeError("pivot index columns must be text")
    if len(selected) != len(set(selected)):
        raise ValueError("pivot index columns must not contain duplicates")
    unsupported = [column for column in selected if column not in PIVOT_INDEX_COLUMNS]
    if unsupported:
        raise ValueError(
            "unsupported pivot index columns: "
            f"{unsupported}; allowed columns are {list(PIVOT_INDEX_COLUMNS)}"
        )
    return selected


def _pivot_order_ranks(
    selected_rows: pd.DataFrame,
    index_columns: Sequence[str],
    order_column: str,
    helper: str,
) -> pd.DataFrame:
    """Vectorize modal connector-rank selection with smallest-rank tie break."""
    numeric = pd.to_numeric(selected_rows[order_column], errors="coerce")
    valid = numeric.notna() & np.isfinite(numeric)
    if not valid.any():
        return _empty_frame([*index_columns, helper])
    ranked = selected_rows.loc[valid, list(index_columns)].copy()
    ranked[helper] = numeric.loc[valid].to_numpy()
    count_column = "__pivot_rank_count__"
    counts = (
        ranked.groupby(
            [*index_columns, helper],
            as_index=False,
            dropna=False,
            sort=False,
        )
        .size()
        .rename(columns={"size": count_column})
    )
    # Stable sort and first-per-group implements: highest frequency, then the
    # smallest market-owned rank. No tenor-label parser is involved.
    counts = counts.sort_values(
        [count_column, helper],
        ascending=[False, True],
        kind="stable",
    )
    return counts.drop_duplicates(subset=list(index_columns), keep="first").loc[
        :, [*index_columns, helper]
    ]


def _sort_pivot(
    pivot: pd.DataFrame,
    selected_rows: pd.DataFrame,
    index_columns: Sequence[str],
) -> pd.DataFrame:
    """Sort pivot rows using market-owned ranks, then discard sort helpers."""
    if pivot.empty:
        return pivot.reset_index(drop=True)
    result = pivot.copy()
    sort_columns: list[str] = []
    helpers: list[str] = []
    for position, column in enumerate(index_columns):
        if column in TENOR_COLUMNS:
            order_column = TENOR_ORDER_BY_COLUMN[column]
            helper = f"__pivot_order_{position}__"
            ranks = _pivot_order_ranks(
                selected_rows,
                index_columns,
                order_column,
                helper,
            )
            if not ranks.empty:
                result = result.merge(
                    ranks,
                    on=list(index_columns),
                    how="left",
                    validate="one_to_one",
                    sort=False,
                )
            else:
                result[helper] = float("inf")
            helpers.append(helper)
            sort_columns.append(helper)
        text_helper = f"__pivot_text_{position}__"
        result[text_helper] = result[column].astype("string").str.casefold()
        helpers.append(text_helper)
        sort_columns.append(text_helper)
    result = result.sort_values(
        sort_columns,
        kind="stable",
        na_position="last",
    )
    return result.drop(columns=helpers).reset_index(drop=True)


def _sort_combined_pivot(
    pivot: pd.DataFrame,
    market_rows: pd.DataFrame,
    risk_rows: pd.DataFrame,
    index_columns: Sequence[str],
) -> pd.DataFrame:
    """Sort with market-owned tenor ranks and risk ranks only as fallback."""
    if pivot.empty:
        return pivot.reset_index(drop=True)
    result = pivot.copy()
    sort_columns: list[str] = []
    helpers: list[str] = []
    for position, column in enumerate(index_columns):
        if column in TENOR_COLUMNS:
            order_column = TENOR_ORDER_BY_COLUMN[column]
            helper = f"__combined_pivot_order_{position}__"
            market_ranks = _pivot_order_ranks(
                market_rows,
                index_columns,
                order_column,
                helper,
            )
            risk_ranks = _pivot_order_ranks(
                risk_rows,
                index_columns,
                order_column,
                helper,
            )
            # Market is authoritative. Risk ranks only fill groups for which no
            # current market quote exists; they never vote against a quote rank.
            ranks = pd.concat(
                [market_ranks, risk_ranks],
                ignore_index=True,
                sort=False,
            ).drop_duplicates(subset=list(index_columns), keep="first")
            if not ranks.empty:
                result = result.merge(
                    ranks,
                    on=list(index_columns),
                    how="left",
                    validate="one_to_one",
                    sort=False,
                )
            else:
                result[helper] = float("inf")
            helpers.append(helper)
            sort_columns.append(helper)
        text_helper = f"__combined_pivot_text_{position}__"
        result[text_helper] = result[column].astype("string").str.casefold()
        helpers.append(text_helper)
        sort_columns.append(text_helper)
    result = result.sort_values(sort_columns, kind="stable", na_position="last")
    return result.drop(columns=helpers).reset_index(drop=True)


def _combined_pivot(
    risk_rows: pd.DataFrame,
    market_rows: pd.DataFrame,
    index_columns: Sequence[str],
) -> pd.DataFrame:
    """Aggregate both metric grains independently before their outer join."""
    risk_pivot = _risk_pivot(risk_rows, index_columns)
    market_pivot = _market_pivot(market_rows, index_columns)
    pivot = risk_pivot.merge(
        market_pivot,
        on=list(index_columns),
        how="outer",
        sort=False,
        validate="one_to_one",
    ).loc[:, [*index_columns, *COMBINED_PIVOT_VALUE_COLUMNS]]
    return _sort_combined_pivot(
        pivot,
        market_rows,
        risk_rows,
        index_columns,
    )


def _hierarchy_key(record: Mapping[str, object], columns: Sequence[str]) -> tuple:
    """Return a hashable path key with one stable missing-value sentinel."""
    values: list[object] = []
    for column in columns:
        value = record[column]
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and missing:
            values.append(None)
        elif isinstance(value, np.generic):
            values.append(value.item())
        else:
            values.append(value)
    return tuple(values)


def _risk_pivot(
    selected_rows: pd.DataFrame,
    index_columns: Sequence[str],
) -> pd.DataFrame:
    columns = [*index_columns, *RISK_PIVOT_VALUE_COLUMNS]
    if selected_rows.empty:
        return _empty_frame(columns)
    pivot = (
        selected_rows.groupby(
            list(index_columns),
            as_index=False,
            dropna=False,
            sort=False,
        )[list(RISK_PIVOT_VALUE_COLUMNS)]
        .sum(min_count=1)
        .loc[:, columns]
    )
    return _sort_pivot(pivot, selected_rows, index_columns).loc[:, columns]


def _ordered_union(*parts: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(column for part in parts for column in part))


def _risk_quote_positions(
    risk_frame: pd.DataFrame,
    market_frame: pd.DataFrame,
) -> np.ndarray:
    """Map each position row to a compact quote row without expanding quotes."""
    if len(market_frame) > np.iinfo(np.int32).max:
        raise ValueError("market catalog exceeds the supported quote count")
    if market_frame.empty:
        mapping = np.full(len(risk_frame), -1, dtype=np.int32)
    else:
        quote_keys = pd.MultiIndex.from_frame(
            market_frame.loc[:, list(_QUOTE_IDENTITY)]
        )
        if quote_keys.has_duplicates:
            raise ValueError(
                "market search catalog contains duplicate quote identities"
            )
        risk_keys = pd.MultiIndex.from_frame(risk_frame.loc[:, list(_QUOTE_IDENTITY)])
        mapping = quote_keys.get_indexer(risk_keys).astype(np.int32, copy=False)
    mapping.flags.writeable = False
    return mapping


def _market_pivot(
    selected_rows: pd.DataFrame,
    index_columns: Sequence[str],
) -> pd.DataFrame:
    """Roll up paired quotes through option -> swap -> underlying means.

    Open and Current must describe the same quote population at every level.
    Incomplete quote rows remain as index members, but neither leg contributes
    to a rollup until both are available. Move is then derived from the two
    aggregated legs instead of being averaged independently.
    """
    axis_order_columns = [
        TENOR_ORDER_BY_COLUMN[column]
        for column in index_columns
        if column in TENOR_COLUMNS
    ]
    columns = [
        *index_columns,
        *axis_order_columns,
        *MARKET_PIVOT_VALUE_COLUMNS,
        MARKET_STATUS,
    ]
    if selected_rows.empty:
        return _empty_frame(columns)

    governance_index = [
        column for column in index_columns if column in GOVERNANCE_COLUMNS
    ]
    dedupe_columns = _ordered_union(_QUOTE_IDENTITY, governance_index)
    quotes = selected_rows.drop_duplicates(
        subset=dedupe_columns,
        keep="first",
    ).copy()

    # A mean of every available Open and every available Current can combine
    # different child quotes and manufacture a parent Move. Mask both legs as
    # one pair so every aggregate compares like with like.
    complete_quote = quotes[OPEN].notna() & quotes[CURRENT].notna()
    quotes.loc[~complete_quote, [OPEN, CURRENT]] = np.nan
    quote_columns = [OPEN, CURRENT]

    # Equal-weight at every child layer prevents a dense option grid, a long
    # swap curve, or many portfolios from dominating a broader market value.
    option_keys = _ordered_union(
        index_columns,
        (UNDERLYING, *TENOR_COLUMNS),
    )
    option = quotes.groupby(option_keys, as_index=False, dropna=False, sort=False)[
        quote_columns
    ].mean()
    swap_keys = _ordered_union(index_columns, (UNDERLYING, TENOR_SWAP))
    swap = option.groupby(swap_keys, as_index=False, dropna=False, sort=False)[
        quote_columns
    ].mean()
    underlying_keys = _ordered_union(index_columns, (UNDERLYING,))
    underlying = swap.groupby(
        underlying_keys, as_index=False, dropna=False, sort=False
    )[quote_columns].mean()
    pivot = underlying.groupby(
        list(index_columns), as_index=False, dropna=False, sort=False
    )[quote_columns].mean()
    pivot[MOVE] = pivot[CURRENT] - pivot[OPEN]

    # Keep the connector-owned ranks in the exact MarketBook result. The UI
    # must never reconstruct order by parsing labels such as IMM dates, odd
    # stubs, or site-specific tenor names.
    for order_column in axis_order_columns:
        ranks = _pivot_order_ranks(
            quotes,
            index_columns,
            order_column,
            order_column,
        )
        if ranks.empty:
            pivot[order_column] = pd.Series(pd.NA, index=pivot.index, dtype="Int64")
        else:
            pivot = pivot.merge(
                ranks,
                on=list(index_columns),
                how="left",
                validate="one_to_one",
                sort=False,
            )

    statuses = quotes[MARKET_STATUS].dropna().astype(str).unique().tolist()
    if len(statuses) != 1:
        raise ValueError(
            "exact MarketBook identity must contain one authoritative Market Status"
        )
    pivot[MARKET_STATUS] = statuses[0]
    pivot = pivot.loc[:, columns]
    return _sort_pivot(pivot, quotes, index_columns).loc[:, columns]


class SearchCatalog:
    """Current-revision exact identities and pivot source data.

    Frames and exact-position maps are never exposed directly. Publication is
    a single pointer swap in ``RiskRefreshManager`` and every result owns its
    DataFrame. Bounded dropdown filtering scans only pre-normalized identity
    labels; it never tokenizes every position or quote row.
    """

    __slots__ = (
        "revision",
        "risk_dates",
        "market_date",
        "_market_frame",
        "_risk_pivot_frame",
        "_risk_to_quote",
        "_risk_combine_positions",
        "_raw_risk_combine_positions",
        "_market_combine_positions",
        "_combine_udl_options",
        "_combine_udl_search_labels",
        "_raw_combine_udl_options",
        "_raw_combine_udl_search_labels",
        "_market_udl_options",
        "_market_udl_search_labels",
    )

    def __init__(
        self,
        *,
        revision: int,
        risk_dates: Mapping[str, pd.Timestamp],
        market_date: pd.Timestamp,
        market_frame: pd.DataFrame,
        risk_pivot_frame: pd.DataFrame,
    ) -> None:
        self.revision = int(revision)
        self.risk_dates = MappingProxyType(
            {
                source_type: pd.Timestamp(value).normalize()
                for source_type, value in risk_dates.items()
            }
        )
        self.market_date = pd.Timestamp(market_date).normalize()
        self._market_frame = market_frame.copy(deep=True)
        # Validate direct constructor callers as strictly as the managed
        # ``build_search_catalog`` path; this also owns the defensive copy.
        self._risk_pivot_frame = _risk_pivot_catalog_frame(risk_pivot_frame)
        self._risk_to_quote = _risk_quote_positions(
            self._risk_pivot_frame,
            self._market_frame,
        )
        _validate_combine_udl_components(
            self._risk_pivot_frame,
            frame_name="risk pivot catalog",
            underlying_column=REPORTED_UNDERLYING,
        )
        _validate_combine_udl_components(
            self._risk_pivot_frame,
            frame_name="risk pivot catalog",
            underlying_column=UNDERLYING,
        )
        _validate_combine_udl_components(
            self._market_frame,
            frame_name="market catalog",
        )
        self._risk_combine_positions = _build_exact_positions(
            self._risk_pivot_frame,
            underlying_column=REPORTED_UNDERLYING,
        )
        self._raw_risk_combine_positions = _build_exact_positions(
            self._risk_pivot_frame,
            underlying_column=UNDERLYING,
        )
        self._market_combine_positions = _build_exact_positions(self._market_frame)
        # Risk drives market acquisition: only identities present in committed
        # positions are valid dropdown choices. The tuple is immutable and safe
        # to return without copying on every Dash callback.
        self._combine_udl_options = tuple(self._risk_combine_positions)
        self._combine_udl_search_labels = tuple(
            _dropdown_search_label(value) for value in self._combine_udl_options
        )
        self._raw_combine_udl_options = tuple(self._raw_risk_combine_positions)
        self._raw_combine_udl_search_labels = tuple(
            _dropdown_search_label(value) for value in self._raw_combine_udl_options
        )
        self._market_udl_options = tuple(self._market_combine_positions)
        self._market_udl_search_labels = tuple(
            _dropdown_search_label(value) for value in self._market_udl_options
        )

    def _quick_risk_index(
        self,
        identity_mode: str,
    ) -> tuple[
        Mapping[str, np.ndarray],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        identity_column = _quick_risk_identity_column(identity_mode)
        if identity_column == REPORTED_UNDERLYING:
            return (
                self._risk_combine_positions,
                self._combine_udl_options,
                self._combine_udl_search_labels,
            )
        return (
            self._raw_risk_combine_positions,
            self._raw_combine_udl_options,
            self._raw_combine_udl_search_labels,
        )

    def combine_udl_options(
        self,
        *,
        identity_mode: str = "reported",
    ) -> tuple[str, ...]:
        """Return exact Quick Risk identities for the selected authority."""

        _positions, options, _search_labels = self._quick_risk_index(identity_mode)
        return options

    def market_udl_options(self) -> tuple[str, ...]:
        """Return exact identities from the full MarketBook."""

        return self._market_udl_options

    def search_market_udl_options(
        self,
        search_value: str | None,
        *,
        limit: int = 100,
        include: str | None = None,
    ) -> tuple[str, ...]:
        """Return a bounded filtered slice of full-MarketBook identities."""

        selected_limit = _validate_limit(limit)
        terms = _dropdown_search_terms(search_value)
        matches: list[str] = []
        for option, search_label in zip(
            self._market_udl_options,
            self._market_udl_search_labels,
            strict=True,
        ):
            if terms and not all(term in search_label for term in terms):
                continue
            matches.append(option)
            if len(matches) >= selected_limit:
                break
        if include is not None:
            if not isinstance(include, str):
                raise TypeError("included Market selection must be text")
            if include in self._market_combine_positions and include not in matches:
                matches.insert(0, include)
        return tuple(matches)

    def search_combine_udl_options(
        self,
        search_value: str | None,
        *,
        identity_mode: str = "reported",
        limit: int = 100,
        include: str | None = None,
    ) -> tuple[str, ...]:
        """Return a bounded, server-filtered slice of exact dropdown values.

        Display-label normalization is precomputed at catalog publication. The
        scan stops as soon as ``limit`` matches are found. A valid current
        selection is retained so Dash never clears it merely because its option
        was paged.
        """
        selected_limit = _validate_limit(limit)
        terms = _dropdown_search_terms(search_value)
        positions, options, search_labels = self._quick_risk_index(identity_mode)
        matches: list[str] = []
        for option, search_label in zip(
            options,
            search_labels,
            strict=True,
        ):
            if terms and not all(term in search_label for term in terms):
                continue
            matches.append(option)
            if len(matches) >= selected_limit:
                break

        if include is not None:
            if not isinstance(include, str):
                raise TypeError("included Combine Udl selection must be text")
            if include in positions and include not in matches:
                # At most one current selection may sit alongside ``limit``
                # search matches, keeping the callback payload strictly bounded.
                matches.insert(0, include)
        return tuple(matches)

    @staticmethod
    def _exact_positions(
        positions: Mapping[str, np.ndarray],
        combine_udl: str,
    ) -> np.ndarray:
        if not isinstance(combine_udl, str):
            raise TypeError("Combine Udl selection must be text")
        selected = positions.get(combine_udl)
        if selected is None:
            return np.empty(0, dtype=np.int32)
        return selected

    def _contextual_market_rows(
        self,
        quote_positions: np.ndarray,
        risk_positions: np.ndarray,
        risk_context_index: Sequence[str],
    ) -> pd.DataFrame:
        """Attach only requested governance, without expanding quote storage."""
        selected_quotes = self._market_frame.iloc[quote_positions]
        if not risk_context_index:
            return selected_quotes

        valid_risk_positions = risk_positions[self._risk_to_quote[risk_positions] >= 0]
        if len(valid_risk_positions):
            bridge = self._risk_pivot_frame.iloc[valid_risk_positions][
                list(risk_context_index)
            ].reset_index(drop=True)
            bridge.insert(
                0,
                "__quote_position__",
                self._risk_to_quote[valid_risk_positions],
            )
            # A query can select risk rows that are not among the selected
            # exact market identity. Keep the bridge bounded to this quote set.
            bridge = bridge.loc[
                bridge["__quote_position__"].isin(quote_positions)
            ].drop_duplicates(subset=["__quote_position__", *risk_context_index])
            contextual_quotes = self._market_frame.iloc[
                bridge["__quote_position__"].to_numpy(dtype=np.int32)
            ].reset_index(drop=True)
            contextual_quotes = pd.concat(
                [
                    contextual_quotes,
                    bridge.loc[:, list(risk_context_index)].reset_index(drop=True),
                ],
                axis=1,
            )
            contextual_quote_positions = np.unique(
                bridge["__quote_position__"].to_numpy(dtype=np.int32)
            )
        else:
            contextual_quotes = self._market_frame.iloc[0:0].copy()
            for column in risk_context_index:
                contextual_quotes[column] = pd.Series(dtype="object")
            contextual_quote_positions = np.empty(0, dtype=np.int32)

        uncontextualized_positions = np.setdiff1d(
            quote_positions,
            contextual_quote_positions,
            assume_unique=True,
        ).astype(np.int32, copy=False)
        uncontextualized = self._market_frame.iloc[uncontextualized_positions].copy()
        for column in risk_context_index:
            uncontextualized[column] = UNSPECIFIED
        return pd.concat(
            [contextual_quotes, uncontextualized],
            ignore_index=True,
            sort=False,
        )

    def pivot_market_exact(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = DEFAULT_PIVOT_INDEX,
        limit: int | None = None,
    ) -> SearchResult:
        """Pivot one complete exact MarketBook identity without Risk filtering.

        ``None`` deliberately means no row cap: once the bounded dropdown has
        selected one exact Risk Type + Risk Greek + Underlying identity, every
        connector-owned tenor is part of that result. Callers may still supply
        an explicit bounded limit for a compact secondary view.
        """

        selected_index = _validate_pivot_index(index_columns)
        unsupported = [
            column for column in selected_index if column in RISK_ONLY_INDEX_COLUMNS
        ]
        if unsupported:
            raise ValueError(
                f"Quick Market index cannot contain Risk-only columns: {unsupported}"
            )
        selected_limit = None if limit is None else _validate_limit(limit)
        positions = self._exact_positions(self._market_combine_positions, combine_udl)
        quotes = self._market_frame.iloc[positions]
        pivot = _market_pivot(quotes, selected_index)
        return SearchResult(
            revision=self.revision,
            frame=(pivot if selected_limit is None else pivot.iloc[:selected_limit])
            .copy(deep=True)
            .reset_index(drop=True),
            risk_dates=MappingProxyType(dict(self.risk_dates)),
            market_date=self.market_date,
            query=combine_udl,
            total=len(pivot),
        )

    def pivot_combined(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = DEFAULT_PIVOT_INDEX,
        limit: int = _MAX_RESULT_LIMIT,
        identity_mode: str = "reported",
        risk_filters: Mapping[str, Sequence[str] | None] | None = None,
    ) -> SearchResult:
        """Return one exact-selection Risk/P&L + Market pivot.

        Risk values aggregate from position grain. Market values aggregate
        independently from unique quote grain and are only then outer-joined;
        portfolio density therefore cannot weight Open, Current, or Move.
        """
        selected_index = _validate_pivot_index(index_columns)
        selected_limit = _validate_limit(limit)
        selected_risk, selected_quotes = self._combined_source_rows(
            combine_udl,
            selected_index,
            identity_mode=identity_mode,
            risk_filters=risk_filters,
        )
        pivot = _combined_pivot(
            selected_risk,
            selected_quotes,
            selected_index,
        )
        if UNDERLYING not in selected_index:
            pivot.loc[:, list(MARKET_PIVOT_VALUE_COLUMNS)] = np.nan
        total = len(pivot)
        return SearchResult(
            revision=self.revision,
            frame=pivot.iloc[:selected_limit].copy(deep=True).reset_index(drop=True),
            risk_dates=MappingProxyType(dict(self.risk_dates)),
            market_date=self.market_date,
            query=combine_udl,
            total=total,
        )

    def _combined_source_rows(
        self,
        combine_udl: str,
        selected_index: Sequence[str],
        *,
        identity_mode: str = "reported",
        risk_filters: Mapping[str, Sequence[str] | None] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Select exact Risk and quote source rows once for combined pivots."""
        positions, _options, _search_labels = self._quick_risk_index(identity_mode)
        risk_positions = self._exact_positions(
            positions,
            combine_udl,
        )
        risk_positions = _filter_risk_positions(
            self._risk_pivot_frame,
            risk_positions,
            risk_filters,
        )
        if len(risk_positions):
            referenced_quote_positions = self._risk_to_quote[risk_positions]
            referenced_quote_positions = referenced_quote_positions[
                referenced_quote_positions >= 0
            ]
            if len(referenced_quote_positions):
                referenced_quote_positions = np.unique(
                    referenced_quote_positions
                ).astype(np.int32, copy=False)
                # A reported identity can deliberately reference several raw
                # Underlyings. The raw Risk-to-quote bridge is authoritative;
                # never look for a fictional market identity such as ``CNx``.
                quote_positions = referenced_quote_positions
            else:
                quote_positions = np.empty(0, dtype=np.int32)
        else:
            quote_positions = np.empty(0, dtype=np.int32)
        selected_risk = self._risk_pivot_frame.iloc[risk_positions]
        risk_context_index = [
            column for column in selected_index if column in RISK_ONLY_INDEX_COLUMNS
        ]
        selected_quotes = self._contextual_market_rows(
            quote_positions,
            risk_positions,
            risk_context_index,
        )
        return selected_risk, selected_quotes

    def pivot_combined_hierarchy(
        self,
        combine_udl: str,
        *,
        index_columns: Sequence[str] = DEFAULT_PIVOT_INDEX,
        leaf_limit: int = _MAX_RESULT_LIMIT,
        identity_mode: str = "reported",
        risk_filters: Mapping[str, Sequence[str] | None] | None = None,
    ) -> SearchResult:
        """Return independently aggregated ordered prefix levels.

        The deepest groups are capped only after full aggregation. Every
        visible ancestor is recomputed from all matching source positions and
        quotes, then filtered to ancestors of those visible leaves. This keeps
        non-additive Market parent values correct while bounding the payload to
        at most ``len(index_columns) * leaf_limit`` rows.
        """
        selected_index = _validate_pivot_index(index_columns)
        selected_limit = _validate_limit(leaf_limit)
        selected_risk, selected_quotes = self._combined_source_rows(
            combine_udl,
            selected_index,
            identity_mode=identity_mode,
            risk_filters=risk_filters,
        )
        levels = []
        for depth in range(1, len(selected_index) + 1):
            prefix = selected_index[:depth]
            level = _combined_pivot(
                selected_risk,
                selected_quotes,
                prefix,
            )
            if UNDERLYING not in prefix:
                level.loc[:, list(MARKET_PIVOT_VALUE_COLUMNS)] = np.nan
            levels.append(level)
        deepest = levels[-1]
        total = len(deepest)
        visible_leaves = deepest.iloc[:selected_limit]
        output_columns = [
            HIERARCHY_DEPTH,
            *selected_index,
            *COMBINED_PIVOT_VALUE_COLUMNS,
        ]
        if visible_leaves.empty:
            hierarchy = _empty_frame(output_columns)
        else:
            lookups: list[dict[tuple, dict[str, object]]] = []
            for depth, level in enumerate(levels, start=1):
                prefix = selected_index[:depth]
                wanted = visible_leaves.loc[:, list(prefix)].drop_duplicates()
                visible_level = level.merge(
                    wanted.assign(__hierarchy_wanted__=True),
                    on=list(prefix),
                    how="inner",
                    sort=False,
                    validate="one_to_one",
                ).drop(columns="__hierarchy_wanted__")
                lookups.append(
                    {
                        _hierarchy_key(record, prefix): record
                        for record in visible_level.to_dict("records")
                    }
                )

            records: list[dict[str, object]] = []
            emitted: set[tuple[int, tuple]] = set()
            for leaf in visible_leaves.to_dict("records"):
                for depth in range(1, len(selected_index) + 1):
                    prefix = selected_index[:depth]
                    key = _hierarchy_key(leaf, prefix)
                    marker = (depth, key)
                    if marker in emitted:
                        continue
                    emitted.add(marker)
                    record = dict(lookups[depth - 1][key])
                    for column in selected_index[depth:]:
                        record[column] = pd.NA
                    record[HIERARCHY_DEPTH] = depth
                    records.append(record)
            hierarchy = pd.DataFrame.from_records(
                records,
                columns=output_columns,
            )

        return SearchResult(
            revision=self.revision,
            frame=hierarchy.copy(deep=True).reset_index(drop=True),
            risk_dates=MappingProxyType(dict(self.risk_dates)),
            market_date=self.market_date,
            query=combine_udl,
            total=total,
        )


def build_search_catalog(
    *,
    revision: int,
    risk_frames: Mapping[str, pd.DataFrame],
    market_frames: Mapping[str, pd.DataFrame],
    risk_pivot_frame: pd.DataFrame | None = None,
    risk_dates: Mapping[str, pd.Timestamp],
    market_date: pd.Timestamp,
) -> SearchCatalog:
    """Build exact current-revision lookup data before atomic publication."""
    if risk_pivot_frame is None:
        # Direct library callers may omit the governed position frame. Build a
        # minimal exact-pivot source only in that case; the managed app always
        # supplies its committed dashboard and pays no raw-Risk summary cost.
        fallback = _risk_catalog_frame(risk_frames, risk_dates)
        fallback[SPLIT] = "Risk"
        fallback[PORTFOLIO] = UNSPECIFIED
        for column in PORTFOLIO_METADATA_COLUMNS:
            fallback[column] = UNSPECIFIED
        fallback[PL] = pd.NA
        risk_pivot_frame = fallback
    market_frame = _market_catalog_frame(market_frames, market_date)
    return SearchCatalog(
        revision=revision,
        risk_dates=risk_dates,
        market_date=market_date,
        market_frame=market_frame,
        risk_pivot_frame=risk_pivot_frame,
    )


__all__ = [
    "COMBINE_UDL",
    "COMBINED_PIVOT_VALUE_COLUMNS",
    "CURRENT",
    "DEFAULT_PIVOT_INDEX",
    "GOVERNANCE_COLUMNS",
    "HIERARCHY_DEPTH",
    "MARKET_RESULT_COLUMNS",
    "MARKET_PIVOT_VALUE_COLUMNS",
    "PIVOT_INDEX_COLUMNS",
    "QUICK_RISK_FILTER_COLUMNS",
    "QUICK_RISK_IDENTITY_MODE_COLUMNS",
    "REPORTED_UNDERLYING",
    "RISK_RESULT_COLUMNS",
    "RISK_ONLY_INDEX_COLUMNS",
    "RISK_PIVOT_VALUE_COLUMNS",
    "TENOR_COLUMNS",
    "TENOR_OPTION",
    "TENOR_OPTION_ORDER",
    "TENOR_ORDER_COLUMNS",
    "TENOR_SWAP",
    "TENOR_SWAP_ORDER",
    "SearchCatalog",
    "SearchResult",
    "build_search_catalog",
]
