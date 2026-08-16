"""Dash components for the Statics CSV viewer page."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from dash import dash_table, dcc, html

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_STATIC_FILE_CATALOG = [
    {"label": "Readiness Risks Today", "value": "s01_readiness.csv"},
    {"label": "Not Ready Risk File Inventory", "value": "s02_checker.csv"},
    {"label": "All Risks", "value": "s03_risk.csv"},
    {"label": "Market Open", "value": "s04_open.csv"},
    {"label": "Market Status", "value": "s05_current.csv"},
    {"label": "Portfolio Mapping", "value": "s06_portfolios.csv"},
    {"label": "Top Thresholds", "value": "s07_thresholds.csv"},
    {"label": "Concerto Mapping", "value": "s08_concerto.csv"},
    {"label": "Reported Underlying Mapping", "value": "s09_reported.csv"},
]
STATIC_FILE_OPTIONS = [
    option for option in _STATIC_FILE_CATALOG if (DATA_DIR / option["value"]).is_file()
]


def _build_static_data_table(file_key: str) -> html.Div:
    """Build a filterable, sortable DataTable for a static CSV data file."""
    approved_files = {option["value"] for option in STATIC_FILE_OPTIONS}
    if file_key not in approved_files or Path(file_key).name != file_key:
        return html.Div(
            "The selected static data file is not available.",
            className="static-data-empty",
        )
    csv_path = DATA_DIR / file_key
    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    except FileNotFoundError:
        return html.Div(
            f"Data file not found: {file_key}",
            className="static-data-empty",
        )
    except Exception:
        return html.Div(
            f"Error loading data file: {file_key}",
            className="static-data-empty",
        )

    if df.empty:
        return html.Div(
            f"No data rows in: {file_key}",
            className="static-data-empty",
        )

    file_label = next(
        (opt["label"] for opt in STATIC_FILE_OPTIONS if opt["value"] == file_key),
        file_key,
    )

    columns = [{"name": col, "id": col, "editable": False} for col in df.columns]

    return html.Div(
        [
            html.Div(
                [
                    html.Span(
                        f"File: {file_label}", className="static-data-file-label"
                    ),
                    html.Span(f"Rows: {len(df):,}", className="static-data-row-count"),
                    html.Span(
                        f"Columns: {len(df.columns)}",
                        className="static-data-col-count",
                    ),
                ],
                className="static-data-meta",
            ),
            dash_table.DataTable(
                id=f"static-data-table-{file_key.replace('.csv', '')}",
                columns=columns,
                data=df.to_dict("records"),
                editable=False,
                filter_action="native",
                filter_options={"case": "insensitive"},
                sort_action="native",
                sort_mode="multi",
                column_selectable="single",
                row_selectable="multi",
                selected_columns=[],
                selected_rows=[],
                page_action="native",
                page_size=50,
                fixed_rows={"headers": True},
                style_table={"overflowX": "auto", "maxHeight": "68vh"},
                style_header={
                    "backgroundColor": "#E3E5E7",
                    "color": "#111111",
                    "fontWeight": "700",
                    "border": "1px solid #D9E0E7",
                    "fontSize": "12px",
                },
                style_cell={
                    "backgroundColor": "#FFFFFF",
                    "color": "#111111",
                    "border": "1px solid #E5E9ED",
                    "fontFamily": "Inter, Segoe UI, Arial, sans-serif",
                    "fontSize": "12px",
                    "padding": "8px 10px",
                    "textAlign": "left",
                    "whiteSpace": "normal",
                    "overflow": "text",
                    "minWidth": "100px",
                    "width": "150px",
                },
            ),
        ],
        className="static-data-panel",
    )


def build_static_data_page() -> html.Div:
    """Build the Statics viewer page.

    Displays a dropdown selector for static CSV data files with a filterable
    and sortable DataTable.
    """
    if not STATIC_FILE_OPTIONS:
        return html.Div(
            "No approved static data files are available.",
            id="static-data-page",
            className="static-data-page static-data-empty",
        )

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H2(
                                "Statics",
                                className="static-data-page-title",
                            ),
                            html.P(
                                "Select a CSV fixture to view with per-column filtering and sorting.",
                                className="static-data-page-note",
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            dcc.Dropdown(
                                id="static-data-file-selector",
                                options=STATIC_FILE_OPTIONS,
                                value=STATIC_FILE_OPTIONS[0]["value"],
                                clearable=False,
                                style={"minWidth": "350px"},
                            ),
                        ],
                        className="static-data-actions",
                    ),
                ],
                className="static-data-header",
            ),
            html.Div(
                id="static-data-table-container",
                className="static-data-container",
            ),
        ],
        id="static-data-page",
        className="static-data-page",
    )


__all__ = [
    "build_static_data_table",
    "build_static_data_page",
    "STATIC_FILE_OPTIONS",
]


build_static_data_table = _build_static_data_table
