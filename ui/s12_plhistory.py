"""Expandable Colossus/Predict P&L history explorer components."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html

from core.s04_pl import (
    COLOSSUS_TYPE,
    HISTORY_IDENTITY_COLUMNS,
    HISTORY_TYPE,
    MARKET_DATE,
    PL,
    PREDICT_TYPE,
    RISK_GREEK,
    RISK_TYPE,
    pl_history_period_values,
)

from .s02_constants import (
    RISK_TYPE_ORDER,
    ROW_TOGGLE_CLOSED_GLYPH,
    ROW_TOGGLE_OPEN_GLYPH,
)
from .s03_aggregate import format_number


PL_HISTORY_ROW_TOGGLE_TYPE: Final = "pl-history-row-toggle"
PL_HISTORY_METRIC_CELL_TYPE: Final = "pl-history-metric-cell"
PL_HISTORY_PERIOD_HEADER_TYPE: Final = "pl-history-period-header"
DAILY_P_PERIOD: Final = "Daily (P)"
MTD_PERIOD: Final = "MTD"
YTD_PERIOD: Final = "YTD"
PL_HISTORY_TABLE_PERIODS: Final = (DAILY_P_PERIOD, MTD_PERIOD, YTD_PERIOD)
PL_HISTORY_EXPANDABLE_PERIODS: Final = (MTD_PERIOD, YTD_PERIOD)

_DEPTH = "Hierarchy Depth"
_LEVEL = "Hierarchy Level"
_LABEL = "Hierarchy Label"
_PATH = "Hierarchy Path"
_LEAF = "Hierarchy Leaf"
_DAILY_P = "Daily Predict"
_MTD_C = "MTD Colossus"
_MTD_P = "MTD Predict"
_YTD_C = "YTD Colossus"
_YTD_P = "YTD Predict"
_SUMMARY_COLUMNS = (
    _DEPTH,
    _LEVEL,
    _LABEL,
    _PATH,
    _LEAF,
    _DAILY_P,
    _MTD_C,
    _MTD_P,
    _YTD_C,
    _YTD_P,
)


def pl_history_path_token(path: Sequence[str]) -> str:
    """Encode one hierarchy path without parsing labels in callbacks."""

    return json.dumps([str(value) for value in path], separators=(",", ":"))


def pl_history_path_from_token(value: object) -> tuple[str, ...] | None:
    """Decode a bounded JSON path token or return ``None``."""

    if not isinstance(value, str) or len(value) > 5_000:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, list) or len(decoded) > len(HISTORY_IDENTITY_COLUMNS):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in decoded):
        return None
    return tuple(decoded)


def normalize_pl_history_open_tokens(value: object) -> list[str]:
    """Return unique, valid hierarchy tokens in deterministic order."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    paths = {
        path
        for raw in value
        if (path := pl_history_path_from_token(raw)) is not None and path
    }
    return [
        pl_history_path_token(path)
        for path in sorted(
            paths,
            key=lambda item: (len(item), tuple(value.casefold() for value in item)),
        )
    ]


def toggle_pl_history_open_tokens(current: object, requested: object) -> list[str]:
    """Open a branch or close it together with every descendant."""

    path = pl_history_path_from_token(requested)
    normalized = normalize_pl_history_open_tokens(current)
    paths = {
        parsed
        for token in normalized
        if (parsed := pl_history_path_from_token(token)) is not None
    }
    if path is None or not path:
        return normalized
    if path in paths:
        paths = {candidate for candidate in paths if candidate[: len(path)] != path}
    else:
        paths.update(path[:depth] for depth in range(1, len(path) + 1))
    return normalize_pl_history_open_tokens(
        [pl_history_path_token(candidate) for candidate in paths]
    )


