"""Pure components and source boundary for the dated Stock comparison page."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

import pandas as pd
from dash import dash_table, dcc, html

from adapters.s05_stock import (
    StockConnectorAdapter,
    StockSource,
    build_stock_adapter,
    normalize_stock_date,
)
from core.s01_schema import PORTFOLIO_MAPPED_COLUMN
from core.s08_stock import (
    CURRENT_MARKET_VALUE_COLUMN,
    MAPPED_STOCK_COMPARISON_COLUMNS,
    MARKET_VALUE_CHANGE_COLUMN,
    STOCK_CHANGE_COLUMN,
    STOCK_COLUMNS,
    STOCK_COMPARISON_NUMERIC_COLUMNS,
    STOCK_FILTER_COLUMN_BY_KEY,
    filter_stock_comparison,
    map_stock_comparison_portfolios,
)
from .s02_constants import FILTER_DIMENSION_FIELDS


STOCK_FILTER_FIELDS = FILTER_DIMENSION_FIELDS
STOCK_FILTER_IDS = {
    field.key: f"stock-{field.dash_filter_id}" for field in STOCK_FILTER_FIELDS
}


@dataclass(frozen=True)
class StockPageData:
    """One server-owned, mapped comparison and the dates that produced it."""

    mapped_stock: pd.DataFrame
    current_date: pd.Timestamp
    prior_date: pd.Timestamp
    portfolio_date: pd.Timestamp


def default_stock_dates(reference_date: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the prior two business dates relative to a market/reference date."""

    reference = normalize_stock_date(reference_date)
    current_date = reference - pd.offsets.BDay(1)
    prior_date = current_date - pd.offsets.BDay(1)
    return current_date, prior_date


