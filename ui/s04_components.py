"""Reusable Dash component builders for the risk cube."""

from __future__ import annotations

import json
import logging
from html import escape as html_escape
from textwrap import wrap
from typing import Callable, Mapping

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import dash_table, dcc, html
from core.s01_schema import PORTFOLIO_FIELDS, PORTFOLIO_METADATA_COLUMNS
from core.s03_search import HIERARCHY_DEPTH

from .s03_aggregate import (
    HierarchyAggregationIndex,
    aggregate_values,
    apply_credit_measure,
    credit_measure_available,
    credit_measure_values,
    default_open_rows,
    detail_frame,
    dimension_title,
    display_metric,
    filter_ir_family,
    format_number,
    frame_for_context,
    number_sign_class,
    ordered_unique,
    parse_row_key,
    recompute_filtered_promotion,
    row_key,
    selected_dimension,
    should_show_sum,
    tenor_axis_order,
    tree_scope,
    visible_tree_level,
)
from .s02_constants import (
    BASE_GROUPS,
    CREDIT_MEASURES,
    DEFAULT_UNDERLYING_SORT_METRIC,
    DEFAULT_VIEW_DIMENSION,
    DETAIL_COMPONENT_LABELS,
    DETAIL_COMPONENTS,
    DETAIL_MEASURES,
    DIMENSION_FILTER_IDS,
    EXPANDABLE_METRICS,
    FILTER_DIMENSION_FIELDS,
    GRID_METRIC_COLUMNS,
    METRIC_BREAKDOWNS,
    METRIC_COLUMNS,
    PLOT_METRICS,
    RISK_TYPE_ORDER,
    ROW_KEY_COLUMNS,
    TOP_EXPOSURE_GROUPS,
    TOP_EXPOSURE_LABELS,
    UNDERLYING_SORT_METRICS,
    VIEW_DIMENSION_FIELDS,
    get_active_groups,
)
from .s01_contracts import ControlSnapshotProtocol, RefreshSnapshotProtocol
from .s11_saved_views import (
    SavedFilterViewControls,
    build_saved_filter_view_bar,
)


_DETAIL_LOGGER = logging.getLogger(__name__)
_UNSET = object()
_ABSENT_TENOR_LABELS = frozenset(("", "n/a", "na", "spot", "unspecified"))
DETAIL_TENOR_VIEW_LABELS = {
    "auto": "Auto",
    "swap": "Tenor Swap line",
    "option": "Tenor Option line",
    "surface": "Surface",
}
RISK_SAVED_VIEW_CONTROLS = SavedFilterViewControls(
    scope="risk",
    prefix="risk",
    fields=FILTER_DIMENSION_FIELDS,
    filter_ids=DIMENSION_FILTER_IDS,
    exclude_id="risk-filter-exclude-selected",
)


def _active_groups_for_frame(
    frame: pd.DataFrame,
    promotion_enabled: bool,
    region_enabled: bool,
) -> list[str]:
    """Resolve the hierarchy without inventing Region for products that lack it."""
    region_available = bool(
        "region" in frame
        and frame["region"].fillna("").astype(str).str.strip().ne("").any()
    )
    return get_active_groups(
        promotion_enabled,
        region_enabled,
        region_available=region_available,
    )


QUICK_RISK_PIVOT_LIMIT = 250
QUICK_SEARCH_DEFAULT_INDEX = ("Underlying", "Tenor Swap", "Tenor Option")
QUICK_SEARCH_HIERARCHY_DEPTH = HIERARCHY_DEPTH
_MARKET_AXIS_ORDER_COLUMNS = {
    "Tenor Swap": "Tenor Swap Order",
    "Tenor Option": "Tenor Option Order",
}
_QUICK_SEARCH_IDENTITY_OPTIONS = (
    ("Source type", "Source Type"),
    ("Risk type", "Risk Type"),
    ("Risk Greek", "Risk Greek"),
    ("Underlying", "Underlying"),
    ("Tenor Swap", "Tenor Swap"),
    ("Tenor Option", "Tenor Option"),
    ("Portfolio", "Portfolio"),
)
QUICK_SEARCH_INDEX_OPTIONS = (
    *_QUICK_SEARCH_IDENTITY_OPTIONS,
    *((field.label, field.external_name) for field in PORTFOLIO_FIELDS),
)

QUICK_MARKET_DEFAULT_INDEX = (
    "Underlying",
    "Tenor Swap",
    "Tenor Option",
)


def build_quick_search() -> html.Details:
    """Build one collapsible identity picker and lazy combined hierarchy."""

    dimension_options = [
        {"label": label, "value": value} for label, value in QUICK_SEARCH_INDEX_OPTIONS
    ]

    return html.Details(
        [
            html.Summary(
                [
                    html.Span(
                        "Quick Risk Search",
                        className="quick-search-pivot-title",
                    ),
                    html.Span(
                        "Risk · dRisk · PL · Open · Current · Move",
                        className="quick-search-pivot-values",
                    ),
                ],
                id="quick-search-summary",
                n_clicks=0,
                className="quick-search-pivot-summary",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H2("Risk, PL and Market"),
                                    html.P(
                                        "Choose one exact Risk Type, Risk Greek and Underlying identity. "
                                        "The bounded dropdown never refreshes connector data."
                                    ),
                                ],
                                className="quick-search-heading-copy",
                            ),
                            html.Div(
                                [
                                    html.Label(
                                        "Search using",
                                        htmlFor="quick-search-identity-mode",
                                    ),
                                    dcc.RadioItems(
                                        id="quick-search-identity-mode",
                                        options=[
                                            {
                                                "label": "Reported Underlying",
                                                "value": "reported",
                                            },
                                            {
                                                "label": "Underlying",
                                                "value": "underlying",
                                            },
                                        ],
                                        value="reported",
                                        inline=True,
                                        className="quick-search-identity-mode",
                                    ),
                                ],
                                className="quick-search-selector-control",
                            ),
                            html.Div(
                                [
                                    html.Label(
                                        "Search Risk",
                                        htmlFor="quick-search-combine-udl",
                                    ),
                                    dcc.Dropdown(
                                        id="quick-search-combine-udl",
                                        options=[],
                                        value=None,
                                        multi=False,
                                        clearable=False,
                                        searchable=True,
                                        placeholder="Type e.g. IR Delta EUR",
                                        className="quick-search-combine-dropdown",
                                    ),
                                    html.Span(
                                        "Search one full Risk Type | Risk Greek | Underlying identity.",
                                        className="quick-search-selector-help",
                                    ),
                                ],
                                className="quick-search-selector-control",
                            ),
                        ],
                        className="quick-search-heading",
                    ),
                    html.P(
                        "One current-snapshot hierarchy combines Risk, PL and quote-aware Market values.",
                        className="quick-search-pivot-description",
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Hierarchy levels",
                                htmlFor="quick-search-dimensions",
                            ),
                            dcc.Dropdown(
                                id="quick-search-dimensions",
                                options=dimension_options,
                                value=list(QUICK_SEARCH_DEFAULT_INDEX),
                                multi=True,
                                clearable=False,
                                searchable=True,
                                closeOnSelect=False,
                                className="quick-search-dimensions",
                            ),
                            html.Span(
                                "Choose the parent-to-child field order; roots open one useful level by default.",
                                className="quick-search-dimension-help",
                            ),
                        ],
                        className="quick-search-dimension-control",
                    ),
                    dcc.Loading(
                        html.Div(
                            "Open this section to build its current-snapshot hierarchy.",
                            id="quick-search-results",
                            className="quick-search-results quick-search-hint",
                        ),
                        type="dot",
                        delay_show=160,
                        className="quick-search-loading",
                    ),
                ],
                className="quick-search-pivot-body",
            ),
        ],
        id="quick-search-details",
        open=False,
        className="quick-search-shell quick-search-pivot-details",
        **{"aria-label": "Quick Risk Search hierarchy"},
    )


def _quick_search_text(value: object, *, fallback: str = "—") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text or fallback


def _quick_search_path_token(value: object) -> str | None:
    """Preserve missing and literal display-fallback labels as distinct paths."""
    if value is None or pd.isna(value):
        return None
    return str(value).strip()


def _quick_search_number(value: object, *, column: str) -> tuple[str, str]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return _quick_search_text(value), ""
    if not np.isfinite(numeric):
        return "—", ""
    return format_number(numeric, column=column.casefold()), number_sign_class(numeric)


def build_quick_search_pivot(
    frame: pd.DataFrame,
    *,
    combine_udl: str,
    index_columns: list[str] | tuple[str, ...],
    total: int | None = None,
    revision: int | None = None,
) -> html.Div:
    """Render one bounded, selectable hierarchy returned by the backend catalog."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("search result frame must be a pandas DataFrame")
    selected_indexes = [str(value) for value in index_columns]
    if not selected_indexes:
        raise ValueError("at least one pivot index column is required")
    if len(selected_indexes) != len(set(selected_indexes)):
        raise ValueError("pivot index columns must be unique")

    metric_columns = (
        ("Risk", "Risk"),
        ("dRisk", "dRisk"),
        ("PL", "PL"),
        ("Open", "Open"),
        ("Current", "Current"),
        ("Move", "Move"),
    )
    required = [
        QUICK_SEARCH_HIERARCHY_DEPTH,
        *selected_indexes,
        *(column for column, _ in metric_columns),
    ]
    missing = [column for column in required if column not in frame.columns]
    if missing and not frame.empty:
        raise ValueError(f"pivot result is missing columns: {', '.join(missing)}")

    depths = pd.to_numeric(
        frame.get(
            QUICK_SEARCH_HIERARCHY_DEPTH,
            pd.Series(dtype="float64"),
        ),
        errors="coerce",
    )
    if not frame.empty:
        valid_depths = (
            depths.notna()
            & depths.ge(1)
            & depths.le(len(selected_indexes))
            & depths.mod(1).eq(0)
        )
        if not valid_depths.all():
            raise ValueError("pivot hierarchy contains an invalid depth")

    shown_leaves = int(depths.eq(len(selected_indexes)).sum())
    if (
        shown_leaves > QUICK_RISK_PIVOT_LIMIT
        or len(frame) > len(selected_indexes) * QUICK_RISK_PIVOT_LIMIT
    ):
        raise ValueError("pivot hierarchy exceeds the bounded UI contract")
    result_total = max(shown_leaves, int(total)) if total is not None else shown_leaves
    suffix = f" · snapshot {int(revision)}" if revision is not None else ""
    if frame.empty:
        return html.Div(
            [
                html.Div(
                    f"No current groups match '{str(combine_udl).strip()}'{suffix}.",
                    className="quick-search-empty",
                    role="status",
                    **{"aria-live": "polite"},
                )
            ],
            className="quick-search-result-set",
        )

    rows: list[html.Tr] = []
    emitted_paths: set[str] = set()
    for record in frame.to_dict("records"):
        depth = int(record[QUICK_SEARCH_HIERARCHY_DEPTH])
        index_dimension = selected_indexes[depth - 1]
        path_tokens = [
            _quick_search_path_token(record.get(index_column))
            for index_column in selected_indexes[:depth]
        ]
        path = json.dumps(path_tokens, ensure_ascii=False, separators=(",", ":"))
        parent_path = json.dumps(
            path_tokens[:-1],
            ensure_ascii=False,
            separators=(",", ":"),
        )

        if path in emitted_paths:
            raise ValueError("pivot hierarchy contains a duplicate path")
        if depth > 1 and parent_path not in emitted_paths:
            raise ValueError("pivot hierarchy child precedes its parent")
        emitted_paths.add(path)

        display_value = _quick_search_text(record.get(index_dimension))
        has_children = depth < len(selected_indexes)
        is_open = has_children and depth == 1
        if has_children:
            state = "Collapse" if is_open else "Expand"
            index_toggle: html.Button | html.Span = html.Button(
                "\u25be" if is_open else "\u203a",
                type="button",
                className="row-toggle quick-search-hierarchy-toggle",
                title=f"{state} {index_dimension}: {display_value}",
                **{
                    "aria-label": f"{state} {index_dimension}: {display_value}",
                    "aria-expanded": str(is_open).lower(),
                },
            )
        else:
            index_toggle = html.Span(
                "",
                className="quick-search-hierarchy-toggle-spacer",
                **{"aria-hidden": "true"},
            )

        cells: list[html.Th | html.Td] = [
            html.Th(
                [
                    index_toggle,
                    html.Span(
                        display_value,
                        className="row-label-text quick-search-hierarchy-label",
                    ),
                ],
                scope="row",
                className=(
                    "index-cell quick-search-pivot-index "
                    "quick-search-first-index quick-search-last-index "
                    "quick-search-hierarchy-index"
                ),
                style={"paddingLeft": f"{12 + (depth - 1) * 20}px"},
                title=f"{index_dimension}: {display_value}",
                **{
                    "data-metric": "index",
                    "data-copy-value": display_value,
                    "data-index-dimension": index_dimension,
                },
            )
        ]
        for metric_column, label in metric_columns:
            raw_value = record.get(metric_column)
            text_value, sign_class = _quick_search_number(
                raw_value, column=metric_column
            )
            try:
                numeric_value = float(raw_value)
                copy_value = str(numeric_value) if np.isfinite(numeric_value) else ""
            except (TypeError, ValueError):
                copy_value = ""
            cells.append(
                html.Td(
                    text_value,
                    className=(
                        "metric-cell quick-search-number "
                        f"{'quick-search-pl-column ' if metric_column == 'PL' else ''}"
                        f"{sign_class}"
                    ).strip(),
                    **{
                        "data-metric": metric_column,
                        "data-copy-value": copy_value,
                    },
                )
            )

        row_classes = [
            "quick-search-hierarchy-row",
            f"quick-search-hierarchy-depth-{depth}",
        ]
        if depth == 1:
            row_classes.append("quick-search-hierarchy-root")
        if not has_children:
            row_classes.append("quick-search-hierarchy-leaf")
        rows.append(
            html.Tr(
                cells,
                className=" ".join(row_classes),
                hidden=depth > 2,
                **{
                    "aria-level": str(depth),
                    "data-quick-search-depth": str(depth),
                    "data-quick-search-path": path,
                    "data-quick-search-parent-path": parent_path,
                    "data-quick-search-open": str(is_open).lower(),
                    "data-quick-search-label": display_value,
                    "data-quick-search-dimension": index_dimension,
                },
            )
        )

    # Compute totals from leaf rows only
    leaf_rows = [
        r
        for r in frame.to_dict("records")
        if r[QUICK_SEARCH_HIERARCHY_DEPTH] == len(selected_indexes)
    ]
    metric_summaries = {}
    for metric_column, label in metric_columns:
        values = []
        for record in leaf_rows:
            raw = record.get(metric_column)
            try:
                numeric = float(raw)
                if np.isfinite(numeric):
                    values.append(numeric)
            except (TypeError, ValueError):
                pass
        metric_summaries[metric_column] = sum(values) if values else 0.0

    if leaf_rows:
        total_cells: list[html.Th | html.Td] = [
            html.Th(
                html.Span(
                    "Total",
                    className="total-label quick-search-total-label",
                ),
                scope="col",
                className="index-cell quick-search-pivot-index quick-search-total-index",
                style={"fontWeight": "bold"},
            )
        ]
        for metric_column, label in metric_columns:
            total_value, sign_class = _quick_search_number(
                metric_summaries[metric_column], column=metric_column
            )
            total_cells.append(
                html.Td(
                    total_value,
                    className=(
                        "metric-cell quick-search-number quick-search-total-cell "
                        f"{'quick-search-pl-column ' if metric_column == 'PL' else ''}"
                        f"{sign_class}"
                    ).strip(),
                    style={"fontWeight": "bold"},
                    **{
                        "data-metric": metric_column,
                        "data-copy-value": str(metric_summaries[metric_column]),
                    },
                )
            )
        rows.append(
            html.Tr(
                total_cells,
                className="quick-search-total-row",
                **{
                    "data-quick-search-total": "true",
                },
            )
        )

    status = (
        f"Showing {shown_leaves:,} of {result_total:,} leaf groups "
        f"across {len(rows):,} hierarchy rows{suffix}"
    )
    index_header = html.Th(
        "Index",
        scope="col",
        className=(
            "index-header quick-search-pivot-index-header "
            "quick-search-first-index quick-search-last-index"
        ),
        title="Hierarchy: " + " · ".join(selected_indexes),
        **{"data-metric": "index"},
    )
    metric_headers = [
        html.Th(
            label,
            scope="col",
            className=(
                "metric-header quick-search-pivot-metric-header "
                f"{'quick-search-pl-column' if column == 'PL' else ''}"
            ),
            **{"data-metric": column},
        )
        for column, label in metric_columns
    ]
    return html.Div(
        [
            html.Div(
                status,
                className="quick-search-result-count",
                role="status",
                **{"aria-live": "polite", "aria-atomic": "true"},
            ),
            html.Div(
                [
                    html.Div(
                        "",
                        className="selection-summary",
                        **{"aria-live": "polite"},
                    ),
                    html.Table(
                        [
                            html.Caption(
                                "Current Risk, PL and Market hierarchy ordered by "
                                f"{' · '.join(selected_indexes)}",
                                className="sr-only",
                            ),
                            html.Thead(html.Tr([index_header, *metric_headers])),
                            html.Tbody(rows),
                        ],
                        className="cell-selection-table quick-search-pivot-table",
                        role="treegrid",
                        **{
                            "aria-label": "Current combined Quick Search hierarchy",
                            "data-quick-search-level-count": str(len(selected_indexes)),
                        },
                    ),
                ],
                className="risk-table-wrap quick-search-pivot-table-wrap",
                tabIndex=0,
                **{"aria-label": "Scrollable current combined hierarchy"},
            ),
        ],
        className="quick-search-result-set",
        **({"data-snapshot-revision": str(revision)} if revision is not None else {}),
    )


# ---------------------------------------------------------------------------
# Heatmap companion table helpers (read.md strategy)
# ---------------------------------------------------------------------------


def _compact_tenor_label(
    value: object,
    *,
    max_chars: int = 18,
) -> str:
    """Return bounded visible text without changing the canonical value."""
    text = " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}…"


def _wrapped_plotly_label(
    value: object,
    *,
    line_width: int = 24,
    max_lines: int = 3,
) -> str:
    """Return safe Plotly hover HTML with a strict line and length bound."""
    text = " ".join(str(value).split()) or "—"
    lines = wrap(
        text,
        width=line_width,
        break_long_words=True,
        break_on_hyphens=False,
        max_lines=max_lines,
        placeholder="…",
    )
    return "<br>".join(html_escape(line) for line in lines)


def _surface_hover_data(
    pivot: pd.DataFrame,
    metric: str,
) -> list[list[list[str]]]:
    """Build wrapped axis labels and the matrix-formatted value per cell."""
    return [
        [
            [
                _wrapped_plotly_label(swap_tenor),
                _wrapped_plotly_label(option_tenor),
                (
                    ""
                    if pd.isna(pivot.iat[row_number, column_number])
                    else format_number(
                        pivot.iat[row_number, column_number],
                        column=metric,
                    )
                ),
            ]
            for column_number, swap_tenor in enumerate(pivot.columns)
        ]
        for row_number, option_tenor in enumerate(pivot.index)
    ]


def _tenor_surface_pivot(
    detail: pd.DataFrame,
    metric: str,
) -> tuple[pd.DataFrame, bool, bool]:
    """Build an ordered surface pivot from long-form detail rows."""
    surface_columns = ["tenor option", "tenor swap", metric]
    surface_columns.extend(
        column
        for column in ("tenor option order", "tenor swap order")
        if column in detail
    )
    surface = detail[surface_columns].copy()

    for column in ("tenor option", "tenor swap"):
        surface[column] = surface[column].astype("string").str.strip()
        surface = surface.loc[_meaningful_tenor_mask(surface[column])]

    option_tenors, ambiguous_option_order = tenor_axis_order(
        surface,
        "tenor option",
        "tenor option order",
    )
    swap_tenors, ambiguous_swap_order = tenor_axis_order(
        surface,
        "tenor swap",
        "tenor swap order",
    )

    grouped = surface.groupby(
        ["tenor option", "tenor swap"],
        dropna=False,
    )[metric]
    values = (
        grouped.mean()
        if metric in {"move", "open", "current"}
        else grouped.sum(min_count=1)
    )
    pivot = values.unstack("tenor swap").reindex(
        index=reversed(option_tenors),
        columns=swap_tenors,
    )
    return pivot, ambiguous_option_order, ambiguous_swap_order


def build_surface_matrix_table(
    pivot: pd.DataFrame,
    metric: str,
    *,
    metric_label: str | None = None,
    row_axis: str = "Tenor Option",
    column_axis: str = "Tenor Swap",
    wrapper_class: str = "detail-table-wrap tenor-matrix-wrap",
) -> html.Div:
    """Render one heatmap pivot as an accessible HTML matrix.

    The row axis (Tenor Option) is presented in reverse order to match the
    heatmap orientation where the shortest option tenor appears at the top.
    """
    display_metric = metric_label or metric_title(metric)
    # The pivot index is in reversed order (shortest tenor first), but Plotly
    # places the first category at the bottom of the y-axis. The HTML table
    # places the first <tr> at the top, so we reverse again to match the
    # visual heatmap orientation (longest tenor at top).
    option_order = list(reversed(pivot.index))
    ordered_pivot = pivot.loc[option_order]

    headers = [
        html.Th(
            f"{row_axis} / {column_axis}",
            scope="col",
            className="tenor-matrix-corner",
        ),
        *[
            html.Th(
                _compact_tenor_label(label),
                scope="col",
                className="detail-tenor tenor-matrix-column-header",
                title=str(label),
                **{"data-copy-value": str(label)},
            )
            for label in pivot.columns
        ],
    ]

    rows = []
    for row_label, row_values in ordered_pivot.iterrows():
        cells = [
            html.Th(
                _compact_tenor_label(row_label),
                scope="row",
                className="detail-tenor tenor-matrix-row-header",
                title=str(row_label),
                **{"data-copy-value": str(row_label)},
            )
        ]
        for value in row_values:
            if pd.isna(value):
                cells.append(
                    html.Td(
                        "",
                        className=("detail-number tenor-matrix-empty"),
                        **{"data-copy-value": ""},
                    )
                )
                continue

            cells.append(
                html.Td(
                    format_number(value, column=metric),
                    className=(f"detail-number {number_sign_class(value)}"),
                    **{"data-copy-value": str(value)},
                )
            )
        rows.append(html.Tr(cells))

    table = html.Table(
        [
            html.Caption(
                f"{display_metric} tenor matrix",
                className="sr-only",
            ),
            html.Thead(html.Tr(headers)),
            html.Tbody(rows),
        ],
        className="detail-table tenor-matrix-table",
    )
    return html.Div(
        table,
        className=wrapper_class,
        tabIndex=0,
        role="region",
        **{
            "aria-label": (
                f"{display_metric} matrix. Rows are {row_axis}; "
                f"columns are {column_axis}."
            )
        },
    )


def _meaningful_tenor_mask(values: pd.Series) -> pd.Series:
    labels = values.astype("string").str.strip().fillna("")
    return ~labels.str.casefold().isin(_ABSENT_TENOR_LABELS)


def detail_tenor_partitions(detail: pd.DataFrame) -> dict[str, pd.Series]:
    """Return mutually exclusive tenor-shape masks for one detail frame."""
    index = detail.index
    swap_values = detail.get(
        "tenor swap", pd.Series(pd.NA, index=index, dtype="string")
    )
    option_values = detail.get(
        "tenor option", pd.Series(pd.NA, index=index, dtype="string")
    )
    swap = _meaningful_tenor_mask(swap_values).reindex(index, fill_value=False)
    option = _meaningful_tenor_mask(option_values).reindex(index, fill_value=False)
    return {
        "paired": swap & option,
        "swap_only": swap & ~option,
        "option_only": ~swap & option,
        "no_tenor": ~swap & ~option,
    }


def build_quick_market_search() -> html.Details:
    """Build a native collapsible full-MarketBook search section."""

    return html.Details(
        [
            html.Summary(
                [
                    html.Span(
                        "Quick Market Search",
                        className="quick-search-pivot-title",
                    ),
                    html.Span(
                        "Full market tenor structure",
                        className="quick-search-pivot-values",
                    ),
                ],
                id="quick-market-summary",
                n_clicks=0,
                className="quick-search-pivot-summary",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H2("Market curves and surfaces"),
                                    html.P(
                                        "Select Risk Type, Risk Greek and Underlying. "
                                        "This reads the complete saved MarketBook, including tenors with no Risk row."
                                    ),
                                ],
                                className="quick-search-heading-copy",
                            ),
                            html.Div(
                                [
                                    html.Label(
                                        "Market identity",
                                        htmlFor="quick-market-combine-udl",
                                    ),
                                    dcc.Dropdown(
                                        id="quick-market-combine-udl",
                                        options=[],
                                        value=None,
                                        clearable=False,
                                        searchable=True,
                                        placeholder="Select Risk Type · Risk Greek · Underlying",
                                        className="quick-search-combine-dropdown",
                                    ),
                                ],
                                className="quick-search-selector-control",
                            ),
                        ],
                        className="quick-search-heading",
                    ),
                    html.Div(
                        [
                            html.Label("Chart", htmlFor="quick-market-view"),
                            dcc.RadioItems(
                                id="quick-market-view",
                                options=[
                                    {"label": "Auto", "value": "auto"},
                                    {"label": "Tenor Swap line", "value": "swap"},
                                    {"label": "Tenor Option line", "value": "option"},
                                    {"label": "Surface", "value": "surface"},
                                ],
                                value="auto",
                                inline=True,
                                className="detail-tenor-view-radio",
                            ),
                        ],
                        className="quick-search-dimension-control",
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Heatmap",
                                htmlFor="quick-market-surface-metric",
                            ),
                            dcc.RadioItems(
                                id="quick-market-surface-metric",
                                options=[
                                    {"label": "Open", "value": "open"},
                                    {
                                        "label": "Market Status",
                                        "value": "current",
                                    },
                                    {"label": "Move", "value": "move"},
                                ],
                                value="current",
                                inline=True,
                                className="detail-tenor-view-radio",
                            ),
                        ],
                        id="quick-market-surface-metric-control",
                        className="quick-search-dimension-control",
                        hidden=True,
                    ),
                    dcc.Loading(
                        html.Div(
                            "Open this section to read the current MarketBook.",
                            id="quick-market-results",
                            className="quick-search-results quick-search-hint",
                        ),
                        type="dot",
                        delay_show=160,
                    ),
                ],
                className="quick-search-pivot-body",
            ),
        ],
        id="quick-market-details",
        open=False,
        className="quick-search-shell quick-search-pivot-details",
        **{"aria-label": "Quick Market Search"},
    )


def _market_axis(frame: pd.DataFrame, column: str) -> bool:
    return column in frame and _meaningful_tenor_mask(frame[column]).any()


def _market_surface_metric_options(
    market_status: str,
) -> list[dict[str, str]]:
    """Label the current quote with the resolver's exact live/OFFICIAL status."""
    return [
        {"label": "Open", "value": "open"},
        {"label": market_status, "value": "current"},
        {"label": "Move", "value": "move"},
    ]


