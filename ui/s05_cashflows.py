"""Dash components for the independently validated Intraday Cashflows page."""

from __future__ import annotations

from datetime import date, datetime, timezone
import pandas as pd
from dash import dash_table, dcc, html
from core.s06_cashflow import (
    AMOUNT,
    CASHFLOW_ID,
    CASHFLOW_TIME,
    CURRENCY,
    INTRADAY_CASHFLOW_COLUMNS,
    PORTFOLIO,
    STATUS,
    VALUE_DATE,
    empty_intraday_cashflows,
    normalize_cashflow_date,
    validate_intraday_cashflows,
)


def _display_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    display = frame.copy()
    display[CASHFLOW_TIME] = display[CASHFLOW_TIME].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    display[VALUE_DATE] = display[VALUE_DATE].dt.strftime("%Y-%m-%d")
    return display.to_dict("records")


def build_intraday_cashflows_page(
    frame: pd.DataFrame | None = None,
    *,
    selected_date: date | datetime | str | pd.Timestamp | None = None,
) -> html.Div:
    """Build a self-contained, read-only Intraday Cashflows page.

    Routing and connector callbacks intentionally remain outside this module.
    The factory should keep this component mounted, load data through
    ``load_intraday_cashflows``, and replace the table/page children after a
    date or refresh action.
    """
    validated = validate_intraday_cashflows(
        empty_intraday_cashflows() if frame is None else frame
    )
    as_of = normalize_cashflow_date(
        datetime.now(timezone.utc) if selected_date is None else selected_date
    ).date()
    pending_count = int(validated[STATUS].eq("Pending").sum())
    currency_totals = (
        validated.groupby(CURRENCY, as_index=False, sort=True)[AMOUNT]
        .sum()
        .rename(columns={AMOUNT: "Net Amount"})
    )

    summary_cards = [
        ("Cashflows", len(validated), "intraday-cashflows-count"),
        ("Portfolios", validated[PORTFOLIO].nunique(), "intraday-portfolios-count"),
        ("Currencies", validated[CURRENCY].nunique(), "intraday-currencies-count"),
        ("Pending", pending_count, "intraday-pending-count"),
    ]
    table_columns = [
        {
            "name": column,
            "id": column,
            **(
                {"type": "numeric", "format": {"specifier": ",.2f"}}
                if column == AMOUNT
                else {}
            ),
        }
        for column in INTRADAY_CASHFLOW_COLUMNS
    ]

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H2(
                                "Intraday Cashflows",
                                className="intraday-cashflows-title",
                            ),
                            html.P(
                                "Date-specific cashflows from a validated personal connector.",
                                className="intraday-cashflows-note",
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            dcc.DatePickerSingle(
                                id="intraday-cashflows-date",
                                date=as_of,
                                display_format="YYYY-MM-DD",
                                clearable=False,
                            ),
                            html.Button(
                                "Refresh cashflows",
                                id="intraday-cashflows-refresh-button",
                                n_clicks=0,
                                type="button",
                            ),
                        ],
                        className="intraday-cashflows-actions",
                    ),
                ],
                className="intraday-cashflows-header",
            ),
            html.Div(
                [
                    html.Div(
                        [html.Span(label), html.Strong(f"{value:,}", id=value_id)],
                        className="intraday-cashflows-summary-card",
                    )
                    for label, value, value_id in summary_cards
                ],
                className="intraday-cashflows-summary",
            ),
            html.Div(
                "No intraday cashflows are available for the selected date."
                if validated.empty
                else f"Loaded {len(validated):,} validated cashflows.",
                id="intraday-cashflows-status",
                className="intraday-cashflows-status",
                role="status",
            ),
            dash_table.DataTable(
                id="intraday-cashflows-table",
                columns=table_columns,
                data=_display_records(validated),
                editable=False,
                filter_action="native",
                sort_action="native",
                sort_mode="multi",
                page_action="native",
                page_size=30,
                fixed_rows={"headers": True},
                style_table={"overflowX": "auto", "maxHeight": "68vh"},
                style_header={
                    "backgroundColor": "#F3F5F7",
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
                    "whiteSpace": "nowrap",
                },
                style_cell_conditional=[
                    {"if": {"column_id": AMOUNT}, "textAlign": "right"},
                ],
                style_header_conditional=[
                    {
                        "if": {"column_id": CASHFLOW_ID},
                        "backgroundColor": "#C4DEF5",
                        "borderRight": "2px dotted #000000",
                    }
                ],
                style_data_conditional=[
                    {
                        "if": {"column_id": CASHFLOW_ID},
                        "backgroundColor": "#C4DEF5",
                        "color": "#111111",
                        "fontWeight": "700",
                        "borderRight": "2px dotted #000000",
                    },
                    {
                        "if": {
                            "filter_query": f"{{{AMOUNT}}} < 0",
                            "column_id": AMOUNT,
                        },
                        "color": "#B42318",
                    },
                ],
            ),
            html.Div(
                [
                    html.H3("Net amount by currency"),
                    dash_table.DataTable(
                        id="intraday-cashflows-currency-summary",
                        columns=[
                            {"name": CURRENCY, "id": CURRENCY},
                            {
                                "name": "Net Amount",
                                "id": "Net Amount",
                                "type": "numeric",
                                "format": {"specifier": ",.2f"},
                            },
                        ],
                        data=currency_totals.to_dict("records"),
                        editable=False,
                        style_table={"maxWidth": "520px", "overflowX": "auto"},
                        style_header={
                            "backgroundColor": "#F3F5F7",
                            "color": "#111111",
                            "fontWeight": "700",
                        },
                        style_cell={
                            "backgroundColor": "#FFFFFF",
                            "color": "#111111",
                            "border": "1px solid #E5E9ED",
                            "padding": "7px 10px",
                        },
                        style_cell_conditional=[
                            {"if": {"column_id": "Net Amount"}, "textAlign": "right"},
                        ],
                        style_data_conditional=[
                            {
                                "if": {
                                    "filter_query": "{Net Amount} < 0",
                                    "column_id": "Net Amount",
                                },
                                "color": "#B42318",
                            }
                        ],
                    ),
                ],
                className="intraday-cashflows-currency-panel",
            ),
        ],
        id="intraday-cashflows-page",
        className="intraday-cashflows-page",
    )


__all__ = [
    "build_intraday_cashflows_page",
]
