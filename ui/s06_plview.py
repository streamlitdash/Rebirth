"""Dash components for governed PL preview, adjustment, send, and save flows."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
from dash import dash_table, dcc, html
from core.s04_pl import HISTORY_TYPE, PL_HISTORY_COLUMNS

from .s02_constants import DEFAULT_VIEW_DIMENSION, VIEW_DIMENSION_FIELDS
from .s04_components import build_aggregate_pl_table, build_cube_loader


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
    """Render Aggregate P&L with page-owned, collision-free toggle IDs."""
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
) -> html.Details:
    """Build the P&L page's independent mapped Aggregate P&L section."""
    view_dimension_options = [
        {"label": field.label, "value": field.key} for field in VIEW_DIMENSION_FIELDS
    ]
    return html.Details(
        [
            html.Summary(
                "Aggregate P&L",
                className="aux-summary aggregate-pl-summary",
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
        open=True,
        className="aux-details aggregate-pl-details",
    )


def _preview_columns() -> list[dict[str, object]]:
    """Return the read-only Preview PL DataTable columns."""
    return [
        {
            "name": column,
            "id": column,
            **({"type": "numeric"} if column == "PL" else {}),
        }
        for column in DISPLAY_COLUMNS
    ]


def _preview_table() -> dash_table.DataTable:
    """Build the read-only preview with the same typography as Cube."""
    return dash_table.DataTable(
        id="pl-send-preview-grid",
        columns=_preview_columns(),
        data=[],
        editable=False,
        sort_action="native",
        filter_action="native",
        page_action="native",
        page_size=20,
        markdown_options={"html": True},
        style_table={"overflowX": "auto", "borderRadius": "8px"},
        style_header={
            "backgroundColor": "#FFFFFF",
            "color": "#111111",
            "fontWeight": "800",
            "border": "1px solid #D9DEE5",
            "height": "40px",
        },
        style_cell={
            "backgroundColor": "#FFFFFF",
            "color": "#111111",
            "border": "1px solid #E2E6EA",
            "fontFamily": '"Segoe UI Variable Text", "Segoe UI", Arial, sans-serif',
            "fontSize": "13px",
            "lineHeight": "1.35",
            "height": "38px",
            "padding": "8px 10px",
            "textAlign": "left",
            "minWidth": "118px",
            "maxWidth": "240px",
            "whiteSpace": "nowrap",
        },
        style_cell_conditional=[
            {
                "if": {"column_id": "Risk Type"},
                "backgroundColor": "#C4DEF5",
                "color": "#111111",
                "fontWeight": "800",
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
            },
            {
                "if": {"column_id": "PL"},
                "backgroundColor": "#FFFFE0",
                "color": "#111111",
                "fontWeight": "800",
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
                "textAlign": "right",
            },
            {"if": {"column_id": "Adjustment"}, "textAlign": "center"},
        ],
        style_header_conditional=[
            {
                "if": {"column_id": "Risk Type"},
                "backgroundColor": "#C4DEF5",
                "color": "#111111",
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
            },
            {
                "if": {"column_id": "PL"},
                "backgroundColor": "#FFFFE0",
                "color": "#111111",
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
            },
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{PL} < 0", "column_id": "PL"}, "color": "#B42318"},
        ],
    )


def _historical_table() -> dash_table.DataTable:
    """Build the interactive actual/predicted raw-detail table."""
    return dash_table.DataTable(
        id="pl-history-grid",
        columns=[
            {
                "name": column,
                "id": column,
                **({"type": "numeric"} if column == "PL" else {}),
            }
            for column in PL_HISTORY_COLUMNS
        ],
        data=[],
        editable=False,
        filter_action="native",
        sort_action="native",
        sort_mode="multi",
        page_action="native",
        page_size=25,
        fixed_rows={"headers": True},
        style_table={"overflowX": "auto", "maxHeight": "560px"},
        style_header={
            "backgroundColor": "#F7F8FA",
            "color": "#111111",
            "fontWeight": "850",
            "border": "1px solid #D9DEE5",
        },
        style_cell={
            "backgroundColor": "#FFFFFF",
            "color": "#111111",
            "border": "1px solid #E2E6EA",
            "fontFamily": '"Segoe UI Variable Text", "Segoe UI", Arial, sans-serif',
            "fontSize": "13px",
            "padding": "8px 10px",
            "textAlign": "left",
            "minWidth": "140px",
            "whiteSpace": "nowrap",
        },
        style_cell_conditional=[
            {
                "if": {"column_id": "PL"},
                "fontWeight": "850",
                "fontVariantNumeric": "tabular-nums",
                "textAlign": "right",
            }
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{PL} < 0", "column_id": "PL"}, "color": "#B42318"}
        ],
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
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
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
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
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
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
            },
            {
                "if": {"column_id": "PL"},
                "backgroundColor": "#FFFFE0",
                "color": "#111111",
                "borderLeft": "2px dotted #111111",
                "borderRight": "2px dotted #111111",
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
    preview = html.Details(
        [
            html.Summary(
                "P&L Preview",
                id="pl-preview-summary",
                n_clicks=0,
                className="aux-summary",
            ),
            html.Div(
                [
                    dcc.Checklist(
                        id="pl-include-adjustments",
                        options=[{"label": "Show adjustments", "value": "include"}],
                        value=[],
                        className="pl-adjustment-toggle",
                    ),
                    html.P(
                        "Off uses the raw aggregate only. On replaces matching "
                        "Market Date + Portfolio + ConcertoField rows from "
                        "adjustments/<YYYY-MM-DD>/<safe-portfolio>_<hash>.csv.",
                        className="unmapped-note",
                    ),
                    html.Div(_preview_table(), className="pl-send-table"),
                    html.Div(
                        id="pl-send-preview-status",
                        className="pl-send-status",
                        role="status",
                    ),
                ],
                className="pl-send-panel",
            ),
        ],
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
    save = html.Details(
        [
            html.Summary("Write PL to S3", className="aux-summary"),
            html.Div(
                [
                    html.P(
                        "Save Adjustments in the SOG or Portfolio section first. A configured write_pl connector receives every unadjusted raw row plus separately flagged saved adjustment rows. If no connector is configured, Cube writes a clearly identified local CSV fallback and downloads it.",
                        className="unmapped-note",
                    ),
                    html.Button("Write PL to S3", id="save-pl-button", n_clicks=0),
                    dcc.Download(id="save-pl-download"),
                    html.Div(
                        id="save-pl-status", className="pl-send-status", role="status"
                    ),
                ],
                className="pl-send-panel",
            ),
        ],
        className="aux-details",
    )
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
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label(
                                        "Portfolio / Book",
                                        htmlFor="pl-history-portfolio-filter",
                                    ),
                                    dcc.Dropdown(
                                        id="pl-history-portfolio-filter",
                                        options=[],
                                        value=[],
                                        multi=True,
                                        placeholder="All portfolios",
                                    ),
                                ],
                                className="pl-editor-filter",
                            ),
                            html.Div(
                                [
                                    html.Label(
                                        "ConcertoField",
                                        htmlFor="pl-history-concerto-filter",
                                    ),
                                    dcc.Dropdown(
                                        id="pl-history-concerto-filter",
                                        options=[],
                                        value=[],
                                        multi=True,
                                        placeholder="All Concerto fields",
                                    ),
                                ],
                                className="pl-editor-filter",
                            ),
                            html.Div(
                                [
                                    html.Label(
                                        HISTORY_TYPE,
                                        htmlFor="pl-history-type-filter",
                                    ),
                                    dcc.Dropdown(
                                        id="pl-history-type-filter",
                                        options=[],
                                        value=[],
                                        multi=True,
                                        placeholder="Histo and Predicted",
                                    ),
                                ],
                                className="pl-editor-filter",
                            ),
                        ],
                        className="pl-editor-toolbar",
                    ),
                    html.P(
                        "Compare validated Histo and Predicted daily P&L at Market "
                        "Date + P&L Type + Portfolio + ConcertoField grain. The "
                        "selectors apply to both the chart and the raw-detail table. "
                        "Files are read from histo/YYYY/MM-DD/{histo,predicted}.csv.",
                        className="pl-editor-guide",
                    ),
                    dcc.Loading(
                        dcc.Graph(
                            id="pl-history-chart",
                            figure={
                                "data": [],
                                "layout": {
                                    "title": "Historical vs predicted P&L",
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
                        _historical_table(),
                        className="pl-send-table",
                    ),
                    html.Div(
                        "Open Histo P&L to load its validated rows.",
                        id="pl-history-status",
                        className="pl-send-status",
                        role="status",
                    ),
                ],
                className="pl-send-panel",
            ),
        ],
        className="aux-details",
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
    return [state, preview, by_sog, by_portfolio, save, history]


def build_pl_page(
    *,
    start_initial_load: bool = False,
    send_workflow_available: bool = True,
    initial_aggregate_frame: pd.DataFrame | None = None,
) -> html.Main:
    """Build the native P&L page and its independent Aggregate P&L state."""
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
                dcc.Store(id="pnl-aggregate-open-risk-types", data=[]),
                (
                    dcc.Store(id="pl-adjustment-revision-store", data=0)
                    if send_workflow_available
                    else None
                ),
                html.H1("P&L Sender", className="static-data-page-title"),
                html.P(
                    (
                        "Review mapped Aggregate P&L, preview governed P&L, edit "
                        "and send it by SOG or Portfolio, write the complete file, "
                        "and compare Histo with Predicted P&L."
                        if send_workflow_available
                        else "Review mapped Aggregate P&L from the latest committed "
                        "risk refresh."
                    ),
                    className="static-data-page-note",
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
    "build_pl_aggregate_table",
    "build_pl_page",
    "build_pl_send_sections",
]