def _ordered_market_axis(frame: pd.DataFrame, column: str) -> list[str]:
    """Return one market axis in its connector-supplied rank order."""

    if column not in _MARKET_AXIS_ORDER_COLUMNS:
        raise ValueError(f"Unsupported market tenor axis: {column}")
    ordered, _ambiguous = tenor_axis_order(
        frame,
        column,
        _MARKET_AXIS_ORDER_COLUMNS[column],
    )
    return ordered


def _sort_market_rows(frame: pd.DataFrame, axes: list[str]) -> pd.DataFrame:
    """Sort a MarketBook view by authoritative ranks without mutating it."""

    if frame.empty or not axes:
        return frame.copy()
    ordered = frame.copy()
    rank_columns: list[str] = []
    for position, axis in enumerate(axes):
        rank_column = f"__cube_market_axis_{position}__"
        axis_order = _ordered_market_axis(ordered, axis)
        ranks = {label: rank for rank, label in enumerate(axis_order)}
        ordered[rank_column] = ordered[axis].astype("string").str.strip().map(ranks)
        # Missing/absent coordinates remain visible after all ranked labels.
        ordered[rank_column] = ordered[rank_column].fillna(len(ranks))
        rank_columns.append(rank_column)
    return ordered.sort_values(rank_columns, kind="stable").drop(columns=rank_columns)


def _market_line_chart(
    frame: pd.DataFrame,
    *,
    axis: str,
    market_status: str,
) -> dcc.Graph:
    curve = frame.loc[_meaningful_tenor_mask(frame[axis])].copy()
    axis_order = _ordered_market_axis(curve, axis)
    curve = curve.groupby(axis, as_index=False, sort=False)[["Open", "Current"]].mean()
    # Keep the chart aligned with the paired-quote aggregation contract used by
    # Quick Market: Move is always the displayed Current quote minus Open.
    curve["Move"] = curve["Current"] - curve["Open"]
    curve[axis] = curve[axis].astype(str)
    curve["__cube_market_axis_order__"] = curve[axis].map(
        {label: rank for rank, label in enumerate(axis_order)}
    )
    curve = curve.sort_values("__cube_market_axis_order__", kind="stable").drop(
        columns="__cube_market_axis_order__"
    )
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    # Add bar chart first so it renders behind the line traces
    figure.add_trace(
        go.Bar(
            name="Market Move",
            x=curve[axis],
            y=curve["Move"],
            marker_color="#D88989",
            marker_line_color="rgba(0,0,0,0.3)",
            marker_line_width=1,
            opacity=0.4,
            hoverlabel={"font": {"size": 10}},
        ),
        secondary_y=True,
    )
    # Add line traces on top
    figure.add_trace(
        go.Scatter(
            name="Open",
            x=curve[axis],
            y=curve["Open"],
            mode="lines+markers",
            line={"color": "#7FAD7F", "width": 3},
            hoverlabel={"font": {"size": 10}},
        ),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            name=market_status,
            x=curve[axis],
            y=curve["Current"],
            mode="lines+markers",
            line={"color": "#79BE89", "width": 3},
            hoverlabel={"font": {"size": 10}},
        ),
        secondary_y=False,
    )
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=300,
        margin={"l": 54, "r": 60, "t": 30, "b": 52},
        legend={"orientation": "h", "y": 1.12},
        xaxis={
            "title": axis,
            "type": "category",
            "categoryorder": "array",
            "categoryarray": axis_order,
        },
        yaxis={"title": f"Open / {market_status}"},
        yaxis2={
            "title": "Market Move",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        },
        uniformtext={"mode": "hide", "minsize": 10},
    )
    return dcc.Graph(figure=figure, config={"displayModeBar": False})


def _market_surface_chart(
    frame: pd.DataFrame,
    *,
    market_status: str,
    metric: str,
) -> tuple[dcc.Graph, pd.DataFrame, str, str]:
    """Build the Quick Market surface heatmap and return its pivot matrix."""
    surface = frame.loc[
        _meaningful_tenor_mask(frame["Tenor Swap"])
        & _meaningful_tenor_mask(frame["Tenor Option"])
    ].copy()
    surface["Move"] = surface["Current"] - surface["Open"]
    swap = _ordered_market_axis(surface, "Tenor Swap")
    option_ordered = _ordered_market_axis(surface, "Tenor Option")
    option_reversed = list(reversed(option_ordered))
    metric_settings = {
        "open": (
            "Open",
            "Open",
            [[0.0, "#F7FBFF"], [1.0, "#9CC7EB"]],
        ),
        "current": (
            "Current",
            market_status,
            [[0.0, "#F4FBF5"], [1.0, "#9ED5AA"]],
        ),
        "move": (
            "Move",
            "Move",
            [
                [0.0, "#D98282"],
                [0.25, "#F2BABA"],
                [0.5, "#FFFDF6"],
                [0.75, "#BFE4C7"],
                [1.0, "#79BE89"],
            ],
        ),
    }
    selected_metric = str(metric or "current").casefold()
    if selected_metric not in metric_settings:
        selected_metric = "current"
    column, label, colors = metric_settings[selected_metric]
    values = surface.pivot_table(
        index="Tenor Option",
        columns="Tenor Swap",
        values=column,
        aggfunc="mean",
        sort=False,
    ).reindex(index=option_reversed, columns=swap)
    color_bounds: dict[str, float] = {}
    if selected_metric == "move":
        finite_values = np.asarray(values.values, dtype=float)
        finite_values = finite_values[np.isfinite(finite_values)]
        max_abs = float(np.max(np.abs(finite_values))) if finite_values.size else 0.0
        color_bounds["zmid"] = 0.0
        if max_abs > 0:
            color_bounds.update(zmin=-max_abs, zmax=max_abs)

    hover_data = _surface_hover_data(values, column.casefold())
    trace = go.Heatmap(
        z=values.values,
        x=swap,
        y=option_reversed,
        customdata=hover_data,
        colorscale=colors,
        hoverongaps=False,
        xgap=1,
        ygap=1,
        colorbar={
            "title": {"text": label},
            "thickness": 12,
            "len": 0.78,
            "xpad": 6,
        },
        hovertemplate=(
            "<b>Tenor Swap</b>: %{customdata[0]}<br>"
            "<b>Tenor Option</b>: %{customdata[1]}<br>"
            f"<b>{label}</b>: %{{customdata[2]}}"
            "<extra></extra>"
        ),
        hoverlabel={"font": {"size": 11}},
        **color_bounds,
    )

    figure = go.Figure(data=[trace])
    figure.update_xaxes(
        title={"text": "Tenor Swap", "standoff": 10},
        type="category",
        categoryorder="array",
        categoryarray=list(values.columns),
        tickmode="array",
        tickvals=list(values.columns),
        ticktext=[_compact_tenor_label(value) for value in values.columns],
        side="top",
        ticklabelposition="outside top",
        ticks="outside",
        ticklen=5,
        automargin=True,
        constrain="domain",
    )
    figure.update_yaxes(
        title_text="Tenor Option",
        type="category",
        categoryorder="array",
        categoryarray=option_reversed,
        tickmode="array",
        tickvals=option_reversed,
        ticktext=[_compact_tenor_label(value) for value in option_reversed],
        automargin=True,
        constrain="domain",
    )
    figure.update_layout(
        autosize=True,
        hovermode="closest",
        hoverlabel={
            "align": "left",
            "font": {"size": 11},
        },
        margin={"l": 58, "r": 46, "t": 90, "b": 44},
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )

    graph = dcc.Graph(
        figure=figure,
        responsive=True,
        className="tenor-surface-graph",
        config={
            "displayModeBar": False,
            "responsive": True,
        },
        style={"height": "800px"},
    )
    return (
        graph,
        values,
        column.casefold(),
        label,
    )