def normalize_pl_history_expanded_periods(value: object) -> list[str]:
    """Return the valid MTD/YTD header disclosures in table order."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    requested = {str(period) for period in value}
    return [period for period in PL_HISTORY_EXPANDABLE_PERIODS if period in requested]


def toggle_pl_history_expanded_periods(
    current: object,
    requested: object,
) -> list[str]:
    """Toggle one table-wide Colossus/Predict period disclosure."""

    normalized = normalize_pl_history_expanded_periods(current)
    period = str(requested)
    if period not in PL_HISTORY_EXPANDABLE_PERIODS:
        return normalized
    expanded = set(normalized)
    if period in expanded:
        expanded.remove(period)
    else:
        expanded.add(period)
    return [
        candidate
        for candidate in PL_HISTORY_EXPANDABLE_PERIODS
        if candidate in expanded
    ]


def _period_value(
    period_values: pd.DataFrame,
    period: str,
    history_type: str,
) -> float | None:
    scoped = period_values.loc[
        period_values["Period"].eq(period)
        & period_values[HISTORY_TYPE].eq(history_type),
        PL,
    ]
    if scoped.empty or pd.isna(scoped.iloc[0]):
        return None
    return float(scoped.iloc[0])


def _summary_record(
    scope: pd.DataFrame,
    *,
    path: tuple[str, ...],
    level: str,
    label: str,
    as_of: object,
) -> dict[str, object]:
    period_values = pl_history_period_values(scope, (), as_of=as_of)
    return {
        _DEPTH: len(path),
        _LEVEL: level,
        _LABEL: label,
        _PATH: path,
        _LEAF: len(path) == len(HISTORY_IDENTITY_COLUMNS),
        _DAILY_P: _period_value(period_values, DAILY_P_PERIOD, PREDICT_TYPE),
        _MTD_C: _period_value(period_values, MTD_PERIOD, COLOSSUS_TYPE),
        _MTD_P: _period_value(period_values, MTD_PERIOD, PREDICT_TYPE),
        _YTD_C: _period_value(period_values, YTD_PERIOD, COLOSSUS_TYPE),
        _YTD_P: _period_value(period_values, YTD_PERIOD, PREDICT_TYPE),
    }


def summarize_visible_pl_history(
    history: pd.DataFrame,
    *,
    open_path_tokens: object = None,
) -> pd.DataFrame:
    """Return Total plus only descendants of explicitly open branches."""

    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a pandas DataFrame")
    if history.empty:
        return pd.DataFrame(columns=list(_SUMMARY_COLUMNS))
    requested_paths = {
        path
        for token in normalize_pl_history_open_tokens(open_path_tokens)
        if (path := pl_history_path_from_token(token)) is not None
    }
    # Every row is evaluated against the same global valuation date.  A stale
    # identity therefore remains discoverable in the hierarchy, but does not
    # masquerade as today's Predict P&L by using its own last observation.
    latest_global = pd.to_datetime(history[MARKET_DATE], errors="raise").max()
    records = [
        _summary_record(
            history,
            path=(),
            level="Total",
            label="TOTAL",
            as_of=latest_global,
        )
    ]

    def ordered_values(scope: pd.DataFrame, column: str) -> list[str]:
        values = scope[column].dropna().astype(str).drop_duplicates().tolist()
        if column == RISK_TYPE:
            return sorted(
                values,
                key=lambda value: (RISK_TYPE_ORDER.get(value, 99), value.casefold()),
            )
        return sorted(values, key=str.casefold)

    def visit(scope: pd.DataFrame, depth: int, path: tuple[str, ...]) -> None:
        if depth >= len(HISTORY_IDENTITY_COLUMNS):
            return
        column = HISTORY_IDENTITY_COLUMNS[depth]
        for value in ordered_values(scope, column):
            child = scope.loc[scope[column].astype(str).eq(value)]
            child_path = (*path, value)
            records.append(
                _summary_record(
                    child,
                    path=child_path,
                    level=column,
                    label=value,
                    as_of=latest_global,
                )
            )
            if child_path in requested_paths:
                visit(child, depth + 1, child_path)

    visit(history, 0, ())
    return pd.DataFrame.from_records(records, columns=list(_SUMMARY_COLUMNS))


def _number_class(value: object) -> str:
    if value is None or pd.isna(value):
        return "number-unavailable"
    return "number-negative" if float(value) < 0 else "number-positive"


def _number(value: object) -> str:
    return "—" if value is None or pd.isna(value) else format_number(float(value))


def _display_period_columns(
    expanded_periods: object,
) -> list[tuple[str, str, str, str]]:
    """Return period, source, summary column, and header for visible metrics."""

    expanded = set(normalize_pl_history_expanded_periods(expanded_periods))
    columns = [(DAILY_P_PERIOD, PREDICT_TYPE, _DAILY_P, DAILY_P_PERIOD)]
    for period, colossus_column, predict_column in (
        (MTD_PERIOD, _MTD_C, _MTD_P),
        (YTD_PERIOD, _YTD_C, _YTD_P),
    ):
        if period in expanded:
            columns.extend(
                [
                    (period, COLOSSUS_TYPE, colossus_column, f"{period} (C)"),
                    (period, PREDICT_TYPE, predict_column, f"{period} (P)"),
                ]
            )
        else:
            columns.append((period, COLOSSUS_TYPE, colossus_column, period))
    return columns


def _period_header(
    *,
    period: str,
    history_type: str,
    label: str,
    expanded_periods: object,
) -> html.Th:
    """Build a Risk-Explorer-style MTD/YTD column disclosure header."""

    expanded = period in set(normalize_pl_history_expanded_periods(expanded_periods))
    classes = ["metric-header", "pl-history-metric-header"]
    if history_type == PREDICT_TYPE and period != DAILY_P_PERIOD:
        classes.append("metric-child")
    if history_type == COLOSSUS_TYPE and period in PL_HISTORY_EXPANDABLE_PERIODS:
        action = "Collapse" if expanded else "Expand"
        comparison = f"{period} Colossus and Predict columns"
        return html.Th(
            html.Button(
                f"{ROW_TOGGLE_OPEN_GLYPH if expanded else ROW_TOGGLE_CLOSED_GLYPH} "
                f"{label}",
                id={"type": PL_HISTORY_PERIOD_HEADER_TYPE, "period": period},
                n_clicks=0,
                type="button",
                className="metric-header-button pl-history-period-header-button",
                title=f"{action} {comparison}",
                **{
                    "aria-label": f"{action} {comparison}",
                    "aria-expanded": str(expanded).lower(),
                },
            ),
            className=" ".join(classes),
            scope="col",
            **{"data-metric": f"{period} {history_type}"},
        )
    return html.Th(
        label,
        className=" ".join(classes),
        scope="col",
        **{"data-metric": f"{period} {history_type}"},
    )


def _metric_cell(
    row: pd.Series,
    *,
    period: str,
    history_type: str,
    summary_column: str,
    selected: bool,
) -> html.Td:
    path = tuple(row[_PATH])
    primary = row[summary_column]
    title = f"Plot {period} {history_type} P&L for {row[_LABEL]}"
    classes = ["metric-cell", "pl-history-metric-cell", _number_class(primary)]
    if history_type == PREDICT_TYPE and period != DAILY_P_PERIOD:
        classes.append("metric-child")
    return html.Td(
        html.Button(
            html.Span(_number(primary), className="copy-value"),
            id={
                "type": PL_HISTORY_METRIC_CELL_TYPE,
                "path": pl_history_path_token(path),
                "period": period,
                "series": history_type,
            },
            n_clicks=0,
            type="button",
            className="metric-cell-button pl-history-metric-button"
            + (" is-selected" if selected else ""),
            title=title,
            **{"aria-pressed": str(selected).lower()},
        ),
        className=" ".join(classes),
        **{
            "data-metric": f"{period} {history_type}",
            "data-copy-value": _number(primary),
        },
    )


def build_pl_history_table_with_state(
    history: pd.DataFrame,
    *,
    open_path_tokens: object = None,
    open_comparison_tokens: object = None,
    selection: Mapping[str, object] | None = None,
) -> tuple[html.Div, list[str], list[str], dict[str, object]]:
    """Render one Risk-Explorer-style history tree and prune its UI state."""

    requested_open = normalize_pl_history_open_tokens(open_path_tokens)
    summary = summarize_visible_pl_history(history, open_path_tokens=requested_open)
    if summary.empty:
        return (
            html.Div(
                "No validated Colossus/Predict P&L history is available.",
                className="static-data-empty",
            ),
            [],
            [],
            {},
        )
    visible_paths = {tuple(path) for path in summary[_PATH]}
    expandable_paths = {
        tuple(row[_PATH])
        for _, row in summary.iterrows()
        if tuple(row[_PATH]) and not bool(row[_LEAF])
    }
    effective_open_paths = {
        path
        for token in requested_open
        if (path := pl_history_path_from_token(token)) in expandable_paths
    }
    effective_open = normalize_pl_history_open_tokens(
        [pl_history_path_token(path) for path in effective_open_paths]
    )
    effective_comparison_tokens = normalize_pl_history_expanded_periods(
        open_comparison_tokens
    )
    display_columns = _display_period_columns(effective_comparison_tokens)
    raw_selection_path = (selection or {}).get("path", [])
    selected_path = (
        tuple(str(value) for value in raw_selection_path)
        if isinstance(raw_selection_path, Sequence)
        and not isinstance(raw_selection_path, (str, bytes))
        else ()
    )
    selected_period = str((selection or {}).get("period", ""))
    effective_selection: dict[str, object] = {}
    if selected_path in visible_paths:
        effective_selection["path"] = list(selected_path)
        if selected_period in PL_HISTORY_TABLE_PERIODS:
            effective_selection["period"] = selected_period

    def hierarchy_row(row: pd.Series) -> html.Tr:
        path = tuple(row[_PATH])
        depth = int(row[_DEPTH])
        is_leaf = bool(row[_LEAF])
        is_open = path in effective_open_paths
        index_children: list[object] = []
        if not is_leaf:
            action = "Collapse" if is_open else "Expand"
            index_children.append(
                html.Button(
                    (ROW_TOGGLE_OPEN_GLYPH if is_open else ROW_TOGGLE_CLOSED_GLYPH),
                    id={
                        "type": PL_HISTORY_ROW_TOGGLE_TYPE,
                        "path": pl_history_path_token(path),
                    },
                    n_clicks=0,
                    type="button",
                    className="row-toggle pl-history-row-toggle",
                    title=f"{action} {row[_LEVEL]}: {row[_LABEL]}",
                    **{
                        "aria-label": f"{action} {row[_LEVEL]}: {row[_LABEL]}",
                        "aria-expanded": str(is_open).lower(),
                    },
                )
            )
        else:
            index_children.append(
                html.Button(
                    "",
                    type="button",
                    disabled=True,
                    tabIndex=-1,
                    className="row-toggle pl-history-row-toggle",
                    **{"aria-hidden": "true"},
                )
            )
        index_children.append(html.Span(str(row[_LABEL]), className="row-label-text"))
        selected = path == selected_path
        cells = [
            html.Th(
                index_children,
                className=f"index-cell level-{max(depth - 1, 0)}",
                scope="row",
                style={"paddingLeft": f"{14 + max(depth - 1, 0) * 18}px"},
                title=str(row[_LABEL]),
                **{"data-metric": "index", "data-copy-value": str(row[_LABEL])},
            ),
            *[
                _metric_cell(
                    row,
                    period=period,
                    history_type=history_type,
                    summary_column=summary_column,
                    selected=selected,
                )
                for period, history_type, summary_column, _label in display_columns
            ],
        ]
        row_classes = [
            "group-row",
            f"group-level-{max(depth - 1, 0)}",
            "pl-history-row",
        ]
        if row[_LEVEL] in {RISK_TYPE, RISK_GREEK}:
            row_classes.append("hierarchy-total-row")
        if selected:
            row_classes.append("is-selected")
        return html.Tr(
            cells,
            className=" ".join(row_classes),
            **{
                "aria-level": str(depth),
                **({"aria-expanded": str(is_open).lower()} if not is_leaf else {}),
            },
        )

    root = summary.iloc[0]
    root_path: tuple[str, ...] = ()
    root_selected = selected_path == root_path and bool(selection)
    total_cells = [
        html.Th(
            html.Span("TOTAL", className="row-label-text"),
            className="index-cell total-index",
            scope="row",
            **{"data-metric": "index", "data-copy-value": "TOTAL"},
        ),
        *[
            _metric_cell(
                root,
                period=period,
                history_type=history_type,
                summary_column=summary_column,
                selected=root_selected,
            )
            for period, history_type, summary_column, _label in display_columns
        ],
    ]
    table = html.Table(
        [
            html.Caption(
                "Colossus and Predict P&L hierarchy",
                className="sr-only",
            ),
            html.Thead(
                html.Tr(
                    [
                        html.Th("Index", className="index-header"),
                        *[
                            _period_header(
                                period=period,
                                history_type=history_type,
                                label=label,
                                expanded_periods=effective_comparison_tokens,
                            )
                            for period, history_type, _column, label in display_columns
                        ],
                    ]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        total_cells,
                        className=(
                            "total-row pl-history-total-row"
                            + (" is-selected" if root_selected else "")
                        ),
                    ),
                    *[hierarchy_row(row) for _, row in summary.iloc[1:].iterrows()],
                ]
            ),
        ],
        className="risk-table pl-history-table",
        role="treegrid",
        **{"aria-label": "Colossus and Predict P&L hierarchy"},
    )
    return (
        html.Div(
            [
                html.Div("", className="selection-summary", **{"aria-live": "polite"}),
                table,
            ],
            id="pl-history-table-content",
            className="risk-table-wrap pl-history-table-wrap",
        ),
        effective_open,
        effective_comparison_tokens,
        effective_selection,
    )


def build_pl_history_figure(
    series: pd.DataFrame,
    *,
    path: Sequence[str] = (),
) -> go.Figure:
    """Plot observed Colossus and/or Predict daily series without filling gaps."""

    figure = go.Figure()
    label = " → ".join(str(value) for value in path) or "TOTAL"
    required_columns = {MARKET_DATE, HISTORY_TYPE, PL}
    if not isinstance(series, pd.DataFrame):
        raise TypeError("series must be a pandas DataFrame")
    if not required_columns.issubset(series.columns):
        if not series.empty:
            missing = sorted(required_columns - set(series.columns))
            raise ValueError(f"P&L history series is missing columns: {missing}")
        series = pd.DataFrame(columns=[MARKET_DATE, HISTORY_TYPE, PL])
    styles = {
        COLOSSUS_TYPE: {"color": "#1f77b4", "dash": "solid", "symbol": "circle"},
        PREDICT_TYPE: {"color": "#ff7f0e", "dash": "dash", "symbol": "diamond"},
    }
    for history_type in (COLOSSUS_TYPE, PREDICT_TYPE):
        scoped = series.loc[series[HISTORY_TYPE].eq(history_type)].sort_values(
            MARKET_DATE,
            kind="stable",
        )
        if scoped.empty:
            continue
        style = styles[history_type]
        figure.add_trace(
            go.Scatter(
                x=scoped[MARKET_DATE],
                y=scoped[PL],
                mode="lines+markers",
                name=history_type,
                line={"color": style["color"], "dash": style["dash"]},
                marker={"symbol": style["symbol"]},
                customdata=[[history_type, label] for _index in scoped.index],
                hovertemplate=(
                    "Market Date %{x}<br>P&L Type %{customdata[0]}<br>"
                    "Scope %{customdata[1]}<br>P&L %{y:,.2f}<extra></extra>"
                ),
            )
        )
    if not figure.data:
        figure.add_annotation(
            text="Select a P&L cell to plot its observed daily series.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
    figure.update_layout(
        title=f"Colossus / Predict P&L · {label}",
        xaxis_title="Market Date",
        yaxis_title="P&L",
        hovermode="x unified",
        margin={"l": 70, "r": 30, "t": 60, "b": 70},
        template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    figure.update_xaxes(type="date", automargin=True)
    figure.update_yaxes(automargin=True, tickformat=",.2f", zeroline=True)
    return figure


def build_pl_history_series_selector() -> dcc.RadioItems:
    """Return the page-local Colossus/Predict/Both selector."""

    return dcc.RadioItems(
        id="pl-history-series-selector",
        options=[
            {"label": "Both", "value": "both"},
            {"label": COLOSSUS_TYPE, "value": "colossus"},
            {"label": PREDICT_TYPE, "value": "predict"},
        ],
        value="both",
        inline=True,
        className="detail-tenor-view-radio pl-history-series-selector",
    )


__all__ = [
    "DAILY_P_PERIOD",
    "MTD_PERIOD",
    "PL_HISTORY_EXPANDABLE_PERIODS",
    "PL_HISTORY_METRIC_CELL_TYPE",
    "PL_HISTORY_PERIOD_HEADER_TYPE",
    "PL_HISTORY_ROW_TOGGLE_TYPE",
    "PL_HISTORY_TABLE_PERIODS",
    "YTD_PERIOD",
    "build_pl_history_figure",
    "build_pl_history_series_selector",
    "build_pl_history_table_with_state",
    "normalize_pl_history_expanded_periods",
    "normalize_pl_history_open_tokens",
    "pl_history_path_from_token",
    "pl_history_path_token",
    "summarize_visible_pl_history",
    "toggle_pl_history_expanded_periods",
    "toggle_pl_history_open_tokens",
]
