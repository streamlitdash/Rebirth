"""Dash components for governed P&L adjustment, send, save, and exploration."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import pandas as pd
from dash import dash_table, dcc, html

from .s02_constants import (
    DEFAULT_VIEW_DIMENSION,
    FILTER_DIMENSION_FIELDS,
    VIEW_DIMENSION_FIELDS,
)
from .s04_components import build_aggregate_pl_table, build_cube_loader
from .s11_saved_views import SavedFilterViewControls, build_saved_filter_view_bar
from .s12_plhistory import build_pl_history_series_selector
from .s13_validate_pl import build_validate_pl_section
from .s14_pl_filters import PL_FILTER_EXCLUDE_ID, PL_FILTER_IDS


DISPLAY_COLUMNS = (
    "Risk Type",
    "Risk Greek",
    "Portfolio",
    "SignoffGroup",
    "ConcertoField",
    "PL",
    "Adjustment",
)

GRID_ROW_ID = "id"
PL_AGGREGATE_TOGGLE_TYPE = "pnl-aggregate-row-toggle"
PL_FILTER_FIELDS = FILTER_DIMENSION_FIELDS
PL_SAVED_VIEW_CONTROLS = SavedFilterViewControls(
    scope="pnl",
    prefix="pnl",
    fields=PL_FILTER_FIELDS,
    filter_ids=PL_FILTER_IDS,
    exclude_id=PL_FILTER_EXCLUDE_ID,
)
PL_FILTER_NOTE = (
    "Include mode uses OR within one filter (for example B or D) and AND across "
    "filters. Exclude mode removes a row if it matches any selected value in any "
    "populated filter. Leave blank for all values; live P&L selections remain "
    "independent from Risk and Stock."
)


def pl_filter_map(
    values: Sequence[Sequence[str] | None],
) -> dict[str, list[str]]:
    """Normalize P&L-local reporting filters without sharing Risk/Stock state."""
    if len(values) != len(PL_FILTER_FIELDS):
        raise ValueError(
            f"Expected {len(PL_FILTER_FIELDS)} P&L filters; found {len(values)}"
        )
    return {
        field.key: [str(value) for value in (selected or []) if str(value).strip()]
        for field, selected in zip(PL_FILTER_FIELDS, values, strict=True)
    }


def pl_filter_options(frame: pd.DataFrame) -> dict[str, list[dict[str, str]]]:
    """Return stable options for each independent P&L reporting filter."""
    result: dict[str, list[dict[str, str]]] = {}
    for field in PL_FILTER_FIELDS:
        canonical: dict[str, str] = {}
        for raw in frame[field.key].dropna().tolist():
            value = str(raw).strip()
            if value:
                canonical.setdefault(value.casefold(), value)
        values = sorted(canonical.values(), key=str.casefold)
        result[field.key] = [{"label": value, "value": value} for value in values]
    return result


def _walk_components(component: object) -> Iterable[object]:
    """Yield a Dash component tree without relying on private Dash helpers."""
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk_components(child)
    else:
        yield from _walk_components(children)


def build_pl_aggregate_table(
    frame: pd.DataFrame,
    dimension: str,
    open_risk_types: list[str] | None,
) -> html.Div:
    """Render Aggregate P&L with page-owned, collision-free chevron IDs."""
    table = build_aggregate_pl_table(frame, dimension, open_risk_types)
    for component in _walk_components(table):
        component_id = getattr(component, "id", None)
        if not isinstance(component_id, dict):
            continue
        if component_id.get("type") != "aggregate-row-toggle":
            continue
        risk_type = component_id.get("risk_type", component_id.get("risk type"))
        component.id = {
            "type": PL_AGGREGATE_TOGGLE_TYPE,
            "risk_type": str(risk_type),
        }
    return table


def _pl_aggregate_section(
    initial_frame: pd.DataFrame | None = None,
) -> html.Section:
    """Build an always-visible, P&L-local mapped Aggregate P&L section."""
    view_dimension_options = [
        {"label": field.label, "value": field.key} for field in VIEW_DIMENSION_FIELDS
    ]
    return html.Section(
        [
            html.H2(
                "Aggregate P&L",
                className="aux-summary aggregate-pl-summary pnl-static-heading",
            ),
            html.Div(
                [
                    html.Div("View by", className="aggregate-pl-title"),
                    dcc.RadioItems(
                        id="pnl-aggregate-pl-dimension",
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
                        (
                            build_pl_aggregate_table(
                                initial_frame,
                                DEFAULT_VIEW_DIMENSION,
                                [],
                            )
                            if initial_frame is not None
                            else html.Div(
                                "P&L data is still loading. Aggregate P&L will "
                                "update after the first committed refresh.",
                                className="empty-state",
                                role="status",
                            )
                        ),
                        id="pnl-aggregate-pl-grid",
                    ),
                    custom_spinner=build_cube_loader("Loading aggregate P&L"),
                    delay_show=120,
                    className="cube-loading-boundary",
                ),
                className="aggregate-pl-panel",
            ),
        ],
        className="aux-details aggregate-pl-details pnl-always-open-section",
    )


def build_pl_filter_bar(initial_frame: pd.DataFrame | None = None) -> html.Div:
    """Build the single authoritative five-field P&L filter row."""
    options = (
        pl_filter_options(initial_frame)
        if initial_frame is not None and not initial_frame.empty
        else {field.key: [] for field in PL_FILTER_FIELDS}
    )
    controls = [
        html.Div(
            [
                html.Label(field.label, htmlFor=PL_FILTER_IDS[field.key]),
                dcc.Dropdown(
                    id=PL_FILTER_IDS[field.key],
                    options=options[field.key],
                    value=[],
                    multi=True,
                    placeholder=f"All {field.label.casefold()} values",
                ),
            ],
            className="control-field",
        )
        for field in PL_FILTER_FIELDS
    ]
    return html.Div(
        [
            html.Div(
                [
                    *controls,
                    dcc.Checklist(
                        id=PL_FILTER_EXCLUDE_ID,
                        options=[
                            {
                                "label": "Exclude rows matching any selected value",
                                "value": "exclude",
                            }
                        ],
                        value=[],
                        className="risk-filter-mode filter-mode-control",
                    ),
                ],
                className="controls pnl-filter-controls",
            ),
        ],
        id="pnl-filter-bar",
        className="dimension-filter-bar top-controls",
    )


def _editor_columns(*, portfolio_editable: bool) -> list[dict[str, object]]:
    """Return native DataTable columns with explicitly governed editability."""
    editable_columns = {"Risk Type", "Risk Greek", "PL"}
    if portfolio_editable:
        editable_columns.add("Portfolio")
    return [
        {
            "name": column,
            "id": column,
            "editable": column in editable_columns,
            **(
                {
                    "type": "numeric",
                    "on_change": {"action": "coerce", "failure": "reject"},
                }
                if column == "PL"
                else {}
            ),
            **(
                {"presentation": "dropdown"}
                if column in {"Risk Type", "Risk Greek", "Portfolio"}
                and column in editable_columns
                else {}
            ),
        }
        for column in DISPLAY_COLUMNS
    ]


def _editor_table(table_id: str, *, portfolio_editable: bool) -> dash_table.DataTable:
    """Build a native spreadsheet editor that patches only changed cells."""
    return dash_table.DataTable(
        id=table_id,
        columns=_editor_columns(portfolio_editable=portfolio_editable),
        data=[],
        editable=True,
        dropdown={},
        dropdown_conditional=[],
        sort_action="none",
        filter_action="none",
        page_action="none",
        cell_selectable=True,
        include_headers_on_copy_paste=True,
        fill_width=False,
        markdown_options={"html": True},
        style_table={
            "height": "460px",
            "overflowX": "auto",
            "overflowY": "auto",
            "border": "1px solid #D9DEE5",
            "borderRadius": "8px",
        },
        style_header={
            "height": "40px",
            "backgroundColor": "#F7F8FA",
            "color": "#111111",
            "border": "1px solid #D9DEE5",
            "fontFamily": '"Segoe UI Variable Text", "Segoe UI", Arial, sans-serif',
            "fontSize": "12px",
            "fontWeight": "850",
            "textAlign": "left",
        },
        style_cell={
            "height": "38px",
            "backgroundColor": "#FFFFFF",
            "color": "#111111",
            "border": "1px solid #E2E6EA",
            "fontFamily": '"Segoe UI Variable Text", "Segoe UI", Arial, sans-serif',
            "fontSize": "13px",
            "lineHeight": "1.35",
            "padding": "8px 10px",
            "textAlign": "left",
            "whiteSpace": "nowrap",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
        },
        style_cell_conditional=[
            {
                "if": {"column_id": "Risk Type"},
                "width": "126px",
                "minWidth": "126px",
                "maxWidth": "126px",
                "backgroundColor": "#C4DEF5",
                "color": "#111111",
                "fontWeight": "850",
                "borderLeft": "2px solid #111111",
                "borderRight": "2px solid #111111",
            },
            {
                "if": {"column_id": "Risk Greek"},
                "width": "132px",
                "minWidth": "132px",
                "maxWidth": "132px",
            },
            {
                "if": {"column_id": "Portfolio"},
                "width": "188px",
                "minWidth": "188px",
                "maxWidth": "188px",
            },
            {
                "if": {"column_id": "SignoffGroup"},
                "width": "188px",
                "minWidth": "188px",
                "maxWidth": "188px",
                "backgroundColor": "#F2F4F6",
                "color": "#4D5965",
            },
            {
                "if": {"column_id": "ConcertoField"},
                "width": "202px",
                "minWidth": "202px",
                "maxWidth": "202px",
                "backgroundColor": "#F2F4F6",
                "color": "#4D5965",
            },
            {
                "if": {"column_id": "PL"},
                "width": "146px",
                "minWidth": "146px",
                "maxWidth": "146px",
                "backgroundColor": "#FFFFE0",
                "color": "#111111",
                "fontWeight": "850",
                "fontVariantNumeric": "tabular-nums",
                "textAlign": "right",
                "borderLeft": "2px solid #111111",
                "borderRight": "2px solid #111111",
            },
            {
                "if": {"column_id": "Adjustment"},
                "width": "118px",
                "minWidth": "118px",
                "maxWidth": "118px",
                "textAlign": "center",
            },
        ],
        style_header_conditional=[
            {
                "if": {"column_id": "Risk Type"},
                "backgroundColor": "#C4DEF5",
                "color": "#111111",
                "borderLeft": "2px solid #111111",
                "borderRight": "2px solid #111111",
            },
            {
                "if": {"column_id": "PL"},
                "backgroundColor": "#FFFFE0",
                "color": "#111111",
                "borderLeft": "2px solid #111111",
                "borderRight": "2px solid #111111",
            },
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{PL} < 0", "column_id": "PL"}, "color": "#B42318"},
            {
                "if": {"state": "active"},
                "boxShadow": "inset 0 0 0 2px #111111",
            },
            {
                "if": {"state": "selected"},
                "backgroundColor": "#EAF2FA",
                "boxShadow": "inset 0 0 0 1px #111111",
            },
        ],
        tooltip_header={
            "SignoffGroup": "Derived from the governed Portfolio registry",
            "ConcertoField": "Derived from the Risk Type + Risk Greek mapping",
        },
        css=[
            {
                "selector": ".Select-menu-outer",
                "rule": "display: block !important; z-index: 1200 !important;",
            },
            {
                "selector": "td.dropdown .Select-control",
                "rule": "height: 36px; border: 0; border-radius: 0; box-shadow: none; font: 600 13px 'Segoe UI Variable Text', 'Segoe UI', Arial, sans-serif;",
            },
        ],
    )


def build_pl_send_sections() -> list[html.Div | html.Details]:
    """Return independently collapsible governed P&L sections and their state."""
    send_all = html.Div(
        html.Div(
            [
                html.Div(
                    [
                        html.H2("Send All P&L", className="section-title"),
                        html.P(
                            "Send the complete governed effective P&L, including "
                            "saved adjustments, to both the SOG and Portfolio "
                            "destinations in one action.",
                            className="unmapped-note",
                        ),
                    ]
                ),
                html.Div(
                    [
                        html.Button(
                            "Send All P&L",
                            id="send-all-pl-button",
                            n_clicks=0,
                            className="pl-action-send",
                            type="button",
                        ),
                        html.Div(
                            id="pl-send-all-status",
                            className="pl-send-status",
                            role="status",
                            **{"aria-live": "polite"},
                        ),
                    ],
                    className="pl-send-actions",
                ),
            ],
            className="pl-send-panel pl-editor-toolbar",
        ),
        id="pl-send-all-panel",
        className="aux-details",
    )
    by_sog = html.Details(
        [
            html.Summary(
                "SOG P&L",
                id="pl-sog-summary",
                n_clicks=0,
                className="aux-summary",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label(
                                        "SignoffGroup", htmlFor="pl-send-sog-filter"
                                    ),
                                    dcc.Dropdown(
                                        id="pl-send-sog-filter",
                                        options=[],
                                        clearable=False,
                                    ),
                                ],
                                className="pl-editor-filter",
                            ),
                            dcc.Checklist(
                                id="pl-sog-include-adjustments",
                                options=[
                                    {"label": "Show adjustments", "value": "include"}
                                ],
                                value=[],
                                className="pl-adjustment-toggle",
                            ),
                        ],
                        className="pl-editor-toolbar",
                    ),
                    html.P(
                        "Single-click Risk Type, Risk Greek, Portfolio or PL to edit. "
                        "Derived fields are locked; every changed or new row is "
                        "automatically marked as an adjustment. Enter commits and "
                        "Escape cancels the active edit.",
                        className="pl-editor-guide",
                    ),
                    html.Div(
                        "Waiting for SOG rows...",
                        id="pl-send-sog-grid-data-status",
                        className="pl-editor-statuses",
                        role="status",
                        **{"aria-live": "polite"},
                    ),
                    html.Div(
                        _editor_table("pl-send-sog-grid", portfolio_editable=True),
                        className="pl-send-editor-table pl-send-table--editor",
                    ),
                    html.Div(
                        [
                            html.Span(
                                id="pl-send-sog-grid-selection-summary-text",
                                className="pl-editor-selection-summary-text",
                            ),
                            html.Button(
                                "×",
                                id="pl-send-sog-grid-selection-clear",
                                className="pl-editor-selection-summary-dismiss",
                                type="button",
                                title="Clear selection",
                                **{"aria-label": "Clear selected cells"},
                            ),
                        ],
                        id="pl-send-sog-grid-selection-summary",
                        className="pl-editor-selection-summary",
                        role="status",
                        hidden=True,
                        **{"aria-live": "polite"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Add row",
                                id="add-sog-pl-row",
                                n_clicks=0,
                                className="pl-action-secondary",
                            ),
                            html.Button(
                                "Save Adjustments",
                                id="save-sog-adjustments-button",
                                n_clicks=0,
                                className="pl-action-primary",
                            ),
                            html.Button(
                                "Send SOG PL",
                                id="send-sog-pl-button",
                                n_clicks=0,
                                className="pl-action-send",
                            ),
                        ],
                        className="pl-send-actions",
                    ),
                    html.Div(
                        id="pl-save-sog-adjustments-status",
                        className="pl-send-status",
                        role="status",
                    ),
                    html.Div(
                        id="pl-send-sog-status",
                        className="pl-send-status",
                        role="status",
                    ),
                ],
                className="pl-send-panel",
            ),
        ],
        className="aux-details",
    )
    by_portfolio = html.Details(
        [
            html.Summary(
                "Portfolio P&L",
                id="pl-portfolio-summary",
                n_clicks=0,
                className="aux-summary",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label(
                                        "Portfolio", htmlFor="pl-send-portfolio-filter"
                                    ),
                                    dcc.Dropdown(
                                        id="pl-send-portfolio-filter",
                                        options=[],
                                        clearable=False,
                                    ),
                                ],
                                className="pl-editor-filter",
                            ),
                            dcc.Checklist(
                                id="pl-portfolio-include-adjustments",
                                options=[
                                    {"label": "Show adjustments", "value": "include"}
                                ],
                                value=[],
                                className="pl-adjustment-toggle",
                            ),
                        ],
                        className="pl-editor-toolbar",
                    ),
                    html.P(
                        "Single-click Risk Type, Risk Greek or PL to edit. Portfolio, "
                        "SignoffGroup and ConcertoField stay locked to this governed "
                        "scope. Duplicate ConcertoField rows are aggregated before sending.",
                        className="pl-editor-guide",
                    ),
                    html.Div(
                        "Waiting for Portfolio rows...",
                        id="pl-send-portfolio-grid-data-status",
                        className="pl-editor-statuses",
                        role="status",
                        **{"aria-live": "polite"},
                    ),
                    html.Div(
                        _editor_table(
                            "pl-send-portfolio-grid", portfolio_editable=False
                        ),
                        className="pl-send-editor-table pl-send-table--editor",
                    ),
                    html.Div(
                        [
                            html.Span(
                                id="pl-send-portfolio-grid-selection-summary-text",
                                className="pl-editor-selection-summary-text",
                            ),
                            html.Button(
                                "×",
                                id="pl-send-portfolio-grid-selection-clear",
                                className="pl-editor-selection-summary-dismiss",
                                type="button",
                                title="Clear selection",
                                **{"aria-label": "Clear selected cells"},
                            ),
                        ],
                        id="pl-send-portfolio-grid-selection-summary",
                        className="pl-editor-selection-summary",
                        role="status",
                        hidden=True,
                        **{"aria-live": "polite"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Add row",
                                id="add-portfolio-pl-row",
                                n_clicks=0,
                                className="pl-action-secondary",
                            ),
                            html.Button(
                                "Save Adjustments",
                                id="save-portfolio-adjustments-button",
                                n_clicks=0,
                                className="pl-action-primary",
                            ),
                            html.Button(
                                "Send Portfolio PL",
                                id="send-portfolio-pl-button",
                                n_clicks=0,
                                className="pl-action-send",
                            ),
                        ],
                        className="pl-send-actions",
                    ),
                    html.Div(
                        id="pl-save-portfolio-adjustments-status",
                        className="pl-send-status",
                        role="status",
                    ),
                    html.Div(
                        id="pl-send-portfolio-status",
                        className="pl-send-status",
                        role="status",
                    ),
                ],
                className="pl-send-panel",
            ),
        ],
        className="aux-details",
    )
    validate_pl = build_validate_pl_section()
    history = html.Details(
        [
            html.Summary(
                "Histo P&L",
                id="pl-history-summary",
                n_clicks=0,
                className="aux-summary",
            ),
            html.Div(
                [
                    html.P(
                        "Expand Signoff Group → Risk Type → Risk Greek → Underlying "
                        "→ Product → Portfolio. Daily (P) is today's Predict P&L. "
                        "MTD and YTD show Colossus by default; click either period "
                        "header to reveal its Colossus/Predict columns, then click a "
                        "cell to plot that exact filtered scope.",
                        className="pl-editor-guide",
                    ),
                    html.Div(
                        html.Div(
                            "Open Histo P&L to load its expandable hierarchy.",
                            className="static-data-empty",
                        ),
                        id="pl-history-grid",
                        className="pl-history-hierarchy-table",
                    ),
                    html.Div(
                        "Open Histo P&L to load its validated hierarchy.",
                        id="pl-history-status",
                        className="pl-send-status",
                        role="status",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Button(
                                        "WTD",
                                        id="pl-history-range-wtd",
                                        n_clicks=0,
                                        className="pl-history-range-button",
                                    ),
                                    html.Button(
                                        "MTD",
                                        id="pl-history-range-mtd",
                                        n_clicks=0,
                                        className="pl-history-range-button",
                                    ),
                                    html.Button(
                                        "YTD",
                                        id="pl-history-range-ytd",
                                        n_clicks=0,
                                        className="pl-history-range-button",
                                    ),
                                    html.Button(
                                        "All",
                                        id="pl-history-range-all",
                                        n_clicks=0,
                                        className="pl-history-range-button is-active",
                                    ),
                                ],
                                className="pl-history-range-presets",
                            ),
                            html.Div(
                                [
                                    html.Span(
                                        "Plot",
                                        className="pl-history-toolbar-label",
                                    ),
                                    build_pl_history_series_selector(),
                                ],
                                className="pl-history-series-control",
                            ),
                            dcc.DatePickerRange(
                                id="pl-history-date-range",
                                minimum_nights=0,
                                display_format="YYYY-MM-DD",
                                clearable=True,
                                start_date_placeholder_text="Start date",
                                end_date_placeholder_text="End date",
                            ),
                        ],
                        className="pl-history-range-toolbar",
                    ),
                    dcc.Loading(
                        dcc.Graph(
                            id="pl-history-chart",
                            figure={
                                "data": [],
                                "layout": {
                                    "title": "Select a P&L hierarchy cell",
                                    "xaxis": {"title": "Market Date"},
                                    "yaxis": {"title": "P&L"},
                                },
                            },
                            config={"displaylogo": False, "responsive": True},
                            responsive=True,
                            style={"minHeight": "360px"},
                        ),
                        delay_show=120,
                    ),
                    html.Div(
                        "Select a hierarchy cell to plot observed daily Colossus, "
                        "Predict, or both.",
                        id="pl-history-plot-status",
                        className="pl-send-status",
                        role="status",
                    ),
                    dcc.Store(id="pl-history-range-store", data={"preset": "all"}),
                    dcc.Store(id="pl-history-selection-store", data={}),
                    dcc.Store(id="pl-history-open-paths", data=[]),
                    dcc.Store(id="pl-history-open-comparisons", data=[]),
                ],
                className="pl-send-panel",
            ),
        ],
        className="aux-details",
    )
    explorer = html.Section(
        [
            html.H2("P&L Explorer", className="static-data-page-title"),
            html.P(
                "The page filter also governs Validate P&L and Histo P&L. Missing "
                "Predict or Colossus values remain unavailable rather than zero.",
                className="static-data-page-note",
            ),
            validate_pl,
            history,
        ],
        id="pnl-explorer",
        className="pnl-explorer",
    )
    state = html.Div(
        [
            dcc.Store(id="pl-send-sog-effective-store", data={}),
            dcc.Store(id="pl-send-portfolio-effective-store", data={}),
            dcc.Store(id="pl-send-sog-drafts-store", data={}),
            dcc.Store(id="pl-send-portfolio-drafts-store", data={}),
            dcc.Store(id="pl-send-sog-active-scope-store", data={}),
            dcc.Store(id="pl-send-portfolio-active-scope-store", data={}),
            dcc.Store(id="pl-sog-adjustment-revision-store", data=0),
            dcc.Store(id="pl-portfolio-adjustment-revision-store", data=0),
        ],
        id="pl-workflow-state",
        hidden=True,
    )
    return [
        state,
        send_all,
        by_sog,
        by_portfolio,
        explorer,
    ]


def build_pl_page(
    *,
    start_initial_load: bool = False,
    send_workflow_available: bool = True,
    initial_aggregate_frame: pd.DataFrame | None = None,
    saved_view_bar: object | None = None,
) -> html.Main:
    """Build the native P&L page around one authoritative filter set."""
    workflow_sections = (
        build_pl_send_sections()
        if send_workflow_available
        else [
            html.P(
                "P&L sending is not configured for this application.",
                id="pnl-unavailable",
                className="static-data-empty",
            )
        ]
    )
    return html.Main(
        html.Section(
            [
                (
                    dcc.Interval(
                        id="pnl-initial-load-trigger",
                        interval=500,
                        n_intervals=0,
                        max_intervals=1,
                    )
                    if start_initial_load
                    else None
                ),
                (
                    dcc.Store(id="pl-adjustment-revision-store", data=0)
                    if send_workflow_available
                    else None
                ),
                dcc.Store(id="pnl-aggregate-open-risk-types", data=[]),
                html.H1("P&L Sender", className="static-data-page-title"),
                html.P(
                    (
                        "Review mapped Aggregate P&L, edit and send it by SOG or "
                        "Portfolio, and explore official Predict versus Colossus "
                        "P&L. The one saved-view filter governs every section."
                        if send_workflow_available
                        else "Review mapped Aggregate P&L from the latest committed "
                        "risk refresh."
                    ),
                    className="static-data-page-note",
                ),
                (
                    saved_view_bar
                    if saved_view_bar is not None
                    else build_saved_filter_view_bar(
                        PL_SAVED_VIEW_CONTROLS,
                        filter_note=PL_FILTER_NOTE,
                        filter_bar=build_pl_filter_bar(initial_aggregate_frame),
                    )
                ),
                _pl_aggregate_section(initial_aggregate_frame),
                *workflow_sections,
            ],
            id="pnl-page",
            className="static-data-page",
        ),
        id="pnl-page-container",
    )


__all__ = [
    "DISPLAY_COLUMNS",
    "GRID_ROW_ID",
    "PL_AGGREGATE_TOGGLE_TYPE",
    "PL_FILTER_FIELDS",
    "PL_FILTER_EXCLUDE_ID",
    "PL_FILTER_IDS",
    "PL_FILTER_NOTE",
    "PL_SAVED_VIEW_CONTROLS",
    "build_pl_aggregate_table",
    "build_pl_filter_bar",
    "build_pl_page",
    "build_pl_send_sections",
    "pl_filter_map",
    "pl_filter_options",
]