def build_quick_market_result(
    frame: pd.DataFrame,
    *,
    combine_udl: str,
    requested_view: str,
    surface_metric: str,
    market_status: str,
    revision: int,
) -> tuple[
    html.Div,
    str,
    list[dict[str, object]],
    list[dict[str, str]],
]:
    """Render a full-market table and status-aware curve/surface."""

    if frame.empty:
        return (
            html.Div(
                f"No MarketBook rows match '{combine_udl}'.",
                className="quick-search-empty",
            ),
            "auto",
            [{"label": "Auto", "value": "auto"}],
            _market_surface_metric_options(market_status),
        )

    available = {
        "swap": _market_axis(frame, "Tenor Swap"),
        "option": _market_axis(frame, "Tenor Option"),
    }
    available["surface"] = available["swap"] and available["option"]
    automatic = (
        "surface"
        if available["surface"]
        else "swap"
        if available["swap"]
        else "option"
        if available["option"]
        else "auto"
    )
    selected = requested_view if available.get(requested_view, False) else automatic
    labels = {
        "auto": "Auto",
        "swap": "Tenor Swap line",
        "option": "Tenor Option line",
        "surface": "Surface",
    }
    options = [
        {
            "label": label,
            "value": value,
            "disabled": value != "auto" and not available.get(value, False),
        }
        for value, label in labels.items()
    ]

    chart = None
    matrix = None
    matrix_metric = None
    matrix_label = None
    if selected == "surface":
        chart, matrix, matrix_metric, matrix_label = _market_surface_chart(
            frame,
            market_status=market_status,
            metric=surface_metric,
        )
        table = html.Div(
            build_surface_matrix_table(
                matrix,
                matrix_metric,
                metric_label=matrix_label,
                wrapper_class=(
                    "risk-table-wrap quick-search-pivot-table-wrap tenor-matrix-wrap"
                ),
            ),
            className="tenor-surface-pair",
        )
    elif selected in {"swap", "option"}:
        axis = {
            "swap": "Tenor Swap",
            "option": "Tenor Option",
        }[selected]
        chart = _market_line_chart(frame, axis=axis, market_status=market_status)
        axes = [
            column
            for column in ("Tenor Swap", "Tenor Option")
            if _market_axis(frame, column)
        ]
        display_frame = _sort_market_rows(frame, axes)
        columns = [*axes, "Open", "Current", "Move"]
        header = [
            html.Th(
                market_status if column == "Current" else column,
                className="index-header" if column in axes else "metric-header",
            )
            for column in columns
        ]
        body = []
        for record in display_frame.to_dict("records"):
            cells = []
            for column in columns:
                value = record.get(column)
                if column in {"Open", "Current", "Move"}:
                    text, sign = _quick_search_number(value, column=column)
                    cells.append(
                        html.Td(
                            text,
                            className=f"metric-cell {sign}",
                            **{"data-copy-value": "" if pd.isna(value) else str(value)},
                        )
                    )
                else:
                    cells.append(
                        html.Th(
                            _quick_search_text(value),
                            scope="row",
                            className="index-cell",
                            **{
                                "data-copy-value": _quick_search_text(
                                    value, fallback=""
                                )
                            },
                        )
                    )
            body.append(html.Tr(cells))
        table = html.Div(
            html.Table(
                [html.Thead(html.Tr(header)), html.Tbody(body)],
                className="cell-selection-table quick-search-pivot-table",
            ),
            className="risk-table-wrap quick-search-pivot-table-wrap",
            tabIndex=0,
        )
    else:
        axes = [
            column
            for column in ("Tenor Swap", "Tenor Option")
            if _market_axis(frame, column)
        ]
        display_frame = _sort_market_rows(frame, axes)
        columns = [*axes, "Open", "Current", "Move"]
        header = [
            html.Th(
                market_status if column == "Current" else column,
                className="index-header" if column in axes else "metric-header",
            )
            for column in columns
        ]
        body = []
        for record in display_frame.to_dict("records"):
            cells = []
            for column in columns:
                value = record.get(column)
                if column in {"Open", "Current", "Move"}:
                    text, sign = _quick_search_number(value, column=column)
                    cells.append(
                        html.Td(
                            text,
                            className=f"metric-cell {sign}",
                            **{"data-copy-value": "" if pd.isna(value) else str(value)},
                        )
                    )
                else:
                    cells.append(
                        html.Th(
                            _quick_search_text(value),
                            scope="row",
                            className="index-cell",
                            **{
                                "data-copy-value": _quick_search_text(
                                    value, fallback=""
                                )
                            },
                        )
                    )
            body.append(html.Tr(cells))
        table = html.Div(
            html.Table(
                [html.Thead(html.Tr(header)), html.Tbody(body)],
                className="cell-selection-table quick-search-pivot-table",
            ),
            className="risk-table-wrap quick-search-pivot-table-wrap",
            tabIndex=0,
        )

    result = html.Div(
        [
            html.Div(
                f"{len(frame):,} full-market rows · {market_status} · snapshot {revision}",
                className="quick-search-result-count",
            ),
            *([chart] if chart is not None else []),
            table,
        ],
        className="quick-search-result-set",
    )
    return (
        result,
        selected,
        options,
        _market_surface_metric_options(market_status),
    )


def detail_tenor_view_state(
    detail: pd.DataFrame,
    requested_view: str | None,
) -> tuple[list[dict[str, object]], str]:
    """Return fixed dropdown options and a valid requested tenor view."""
    partitions = detail_tenor_partitions(detail)
    available = {
        "auto": True,
        "swap": bool(partitions["paired"].any() or partitions["swap_only"].any()),
        "option": bool(partitions["paired"].any() or partitions["option_only"].any()),
        "surface": bool(partitions["paired"].any()),
    }
    options = [
        {
            "label": label,
            "value": value,
            "disabled": not available[value],
        }
        for value, label in DETAIL_TENOR_VIEW_LABELS.items()
    ]
    requested = str(requested_view or "auto")
    resolved = requested if available.get(requested, False) else "auto"
    return options, resolved


def metric_class(column: str, expanded_metrics: list[str] | None = None) -> str:
    classes = ["metric-cell"]
    expanded = set(expanded_metrics or [])
    if column == "pl" or column.startswith("pl "):
        classes.append("pl-cell")
    if column == "pl":
        classes.append("pl-block-left")
        if "pl" not in expanded:
            classes.append("pl-block-right")
    if column == "pl hedges":
        classes.append("pl-block-right")
    if column.endswith("expo"):
        classes.extend(["metric-child", "metric-exposure"])
    if column.endswith("hedges"):
        classes.extend(["metric-child", "metric-hedges"])
    if column == "move":
        classes.append("market-block-left")
        if "move" not in expanded:
            classes.append("market-block-right")
    if column in {"open", "current"}:
        classes.extend(["metric-child", "market-child"])
    if column == "current":
        classes.append("market-block-right")
    return " ".join(classes)


def metric_header(column: str, expanded_metrics: list[str]) -> html.Th:
    if column in METRIC_BREAKDOWNS:
        expanded = column in set(expanded_metrics or [])
        breakdown_names = " and ".join(
            metric_title(value) for value in METRIC_BREAKDOWNS[column]
        )
        return html.Th(
            html.Button(
                f"{'Hide' if expanded else '▾'} {metric_title(column)}",
                type="button",
                className="metric-header-button",
                title=f"{'Hide' if expanded else 'Show'} {breakdown_names}",
                **{
                    "data-risk-metric": column,
                    "aria-label": f"{'Hide' if expanded else 'Show'} {breakdown_names}",
                    "aria-expanded": str(expanded).lower(),
                },
            ),
            className=f"metric-header {'pl-header' if column == 'pl' else ''} {metric_class(column, expanded_metrics)}",
            scope="col",
            **{"data-metric": column},
        )
    display = metric_title(column)
    return html.Th(
        display,
        className=f"metric-header {metric_class(column, expanded_metrics)}",
        scope="col",
        **{"data-metric": column},
    )


def build_columns(expanded_metrics: list[str] | None) -> list[str]:
    expanded = set(expanded_metrics or [])
    columns: list[str] = []
    for metric in GRID_METRIC_COLUMNS:
        columns.append(metric)
        if metric in expanded and metric in METRIC_BREAKDOWNS:
            columns.extend(METRIC_BREAKDOWNS[metric])
    return columns


def build_tree_rows(
    frame: pd.DataFrame,
    columns: list[str],
    open_rows: list[str] | None,
    expanded_metrics: list[str] | None,
    level: int = 0,
    depth: int = 0,
    context: dict[str, str] | None = None,
    groups: list[str] | None = None,
    cell_builder: Callable[[pd.DataFrame, dict[str, str]], list[html.Td]] | None = None,
    toggle_type: str = "row-toggle",
    cell_type: str = "risk-cell",
    aggregation_index: HierarchyAggregationIndex | None = None,
    delegated_actions: bool = False,
    underlying_sort_metric: str | None = None,
) -> list[html.Tr]:
    context = context or {}
    groups = BASE_GROUPS if groups is None else groups
    open_set = set(open_rows or [])
    level = visible_tree_level(frame, level, context, groups)
    group_column = groups[level] if level < len(groups) else None
    rows: list[html.Tr] = []

    if group_column is None:
        return rows

    for value in ordered_unique(
        frame,
        group_column,
        underlying_sort_metric=underlying_sort_metric,
    ):
        next_context = {**context, group_column: value}
        scoped = tree_scope(frame, group_column, value)
        if scoped.empty:
            continue
        key = row_key(next_context)
        next_level = visible_tree_level(scoped, level + 1, next_context, groups)
        can_expand = next_level < len(groups)
        is_open = key in open_set
        metrics = (
            (
                aggregation_index.aggregate(
                    scoped,
                    include_market=should_show_sum("move", next_context),
                )
                if aggregation_index is not None
                else aggregate_values(
                    scoped,
                    include_market=should_show_sum("move", next_context),
                )
            )
            if cell_builder is None
            else None
        )
        indent = 14 + depth * 18
        toggle_props = {
            "type": "button",
            "className": "row-toggle",
            "title": ("Expand" if not is_open else "Collapse") + f" {value}",
            "disabled": not can_expand,
            "aria-label": ("Expand" if not is_open else "Collapse") + f" {value}",
            "aria-expanded": str(is_open).lower() if can_expand else "false",
        }
        if not delegated_actions:
            toggle_props.update(
                {
                    "id": {"type": toggle_type, "key": key},
                    "n_clicks": 0,
                }
            )
        label = html.Button(
            ("−" if is_open else "▸") if can_expand else "",
            **toggle_props,
        )
        index_children = [
            label,
            html.Span(str(value), className="row-label-text"),
        ]
        if group_column == "display bucket" and value != "Other":
            reasons = scoped["promotion reason"].dropna().astype(str)
            reason = next((item for item in reasons if item), "Top risk")
            index_children.append(html.Span(reason, className="promotion-badge"))
        cells = [
            html.Th(
                index_children,
                className=f"index-cell level-{level}",
                style={"paddingLeft": f"{indent}px"},
                scope="row",
                **{"data-metric": "index", "data-copy-value": str(value)},
            )
        ]
        if cell_builder is not None:
            cells.extend(cell_builder(scoped, next_context))
        else:
            for column in columns:
                metric_value = metrics[column]
                display_value = display_metric(
                    metrics,
                    column,
                    next_context,
                )
                cell_class = f"{metric_class(column, expanded_metrics)} {number_sign_class(metric_value)}"
                if not display_value:
                    cells.append(
                        html.Td(
                            "",
                            className=f"{cell_class} metric-cell-inert",
                            **{"data-metric": column},
                        )
                    )
                    continue
                cells.append(
                    html.Td(
                        html.Button(
                            display_value,
                            type="button",
                            className="metric-cell-button",
                            title=f"Open tenor detail for {metric_title(column)}",
                            **(
                                {
                                    "data-risk-metric": column,
                                    "aria-label": f"Open {metric_title(column)} detail for {value}: {display_value}",
                                }
                                if delegated_actions
                                else {
                                    "id": {
                                        "type": cell_type,
                                        "key": key,
                                        "metric": column,
                                    },
                                    "n_clicks": 0,
                                    "aria-label": f"Open {metric_title(column)} detail for {value}: {display_value}",
                                }
                            ),
                        ),
                        className=cell_class,
                        **{"data-metric": column},
                    )
                )
        row_kind = group_column.replace(" ", "-").replace("(", "").replace(")", "")
        row_classes = [
            "group-row",
            f"group-level-{depth}",
            f"group-kind-{row_kind}",
        ]
        if group_column in {"label", "risk type", "risk greek"}:
            row_classes.append("hierarchy-total-row")
        if group_column == "display bucket" and value != "Other":
            row_classes.append("promoted-underlying-row")
        row_props = {"data-risk-key": key} if delegated_actions else {}
        rows.append(html.Tr(cells, className=" ".join(row_classes), **row_props))
        if can_expand and is_open:
            rows.extend(
                build_tree_rows(
                    scoped,
                    columns,
                    open_rows,
                    expanded_metrics,
                    next_level,
                    depth + 1,
                    next_context,
                    groups,
                    cell_builder,
                    toggle_type,
                    cell_type,
                    aggregation_index,
                    delegated_actions,
                    underlying_sort_metric=underlying_sort_metric,
                )
            )
    return rows


def build_risk_table(
    frame: pd.DataFrame,
    expanded_metrics: list[str] | None,
    open_rows: list[str] | None,
    *,
    dimension: str = "activity",
    toggle_type: str = "row-toggle",
    cell_type: str = "risk-cell",
    index_label: str = "Index",
    view_token: str | None = None,
    promotion_enabled: bool = True,
    region_enabled: bool = True,
    underlying_sort_metric: str | None = None,
) -> html.Div:
    if frame.empty:
        return html.Div(
            [
                html.Strong("No matching risk rows"),
                html.Span("Try clearing one or more filters."),
            ],
            className="empty-state",
            role="status",
        )
    columns = build_columns(expanded_metrics)
    aggregation_index = HierarchyAggregationIndex(frame)
    frame = aggregation_index.frame
    total_metrics = aggregation_index.aggregate(frame, include_market=False)
    total_cells = [
        html.Th(
            html.Span("TOTAL", className="row-label-text"),
            className="index-cell total-index",
            scope="row",
            **{"data-metric": "index", "data-copy-value": "TOTAL"},
        )
    ]
    for column in columns:
        metric_value = total_metrics[column]
        display_value = display_metric(total_metrics, column, {})
        cell_class = f"{metric_class(column, expanded_metrics)} {number_sign_class(metric_value)}"
        if not display_value:
            total_cells.append(
                html.Td(
                    "",
                    className=f"{cell_class} metric-cell-inert",
                    **{"data-metric": column},
                )
            )
            continue
        total_cells.append(
            html.Td(
                html.Button(
                    display_value,
                    type="button",
                    className="metric-cell-button",
                    title=f"Open tenor detail for {metric_title(column)}",
                    **{
                        "data-risk-metric": column,
                        "aria-label": f"Open total {metric_title(column)} detail: {display_value}",
                    },
                ),
                className=cell_class,
                **{"data-metric": column},
            )
        )
    body_rows = [
        html.Tr(
            total_cells,
            className="total-row",
            **{"data-risk-key": ""},
        )
    ]
    if not frame.empty:
        body_rows.extend(
            build_tree_rows(
                frame,
                columns,
                open_rows,
                expanded_metrics,
                groups=_active_groups_for_frame(
                    frame,
                    promotion_enabled,
                    region_enabled,
                ),
                toggle_type=toggle_type,
                cell_type=cell_type,
                aggregation_index=aggregation_index,
                delegated_actions=True,
                underlying_sort_metric=underlying_sort_metric,
            )
        )
    header = html.Thead(
        html.Tr(
            [
                html.Th(
                    index_label,
                    className="index-header",
                    scope="col",
                    **{"data-metric": "index"},
                )
            ]
            + [metric_header(column, expanded_metrics or []) for column in columns]
        )
    )
    return html.Div(
        [
            html.Div("", className="selection-summary", **{"aria-live": "polite"}),
            html.Table(
                [
                    html.Caption(
                        f"{index_label} hierarchy and risk metrics",
                        className="sr-only",
                    ),
                    header,
                    html.Tbody(body_rows),
                ],
                className="risk-table",
            ),
        ],
        className="risk-table-wrap",
        **(
            {
                "data-risk-view-token": view_token,
                "data-risk-open-rows": json.dumps(
                    sorted(open_rows or []), separators=(",", ":")
                ),
            }
            if view_token
            else {}
        ),
    )


def build_alt_risk_table(
    frame: pd.DataFrame,
    metric: str,
    open_rows: list[str] | None,
    dimension: str = "activity",
    index_label: str = "Index",
    view_token: str | None = None,
    promotion_enabled: bool = True,
    region_enabled: bool = True,
    underlying_sort_metric: str | None = None,
) -> html.Div:
    """Build the indexed hierarchy with the selected dimension pivoted into columns."""
    if frame.empty:
        return html.Div(
            [
                html.Strong("No matching risk rows"),
                html.Span("Try clearing one or more filters."),
            ],
            className="empty-state",
            role="status",
        )
    selected_metric = metric if metric in METRIC_COLUMNS else "risk"
    dimension_column = selected_dimension(dimension)
    dimension_values = (
        ordered_unique(frame, dimension_column) if not frame.empty else []
    )

    def dimension_cells(scoped: pd.DataFrame, context: dict[str, str]) -> list[html.Td]:
        by_dimension = (
            scoped.groupby(dimension_column)[selected_metric]
            .sum(min_count=1)
            .reindex(dimension_values)
        )
        values = [
            (dimension_value, float(by_dimension[dimension_value]))
            for dimension_value in dimension_values
        ]
        values.append(("Total", float(scoped[selected_metric].sum(min_count=1))))
        show_value = should_show_sum(selected_metric, context)
        cells: list[html.Td] = []
        for dimension_value, value in values:
            cell_context = (
                context
                if dimension_value == "Total"
                else {**context, dimension_column: dimension_value}
            )
            total_class = " total-column" if dimension_value == "Total" else ""
            display_value = format_number(value) if show_value else ""
            if not display_value:
                cells.append(
                    html.Td(
                        "",
                        className=f"metric-cell alt-dimension-cell metric-cell-inert{total_class} {number_sign_class(value)}",
                        **{"data-metric": f"{selected_metric}:{dimension_value}"},
                    )
                )
                continue
            cells.append(
                html.Td(
                    html.Button(
                        display_value,
                        type="button",
                        className="metric-cell-button",
                        title=f"Open {dimension_value} detail for {metric_title(selected_metric)}",
                        **{
                            "data-risk-key": (
                                row_key(cell_context) if cell_context else ""
                            ),
                            "data-risk-metric": selected_metric,
                            "aria-label": f"Open {dimension_value} {metric_title(selected_metric)} detail: {display_value}",
                        },
                    ),
                    className=f"metric-cell alt-dimension-cell{total_class} {number_sign_class(value)}",
                    **{"data-metric": f"{selected_metric}:{dimension_value}"},
                )
            )
        return cells

    total_cells = [
        html.Th(
            html.Span("TOTAL", className="row-label-text"),
            className="index-cell total-index",
            scope="row",
            **{"data-metric": "index", "data-copy-value": "TOTAL"},
        ),
        *dimension_cells(frame, {}),
    ]
    body_rows = [html.Tr(total_cells, className="total-row")]
    if not frame.empty:
        body_rows.extend(
            build_tree_rows(
                frame,
                [],
                open_rows,
                [],
                groups=_active_groups_for_frame(
                    frame,
                    promotion_enabled,
                    region_enabled,
                ),
                cell_builder=dimension_cells,
                toggle_type="alt-row-toggle",
                cell_type="alt-risk-cell",
                delegated_actions=True,
                underlying_sort_metric=underlying_sort_metric,
            )
        )
    header = html.Thead(
        html.Tr(
            [
                html.Th(
                    index_label,
                    className="index-header",
                    scope="col",
                    **{"data-metric": "index"},
                )
            ]
            + [
                html.Th(
                    value,
                    className=(
                        "metric-header alt-dimension-header total-column"
                        if value == "Total"
                        else "metric-header alt-dimension-header"
                    ),
                    scope="col",
                    **{"data-metric": f"{selected_metric}:{value}"},
                )
                for value in [*dimension_values, "Total"]
            ]
        )
    )
    return html.Div(
        [
            html.Div(
                f"{metric_title(selected_metric)} by {dimension_title(dimension)}; Risk and dRisk show scoped sums from Risk Greek through every descendant level.",
                className="alt-table-note",
            ),
            html.Div("", className="selection-summary", **{"aria-live": "polite"}),
            html.Table(
                [
                    html.Caption(
                        f"{index_label} hierarchy by {dimension_title(dimension)}",
                        className="sr-only",
                    ),
                    header,
                    html.Tbody(body_rows),
                ],
                className="risk-table alt-risk-table",
            ),
        ],
        className="risk-table-wrap",
        **(
            {
                "data-risk-view-token": view_token,
                "data-risk-open-rows": json.dumps(
                    sorted(open_rows or []), separators=(",", ":")
                ),
            }
            if view_token
            else {}
        ),
    )


