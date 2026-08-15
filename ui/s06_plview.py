"""Dash components for governed PL preview, adjustment, send, and save flows."""

from __future__ import annotations

from dash import dash_table, dcc, html
from core.s04_pl import HISTORICAL_PL_COLUMNS


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
    """Build the read-only daily Portfolio/ConcertoField history table."""
    return dash_table.DataTable(
        id="pl-history-grid",
        columns=[
            {
                "name": column,
                "id": column,
                **({"type": "numeric"} if column == "PL" else {}),
            }
            for column in HISTORICAL_PL_COLUMNS
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
                "Histo Data",
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
                        ],
                        className="pl-editor-toolbar",
                    ),
                    html.P(
                        "Track validated daily P&L at Market Date + Portfolio + "
                        "ConcertoField grain. The selectors apply to both the chart "
                        "and the table.",
                        className="pl-editor-guide",
                    ),
                    dcc.Loading(
                        dcc.Graph(
                            id="pl-history-chart",
                            figure={
                                "data": [],
                                "layout": {
                                    "title": "Historical P&L",
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
                        "Open Histo Data to load its validated rows.",
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


__all__ = [
    "DISPLAY_COLUMNS",
    "GRID_ROW_ID",
    "build_pl_send_sections",
]