def normalize_stock_date_pair(
    current_date: object,
    prior_date: object,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Validate two distinct, ordered Stock comparison dates."""

    current = normalize_stock_date(current_date)
    prior = normalize_stock_date(prior_date)
    if prior >= current:
        raise ValueError("Prior Stock date must be earlier than current Stock date")
    return current, prior


def _json_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    display = frame.astype(object).where(pd.notna(frame), None)
    return display.to_dict("records")


def stock_filter_map(
    values: Sequence[Sequence[str] | None],
) -> dict[str, list[str]]:
    """Bind Stock-only dropdown values to governed reporting keys."""

    return {
        field.key: list(selected or [])
        for field, selected in zip(STOCK_FILTER_FIELDS, values, strict=True)
    }


def stock_exclude_selected(value: Sequence[str] | None) -> bool:
    """Normalize the Stock-local exclusion checklist value."""

    return "exclude" in (value or [])


def stock_filter_options(
    mapped_stock: pd.DataFrame,
    selected_filters: Mapping[str, Sequence[str] | None] | None = None,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[str]]]:
    """Return full-snapshot options and selected values that remain valid."""

    selected = dict(selected_filters or {})
    unknown = sorted(set(selected) - set(STOCK_FILTER_COLUMN_BY_KEY))
    if unknown:
        raise ValueError(f"Unknown Stock reporting-dimension filters: {unknown}")
    options: dict[str, list[dict[str, str]]] = {}
    valid: dict[str, list[str]] = {}
    for field in STOCK_FILTER_FIELDS:
        column = field.external_name
        available = sorted(
            mapped_stock[column].dropna().astype(str).unique().tolist(),
            key=str.casefold,
        )
        options[field.key] = [{"label": value, "value": value} for value in available]
        valid[field.key] = [
            str(value)
            for value in (selected.get(field.key) or [])
            if str(value) in available
        ]
    return options, valid


def build_stock_table(mapped_stock: pd.DataFrame) -> dash_table.DataTable:
    """Render one already-mapped and optionally filtered Stock comparison."""

    if not isinstance(mapped_stock, pd.DataFrame):
        raise TypeError("mapped_stock must be a pandas DataFrame")
    missing = [
        column
        for column in MAPPED_STOCK_COMPARISON_COLUMNS
        if column not in mapped_stock
    ]
    if missing:
        raise ValueError(f"mapped_stock is missing required columns: {missing}")
    frame = mapped_stock[list(MAPPED_STOCK_COMPARISON_COLUMNS)].copy()
    columns = [
        {
            "name": column,
            "id": column,
            **(
                {"type": "numeric", "format": {"specifier": ",.2f"}}
                if column in STOCK_COMPARISON_NUMERIC_COLUMNS
                else {}
            ),
        }
        for column in frame.columns
    ]
    return dash_table.DataTable(
        id="stock-table",
        columns=columns,
        data=_json_records(frame),
        editable=False,
        filter_action="native",
        sort_action="native",
        sort_mode="multi",
        page_action="native",
        page_size=50,
        fixed_rows={"headers": True},
        style_table={"overflowX": "auto", "maxHeight": "72vh"},
        style_header={
            "backgroundColor": "#E3E5E7",
            "color": "#111111",
            "fontWeight": "700",
            "border": "1px solid #D9E0E7",
        },
        style_cell={
            "backgroundColor": "#FFFFFF",
            "color": "#111111",
            "border": "1px solid #E5E9ED",
            "fontFamily": "Inter, Segoe UI, Arial, sans-serif",
            "fontSize": "12px",
            "padding": "8px 10px",
            "textAlign": "left",
            "minWidth": "110px",
            "whiteSpace": "nowrap",
        },
        style_cell_conditional=[
            {
                "if": {"column_id": list(STOCK_COMPARISON_NUMERIC_COLUMNS)},
                "fontVariantNumeric": "tabular-nums",
                "textAlign": "right",
            }
        ],
        style_data_conditional=[
            {
                "if": {"filter_query": f"{{{PORTFOLIO_MAPPED_COLUMN}}} = false"},
                "backgroundColor": "#FFF3E0",
            },
            {
                "if": {
                    "filter_query": f"{{{MARKET_VALUE_CHANGE_COLUMN}}} < 0",
                    "column_id": MARKET_VALUE_CHANGE_COLUMN,
                },
                "color": "#B42318",
            },
            {
                "if": {"filter_query": f'{{{STOCK_CHANGE_COLUMN}}} = "Added"'},
                "backgroundColor": "#ECFDF3",
            },
            {
                "if": {"filter_query": f'{{{STOCK_CHANGE_COLUMN}}} = "Removed"'},
                "backgroundColor": "#FFF7ED",
            },
        ],
        tooltip_header={
            "Portfolio": "Stock Portfolio used for the governed mapping",
            PORTFOLIO_MAPPED_COLUMN: (
                "True when Portfolio exists in the authoritative mapping"
            ),
            CURRENT_MARKET_VALUE_COLUMN: "Market value on the selected current date",
            MARKET_VALUE_CHANGE_COLUMN: "Current market value minus prior market value",
        },
    )


def build_stock_table_panel(
    filtered: pd.DataFrame,
    *,
    has_unfiltered_rows: bool,
) -> object:
    if not filtered.empty:
        return build_stock_table(filtered)
    message = (
        "No Stock rows match the selected filters."
        if has_unfiltered_rows
        else "GetStock returned no rows for either selected date."
    )
    return html.Div(message, id="stock-empty-state", className="static-data-empty")


def stock_summary_text(
    filtered: pd.DataFrame,
    *,
    total_rows: int,
    current_date: pd.Timestamp,
    prior_date: pd.Timestamp,
) -> tuple[str, str, str]:
    """Return the three page counters for one filtered Stock view."""

    mapped_count = int(filtered[PORTFOLIO_MAPPED_COLUMN].eq(True).sum())
    unmapped_count = len(filtered) - mapped_count
    rows = (
        f"Rows: {len(filtered):,} of {total_rows:,} · Current "
        f"{current_date.date().isoformat()} · Prior {prior_date.date().isoformat()}"
    )
    return rows, f"Mapped: {mapped_count:,}", f"Unmapped: {unmapped_count:,}"


def build_stock_page_from_data(
    page_data: StockPageData,
    *,
    selected_filters: Mapping[str, Sequence[str] | None] | None = None,
    exclude_selected: bool = False,
) -> html.Main:
    """Build Stock comparison content from one server-owned page snapshot."""

    filtered = filter_stock_comparison(
        page_data.mapped_stock,
        dict(selected_filters or {}),
        exclude_selected=exclude_selected,
    )
    rows, mapped, unmapped = stock_summary_text(
        filtered,
        total_rows=len(page_data.mapped_stock),
        current_date=page_data.current_date,
        prior_date=page_data.prior_date,
    )
    return html.Main(
        [
            html.Div(
                [
                    html.Span(
                        rows,
                        id="stock-row-count",
                        className="static-data-row-count",
                    ),
                    html.Span(
                        mapped,
                        id="stock-mapped-count",
                        className="static-data-col-count",
                    ),
                    html.Span(
                        unmapped,
                        id="stock-unmapped-count",
                        className="static-data-col-count",
                    ),
                ],
                className="static-data-meta",
            ),
            html.Div(
                build_stock_table_panel(
                    filtered,
                    has_unfiltered_rows=not page_data.mapped_stock.empty,
                ),
                id="stock-table-panel",
                className="static-data-panel",
            ),
        ],
        id="stock-comparison-view",
        **{
            "data-stock-columns": ",".join(STOCK_COLUMNS),
            "data-current-date": page_data.current_date.date().isoformat(),
            "data-prior-date": page_data.prior_date.date().isoformat(),
        },
    )


def build_stock_page_shell(
    *,
    current_date: object,
    prior_date: object,
) -> html.Main:
    """Paint the complete Stock control shell before any connector work."""

    current, prior = normalize_stock_date_pair(current_date, prior_date)
    filter_controls = [
        html.Div(
            [
                html.Label(field.label, htmlFor=STOCK_FILTER_IDS[field.key]),
                dcc.Dropdown(
                    id=STOCK_FILTER_IDS[field.key],
                    options=[],
                    multi=True,
                    placeholder=f"All {field.label.casefold()} values",
                    value=[],
                ),
            ],
            className="control-field",
        )
        for field in STOCK_FILTER_FIELDS
    ]
    return html.Main(
        [
            dcc.Store(id="stock-loaded-revision", data=-1),
            dcc.Store(id="stock-loaded-dates", data=None),
            dcc.Store(
                id="stock-dimension-filter-store",
                data={
                    "filters": {field.key: [] for field in STOCK_FILTER_FIELDS},
                    "exclude_selected": False,
                },
            ),
            dcc.Interval(
                id="stock-load-trigger",
                interval=1_000,
                n_intervals=0,
                disabled=False,
            ),
            html.Div(
                [
                    html.H2("Stock", className="static-data-page-title"),
                    html.P(
                        "Compare two dated Stock snapshots, enriched through the authoritative Portfolio mapping.",
                        className="static-data-page-note",
                    ),
                ],
                className="static-data-header",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label(
                                "Current stock date", htmlFor="stock-current-date"
                            ),
                            dcc.DatePickerSingle(
                                id="stock-current-date",
                                date=current.date().isoformat(),
                                display_format="YYYY-MM-DD",
                                clearable=False,
                            ),
                        ],
                        className="control-field",
                    ),
                    html.Div(
                        [
                            html.Label("Prior stock date", htmlFor="stock-prior-date"),
                            dcc.DatePickerSingle(
                                id="stock-prior-date",
                                date=prior.date().isoformat(),
                                display_format="YYYY-MM-DD",
                                clearable=False,
                            ),
                        ],
                        className="control-field",
                    ),
                    html.Button(
                        "Compare dates",
                        id="stock-compare-button",
                        n_clicks=0,
                        className="refresh-button",
                    ),
                ],
                className="controls top-controls",
            ),
            html.Div(
                [
                    html.Div(
                        "Leave blank to include all values. Stock filters are independent from Risk filters.",
                        className="filter-note",
                    ),
                    html.Div(filter_controls, className="controls"),
                    dcc.Checklist(
                        id="stock-filter-exclude-selected",
                        options=[
                            {
                                "label": "Exclude selected values",
                                "value": "exclude",
                            }
                        ],
                        value=[],
                        className="stock-filter-mode",
                    ),
                ],
                className="dimension-filter-bar top-controls",
            ),
            dcc.Loading(
                html.Div(
                    [
                        html.P(
                            "Loading both GetStock dates and the Portfolio mapping…",
                            className="static-data-page-note",
                        )
                    ],
                    id="stock-page-content",
                ),
                delay_show=120,
            ),
        ],
        id="stock-page",
        className="static-data-page",
    )


def load_stock_page_data(
    *,
    stock_source: StockSource | StockConnectorAdapter,
    portfolio_config_source: (
        pd.DataFrame | str | Path | Callable[[pd.Timestamp], pd.DataFrame | str | Path]
    ),
    current_date: object,
    prior_date: object,
    portfolio_date: object | None = None,
) -> StockPageData:
    """Resolve both dated Stock legs and one current Portfolio authority."""

    current, prior = normalize_stock_date_pair(current_date, prior_date)
    selected_portfolio_date = normalize_stock_date(
        current if portfolio_date is None else portfolio_date
    )
    adapter = (
        stock_source
        if isinstance(stock_source, StockConnectorAdapter)
        else build_stock_adapter(stock=stock_source)
    )
    current_stock = adapter.get_stock(current)
    prior_stock = adapter.get_stock(prior)
    portfolio_config = (
        portfolio_config_source(selected_portfolio_date)
        if callable(portfolio_config_source)
        else portfolio_config_source
    )
    mapped = map_stock_comparison_portfolios(
        current_stock,
        prior_stock,
        portfolio_config,
    )
    return StockPageData(
        mapped_stock=mapped,
        current_date=current,
        prior_date=prior,
        portfolio_date=selected_portfolio_date,
    )


def build_stock_page(
    current_stock: pd.DataFrame,
    prior_stock: pd.DataFrame,
    portfolio_config: pd.DataFrame | str | Path,
    *,
    current_date: object,
    prior_date: object,
    selected_filters: Mapping[str, Sequence[str] | None] | None = None,
    exclude_selected: bool = False,
) -> html.Main:
    """Build the pure mapped comparison page from in-memory inputs."""

    current, prior = normalize_stock_date_pair(current_date, prior_date)
    data = StockPageData(
        mapped_stock=map_stock_comparison_portfolios(
            current_stock,
            prior_stock,
            portfolio_config,
        ),
        current_date=current,
        prior_date=prior,
        portfolio_date=current,
    )
    return build_stock_page_from_data(
        data,
        selected_filters=selected_filters,
        exclude_selected=exclude_selected,
    )


def build_stock_page_from_sources(
    *,
    stock_source: StockSource | StockConnectorAdapter,
    portfolio_config_source: (
        pd.DataFrame | str | Path | Callable[[pd.Timestamp], pd.DataFrame | str | Path]
    ),
    current_date: object,
    prior_date: object,
    portfolio_date: object | None = None,
    selected_filters: Mapping[str, Sequence[str] | None] | None = None,
    exclude_selected: bool = False,
) -> html.Main:
    """Load both snapshots, then delegate to the pure Stock page builder."""

    page_data = load_stock_page_data(
        stock_source=stock_source,
        portfolio_config_source=portfolio_config_source,
        current_date=current_date,
        prior_date=prior_date,
        portfolio_date=portfolio_date,
    )
    return build_stock_page_from_data(
        page_data,
        selected_filters=selected_filters,
        exclude_selected=exclude_selected,
    )


__all__ = [
    "STOCK_FILTER_FIELDS",
    "STOCK_FILTER_IDS",
    "StockPageData",
    "build_stock_page",
    "build_stock_page_from_data",
    "build_stock_page_from_sources",
    "build_stock_page_shell",
    "build_stock_table",
    "build_stock_table_panel",
    "default_stock_dates",
    "load_stock_page_data",
    "normalize_stock_date_pair",
    "stock_exclude_selected",
    "stock_filter_map",
    "stock_filter_options",
    "stock_summary_text",
]