def build_credit_multi_table(
    frame: pd.DataFrame,
    metric: str,
    open_rows: list[str] | None,
    dimension: str = "activity",
    view_token: str | None = None,
    promotion_enabled: bool | None = None,
    region_enabled: bool = True,
    underlying_sort_metric: str | None = None,
) -> html.Div:
    """Build Credit Multi: one selected metric across all credit measures."""
    if frame.empty:
        return html.Div(
            [
                html.Strong("No matching credit rows"),
                html.Span("Try clearing one or more filters."),
            ],
            className="empty-state",
            role="status",
        )
    selected_metric = metric if metric in METRIC_COLUMNS else "risk"
    measure_completeness = {
        measure: credit_measure_available(frame, measure) for measure in CREDIT_MEASURES
    }

    def measure_cells(
        scoped: pd.DataFrame,
        context: dict[str, str],
    ) -> list[html.Td]:
        show_value = should_show_sum(selected_metric, context)
        cells: list[html.Td] = []
        for measure in CREDIT_MEASURES:
            series = credit_measure_values(
                scoped,
                selected_metric,
                measure,
                connector_complete=measure_completeness[measure],
            )
            value = float(series.sum(min_count=1))
            display_value = (
                format_number(value, column=selected_metric) if show_value else ""
            )
            classes = f"metric-cell credit-measure-cell {number_sign_class(value)}"
            content = ""
            if display_value:
                content = html.Button(
                    display_value,
                    type="button",
                    className="metric-cell-button credit-measure-cell-button",
                    title=(
                        "Use Shift, Control or Command with Enter or Space "
                        f"to select this {metric_title(selected_metric)} value"
                    ),
                    **{
                        "data-risk-metric": selected_metric,
                        "data-risk-measure": measure,
                        "aria-label": (
                            f"{metric_title(selected_metric)} {measure} value "
                            f"{display_value}. Use a modifier key with Enter or "
                            "Space to select it."
                        ),
                    },
                )
            cells.append(
                html.Td(
                    content,
                    className=classes
                    if display_value
                    else f"{classes} metric-cell-inert",
                    **{"data-metric": f"{selected_metric}:{measure}"},
                )
            )
        return cells

    body_rows = [
        html.Tr(
            [
                html.Th(
                    html.Span("TOTAL", className="row-label-text"),
                    className="index-cell total-index",
                    scope="row",
                    **{"data-metric": "index", "data-copy-value": "TOTAL"},
                ),
                *measure_cells(frame, {}),
            ],
            className="total-row",
            **{"data-risk-key": ""},
        )
    ]
    body_rows.extend(
        build_tree_rows(
            frame,
            [],
            open_rows,
            [],
            groups=_active_groups_for_frame(
                frame,
                promotion_enabled,
                region_enabled,
            ),
            cell_builder=measure_cells,
            toggle_type="main-row-toggle",
            cell_type="main-risk-cell",
            delegated_actions=True,
            underlying_sort_metric=underlying_sort_metric,
        )
    )
    missing_measures = [
        measure
        for measure in CREDIT_MEASURES
        if not credit_measure_available(frame, measure)
    ]
    if selected_metric == "pl":
        availability_note = "P&L is measure-invariant, so the same portfolio P&L appears under every credit measure."
    elif missing_measures and selected_metric != "pl":
        availability_note = (
            "Unavailable connector measures are blank: "
            + ", ".join(missing_measures)
            + "."
        )
    else:
        availability_note = (
            "All values come from optional connector credit-measure columns."
        )
    return html.Div(
        [
            html.Div(
                availability_note,
                className="alt-table-note credit-measure-note",
                role="status",
            ),
            html.Div("", className="selection-summary", **{"aria-live": "polite"}),
            html.Table(
                [
                    html.Caption(
                        f"Credit hierarchy with {metric_title(selected_metric)} by measure",
                        className="sr-only",
                    ),
                    html.Thead(
                        html.Tr(
                            [
                                html.Th(
                                    "Credit",
                                    className="index-header",
                                    scope="col",
                                    **{"data-metric": "index"},
                                )
                            ]
                            + [
                                html.Th(
                                    measure,
                                    className="metric-header credit-measure-header",
                                    scope="col",
                                    **{"data-metric": f"{selected_metric}:{measure}"},
                                )
                                for measure in CREDIT_MEASURES
                            ]
                        )
                    ),
                    html.Tbody(body_rows),
                ],
                className="risk-table credit-multi-table",
            ),
        ],
        className="risk-table-wrap credit-multi-wrap",
        **(
            {
                "data-risk-view-token": view_token,
                "data-risk-open-rows": json.dumps(
                    sorted(open_rows or []), separators=(",", ":")
                ),
            }
            if view_token
            else {}
        ),
    )


def build_aggregate_pl_table(
    frame: pd.DataFrame,
    dimension: str,
    open_risk_types: list[str] | None,
) -> html.Div:
    """Build the global P&L pivot, collapsed to Risk Type on first load."""
    if frame.empty:
        return html.Div(
            "No P&L rows are available.", className="empty-state", role="status"
        )
    column = selected_dimension(dimension)
    dimension_values = ordered_unique(frame, column)
    open_set = set(open_risk_types or [])

    def pl_cells(scoped: pd.DataFrame) -> list[html.Td]:
        values = scoped.groupby(column)["pl"].sum(min_count=1).reindex(dimension_values)
        numbers = [float(values[value]) for value in dimension_values]
        numbers.append(float(scoped["pl"].sum(min_count=1)))
        return [
            html.Td(
                html.Span(format_number(value), className="copy-value"),
                className=(
                    f"aggregate-pl-number metric-cell {number_sign_class(value)}"
                    + (" pl-cell total-column" if index == len(numbers) - 1 else "")
                ),
                **{
                    "data-metric": (
                        f"pl:{dimension_values[index]}"
                        if index < len(dimension_values)
                        else "pl:Total"
                    )
                },
            )
            for index, value in enumerate(numbers)
        ]

    rows: list[html.Tr] = []
    for risk_type in ordered_unique(frame, "risk type"):
        scoped_type = frame.loc[frame["risk type"].eq(risk_type)]
        is_open = risk_type in open_set
        rows.append(
            html.Tr(
                [
                    html.Th(
                        [
                            html.Button(
                                "⌄" if is_open else "›",
                                id={
                                    "type": "aggregate-row-toggle",
                                    "risk_type": risk_type,
                                },
                                n_clicks=0,
                                className="aggregate-row-toggle",
                                title="Close greeks" if is_open else "Open greeks",
                                **{
                                    "aria-label": ("Collapse" if is_open else "Expand")
                                    + f" {risk_type} greeks",
                                    "aria-expanded": str(is_open).lower(),
                                },
                            ),
                            html.Span(risk_type),
                        ],
                        scope="row",
                        className="aggregate-index aggregate-risk-type",
                        **{"data-metric": "index", "data-copy-value": str(risk_type)},
                    ),
                    *pl_cells(scoped_type),
                ],
                className="aggregate-risk-row",
            )
        )
        if is_open:
            for greek in ordered_unique(scoped_type, "risk greek"):
                scoped_greek = scoped_type.loc[scoped_type["risk greek"].eq(greek)]
                rows.append(
                    html.Tr(
                        [
                            html.Th(
                                greek,
                                scope="row",
                                className="aggregate-index aggregate-greek",
                                **{
                                    "data-metric": "index",
                                    "data-copy-value": str(greek),
                                },
                            ),
                            *pl_cells(scoped_greek),
                        ],
                        className="aggregate-greek-row",
                    )
                )

    rows.append(
        html.Tr(
            [
                html.Th(
                    "TOTAL",
                    scope="row",
                    className="aggregate-index aggregate-total-index",
                    **{"data-metric": "index", "data-copy-value": "TOTAL"},
                ),
                *pl_cells(frame),
            ],
            className="aggregate-total-row",
        )
    )

    headers = ["Index", *dimension_values, "Total"]
    return html.Div(
        [
            html.Div("", className="selection-summary", **{"aria-live": "polite"}),
            html.Table(
                [
                    html.Caption(
                        f"Aggregate P&L by {dimension_title(dimension)}",
                        className="sr-only",
                    ),
                    html.Thead(
                        html.Tr(
                            [
                                html.Th(
                                    value,
                                    scope="col",
                                    className=(
                                        "index-header"
                                        if value == "Index"
                                        else "metric-header total-column"
                                        if value == "Total"
                                        else "metric-header"
                                    ),
                                    **{
                                        "data-metric": (
                                            f"pl:{value}"
                                            if value != "Index"
                                            else "index"
                                        )
                                    },
                                )
                                for value in headers
                            ]
                        )
                    ),
                    html.Tbody(rows),
                ],
                className="cell-selection-table aggregate-pl-table",
            ),
        ],
        className="risk-table-wrap aggregate-pl-table-wrap",
    )


def top_book_exposure_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only exposures already promoted by the risk-threshold rules."""
    output_columns = [
        "Risk Type",
        "Risk Greek",
        "Label",
        "Underlying",
        "Risk",
        "dRisk",
        "P&L",
        "Score",
    ]
    if frame.empty:
        return pd.DataFrame(columns=output_columns)
    required = [
        "risk type",
        "risk greek",
        "reported underlying",
        "promotion reason",
        "promotion score",
        "risk",
        "drisk",
        "pl",
    ]
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"Top book exposures require columns: {missing}")

    promoted = frame.loc[
        frame["promotion reason"].fillna("").astype(str).str.strip().ne("")
    ].copy()
    if promoted.empty:
        return pd.DataFrame(columns=output_columns)

    label_order = {"Big Risk": 0, "Big dRisk": 1, "Big PL": 2}

    def combined_label(values: pd.Series) -> str:
        unique = {str(value).strip() for value in values if str(value).strip()}
        return " / ".join(
            sorted(unique, key=lambda value: (label_order.get(value, 99), value))
        )

    aggregated = (
        promoted.groupby(
            ["risk type", "risk greek", "reported underlying"], dropna=False, sort=False
        )
        .agg(
            {
                "promotion reason": combined_label,
                "promotion score": "max",
                "risk": lambda values: values.sum(min_count=1),
                "drisk": lambda values: values.sum(min_count=1),
                "pl": lambda values: values.sum(min_count=1),
            }
        )
        .reset_index()
    )
    aggregated["_risk_order"] = aggregated["risk type"].map(RISK_TYPE_ORDER).fillna(99)
    aggregated = aggregated.sort_values(
        [
            "_risk_order",
            "risk type",
            "risk greek",
            "promotion score",
            "reported underlying",
        ],
        ascending=[True, True, True, False, True],
        kind="mergesort",
    )
    return aggregated.rename(
        columns={
            "risk type": "Risk Type",
            "risk greek": "Risk Greek",
            "promotion reason": "Label",
            "reported underlying": "Underlying",
            "risk": "Risk",
            "drisk": "dRisk",
            "pl": "P&L",
            "promotion score": "Score",
        }
    )[output_columns].reset_index(drop=True)


def top_book_hierarchy_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Explode existing underlying labels into Cross hierarchy memberships.

    Threshold classification remains authoritative.  An underlying carrying
    more than one existing label appears once beneath each applicable label,
    with the same already-aggregated exposure in each branch.
    """
    columns = [
        "label",
        "risk type",
        "risk greek",
        "reported underlying",
        "risk",
        "drisk",
        "pl",
    ]
    promoted = top_book_exposure_frame(frame)
    if promoted.empty:
        return pd.DataFrame(columns=columns)

    def labels(value: object) -> tuple[str, ...]:
        supplied = {
            token.strip()
            for token in str(value).replace("/", ",").split(",")
            if token.strip()
        }
        return tuple(label for label in TOP_EXPOSURE_LABELS if label in supplied)

    hierarchy = promoted.copy()
    hierarchy["label"] = hierarchy["Label"].map(labels)
    hierarchy = hierarchy.explode("label").dropna(subset=["label"])
    hierarchy = hierarchy.rename(
        columns={
            "Risk Type": "risk type",
            "Risk Greek": "risk greek",
            "Underlying": "reported underlying",
            "Risk": "risk",
            "dRisk": "drisk",
            "P&L": "pl",
        }
    )
    return hierarchy[columns].reset_index(drop=True)


def default_top_book_open_rows(frame: pd.DataFrame) -> list[str]:
    """Open Label, Risk Type, and Risk Greek when lazy Top Book is mounted.

    The disclosure itself remains closed and has no children on initial page
    load.  Once requested, the useful hierarchy is immediately visible through
    Underlying without requiring a long sequence of chevron clicks.
    """
    hierarchy = top_book_hierarchy_frame(frame)
    open_rows: list[str] = []
    for label in ordered_unique(hierarchy, "label"):
        label_context = {"label": label}
        open_rows.append(row_key(label_context))
        label_frame = hierarchy.loc[hierarchy["label"].eq(label)]
        for risk_type in ordered_unique(label_frame, "risk type"):
            risk_type_context = {**label_context, "risk type": risk_type}
            open_rows.append(row_key(risk_type_context))
            risk_type_frame = label_frame.loc[label_frame["risk type"].eq(risk_type)]
            for risk_greek in ordered_unique(risk_type_frame, "risk greek"):
                open_rows.append(
                    row_key({**risk_type_context, "risk greek": risk_greek})
                )
    return sorted(set(open_rows))


def build_top_book_exposures(
    frame: pd.DataFrame,
    open_rows: list[str] | None = None,
    *,
    view_token: str = "top-book",
) -> html.Div:
    """Render existing Big Risk/dRisk/PL labels as one Cross-only hierarchy."""
    hierarchy = top_book_hierarchy_frame(frame)
    if hierarchy.empty:
        return html.Div(
            "No labelled book exposures are available.",
            className="empty-state",
            role="status",
        )

    resolved_open_rows = (
        default_top_book_open_rows(frame) if open_rows is None else open_rows
    )
    columns = list(METRIC_COLUMNS)

    def exposure_cells(
        scoped: pd.DataFrame,
        context: dict[str, str],
    ) -> list[html.Td]:
        cells: list[html.Td] = []
        for column in columns:
            value = scoped[column].sum(min_count=1)
            cell_class = f"metric-class(column, []) {number_sign_class(value)}"
            display_value = (
                format_number(value, column=column)
                if should_show_sum(column, context)
                else ""
            )
            if not display_value:
                cells.append(
                    html.Td(
                        "",
                        className=f"{cell_class} metric-cell-inert",
                        **{"data-metric": column},
                    )
                )
                continue
            cells.append(
                html.Td(
                    html.Button(
                        display_value,
                        type="button",
                        className="metric-cell-button top-book-metric-cell-button",
                        title=(
                            "Use Shift, Control or Command with Enter or Space "
                            f"to select this {metric_title(column)} value"
                        ),
                        **{
                            "data-risk-key": row_key(
                                {
                                    key: item
                                    for key, item in context.items()
                                    if key != "label"
                                }
                            ),
                            "data-risk-metric": column,
                            "aria-label": (
                                f"{metric_title(column)} value {display_value}. "
                                "Use a modifier key with Enter or Space to select it."
                            ),
                        },
                    ),
                    className=cell_class,
                    **{"data-metric": column},
                )
            )
        return cells

    rows = build_tree_rows(
        hierarchy,
        columns,
        resolved_open_rows,
        [],
        groups=list(TOP_EXPOSURE_GROUPS),
        cell_builder=exposure_cells,
        toggle_type="top-book-row-toggle",
        cell_type="top-book-risk-cell",
        delegated_actions=True,
    )
    header = html.Thead(
        html.Tr(
            [
                html.Th(
                    "Label",
                    className="index-header",
                    scope="col",
                    **{"data-metric": "index"},
                )
            ]
            + [
                html.Th(
                    metric_title(column),
                    className=f"metric-header {metric_class(column, [])}",
                    scope="col",
                    **{"data-metric": column},
                )
                for column in columns
            ]
        )
    )
    return html.Div(
        [
            html.Div("", className="selection-summary", **{"aria-live": "polite"}),
            html.Table(
                [
                    html.Caption(
                        "Label, Risk Type, Risk Greek and Underlying Cross hierarchy",
                        className="sr-only",
                    ),
                    header,
                    html.Tbody(rows),
                ],
                className="risk-table top-book-table top-book-cross-table",
            ),
        ],
        className="risk-table-wrap top-book-table-wrap top-book-cross-wrap",
        **{
            "data-risk-view-token": view_token,
            "data-risk-open-rows": json.dumps(
                sorted(resolved_open_rows), separators=(",", ":")
            ),
        },
    )


def build_small_table(frame: pd.DataFrame, metric: str) -> html.Table:
    show_source = "source type" in frame and frame["source type"].nunique() > 1
    show_underlying = "underlying" in frame and frame["underlying"].nunique() > 1
    tenor_columns = [
        (column, label)
        for column, label in (
            ("tenor swap", "Tenor Swap"),
            ("tenor option", "Tenor Option"),
        )
        if column in frame and _meaningful_tenor_mask(frame[column]).any()
    ]
    displayed_metrics = (
        ["move", "open", "current"]
        if metric == "move"
        else [
            metric,
            *METRIC_BREAKDOWNS.get(metric, []),
        ]
    )
    if frame.empty:
        return html.Table(
            [
                html.Tbody(
                    html.Tr(
                        html.Td(
                            "No rows",
                            colSpan=(
                                1
                                + len(tenor_columns)
                                + len(displayed_metrics)
                                + int(show_source)
                                + int(show_underlying)
                            ),
                            className="detail-table-empty",
                        )
                    )
                )
            ],
            className="detail-table",
        )
    header_cells = [
        *([html.Th("Source Type", scope="col")] if show_source else []),
        *([html.Th("Underlying", scope="col")] if show_underlying else []),
        *[html.Th(label, scope="col") for column, label in tenor_columns],
        *[
            html.Th(metric_title(column), className="detail-number", scope="col")
            for column in displayed_metrics
        ],
    ]
    header_cells.append(html.Th("Rows", className="detail-number", scope="col"))
    body_rows = []
    # Total row at the top
    total_cells = (
        [
            html.Th(
                "Total",
                scope="row",
                className="detail-source",
                style={"fontWeight": "bold"},
            ),
        ]
        if show_source
        else []
    )
    if show_underlying:
        total_cells.append(html.Th("", scope="row", className="detail-underlying"))
    for _column, label in tenor_columns:
        total_cells.append(html.Th("", scope="row", className="detail-tenor"))
    for column in displayed_metrics:
        col_sum = frame[column].sum()
        total_cells.append(
            html.Td(
                format_number(col_sum, column=column),
                className=f"detail-number {number_sign_class(col_sum)}",
                style={"fontWeight": "bold"},
            )
        )
    total_rows = frame["rows"].sum() if "rows" in frame else len(frame)
    total_cells.append(
        html.Td(
            format_number(total_rows, 0),
            className="detail-number number-positive",
            style={"fontWeight": "bold"},
        )
    )
    body_rows.append(html.Tr(total_cells))
    # Data rows
    for record in frame.to_dict("records"):
        cells = [
            *(
                [html.Td(record["source type"], className="detail-source")]
                if show_source
                else []
            ),
            *(
                [html.Td(record["underlying"], className="detail-underlying")]
                if show_underlying
                else []
            ),
            *[
                html.Td(record[column], className="detail-tenor")
                for column, _label in tenor_columns
            ],
        ]
        for column in displayed_metrics:
            cells.append(
                html.Td(
                    format_number(record[column], column=column),
                    className=f"detail-number {number_sign_class(record[column])}",
                )
            )
        cells.append(
            html.Td(
                format_number(record["rows"], 0),
                className="detail-number number-positive",
            )
        )
        body_rows.append(html.Tr(cells))
    return html.Table(
        [
            html.Caption("Selected tenor detail", className="sr-only"),
            html.Thead(html.Tr(header_cells)),
            html.Tbody(body_rows),
        ],
        className="detail-table",
    )


def selected_context_title(context: dict[str, str]) -> str:
    return (
        " · ".join(context[column] for column in ROW_KEY_COLUMNS if column in context)
        or "Total"
    )


def metric_title(metric: str) -> str:
    return {
        "risk": "Risk",
        "risk expo": "Risk XVA",
        "risk hedges": "Risk Hedges",
        "drisk": "dRisk",
        "drisk expo": "dRisk XVA",
        "drisk hedges": "dRisk Hedges",
        "pl": "P&L",
        "pl expo": "P&L XVA",
        "pl hedges": "P&L Hedges",
        "open": "Open",
        "current": "Market Status",
        "move": "Move",
    }.get(metric, metric)


def build_line_chart(
    detail: pd.DataFrame,
    group_column: str,
    order_column: str,
    metric: str,
    title: str,
    x_title: str,
) -> dcc.Graph:
    axis_values, ambiguous_order = tenor_axis_order(
        detail,
        group_column,
        order_column,
    )
    if metric in {"move", "open", "current"}:
        market_series = ["move", "open", "current"] if metric == "move" else [metric]
        quote_identity = [
            column for column in ("source type", "underlying") if column in detail
        ]
        if quote_identity:
            by_underlying = detail.groupby(
                [*quote_identity, group_column],
                as_index=False,
                dropna=False,
            )[market_series].mean()
            curve = by_underlying.groupby(
                group_column,
                as_index=False,
                dropna=False,
            )[market_series].mean()
        else:
            curve = detail.groupby(
                group_column,
                as_index=False,
                dropna=False,
            )[market_series].mean()
        curve["_axis_order"] = (
            curve[group_column]
            .astype(str)
            .map({label: rank for rank, label in enumerate(axis_values)})
        )
        curve = curve.sort_values("_axis_order", kind="stable")
        figure = go.Figure()
        market_colors = {"move": "#D88989", "open": "#A8BAC8", "current": "#91C6A1"}
        for column in market_series:
            is_secondary = metric == "move" and column in {"open", "current"}
            if is_secondary:
                # Open and Current are lines on secondary axis
                figure.add_trace(
                    go.Scatter(
                        name=metric_title(column),
                        x=curve[group_column],
                        y=curve[column],
                        mode="lines+markers",
                        line={
                            "color": market_colors[column],
                            "width": 2,
                        },
                        yaxis="y2",
                        hoverlabel={"font": {"size": 10}},
                    )
                )
            else:
                # Move is bar on primary axis
                figure.add_trace(
                    go.Bar(
                        name=metric_title(column),
                        x=curve[group_column],
                        y=curve[column],
                        marker={
                            "color": market_colors[column],
                            "line": {"color": "rgba(0,0,0,0.3)", "width": 1},
                        },
                        opacity=0.7,
                        width=0.8,
                        hoverlabel={"font": {"size": 10}},
                    )
                )
        figure.update_layout(
            title=title,
            xaxis={
                "title": x_title,
                "type": "category",
                "categoryorder": "array",
                "categoryarray": axis_values,
                "tickangle": -30,
                "tickfont": {"size": 10},
            },
            yaxis={"title": metric_title(metric)},
            yaxis2=(
                {
                    "title": "Open / Market Status",
                    "overlaying": "y",
                    "side": "right",
                    "showgrid": False,
                }
                if metric == "move"
                else None
            ),
            legend={"orientation": "h", "y": 1.12},
            template="plotly_white",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin={"l": 52, "r": 58, "t": 58, "b": 70},
            height=300,
        )
    else:
        series = [metric, *METRIC_BREAKDOWNS.get(metric, [])]
        curve = detail.groupby(group_column, as_index=False)[series].sum(min_count=1)
        curve["_axis_order"] = (
            curve[group_column]
            .astype(str)
            .map({label: rank for rank, label in enumerate(axis_values)})
        )
        curve = curve.sort_values("_axis_order", kind="stable")
        figure = go.Figure()
        colors = {
            metric: (
                "#73A9D8"
                if metric.endswith(" expo")
                else "#D99191"
                if metric.endswith(" hedges")
                else "#4C8A4A"
            )
        }
        colors.update(
            {
                component: color
                for component, color in zip(series[1:], ("#73A9D8", "#D99191"))
            }
        )
        # Total goes on primary axis (y) as bar, breakdowns on secondary (y2) as scatter
        figure.add_trace(
            go.Bar(
                name="Total" if metric in METRIC_BREAKDOWNS else metric_title(metric),
                x=curve[group_column],
                y=curve[metric],
                marker={
                    "color": colors[metric],
                    "line": {"color": "rgba(0,0,0,0.3)", "width": 1},
                },
                opacity=0.7,
                width=0.8,
                hoverlabel={"font": {"size": 10}},
            )
        )
        for component in series[1:]:
            figure.add_trace(
                go.Scatter(
                    name=metric_title(component),
                    x=curve[group_column],
                    y=curve[component],
                    mode="lines+markers",
                    line={"color": colors[component], "width": 2},
                    yaxis="y2",
                    hoverlabel={"font": {"size": 10}},
                )
            )
        figure.update_layout(
            title=title,
            xaxis={
                "title": x_title,
                "type": "category",
                "categoryorder": "array",
                "categoryarray": axis_values,
                "tickangle": -30,
                "tickfont": {"size": 10},
            },
            yaxis={"title": f"{metric_title(metric)} amount"},
            yaxis2={
                "title": "XVA / Hedges amount",
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
            },
            legend={"orientation": "h", "y": 1.12},
            template="plotly_white",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin={"l": 52, "r": 58, "t": 58, "b": 70},
            height=300,
        )
        if ambiguous_order:
            figure.add_annotation(
                text=(
                    "Selected underlyings use different tenor ranks; "
                    "labels use modal connector order."
                ),
                x=0,
                xref="paper",
                y=-0.24,
                yref="paper",
                showarrow=False,
                align="left",
                font={"size": 10, "color": "#626B75"},
            )
            figure.update_layout(margin={"l": 52, "r": 58, "t": 58, "b": 68})
        return dcc.Graph(
            figure=figure,
            config={"displayModeBar": False},
        )


def build_tenor_heatmap(
    detail: pd.DataFrame, metric: str, title: str = "Swap x option surface"
) -> dcc.Graph:
    """Build a sparse surface from all observed swap and option tenors.

    Neither axis has a fixed size. Missing combinations stay blank instead of
    being manufactured as zero-valued cells.
    """
    pivot, ambiguous_option_order, ambiguous_swap_order = _tenor_surface_pivot(
        detail,
        metric,
    )
    display_metric = metric_title(metric)
    hover_data = _surface_hover_data(pivot, metric)

    # use same colour scheme as market heatmap
    colorscale = [
        [0.0, "#D98282"],
        [0.25, "#F2BA8A"],
        [0.5, "#FFFDF6"],
        [0.75, "#BFE4C7"],
        [1.0, "#79BE89"],
    ]

    # Mirror market heatmap color-bounds logic for "risk"-style metrics
    finite_values = np.asarray(pivot.values, dtype=float)
    finite_values = finite_values[np.isfinite(finite_values)]
    max_abs = float(np.max(np.abs(finite_values))) if finite_values.size else 0.0
    color_bounds: dict[str, float] = {"zmid": 0.0}
    if max_abs > 0:
        color_bounds.update(zmin=-max_abs, zmax=max_abs)

    option_order = list(pivot.index)
    trace = go.Heatmap(
        z=pivot.values,
        x=list(pivot.columns),
        y=option_order,
        customdata=hover_data,
        hoverongaps=False,
        xgap=1,
        ygap=1,
        colorscale=colorscale,
        colorbar={
            "title": {"text": display_metric},
            "thickness": 12,
            "len": 0.78,
            "xpad": 6,
        },
        hovertemplate=(
            "<b>Tenor Swap</b>: %{customdata[0]}<br>"
            "<b>Tenor Option</b>: %{customdata[1]}<br>"
            f"<b>{display_metric}</b>: %{{customdata[2]}}"
            "<extra></extra>"
        ),
        hoverlabel={"font": {"size": 11}},
        **color_bounds,
    )

    figure = go.Figure(data=[trace])
    figure.update_xaxes(
        title={"text": "Tenor Swap", "standoff": 10},
        type="category",
        categoryorder="array",
        categoryarray=list(pivot.columns),
        tickmode="array",
        tickvals=list(pivot.columns),
        ticktext=[_compact_tenor_label(value) for value in pivot.columns],
        side="top",
        ticklabelposition="outside top",
        ticks="outside",
        ticklen=5,
        automargin=True,
        constrain="domain",
    )

    figure.update_yaxes(
        title_text="Tenor Option",
        type="category",
        categoryorder="array",
        categoryarray=list(pivot.index),
        tickmode="array",
        tickvals=list(pivot.index),
        ticktext=[_compact_tenor_label(value) for value in pivot.index],
        automargin=True,
        constrain="domain",
    )

    figure.update_layout(
        autosize=False,
        hovermode="closest",
        hoverlabel={
            "align": "left",
            "font": {"size": 11},
        },
        margin={"l": 58, "r": 46, "t": 90, "b": 44},
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=500,
    )
    if ambiguous_option_order or ambiguous_swap_order:
        figure.add_annotation(
            text=(
                "Selected underlyings use different tenor ranks; "
                "axes use modal connector order."
            ),
            x=0,
            xref="paper",
            y=-0.24,
            yref="paper",
            showarrow=False,
            align="left",
            font={"size": 10, "color": "#626B75"},
        )
        figure.update_layout(margin={"l": 58, "r": 46, "t": 90, "b": 68})
    return dcc.Graph(
        figure=figure,
        responsive=True,
        className="tenor-surface-graph",
        config={"displayModeBar": False, "responsive": True},
    )


def _detail_source_rows(frame: pd.DataFrame) -> int:
    if "rows" not in frame:
        return len(frame)
    values = pd.to_numeric(frame["rows"], errors="coerce").fillna(0)
    return int(values.sum())


def _detail_chart_card(title: str, chart) -> html.Div:
    return html.Div(
        [html.H3(title, className="detail-chart-title"), chart],
        className="detail-chart-card",
    )


def _build_detail_panel_from_frame(
    *,
    scoped: pd.DataFrame,
    detail: pd.DataFrame,
    context: dict[str, str],
    selected_metric: str,
    metric: str,
    tenor_view: str,
) -> html.Div:
    # Diagnostics: trace tenor view data for debugging
    partitions = detail_tenor_partitions(detail)
    paired = partitions["paired"]
    swap_only = partitions["swap_only"]
    option_only = partitions["option_only"]
    no_tenor = partitions["no_tenor"]
    _DETAIL_LOGGER.debug(
        "_sample tenor_swap=%s paired=%d swap_only=%d option_only=%d no_tenor=%d context=%s metric=%s scoped_rows=%d",
        tenor_view,
        len(detail),
        int(paired.sum()),
        int(swap_only.sum()),
        int(option_only.sum()),
        int(no_tenor.sum()),
        context,
        metric,
        len(scoped),
    )
    if not detail.empty:
        _DETAIL_LOGGER.debug(
            "_sample tenor_swap=%s sample_tenor_option=%s sample_underlying=%s",
            detail["tenor swap"].head(3).tolist()
            if "tenor swap" in detail
            else "MISSING",
            detail["tenor option"].head(3).tolist()
            if "tenor option" in detail
            else "MISSING",
            detail["underlying"].head(3).tolist()
            if "underlying" in detail
            else "MISSING",
        )

    chart_cards: list[html.Div] = []
    displayed_detail = detail
    matrix_detail: pd.DataFrame | None = None

    if tenor_view == "swap":
        included = paired | swap_only
        displayed_detail = detail.loc[included]
        if included.any():
            chart_cards.append(
                _detail_chart_card(
                    "",
                    build_line_chart(
                        displayed_detail,
                        "tenor swap",
                        "tenor swap order",
                        metric,
                        "Tenor Swap line",
                        "Tenor Swap",
                    ),
                )
            )
    elif tenor_view == "option":
        included = paired | option_only
        displayed_detail = detail.loc[included]
        if included.any():
            chart_cards.append(
                _detail_chart_card(
                    "",
                    build_line_chart(
                        displayed_detail,
                        "tenor option",
                        "tenor option order",
                        metric,
                        "Tenor Option line",
                        "Tenor Option",
                    ),
                )
            )
    elif tenor_view == "surface":
        displayed_detail = detail.loc[paired]
        matrix_detail = displayed_detail
        if paired.any():
            chart_cards.append(
                build_tenor_heatmap(
                    displayed_detail, metric, title="Swap x option surface"
                )
            )
    else:
        # Auto uses the mutually exclusive populations. Every detail row therefore
        # appears exactly once across its charts or the no-tenor note.
        _DETAIL_LOGGER.debug(
            "auto path: paired.any()=%s swap_only.any()=%s option_only.any()=%s no_tenor.any()=%s",
            bool(paired.any()),
            bool(swap_only.any()),
            bool(option_only.any()),
            bool(no_tenor.any()),
        )
        if paired.any():
            matrix_detail = detail.loc[paired]
            _DETAIL_LOGGER.debug(
                "building heatmap for paired rows=%d", int(paired.sum())
            )
            chart_cards.append(
                build_tenor_heatmap(
                    detail.loc[paired], metric, title="Swap x option surface"
                )
            )
            _DETAIL_LOGGER.debug("chart_cards length now=%d", len(chart_cards))
        if swap_only.any():
            chart_cards.append(
                _detail_chart_card(
                    "",
                    build_line_chart(
                        detail.loc[swap_only],
                        "tenor swap",
                        "tenor swap order",
                        metric,
                        "",
                        "Tenor Swap",
                    ),
                )
            )
        if option_only.any():
            chart_cards.append(
                _detail_chart_card(
                    "",
                    build_line_chart(
                        detail.loc[option_only],
                        "tenor option",
                        "tenor option order",
                        metric,
                        "",
                        "Tenor Option",
                    ),
                )
            )
        if no_tenor.any():
            chart_cards.append(
                html.Div(
                    f"{_detail_source_rows(detail.loc[no_tenor]):,} source rows "
                    "have no tenor dimension and remain in the table.",
                    className="detail-note detail-chart-card",
                )
            )

    if not chart_cards and not detail.empty:
        chart_cards.append(
            html.Div(
                "This selection has no rows for the chosen tenor view.",
                className="detail-note detail-chart-card",
            )
        )

    total_detail_rows = _detail_source_rows(detail)
    included_detail_rows = _detail_source_rows(displayed_detail)
    coverage = (
        f"{DETAIL_TENOR_VIEW_LABELS[tenor_view]} · "
        f"{included_detail_rows:,} of {total_detail_rows:,} source rows shown"
    )
    if tenor_view != "auto" and included_detail_rows != total_detail_rows:
        coverage += f" · {total_detail_rows - included_detail_rows:,} outside this view"

    title = f"{selected_context_title(context)} — {metric_title(metric)}"

    # Build matrix table for surface view, otherwise use flat table
    if matrix_detail is not None and not matrix_detail.empty:
        pivot, _ambiguous_option, _ambiguous_swap = _tenor_surface_pivot(
            matrix_detail,
            metric,
        )
        detail_table = build_surface_matrix_table(pivot, metric)
    else:
        detail_table = build_small_table(displayed_detail, metric)

    return html.Div(
        [
            html.Div(
                [
                    html.H2(title),
                    html.Div(
                        f"{len(scoped):,} source rows, opened from {selected_metric}",
                        className="detail-subtitle",
                    ),
                    html.Div(
                        coverage,
                        className="detail-tenor-coverage",
                        **{"aria-live": "polite"},
                    ),
                ],
                className="detail-header",
            ),
            html.Div(
                [
                    html.Div(chart_cards, className="detail-chart detail-chart-stack"),
                    html.Div(
                        detail_table,
                        className="detail-table-wrap",
                    ),
                ],
                className="detail-grid",
            ),
        ],
        className="detail-panel-body",
    )


def build_detail_panel_with_state(
    frame: pd.DataFrame,
    selection: dict[str, str] | None,
    plot_metric: str,
    tenor_view: str | None = "auto",
) -> tuple[html.Div, list[dict[str, object]], str]:
    """Build one detail panel and its synchronized tenor-view picker state."""
    if not selection:
        options, resolved_view = detail_tenor_view_state(pd.DataFrame(), "auto")
        return (
            html.Div(
                [
                    html.H2("Tenor detail"),
                    html.P("Select any metric cell to open the tenor table and chart."),
                ],
                className="detail-panel body empty-detail",
            ),
            options,
            resolved_view,
        )

    metric = plot_metric if plot_metric in PLOT_METRICS else "risk"
    selected_metric = selection.get("metric", "risk")
    context = parse_row_key(selection.get("key"))
    scoped = frame_for_context(frame, context)
    detail = detail_frame(frame, context, metric)
    options, resolved_view = detail_tenor_view_state(detail, tenor_view)
    panel = _build_detail_panel_from_frame(
        scoped=scoped,
        detail=detail,
        context=context,
        selected_metric=selected_metric,
        metric=metric,
        tenor_view=resolved_view,
    )
    return panel, options, resolved_view


def build_risk_date_editor(
    snapshot: RefreshSnapshotProtocol,
    applied_overrides: dict[str, str] | None,
    draft_overrides: dict[str, str] | None = None,
    applied_view_date: str | None = None,
    draft_view_date: object = _UNSET,
) -> html.Div:
    applied = dict(applied_overrides or {})
    draft = dict(applied if draft_overrides is None else draft_overrides)
    snapshot_view = getattr(snapshot, "forced_view_date", None)
    applied_view = applied_view_date or (
        pd.Timestamp(snapshot_view).date().isoformat()
        if snapshot_view is not None
        else None
    )
    if draft_view_date is _UNSET:
        draft_view = applied_view
    else:
        draft_view = (
            pd.Timestamp(draft_view_date).date().isoformat()
            if draft_view_date not in (None, "")
            else None
        )
    system_today = pd.Timestamp(snapshot.system_date).date()
    market_date = pd.Timestamp(snapshot.market_date).date()
    view_dirty = draft_view != applied_view
    rows = []
    status = snapshot.risk_status.sort_values("Source Type")
    source_types = status["Source Type"].astype(str).tolist()
    common_forced_dates = {draft.get(source) for source in source_types}
    force_all_risk = (
        bool(source_types)
        and None not in common_forced_dates
        and len(common_forced_dates) == 1
    )
    suggested_market_date = pd.Timestamp(draft_view or market_date).normalize()
    loaded_market_date = pd.Timestamp(snapshot.market_date).normalize()
    status_is_loaded = suggested_market_date == loaded_market_date
    market_status = (
        str(snapshot.market_status) if status_is_loaded else "Resolved on apply"
    )
    market_status_class = (
        f"is-{str(snapshot.market_status).casefold()}"
        if status_is_loaded
        else "is-pending"
    )
    suggested_risk_date = (
        (suggested_market_date - pd.offsets.BDay(1)).date().isoformat()
    )
    loaded_checker_date = pd.Timestamp(snapshot.checker_date).date().isoformat()
    forced_all_risk_date = (
        next(iter(common_forced_dates)) if force_all_risk else suggested_risk_date
    )
    for record in status.to_dict("records"):
        source_type = str(record["Source Type"])
        forced_date = draft.get(source_type)
        applied_date = applied.get(source_type)
        effective = pd.Timestamp(record["Effective Risk Date"]).date().isoformat()
        suggested_source_date = (
            pd.Timestamp(record["Suggested Risk Date"]).date().isoformat()
        )
        age = int(record["Age"])
        age_defaulted = bool(record.get("Age Defaulted", False))
        age_label = f"{age} (T-1 fallback)" if age_defaulted else str(age)
        age_title = (
            "RiskChecker did not report this Risk Type / Risk Greek pair; "
            "Cube uses Age 0, the business day before the market date."
            if age_defaulted
            else f"RiskChecker explicitly reported Age {age}."
        )
        rows.append(
            html.Tr(
                [
                    html.Td(source_type, className="status-source"),
                    html.Td(age_label, title=age_title),
                    html.Td(suggested_source_date),
                    html.Td(effective, className="applied-risk-date"),
                    html.Td(
                        dcc.Checklist(
                            id={"type": "force-risk-checkbox", "source": source_type},
                            options=[{"label": "Force", "value": "force"}],
                            value=["force"] if forced_date else [],
                            className="force-risk-check",
                        )
                    ),
                    html.Td(
                        dcc.DatePickerSingle(
                            id={"type": "forced-risk-date", "source": source_type},
                            date=forced_date or effective,
                            max_date_allowed=system_today,
                            display_format="YYYY-MM-DD",
                            clearable=False,
                            disabled=not bool(forced_date),
                        )
                    ),
                ],
                className="force-risk-row is-dirty"
                if forced_date != applied_date
                else "force-risk-row",
            )
        )
    checker_enabled = bool(getattr(snapshot, "risk_checker_enabled", False))
    checker_content = html.Div(
        (
            f"Open this section to render the dated MMM inventory for {loaded_checker_date}."
            + (
                f" Apply reloads the inventory for {suggested_risk_date}."
                if suggested_risk_date != loaded_checker_date
                else ""
            )
            if checker_enabled
            else "Risk checker is Off; its combined readiness and MMM inventory function is not called."
        ),
        className="status-panel-note",
    )
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Market date", className="date-card-title"),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Span(
                                                "System today",
                                                className="market-view-label",
                                            ),
                                            html.Strong(
                                                system_today.isoformat(),
                                                className="market-view-value",
                                            ),
                                        ],
                                        className="market-view-stat",
                                    ),
                                    html.Div(
                                        [
                                            html.Span(
                                                "Suggested market date",
                                                className="market-view-label",
                                            ),
                                            html.Strong(
                                                suggested_market_date.date().isoformat(),
                                                className="market-view-value",
                                            ),
                                        ],
                                        className="market-view-stat",
                                    ),
                                    html.Div(
                                        [
                                            html.Span(
                                                "Market status",
                                                className="market-view-label",
                                            ),
                                            html.Strong(
                                                market_status,
                                                className=f"market-view-status {market_status_class}",
                                            ),
                                        ],
                                        className="market-view-stat",
                                    ),
                                ],
                                className="date-card-stats",
                            ),
                            html.Div(
                                [
                                    dcc.Checklist(
                                        id="force-view-date-checkbox",
                                        options=[
                                            {
                                                "label": "Force market date",
                                                "value": "force",
                                            }
                                        ],
                                        value=["force"] if draft_view else [],
                                        className="force-view-date-check",
                                    ),
                                    dcc.DatePickerSingle(
                                        id="forced-view-date",
                                        date=draft_view or market_date,
                                        max_date_allowed=system_today,
                                        display_format="YYYY-MM-DD",
                                        clearable=False,
                                        disabled=not bool(draft_view),
                                    ),
                                    html.Span(
                                        "Draft" if view_dirty else "Applied",
                                        className="market-view-draft-state is-dirty"
                                        if view_dirty
                                        else "market-view-draft-state",
                                    ),
                                ],
                                className="market-view-force",
                            ),
                        ],
                        className="market-view-card is-dirty"
                        if view_dirty
                        else "market-view-card",
                    ),
                    html.Div(
                        [
                            html.H3("Risk date", className="date-card-title"),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Span(
                                                "Suggested risk date",
                                                className="market-view-label",
                                            ),
                                            html.Strong(
                                                suggested_risk_date,
                                                className="market-view-value",
                                            ),
                                        ],
                                        className="market-view-stat",
                                    ),
                                    html.Div(
                                        [
                                            html.Span(
                                                "Date rule",
                                                className="market-view-label",
                                            ),
                                            html.Strong(
                                                "Market date - 1 business day",
                                                className="market-view-rule",
                                            ),
                                        ],
                                        className="market-view-stat",
                                    ),
                                    html.Div(
                                        [
                                            html.Span(
                                                "Forced sources",
                                                className="market-view-label",
                                            ),
                                            html.Strong(
                                                str(
                                                    sum(
                                                        bool(draft.get(source))
                                                        for source in source_types
                                                    )
                                                ),
                                                className="market-view-value",
                                            ),
                                        ],
                                        className="market-view-stat",
                                    ),
                                ],
                                className="date-card-stats",
                            ),
                            html.Div(
                                [
                                    dcc.Checklist(
                                        id="force-all-risk-checkbox",
                                        options=[
                                            {
                                                "label": "Force all risk",
                                                "value": "force",
                                            }
                                        ],
                                        value=["force"] if force_all_risk else [],
                                        className="force-view-date-check",
                                    ),
                                    dcc.DatePickerSingle(
                                        id="forced-all-risk-date",
                                        date=forced_all_risk_date,
                                        max_date_allowed=system_today,
                                        display_format="YYYY-MM-DD",
                                        clearable=False,
                                        disabled=not force_all_risk,
                                    ),
                                ],
                                className="market-view-force",
                            ),
                        ],
                        className="market-view-card risk-view-card",
                    ),
                ],
                className="date-control-grid",
            ),
            html.Details(
                [
                    html.Summary("Risk readiness", className="nested-status-summary"),
                    html.Div(
                        "Age 0 uses the business day before the market date; Age 1 uses two business days before it. "
                        "T-1 fallback appears only when RiskChecker omits a configured pair; Cube keeps that product "
                        "at Age 0 instead of silently dropping it. Force all risk or a per-source override is absolute. "
                        "Edit the draft, then choose Apply.",
                        className="status-panel-note",
                    ),
                    html.Div(
                        html.Table(
                            [
                                html.Thead(
                                    html.Tr(
                                        [
                                            html.Th(value)
                                            for value in [
                                                "Source type",
                                                "Age",
                                                "System risk date",
                                                "Applied risk date",
                                                "Force risk",
                                                "Draft override date",
                                            ]
                                        ]
                                    )
                                ),
                                html.Tbody(rows),
                            ],
                            className="status-table",
                        ),
                        className="status-table-wrap",
                    ),
                ],
                open=False,
                className="nested-status-details risk-readiness-inventory-details",
            ),
            html.Details(
                [
                    html.Summary(
                        "Risk checker inventory",
                        id="risk-checker-inventory-summary",
                        n_clicks=0,
                        className="nested-status-summary",
                    ),
                    html.Div(checker_content, id="risk-checker-inventory"),
                ],
                id="risk-checker-inventory-details",
                open=False,
                className="nested-status-details risk-checker-inventory-details",
            ),
        ],
        className="status-panel",
    )


def build_risk_checker_inventory(
    checker_frame: pd.DataFrame,
    checker_date: object,
    *,
    enabled: bool,
    row_limit: int = 2_000,
) -> html.Div:
    """Render the potentially large checker inventory only when requested."""
    if not enabled:
        return html.Div(
            "Risk checker is Off; its combined readiness and MMM inventory function is not called.",
            className="status-panel-note",
        )

    checker_columns = ["Risk Type", "Risk Greek", "MMMFile", "Product"]
    checker = checker_frame.reindex(columns=checker_columns)
    limit = max(1, int(row_limit))
    visible = checker.head(limit)
    rows = [
        html.Tr([html.Td(str(record[column])) for column in checker_columns])
        for record in visible.to_dict("records")
    ]
    loaded_checker_date = pd.Timestamp(checker_date).date().isoformat()
    note = (
        f"{len(checker):,} dated MMM inventory rows loaded for {loaded_checker_date}."
    )
    if len(checker) > len(visible):
        note += f" Showing the first {len(visible):,} rows."
    return html.Div(
        [
            html.Div(note, className="status-panel-note"),
            html.Div(
                html.Table(
                    [
                        html.Thead(
                            html.Tr([html.Th(column) for column in checker_columns])
                        ),
                        html.Tbody(rows),
                    ],
                    className="status-table",
                ),
                className="status-table-wrap",
            ),
        ]
    )


def build_unmapped_books_table(frame: pd.DataFrame) -> html.Div:
    if frame.empty:
        return html.Div(
            "All portfolios are mapped in config.", className="unmapped-empty"
        )

    total_rows = len(frame)
    portfolio_count = (
        frame["Portfolio"].dropna().astype(str).str.strip().replace("", pd.NA).nunique()
        if "Portfolio" in frame
        else 0
    )
    portfolio_label = "portfolio" if portfolio_count == 1 else "portfolios"
    display_frame = frame.head(2_000).copy()
    requested_columns = [
        "Portfolio",
        "Risk Type",
        "Risk Greek",
        "Split",
        *PORTFOLIO_METADATA_COLUMNS,
        "Group",
        "Underlying",
        "Tenor Swap",
        "Tenor Swap Order",
        "Tenor Option",
        "Tenor Option Order",
        "Risk",
        "dRisk",
        "PL",
    ]
    columns = list(
        dict.fromkeys(value for value in requested_columns if value in display_frame)
    )
    table_frame = display_frame[columns].copy()
    table_frame = table_frame.astype(object).where(pd.notna(table_frame), None)
    numeric_columns = {"Tenor Swap Order", "Tenor Option Order", "Risk", "dRisk", "PL"}
    return html.Div(
        [
            html.Div(
                f"{total_rows:,} normalized P&L rows across {portfolio_count:,} "
                f"{portfolio_label} are excluded from mapped dashboard totals because "
                "their Portfolio value has no matching config entry. "
                "They remain visible here for remediation. "
                + ("The first 2,000 are shown." if total_rows > 2_000 else ""),
                className="unmapped-note",
            ),
            html.Div(
                dash_table.DataTable(
                    id="unmapped-books-table",
                    columns=[
                        {
                            "name": column,
                            "id": column,
                            **(
                                {"type": "numeric", "format": {"specifier": ",.0f"}}
                                if column in numeric_columns
                                else {}
                            ),
                        }
                        for column in columns
                    ],
                    data=table_frame.to_dict("records"),
                    editable=False,
                    filter_action="native",
                    sort_action="native",
                    sort_mode="multi",
                    page_action="native",
                    page_size=25,
                    fixed_rows={"headers": True},
                    style_table={"overflowX": "auto", "maxHeight": "520px"},
                    style_header={
                        "backgroundColor": "#F7F8FA",
                        "color": "#111111",
                        "fontWeight": "850",
                    },
                    style_cell={
                        "backgroundColor": "#FFFFFF",
                        "color": "#111111",
                        "border": "1px solid #E2E6EA",
                        "fontFamily": (
                            '"Segoe UI Variable Text", "Segoe UI", Arial, sans-serif'
                        ),
                        "fontSize": "12px",
                        "padding": "7px 9px",
                        "textAlign": "left",
                        "minWidth": "110px",
                        "whiteSpace": "nowrap",
                    },
                    style_cell_conditional=[
                        {
                            "if": {"column_id": list(numeric_columns)},
                            "fontVariantNumeric": "tabular-nums",
                            "textAlign": "right",
                        }
                    ],
                    style_data_conditional=[
                        {
                            "if": {
                                "filter_query": f"{{{column}}} < 0",
                                "column_id": column,
                            },
                            "color": "#B42318",
                        }
                        for column in ("Risk", "dRisk", "PL")
                    ],
                ),
                className="unmapped-table-wrap",
            ),
        ]
    )


def _cube_mark(class_name: str) -> html.Span:
    """Return the shared six-face cube and compact rolling layers."""
    return html.Span(
        html.Span(
            html.Span(
                [
                    html.Span(className="cube-motion__shadow"),
                    html.Span(
                        html.Span(
                            html.Span(
                                html.Span(
                                    [
                                        html.I(
                                            className=(
                                                "cube-motion__face "
                                                f"cube-motion__face--{face}"
                                            )
                                        )
                                        for face in (
                                            "front",
                                            "back",
                                            "right",
                                            "left",
                                            "top",
                                            "bottom",
                                        )
                                    ],
                                    className="cube-motion__solid",
                                ),
                                className="cube-motion__view",
                            ),
                            className="cube-motion__roller",
                        ),
                        className="cube-motion__lift",
                    ),
                ],
                className="cube-motion__traveller",
            ),
            className="cube-motion__scene",
        ),
        className=f"cube-motion {class_name}",
        **{"aria-hidden": "true"},
    )


def build_cube_loader(
    label: str = "Loading Cube data", *, announce: bool = True
) -> html.Div:
    """Accessible wrapper around the calm six-face Cube loading mark."""
    accessibility = {"role": "status"} if announce else {"aria-hidden": "true"}
    return html.Div(
        [
            _cube_mark("cube-motion--loader"),
            html.Span(label, className="sr-only"),
        ],
        className="cube-risk-loader",
        **accessibility,
    )


def _build_theme_toggle() -> html.Button:
    """Return the theme button shared by the loading shell and full page."""
    return html.Button(
        "",
        id="theme-toggle",
        n_clicks=0,
        className="theme-toggle",
        title="Switch to dark mode",
        type="button",
        **{"aria-label": "Switch to dark mode", "aria-pressed": "false"},
    )


def build_operating_date_content(
    snapshot: ControlSnapshotProtocol | RefreshSnapshotProtocol | None,
) -> list[html.Div]:
    """Return the prominent committed Market/Risk date cards."""
    if snapshot is None:
        market_date = "Loading…"
        market_status = "Cold start"
        grouped_risk_dates: list[tuple[str, list[str]]] = []
    else:
        market_date = pd.Timestamp(snapshot.market_date).date().isoformat()
        market_status = str(snapshot.market_status)
        grouped: dict[str, list[str]] = {}
        for source_type, value in sorted(snapshot.risk_dates.items()):
            date_value = pd.Timestamp(value).date().isoformat()
            grouped.setdefault(date_value, []).append(str(source_type))
        grouped_risk_dates = sorted(grouped.items(), reverse=True)

    risk_values = (
        [
            html.Span(
                [
                    html.Strong(date_value, className="operating-date-value"),
                    html.Small(
                        f"{len(source_types)} source"
                        + ("s" if len(source_types) != 1 else ""),
                        className="operating-date-scope",
                    ),
                ],
                className="operating-risk-date",
                title=", ".join(source_types),
            )
            for date_value, source_types in grouped_risk_dates
        ]
        if grouped_risk_dates
        else [
            html.Strong(
                "Loading…",
                className="operating-date-value",
            )
        ]
    )
    return [
        html.Div(
            [
                html.Span("MARKET DATE", className="operating-date-label"),
                html.Strong(market_date, className="operating-date-value"),
                html.Small(market_status, className="operating-date-scope"),
            ],
            className="operating-date-card operating-market-date",
        ),
        html.Div(
            [
                html.Span("RISK DATES", className="operating-date-label"),
                html.Div(risk_values, className="operating-risk-date-values"),
            ],
            className="operating-date-card",
        ),
    ]


def _build_refresh_controls(
    initial_snapshot: ControlSnapshotProtocol | RefreshSnapshotProtocol | None,
    *,
    refresh_enabled: bool,
    initial_loading: bool = False,
    initial_error: bool = False,
    id_prefix: str = "",
) -> html.Div:
    """Build the stable dashboard control strip for startup and operation."""
    theme_toggle = _build_theme_toggle()
    if not refresh_enabled:
        return html.Div(
            html.Div(
                theme_toggle,
                className="refresh-control-actions",
                **{"aria-label": "Display controls"},
            ),
            className="refresh-controls",
        )

    controls_disabled = initial_snapshot is None
    commodity_enabled = bool(
        initial_snapshot.commodity_market_enabled
        if initial_snapshot is not None
        else False
    )
    checker_enabled = bool(
        initial_snapshot.risk_checker_enabled if initial_snapshot is not None else True
    )
    if initial_snapshot is not None:
        refreshed_at = initial_snapshot.refreshed_at.strftime("%H:%M:%S UTC")
        status_text = (
            f"Last success {refreshed_at} · "
            f"T-1 risk {int(((initial_snapshot.risk_status['Age'] == 0) & ~initial_snapshot.risk_status['Force Risk'].astype(bool)).sum())} · "
            f"Forced risk {int((initial_snapshot.risk_status['Force Risk'].astype(bool)).sum())}"
        )
        status_class = "refresh-status"
    elif initial_error:
        status_text = "Initial data load failed · no snapshot was published"
        status_class = "refresh-status is-error"
    elif initial_loading:
        status_text = "Opening Cube · loading the first validated snapshot"
        status_class = "refresh-status is-refreshing"
    else:
        status_text = "Open Risk to load the first validated snapshot"
        status_class = "refresh-status"

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Button(
                                "Refresh Portfolios",
                                id=f"{id_prefix}refresh-portfolios-button",
                                n_clicks=0,
                                disabled=controls_disabled,
                                className="refresh-portfolios-button",
                                title="Reload the portfolio mapping only",
                                type="button",
                            ),
                            html.Button(
                                "Refresh Risk",
                                id=f"{id_prefix}reload-risk-button",
                                n_clicks=0,
                                disabled=controls_disabled,
                                className="reload-risk-button",
                                title="Refresh Risk (Shift+F8)",
                                type="button",
                                **{"aria-keyshortcuts": "Shift+F8"},
                            ),
                            html.Button(
                                "Refresh PL",
                                id=f"{id_prefix}refresh-pl-button",
                                n_clicks=0,
                                disabled=controls_disabled,
                                className="refresh-pl-button",
                                title="Refresh PL (Shift+F9)",
                                type="button",
                                **{"aria-keyshortcuts": "Shift+F9"},
                            ),
                            html.Button(
                                f"Commo: {'On' if commodity_enabled else 'Off'}",
                                id=f"{id_prefix}commo-market-toggle",
                                n_clicks=0,
                                disabled=controls_disabled,
                                className=(
                                    "data-source-toggle is-on"
                                    if commodity_enabled
                                    else "data-source-toggle is-off"
                                ),
                                title=(
                                    "Commodity market data is On"
                                    if commodity_enabled
                                    else "Commodity market data is Off"
                                ),
                                type="button",
                                **{"aria-pressed": str(commodity_enabled).lower()},
                            ),
                            html.Button(
                                f"RiskChecker: {'On' if checker_enabled else 'Off'}",
                                id=f"{id_prefix}risk-checker-toggle",
                                n_clicks=0,
                                disabled=controls_disabled,
                                className=(
                                    "data-source-toggle is-on"
                                    if checker_enabled
                                    else "data-source-toggle is-off"
                                ),
                                title=(
                                    "Risk checker is On"
                                    if checker_enabled
                                    else "Risk checker is Off"
                                ),
                                type="button",
                                **{"aria-pressed": str(checker_enabled).lower()},
                            ),
                            html.Button(
                                "AutoPL: On",
                                id=f"{id_prefix}auto-refresh-toggle",
                                n_clicks=0,
                                disabled=controls_disabled,
                                className="data-source-toggle auto-refresh-toggle is-on",
                                title="Automatic 15-minute P&L refresh is On. Activate to turn it Off.",
                                type="button",
                                **{
                                    "aria-label": "AutoPL is On",
                                    "aria-pressed": "true",
                                },
                            ),
                            theme_toggle,
                        ],
                        className="refresh-control-actions",
                        role="group",
                        **{"aria-label": "Dashboard controls"},
                    ),
                    html.Div(
                        build_operating_date_content(initial_snapshot),
                        id=f"{id_prefix}operating-date-banner",
                        className="operating-date-banner",
                        **{"aria-label": "Committed market and risk dates"},
                    ),
                ],
                className="refresh-control-topline",
            ),
            html.Div(
                status_text,
                id=f"{id_prefix}refresh-status",
                className=status_class,
                **{"aria-live": "polite", "aria-atomic": "true"},
            ),
        ],
        className=(
            "refresh-controls is-initial-loading"
            if initial_loading
            else "refresh-controls"
        ),
    )


def _build_refresh_progress(
    stage_delays: Mapping[str, float] | None,
    *,
    initial_loading: bool = False,
    initial_error: bool = False,
) -> html.Div:
    """Build the shared progress hero without performing any source work."""
    stage_delay_values = dict(stage_delays or {})
    visible = initial_loading or initial_error
    if initial_error:
        title = "Initial data load failed"
        product = "No financial snapshot was published"
        function_name = "Use Retry after checking the connector error"
        class_name = "refresh-progress is-error"
    elif initial_loading:
        title = "Loading Cube data"
        product = "Preparing the first validated snapshot"
        function_name = "Waiting for the browser-triggered refresh"
        class_name = "refresh-progress is-running"
    else:
        title = "Refresh pipeline"
        product = "Preparing product queue"
        function_name = "Waiting for refresh request"
        class_name = "refresh-progress"

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            build_cube_loader("Refreshing Cube data", announce=False),
                            html.Span(
                                title,
                                id="refresh-progress-title",
                                className="refresh-progress-title",
                            ),
                        ],
                        className="refresh-progress-title-wrap",
                    ),
                    html.Span(
                        "",
                        id="refresh-progress-elapsed",
                        className="refresh-progress-elapsed",
                        **{"aria-hidden": "true"},
                    ),
                ],
                className="refresh-progress-header",
            ),
            html.Div(
                [
                    html.Strong(
                        product,
                        id="refresh-progress-product",
                        className="refresh-product-name",
                    ),
                    html.Span("Active call", className="refresh-function-label"),
                    html.Code(
                        function_name,
                        id="refresh-progress-function",
                        className="refresh-function-name",
                    ),
                    html.Span(
                        "",
                        id="refresh-progress-source",
                        className="refresh-function-source",
                    ),
                    html.Span(
                        "",
                        id="refresh-progress-count",
                        className="refresh-function-count",
                    ),
                    html.Span(
                        "",
                        id="refresh-progress-hold",
                        className="refresh-function-hold",
                    ),
                    html.Span(
                        html.Span(
                            id="refresh-progress-bar",
                            className="refresh-progress-bar-fill",
                        ),
                        id="refresh-progress-bar-track",
                        className="refresh-progress-bar-track",
                    ),
                ],
                className="refresh-function-live refresh-product-card",
                role="status",
                **{"aria-live": "polite", "aria-atomic": "true"},
            ),
            html.P(
                "The current committed snapshot stays usable while a staged refresh runs; refresh controls are locked until it finishes.",
                className="refresh-progress-note",
            ),
            html.Ol(
                [
                    html.Li(
                        [
                            html.Span("", className="refresh-stage-icon"),
                            html.Span(
                                "Load RiskChecker readiness",
                                className="refresh-stage-function",
                            ),
                            html.Span(className="refresh-stage-duration"),
                        ],
                        id="refresh-stage-readiness",
                        className="refresh-stage",
                    ),
                    html.Li(
                        [
                            html.Span("", className="refresh-stage-icon"),
                            html.Span(
                                "Risk & @risk product calls (if dates changed)",
                                className="refresh-stage-function",
                            ),
                            html.Span(className="refresh-stage-duration"),
                        ],
                        id="refresh-stage-risk",
                        className="refresh-stage",
                    ),
                    html.Li(
                        [
                            html.Span("", className="refresh-stage-icon"),
                            html.Span(
                                "Open + Current market",
                                className="refresh-stage-function",
                            ),
                            html.Span(className="refresh-stage-duration"),
                        ],
                        id="refresh-stage-market",
                        className="refresh-stage",
                    ),
                    html.Li(
                        [
                            html.Span("", className="refresh-stage-icon"),
                            html.Span(
                                "Calculate product P&L",
                                className="refresh-stage-function",
                            ),
                            html.Span(className="refresh-stage-duration"),
                        ],
                        id="refresh-stage-pl",
                        className="refresh-stage",
                    ),
                    html.Li(
                        [
                            html.Span("", className="refresh-stage-icon"),
                            html.Span(
                                "Validate + publish snapshot",
                                className="refresh-stage-function",
                            ),
                            html.Span("Finalising", className="refresh-stage-duration"),
                        ],
                        id="refresh-stage-final",
                        className="refresh-stage",
                    ),
                ],
                className="refresh-stage-list",
            ),
        ],
        id="refresh-progress",
        className=class_name,
        hidden=not visible,
        **{
            "data-risk-product-delay": str(
                0.0
                if initial_loading or initial_error
                else stage_delay_values.get("risk_product", 0.0)
            ),
            "data-initial-load": "true" if visible else "false",
        },
    )


def build_shared_refresh_shell(
    initial_snapshot: ControlSnapshotProtocol | RefreshSnapshotProtocol | None,
    *,
    refresh_enabled: bool,
    stage_delays: Mapping[str, float] | None = None,
    initial_loading: bool = False,
    initial_error: str | None = None,
    keep_polling: bool = False,
    data_revision: int | None = None,
    style: Mapping[str, str] | None = None,
) -> html.Div:
    """Build the one persistent refresh lifecycle mounted above Dash Pages."""
    applied_forced_dates = (
        {
            str(source): pd.Timestamp(value).date().isoformat()
            for source, value in initial_snapshot.forced_dates.items()
        }
        if initial_snapshot is not None
        else {}
    )
    applied_view_date = (
        pd.Timestamp(initial_snapshot.forced_view_date).date().isoformat()
        if initial_snapshot is not None
        and initial_snapshot.forced_view_date is not None
        else None
    )
    applied_commodity_market = bool(
        initial_snapshot.commodity_market_enabled
        if initial_snapshot is not None
        else False
    )
    applied_risk_checker = bool(
        initial_snapshot.risk_checker_enabled if initial_snapshot is not None else True
    )
    revision = initial_snapshot.revision if initial_snapshot is not None else 0
    rendered_revision = (
        revision if data_revision is None else max(0, int(data_revision))
    )
    error = str(initial_error or "")

    return html.Div(
        [
            dcc.Store(id="data-revision-store", data=rendered_revision),
            html.Span(revision, id="refresh-commit-revision", hidden=True),
            # Backend-affecting settings mirror the one process-wide committed
            # snapshot. AutoPL alone is browser-local scheduling state.
            dcc.Store(
                id="perspective-risk-cube-forced-risk-v1",
                data=applied_forced_dates,
            ),
            dcc.Store(
                id="perspective-risk-cube-view-date-v1",
                data=applied_view_date,
            ),
            dcc.Store(
                id="perspective-risk-cube-auto-refresh-v1",
                data=True,
                storage_type="local",
            ),
            dcc.Store(
                id="perspective-risk-cube-commodity-market-v1",
                data=applied_commodity_market,
            ),
            dcc.Store(
                id="perspective-risk-cube-risk-checker-v1",
                data=applied_risk_checker,
            ),
            dcc.Store(id="force-risk-draft-store", data={}),
            dcc.Store(id="force-risk-render-store", data={}),
            dcc.Store(id="refresh-result-store", data=0),
            dcc.Store(id="refresh-busy-store", data=False),
            dcc.Interval(
                id="auto-refresh-interval",
                interval=15 * 60_000,
                n_intervals=0,
                disabled=True,
            ),
            # Once Risk starts revision 1 this common poll survives navigation,
            # allowing the shell to receive the terminal snapshot even if the
            # cold page and its own interval unmount.
            dcc.Interval(
                id="shared-refresh-bootstrap-interval",
                interval=500,
                n_intervals=0,
                disabled=not (initial_loading or keep_polling),
            ),
            html.Section(
                _build_refresh_controls(
                    initial_snapshot,
                    refresh_enabled=refresh_enabled,
                    initial_loading=initial_loading,
                    initial_error=bool(error),
                ),
                id="refresh-control-strip",
                className="cube-refresh-strip",
                **{"aria-label": "Dashboard controls"},
            ),
            (
                _build_refresh_progress(
                    stage_delays,
                    initial_loading=initial_loading,
                    initial_error=bool(error),
                )
                if refresh_enabled
                else None
            ),
            html.Div(
                error,
                id="error-log",
                className="error-log has-errors" if error else "error-log",
                **{"aria-live": "polite"},
            ),
        ],
        id="shared-refresh-shell",
        style=dict(style) if style is not None else None,
    )


def build_initial_load_layout(
    *,
    stage_delays: Mapping[str, float] | None = None,
    error: str | None = None,
    retry_enabled: bool = True,
    keep_polling: bool = False,
    include_shared_refresh_shell: bool = True,
) -> html.Div:
    """Render the usable app shell before the first connector call begins."""
    loading = error is None
    return html.Div(
        [
            (
                build_shared_refresh_shell(
                    None,
                    refresh_enabled=True,
                    stage_delays=stage_delays,
                    initial_loading=loading,
                    initial_error=error,
                    keep_polling=keep_polling,
                )
                if include_shared_refresh_shell
                else None
            ),
            dcc.Interval(
                id="initial-load-trigger",
                interval=500,
                n_intervals=0,
                # Keep polling while another browser owns the first writer.
                # Replacing this shell with the full layout removes it.
                max_intervals=-1,
                # A failed transaction waits for Retry. A watchdog warning can
                # keep polling the same owned writer without starting another.
                disabled=error is not None and not keep_polling,
            ),
            html.H1("Cube Risk & PL", className="sr-only"),
            html.Div(
                [
                    html.P(
                        error or "Cube is preparing its first validated snapshot.",
                        id="initial-load-message",
                        className="initial-load-message",
                    ),
                    html.Button(
                        "Retry initial load",
                        id="initial-load-retry",
                        n_clicks=0,
                        hidden=error is None,
                        disabled=error is None or not retry_enabled,
                        className="reload-risk-button initial-load-retry",
                        type="button",
                    ),
                ],
                className="initial-load-actions",
                hidden=error is None,
            ),
        ],
        className="app-shell cube-app-shell cube-initial-load-shell",
    )


def build_layout(
    risk_data: pd.DataFrame,
    initial_snapshot: RefreshSnapshotProtocol | None = None,
    *,
    refresh_enabled: bool = False,
    stage_delays: Mapping[str, float] | None = None,
    include_shared_refresh_shell: bool = True,
) -> html.Div:
    """Build the application layout without registering routes or callbacks."""
    risk_values = ordered_unique(risk_data, "risk type")
    risk_options = [{"label": value, "value": value} for value in risk_values]
    split_options = [
        {"label": value, "value": value} for value in ordered_unique(risk_data, "split")
    ]
    expanded_metric_options = [
        {"label": metric_title(value), "value": value} for value in EXPANDABLE_METRICS
    ]
    detail_measure_options = [
        {"label": metric_title(value), "value": value} for value in DETAIL_MEASURES
    ]
    detail_component_options = [
        {"label": DETAIL_COMPONENT_LABELS[value], "value": value}
        for value in DETAIL_COMPONENTS["risk"]
    ]
    detail_tenor_options, _ = detail_tenor_view_state(pd.DataFrame(), "auto")
    view_dimension_options = [
        {"label": field.label, "value": field.key} for field in VIEW_DIMENSION_FIELDS
    ]

    dimension_filter_controls = [
        html.Div(
            [
                html.Label(field.label, htmlFor=DIMENSION_FILTER_IDS[field.key]),
                dcc.Dropdown(
                    id=DIMENSION_FILTER_IDS[field.key],
                    options=[
                        {"label": value, "value": value}
                        for value in ordered_unique(risk_data, field.key)
                    ],
                    multi=True,
                    placeholder=f"All {field.label.casefold()} values",
                    value=None,
                ),
            ],
            className="control-field",
        )
        for field in FILTER_DIMENSION_FIELDS
    ]
    initial_risk_type = risk_options[0]["value"]
    initial_ir_family = "delta" if initial_risk_type == "IR" else None
    initial_risk_frame = risk_data.loc[risk_data["risk type"].eq(initial_risk_type)]
    initial_risk_frame = filter_ir_family(
        initial_risk_frame,
        initial_risk_type,
        initial_ir_family,
    )
    if not initial_risk_frame.empty:
        initial_risk_frame = recompute_filtered_promotion(initial_risk_frame)
    if initial_risk_type == "Credit":
        initial_risk_frame = apply_credit_measure(
            initial_risk_frame,
            CREDIT_MEASURES[0],
        )
    initial_open_rows = default_open_rows(initial_risk_frame, initial_risk_type)
    initial_risk_table = build_risk_table(
        initial_risk_frame,
        [],
        initial_open_rows,
        dimension=DEFAULT_VIEW_DIMENSION,
        toggle_type="main-row-toggle",
        cell_type="main-risk-cell",
        index_label=initial_risk_type,
        promotion_enabled=True,
        region_enabled=False,
        underlying_sort_metric=DEFAULT_UNDERLYING_SORT_METRIC,
    )
    initial_aggregate_table = build_aggregate_pl_table(
        risk_data,
        DEFAULT_VIEW_DIMENSION,
        [],
    )
    top_book_open_rows = default_top_book_open_rows(risk_data)
    return html.Div(
        [
            (
                build_shared_refresh_shell(
                    initial_snapshot,
                    refresh_enabled=refresh_enabled,
                    stage_delays=stage_delays,
                )
                if include_shared_refresh_shell
                else None
            ),
            dcc.Store(
                id="open-rows-store",
                data=default_open_rows(risk_data, risk_options[0]["value"]),
            ),
            # Renderers listen to this synchronized context rather than the raw
            # risk tabs. A tab change can therefore update the Greek choices,
            # default open rows and selection state before expensive tables run.
            dcc.Store(id="risk-view-context-store", data=None),
            # Dynamic hierarchy controls publish small delegated DOM actions to
            # these stable stores. Keeping per-row/per-cell Dash IDs out of the
            # rendered tables prevents the callback graph from being remounted
            # whenever a large risk hierarchy is replaced.
            dcc.Store(id="risk-row-action-store", data=None),
            dcc.Store(id="risk-cell-action-store", data=None),
            dcc.Store(id="risk-metric-action-store", data=None),
            dcc.Store(id="top-book-cell-action-store", data=None),
            dcc.Store(id="top-book-row-action-store", data=None),
            dcc.Store(id="aggregate-open-risk-types", data=[]),
            dcc.Store(
                id="dimension-filter-store",
                data={field.key: None for field in FILTER_DIMENSION_FIELDS},
            ),
            dcc.Store(
                id="dimension-filter-values-store",
                # This Store is positional because the Risk reducer binds it to
                # FILTER_DIMENSION_FIELDS with ``zip(..., strict=True)``.  Its
                # initial value must match the blank dropdowns exactly; the old
                # mapping briefly turned field names into character filters and
                # could replace a warm table with an empty render during mount.
                data=[None for _field in FILTER_DIMENSION_FIELDS],
            ),
            dcc.Store(id="top-book-open-rows-store", data=top_book_open_rows),
            dcc.Store(id="selected-cell-store", data=None),
            dcc.Store(
                id="detail-component-request-store",
                data={"measure": "risk", "component": "total"},
            ),
            # Promotion toggle: True = promotion enabled (display bucket between risk greek and group)
            #                   False = promotion disabled (group immediately after risk greek)
            dcc.Store(id="promotion-toggle-store", data=True),
            # Region toggle: True = region column shown in hierarchy, False = hidden
            dcc.Store(id="region-toggle-store", data=False),
            html.Div(
                [
                    dcc.Checklist(
                        id="expanded-metrics",
                        options=expanded_metric_options,
                        value=[],
                    ),
                ],
                style={"display": "none"},
            ),
            html.H1("Cube Risk & PL", className="sr-only"),
            # New top-level controls: dates/readiness, dimension filters, quick search
            html.Details(
                [
                    html.Summary(
                        "Dates and readiness",
                        className="aux-summary risk-readiness-summary",
                    ),
                    html.Div(
                        [
                            html.Div(
                                build_risk_date_editor(
                                    initial_snapshot,
                                    {
                                        str(source): pd.Timestamp(value)
                                        .date()
                                        .isoformat()
                                        for source, value in initial_snapshot.forced_dates.items()
                                    }
                                    if initial_snapshot is not None
                                    else None,
                                ),
                                id="risk-date-editor",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        "All date settings are applied.",
                                        id="force-risk-edit-status",
                                        className="force-risk-edit-status",
                                        **{"aria-live": "polite"},
                                    ),
                                    html.Div(
                                        [
                                            html.Button(
                                                "Cancel",
                                                id="force-risk-cancel-button",
                                                n_clicks=0,
                                                disabled=True,
                                                className="force-risk-cancel-button",
                                            ),
                                            html.Button(
                                                "Apply date settings",
                                                id="force-risk-apply-button",
                                                n_clicks=0,
                                                disabled=True,
                                                className="force-risk-apply-button",
                                            ),
                                        ],
                                        className="force-risk-action-buttons",
                                    ),
                                ],
                                className="force-risk-actions",
                            ),
                        ],
                    ),
                ],
                open=False,
                className="aux-details risk-readiness-details top-controls",
            )
            if refresh_enabled
            else None,
            build_saved_filter_view_bar(RISK_SAVED_VIEW_CONTROLS)
            if refresh_enabled
            else None,
            html.Div(
                [
                    html.Div(
                        "Leave blank to include all values. Risk filters are independent from Stock filters.",
                        className="filter-note",
                    ),
                    html.Div(
                        dimension_filter_controls,
                        className="controls filter-controls",
                    ),
                    dcc.Checklist(
                        id="risk-filter-exclude-selected",
                        options=[
                            {
                                "label": "Exclude selected values",
                                "value": "exclude",
                            }
                        ],
                        value=[],
                        className="risk-filter-mode",
                    ),
                ],
                className="dimension-filter-bar top-controls",
            )
            if refresh_enabled
            else None,
            build_quick_search() if refresh_enabled else None,
            build_quick_market_search() if refresh_enabled else None,
            html.Details(
                [
                    html.Summary(
                        "Aggregate P&L", className="aux-summary aggregate-pl-summary"
                    ),
                    html.Div(
                        [
                            html.Div("View by", className="aggregate-pl-title"),
                            dcc.RadioItems(
                                id="aggregate-pl-dimension",
                                options=view_dimension_options,
                                value=DEFAULT_VIEW_DIMENSION,
                                inline=True,
                                className="aggregate-pl-selector",
                            ),
                        ],
                        className="aggregate-pl-header",
                    ),
                    html.Div(
                        dcc.Loading(
                            html.Div(
                                initial_aggregate_table,
                                id="aggregate-pl-grid",
                            ),
                            custom_spinner=build_cube_loader("Loading aggregate P&L"),
                            delay_show=120,
                            className="cube-loading-boundary",
                        ),
                        className="aggregate-pl-panel",
                    ),
                ],
                open=True,
                className="aux-details aggregate-pl-details",
            ),
            html.Details(
                [
                    html.Summary(
                        "Top of Book",
                        id="top-book-summary",
                        n_clicks=0,
                        className="aux-summary top-book-summary",
                    ),
                    html.Div(
                        dcc.Loading(
                            html.Div(id="top-book-grid"),
                            custom_spinner=build_cube_loader("Loading Top of Book"),
                            delay_show=120,
                            className="cube-loading-boundary",
                        ),
                        className="top-book-panel",
                    ),
                ],
                id="top-book-details",
                open=False,
                className="aux-details top-book-details",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Risk explorer", className="section-title"),
                            html.Div(
                                "Switch layout and reporting dimension without losing your filters.",
                                className="section-note",
                            ),
                        ],
                        className="section-heading",
                    ),
                    dcc.Tabs(
                        id="table-view-tabs",
                        value="main",
                        children=[
                            dcc.Tab(label="Cross", value="main"),
                            dcc.Tab(label="SplitVA", value="alt"),
                        ],
                        className="workspace-tabs view-mode-tabs",
                    ),
                    html.Div(
                        [
                            html.Span("Dimension", className="table-dimension-label"),
                            dcc.RadioItems(
                                id="table-dimension",
                                options=view_dimension_options,
                                value=DEFAULT_VIEW_DIMENSION,
                                inline=True,
                                className="table-dimension-selector",
                            ),
                        ],
                        className="table-dimension-control",
                    ),
                ],
                className="workspace-toolbar",
            ),
            dcc.Tabs(
                id="risk-type-tabs",
                value=risk_options[0]["value"],
                children=[
                    dcc.Tab(label=option["label"], value=option["value"])
                    for option in risk_options
                ],
                className="workspace-tabs risk-type-tabs",
            ),
            html.Div(
                [
                    dcc.Tabs(
                        id="credit-view-tabs",
                        value="single",
                        children=[
                            dcc.Tab(label="Single", value="single"),
                            dcc.Tab(label="Multi", value="multi"),
                        ],
                        className="workspace-tabs credit-view-tabs",
                    ),
                    html.Div(
                        [
                            html.Label("Credit measure", htmlFor="credit-measure"),
                            dcc.Dropdown(
                                id="credit-measure",
                                options=[
                                    {"label": measure, "value": measure}
                                    for measure in CREDIT_MEASURES
                                ],
                                value=CREDIT_MEASURES[0],
                                clearable=False,
                            ),
                        ],
                        id="credit-single-control",
                        className="credit-measure-control",
                    ),
                    html.Div(
                        [
                            html.Span("Show", className="credit-multi-label"),
                            dcc.RadioItems(
                                id="credit-multi-metric",
                                options=[
                                    {"label": metric_title(metric), "value": metric}
                                    for metric in METRIC_COLUMNS
                                ],
                                value="risk",
                                inline=True,
                                className="credit-multi-metric",
                            ),
                        ],
                        id="credit-multi-control",
                        className="credit-measure-control",
                        style={"display": "none"},
                    ),
                ],
                id="credit-view-controls",
                className="credit-view-controls",
                style={"display": "none"},
            ),
            dcc.Tabs(
                id="ir-family-tabs",
                value="delta",
                children=[
                    dcc.Tab(label="Delta", value="delta"),
                    dcc.Tab(label="Basis", value="basis"),
                    dcc.Tab(label="Vega", value="vega"),
                ],
                className="workspace-tabs ir-family-tabs",
                style={"display": "none"},
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Split", htmlFor="split-filter"),
                            dcc.Dropdown(
                                id="split-filter",
                                options=split_options,
                                multi=True,
                                placeholder="All splits",
                            ),
                        ],
                        className="control-field",
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Sort underlying by", htmlFor="underlying-sort-metric"
                            ),
                            dcc.Dropdown(
                                id="underlying-sort-metric",
                                options=[
                                    {
                                        "label": metric_title(metric),
                                        "value": metric,
                                    }
                                    for metric in UNDERLYING_SORT_METRICS
                                ],
                                value=DEFAULT_UNDERLYING_SORT_METRIC,
                                clearable=False,
                            ),
                        ],
                        className="control-field",
                    ),
                    html.Div(
                        [
                            html.Span("Options", className="control-label"),
                            html.Div(
                                [
                                    html.Button(
                                        "Promotion",
                                        id="promotion-toggle",
                                        n_clicks=0,
                                        disabled=False,
                                        className="data-source-toggle is-on promotion-toggle",
                                        title="Underlying promotion is on. Click to turn off (show group immediately).",
                                        type="button",
                                        **{
                                            "aria-label": "promotion-toggle",
                                            "aria-pressed": "true",
                                        },
                                    ),
                                    html.Button(
                                        "Region",
                                        id="region-toggle",
                                        n_clicks=0,
                                        disabled=False,
                                        className="data-source-toggle is-off region-toggle",
                                        title="Region is off. Click to show.",
                                        type="button",
                                        **{
                                            "aria-label": "region-toggle",
                                            "aria-pressed": "false",
                                        },
                                    ),
                                ],
                                className="toggle-controls inline-toggles",
                            ),
                        ],
                        className="control-field",
                    ),
                ],
                className="controls",
            ),
            html.Div(
                [
                    html.Span("Show", className="alt-metric-label"),
                    dcc.RadioItems(
                        id="alt-metric",
                        options=[
                            {"label": metric_title(metric), "value": metric}
                            for metric in METRIC_COLUMNS
                        ],
                        value="risk",
                        inline=True,
                        className="alt-metric-selector",
                    ),
                ],
                id="alt-metric-control",
                className="alt-metric-control",
                style={"display": "none"},
            ),
            html.Div(
                # Keep the current hierarchy mounted while a tab switch is
                # resolved. Replacing a readable table with a spinner forces
                # a full browser layout and makes ordinary navigation feel
                # like a data refresh. Explicit Refresh Risk / Refresh PL
                # operations retain the dedicated refresh progress loader.
                html.Div(
                    initial_risk_table,
                    id="risk-grid",
                    className="risk-grid",
                ),
                id="main-risk-panel",
                className="risk-panel",
            ),
            html.Div(
                html.Div(id="alt-risk-grid", className="risk-grid"),
                id="alt-risk-panel",
                className="risk-panel",
                style={"display": "none"},
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Detail metric", htmlFor="plot-measure"),
                                    dcc.Dropdown(
                                        id="plot-measure",
                                        options=detail_measure_options,
                                        value="risk",
                                        clearable=False,
                                    ),
                                ],
                                className="detail-plot-control",
                            ),
                            html.Div(
                                [
                                    html.Label("Series", htmlFor="plot-component"),
                                    dcc.Dropdown(
                                        id="plot-component",
                                        options=detail_component_options,
                                        value="total",
                                        clearable=False,
                                    ),
                                ],
                                className="detail-plot-control",
                            ),
                            html.Div(
                                [
                                    html.Label(
                                        "Tenor view", htmlFor="detail-tenor-view"
                                    ),
                                    dcc.Dropdown(
                                        id="detail-tenor-view",
                                        options=detail_tenor_options,
                                        value="auto",
                                        clearable=False,
                                    ),
                                ],
                                className="detail-plot-control",
                            ),
                        ],
                        className="detail-plot-controls",
                    ),
                    # Preserve the current detail while a new cell/tab context
                    # is resolved. A quiet non-animated loading label is
                    # provided by CSS on this stable output node; animated cube
                    # loaders are reserved for Risk/P&L refresh operations.
                    html.Div(id="detail-panel", className="detail-panel"),
                ],
                className="detail-shell",
            ),
            html.Details(
                [
                    html.Summary(
                        "Unmapped Books",
                        id="unmapped-books-summary",
                        n_clicks=0,
                        className="aux-summary",
                    ),
                    html.Div(id="unmapped-books-grid", className="unmapped-panel"),
                ],
                id="unmapped-books-details",
                open=False,
                className="aux-details",
                # Keep the callback graph identical in manager-backed and
                # static-data apps. Dash validates callback dependencies in
                # the browser, so conditionally removing these IDs would make
                # the unified Risk Explorer callback impossible to initialise
                # in static mode. Static apps hide the inert disclosure while
                # retaining the stable layout contract.
                hidden=not refresh_enabled,
            ),
        ],
        className="app-shell cube-app-shell",
    )


__all__ = [
    "RISK_SAVED_VIEW_CONTROLS",
    "build_aggregate_pl_table",
    "build_alt_risk_table",
    "build_columns",
    "build_credit_multi_table",
    "build_cube_loader",
    "build_detail_panel_with_state",
    "build_quick_search",
    "build_quick_search_pivot",
    "build_quick_market_search",
    "build_quick_market_result",
    "build_tenor_heatmap",
    "build_line_chart",
    "build_layout",
    "build_initial_load_layout",
    "build_operating_date_content",
    "build_risk_checker_inventory",
    "build_risk_date_editor",
    "build_risk_table",
    "build_shared_refresh_shell",
    "build_small_table",
    "build_top_book_exposures",
    "build_tree_rows",
    "build_unmapped_books_table",
    "metric_class",
    "metric_header",
    "metric_title",
    "selected_context_title",
    "default_top_book_open_rows",
    "detail_tenor_partitions",
    "detail_tenor_view_state",
    "top_book_exposure_frame",
    "top_book_hierarchy_frame",
]
