"""Official historical Risk comparison for the Validate P&L section."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from dash import ALL, Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from core.s11_risk_archive import (
    COLOSSUS_COLUMNS,
    DRISK,
    RISK,
    list_completed_market_dates,
    load_risk_archive,
    validate_risk_archive_frame,
)

from .s02_constants import (
    RISK_TYPE_ORDER,
    ROW_TOGGLE_CLOSED_GLYPH,
    ROW_TOGGLE_OPEN_GLYPH,
)
from .s03_aggregate import format_number, number_sign_class


VALIDATE_PL_ROW_TOGGLE_TYPE = "validate-pl-row-toggle"
VALIDATE_PL_JOIN_KEYS = ("risk type", "risk greek", "underlying", "portfolio")
VALIDATE_PL_GROUPS = VALIDATE_PL_JOIN_KEYS
VALIDATE_PL_METRICS = ("risk", "drisk", "pl", "colossus")
_COLOSSUS_RENAME = {
    "Portfolio": "portfolio",
    "Underlying": "underlying",
    "Risk Type": "risk type",
    "Risk Greek": "risk greek",
    "PL": "colossus",
}
_METRIC_LABELS = {
    "risk": "Risk",
    "drisk": "dRisk",
    "pl": "P",
    "colossus": "C",
}
_METRIC_TITLES = {
    "risk": "Risk",
    "drisk": "dRisk",
    "pl": "Predict P&L",
    "colossus": "Colossus P&L",
}


def _path_token(context: Mapping[str, object]) -> str:
    payload = {
        column: str(context[column])
        for column in VALIDATE_PL_GROUPS
        if column in context
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _path_from_token(value: object) -> dict[str, str] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    keys = tuple(column for column in VALIDATE_PL_GROUPS if column in parsed)
    if tuple(parsed) and set(parsed) != set(keys):
        return None
    if keys != VALIDATE_PL_GROUPS[: len(keys)]:
        return None
    if any(
        not isinstance(parsed[column], str) or not parsed[column] for column in keys
    ):
        return None
    return {column: parsed[column] for column in keys}


def normalize_validate_pl_open_paths(value: object) -> list[str]:
    """Return stable, unique hierarchy tokens and discard malformed browser state."""

    if not isinstance(value, (list, tuple)):
        return []
    paths: dict[str, None] = {}
    for candidate in value:
        parsed = _path_from_token(candidate)
        if parsed is not None:
            paths[_path_token(parsed)] = None
    return sorted(paths)


def toggle_validate_pl_open_paths(current: object, requested: object) -> list[str]:
    """Toggle one hierarchy path while keeping page-local state normalized."""

    parsed = _path_from_token(requested)
    normalized = normalize_validate_pl_open_paths(current)
    if parsed is None:
        return normalized
    token = _path_token(parsed)
    selected = set(normalized)
    if token in selected:
        selected.remove(token)
        # A collapsed parent cannot retain invisible open descendants.
        selected = {
            candidate
            for candidate in selected
            if not (
                (child := _path_from_token(candidate)) is not None
                and len(child) > len(parsed)
                and all(child.get(key) == value for key, value in parsed.items())
            )
        }
    else:
        selected.add(token)
    return sorted(selected)


def _normalize_colossus(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Colossus history must be a pandas DataFrame")
    actual_columns = tuple(str(column).strip() for column in frame.columns)
    if actual_columns != COLOSSUS_COLUMNS:
        raise ValueError(
            "Colossus history must have exactly these columns in order: "
            f"{list(COLOSSUS_COLUMNS)}; found {list(actual_columns)}"
        )
    normalized = frame.copy(deep=True)
    normalized.columns = list(COLOSSUS_COLUMNS)
    normalized = normalized.rename(columns=_COLOSSUS_RENAME)
    for column in VALIDATE_PL_JOIN_KEYS:
        values = normalized[column]
        invalid = values.isna() | values.astype("string").str.strip().eq("")
        if invalid.any():
            rows = normalized.index[invalid].tolist()[:5]
            raise ValueError(
                f"Colossus history column {column!r} contains blank keys at rows {rows}"
            )
        normalized[column] = values.astype(str).str.strip()
    values = normalized["colossus"]
    boolean = values.map(lambda value: isinstance(value, (bool, np.bool_)))
    numeric = pd.to_numeric(values, errors="coerce")
    invalid = boolean | numeric.isna() | ~np.isfinite(numeric)
    if invalid.any():
        rows = normalized.index[invalid].tolist()[:5]
        raise ValueError(
            f"Colossus history PL must contain finite numbers; invalid rows {rows}"
        )
    normalized["colossus"] = numeric.astype(float)
    duplicate = normalized.duplicated(list(VALIDATE_PL_JOIN_KEYS), keep=False)
    if duplicate.any():
        keys = (
            normalized.loc[duplicate, list(VALIDATE_PL_JOIN_KEYS)]
            .drop_duplicates()
            .to_dict("records")
        )
        raise ValueError(f"Colossus history contains duplicate comparison keys: {keys}")
    return normalized


def build_validate_pl_comparison(
    risk: pd.DataFrame,
    colossus: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate Predict once, then one-to-one join Colossus at its true grain.

    The comparison intentionally stops at Risk Type + Risk Greek + Underlying +
    Portfolio. A Portfolio-level Colossus value is therefore never copied onto
    several tenor or product rows and cannot be multiplied in parent totals.
    """

    prepared = validate_risk_archive_frame(risk).rename(
        columns={
            "Portfolio": "portfolio",
            "Underlying": "underlying",
            "Risk Type": "risk type",
            "Risk Greek": "risk greek",
            RISK: "risk",
            DRISK: "drisk",
            "PL": "pl",
        }
    )
    predicted = prepared.groupby(
        list(VALIDATE_PL_JOIN_KEYS),
        as_index=False,
        dropna=False,
        sort=False,
    ).agg(
        risk=("risk", lambda values: values.sum(min_count=1)),
        drisk=("drisk", lambda values: values.sum(min_count=1)),
        # One unavailable archived tenor makes this four-key Predict value
        # unavailable. Never present a partial P as the complete comparison.
        pl=("pl", lambda values: values.sum(min_count=len(values))),
    )
    actual = _normalize_colossus(colossus)
    comparison = predicted.merge(
        actual,
        on=list(VALIDATE_PL_JOIN_KEYS),
        how="outer",
        validate="one_to_one",
        indicator=True,
    ).rename(columns={"_merge": "comparison status"})
    status_labels = {
        "both": "Matched",
        "left_only": "Predict only",
        "right_only": "Colossus only",
    }
    comparison["comparison status"] = (
        comparison["comparison status"].astype("object").map(status_labels)
    )
    return comparison.sort_values(
        list(VALIDATE_PL_JOIN_KEYS), kind="stable"
    ).reset_index(drop=True)


def _scope(frame: pd.DataFrame, context: Mapping[str, str]) -> pd.DataFrame:
    scoped = frame
    for column, value in context.items():
        scoped = scoped.loc[scoped[column].astype(str).eq(str(value))]
    return scoped


def _ordered_values(frame: pd.DataFrame, column: str) -> list[str]:
    values = frame[column].dropna().astype(str).unique().tolist()
    if column == "risk type":
        return sorted(values, key=lambda value: (RISK_TYPE_ORDER.get(value, 99), value))
    if column == "underlying":
        ranking = frame.groupby(column, as_index=False, dropna=False)["pl"].sum(
            min_count=1
        )
        ranking["_magnitude"] = ranking["pl"].abs()
        ranking["_label"] = ranking[column].astype(str).str.casefold()
        return (
            ranking.sort_values(
                ["_magnitude", "_label"],
                ascending=[False, True],
                kind="stable",
                na_position="last",
            )[column]
            .astype(str)
            .tolist()
        )
    return sorted(values, key=lambda value: value.casefold())


def _sum_metric(frame: pd.DataFrame, metric: str) -> float:
    return float(pd.to_numeric(frame[metric], errors="coerce").sum(min_count=1))


def _metric_cell(metric: str, value: float) -> html.Td:
    classes = ["metric-cell", number_sign_class(value) if pd.notna(value) else ""]
    if metric in {"pl", "colossus"}:
        classes.append("pl-cell")
    rendered = (
        format_number(value, column="pl" if metric == "colossus" else metric)
        if pd.notna(value)
        else ""
    )
    return html.Td(
        rendered,
        className=" ".join(value for value in classes if value)
        + (" metric-cell-inert" if not rendered else ""),
        title=_METRIC_TITLES[metric],
        **{
            "data-metric": metric,
            "data-copy-value": "" if pd.isna(value) else str(value),
        },
    )


def _tree_rows(
    frame: pd.DataFrame,
    open_paths: set[str],
    *,
    level: int = 0,
    context: Mapping[str, str] | None = None,
) -> list[html.Tr]:
    context = dict(context or {})
    if level >= len(VALIDATE_PL_GROUPS):
        return []
    column = VALIDATE_PL_GROUPS[level]
    rows: list[html.Tr] = []
    for value in _ordered_values(frame, column):
        next_context = {**context, column: value}
        scoped = _scope(frame, {column: value})
        if scoped.empty:
            continue
        token = _path_token(next_context)
        can_expand = level + 1 < len(VALIDATE_PL_GROUPS)
        is_open = can_expand and token in open_paths
        if can_expand:
            toggle = html.Button(
                ROW_TOGGLE_OPEN_GLYPH if is_open else ROW_TOGGLE_CLOSED_GLYPH,
                id={"type": VALIDATE_PL_ROW_TOGGLE_TYPE, "path": token},
                n_clicks=0,
                type="button",
                className="row-toggle",
                title=("Collapse" if is_open else "Expand") + f" {value}",
                **{
                    "aria-label": ("Collapse" if is_open else "Expand") + f" {value}",
                    "aria-expanded": str(is_open).lower(),
                },
            )
        else:
            toggle = html.Button(
                "",
                type="button",
                className="row-toggle",
                disabled=True,
                tabIndex=-1,
                **{"aria-hidden": "true"},
            )
        cells: list[object] = [
            html.Th(
                [toggle, html.Span(value, className="row-label-text")],
                className=f"index-cell level-{level}",
                style={"paddingLeft": f"{14 + level * 18}px"},
                scope="row",
                **{"data-metric": "index", "data-copy-value": value},
            ),
            *(
                _metric_cell(metric, _sum_metric(scoped, metric))
                for metric in VALIDATE_PL_METRICS
            ),
        ]
        props: dict[str, object] = {"aria-level": str(level + 1)}
        if can_expand:
            props["aria-expanded"] = str(is_open).lower()
        rows.append(
            html.Tr(
                cells,
                className=(
                    f"group-row group-level-{level} group-kind-{column.replace(' ', '-')}"
                    + (
                        " hierarchy-total-row"
                        if column in {"risk type", "risk greek"}
                        else ""
                    )
                ),
                **props,
            )
        )
        if is_open:
            rows.extend(
                _tree_rows(
                    scoped,
                    open_paths,
                    level=level + 1,
                    context=next_context,
                )
            )
    return rows


def build_validate_pl_table(
    comparison: pd.DataFrame,
    *,
    open_paths: object = None,
) -> html.Div:
    """Render a Risk-Explorer-style tree at the exact P/C comparison grain."""

    if not isinstance(comparison, pd.DataFrame):
        raise TypeError("comparison must be a pandas DataFrame")
    required = [*VALIDATE_PL_JOIN_KEYS, *VALIDATE_PL_METRICS]
    missing = [column for column in required if column not in comparison]
    if missing and not comparison.empty:
        raise ValueError(f"historical Risk comparison is missing columns: {missing}")
    if comparison.empty:
        return html.Div(
            [
                html.Strong("No historical Risk rows"),
                html.Span("The selected official snapshot has no comparison rows."),
            ],
            className="empty-state",
            role="status",
        )
    normalized_open = set(normalize_validate_pl_open_paths(open_paths))
    total_cells: list[object] = [
        html.Th(
            html.Span("TOTAL", className="row-label-text"),
            className="index-cell total-index",
            scope="row",
            **{"data-metric": "index", "data-copy-value": "TOTAL"},
        ),
        *(
            _metric_cell(metric, _sum_metric(comparison, metric))
            for metric in VALIDATE_PL_METRICS
        ),
    ]
    headers = [
        html.Th(
            "Index",
            className="index-header",
            scope="col",
            **{"data-metric": "index"},
        ),
        *(
            html.Th(
                _METRIC_LABELS[metric],
                className=(
                    "metric-header pl-header"
                    if metric in {"pl", "colossus"}
                    else "metric-header"
                ),
                title=_METRIC_TITLES[metric],
                scope="col",
                **{"data-metric": metric},
            )
            for metric in VALIDATE_PL_METRICS
        ),
    ]
    rows = [html.Tr(total_cells, className="total-row")]
    rows.extend(_tree_rows(comparison, normalized_open))
    return html.Div(
        html.Table(
            [
                html.Caption(
                    "Official historical Risk hierarchy with Predict and Colossus P&L",
                    className="sr-only",
                ),
                html.Thead(html.Tr(headers)),
                html.Tbody(rows),
            ],
            className="risk-table validate-pl-table",
            role="treegrid",
            **{"aria-label": "Official historical Risk hierarchy"},
        ),
        className="risk-table-wrap validate-pl-table-wrap",
    )


def _available_dates(root: str | Path) -> tuple[str, ...]:
    return tuple(sorted(list_completed_market_dates(root)))


def build_validate_pl_section() -> html.Details:
    """Build the collapsed P&L validation section without touching storage."""

    return html.Details(
        [
            html.Summary(
                "Validate P&L",
                id="pl-validate-summary",
                n_clicks=0,
                className="aux-summary",
            ),
            html.Div(
                [
                    html.P(
                        "Choose one completed official date and compare the P&L "
                        "predicted by Risk Explorer (P) with Colossus P&L (C). "
                        "The expandable hierarchy is Risk Type → Risk Greek → "
                        "Underlying → Portfolio.",
                        className="pl-editor-guide",
                    ),
                    html.Div(
                        [
                            html.Label("Official date", htmlFor="pl-validate-date"),
                            dcc.Dropdown(
                                id="pl-validate-date",
                                options=[],
                                value=None,
                                clearable=False,
                                searchable=True,
                                disabled=True,
                                placeholder="No completed dates",
                                style={"minWidth": "220px"},
                            ),
                        ],
                        className="pl-editor-filter",
                    ),
                    html.Div(
                        "Open Validate P&L to discover completed official dates.",
                        id="pl-validate-catalog-status",
                        className="pl-send-status",
                        role="status",
                    ),
                    html.Div(
                        "Open Validate P&L and choose an official date.",
                        id="pl-validate-status",
                        className="pl-send-status",
                        role="status",
                    ),
                    dcc.Loading(
                        html.Div(
                            html.Div(
                                "Open Validate P&L to load the official comparison.",
                                className="static-data-empty",
                            ),
                            id="pl-validate-table",
                            className="pl-history-hierarchy-table",
                        ),
                        type="dot",
                        delay_show=160,
                    ),
                    dcc.Store(id="pl-validate-open-paths", data=[]),
                ],
                className="pl-send-panel",
            ),
        ],
        className="aux-details",
    )


def _clicked_path(
    ids: Sequence[Mapping[str, object]] | None,
    clicks: Sequence[int | None] | None,
    triggered_id: object,
) -> str | None:
    if not isinstance(triggered_id, Mapping):
        return None
    for component_id, n_clicks in zip(ids or (), clicks or ()):
        if component_id == triggered_id and int(n_clicks or 0) > 0:
            value = component_id.get("path")
            return value if isinstance(value, str) else None
    return None


def register_validate_pl_callbacks(app, root: str | Path) -> None:
    """Register lazy official-date discovery and validation-tree callbacks."""

    comparison_cache: dict[str, pd.DataFrame] = {}
    cache_lock = Lock()

    def read_comparison(market_date: str) -> pd.DataFrame:
        with cache_lock:
            cached = comparison_cache.get(market_date)
        if cached is not None:
            return cached
        archive = load_risk_archive(root, market_date)
        loaded = build_validate_pl_comparison(archive.risk, archive.colossus)
        with cache_lock:
            comparison_cache.setdefault(market_date, loaded)
            return comparison_cache[market_date]

    @app.callback(
        Output("pl-validate-date", "options"),
        Output("pl-validate-date", "value"),
        Output("pl-validate-date", "disabled"),
        Output("pl-validate-catalog-status", "children"),
        Input("pl-validate-summary", "n_clicks"),
        State("pl-validate-date", "value"),
        prevent_initial_call=True,
    )
    def discover_official_dates(summary_clicks, selected_date):
        if not int(summary_clicks or 0) % 2:
            raise PreventUpdate
        try:
            dates = _available_dates(root)
        except (OSError, ValueError) as exc:
            return [], None, True, f"Official Risk history could not be listed: {exc}"
        if not dates:
            return [], None, True, "No completed official Risk snapshots are available."
        selected = str(selected_date) if str(selected_date) in dates else dates[-1]
        options = [{"label": value, "value": value} for value in reversed(dates)]
        return options, selected, False, ""

    @app.callback(
        Output("pl-validate-table", "children"),
        Output("pl-validate-status", "children"),
        Output("pl-validate-open-paths", "data"),
        Input("pl-validate-date", "value"),
        Input({"type": VALIDATE_PL_ROW_TOGGLE_TYPE, "path": ALL}, "n_clicks"),
        State({"type": VALIDATE_PL_ROW_TOGGLE_TYPE, "path": ALL}, "id"),
        State("pl-validate-open-paths", "data"),
        prevent_initial_call=True,
    )
    def render_validate_pl(
        market_date,
        toggle_clicks,
        toggle_ids,
        open_paths,
    ):
        if not market_date:
            return (
                html.Div(
                    "Choose a completed official date.",
                    className="empty-state",
                ),
                "",
                [],
            )
        triggered_id = ctx.triggered_id
        effective_open = (
            []
            if triggered_id == "pl-validate-date"
            else normalize_validate_pl_open_paths(open_paths)
        )
        requested = _clicked_path(toggle_ids, toggle_clicks, triggered_id)
        if requested is not None:
            effective_open = toggle_validate_pl_open_paths(effective_open, requested)
        try:
            comparison = read_comparison(str(market_date))
        except (OSError, TypeError, ValueError) as exc:
            message = f"Official Risk snapshot {market_date} could not be loaded: {exc}"
            return html.Div(message, className="empty-state"), message, []

        counts = comparison["comparison status"].value_counts()
        matched = int(counts.get("Matched", 0))
        predict_only = int(counts.get("Predict only", 0))
        colossus_only = int(counts.get("Colossus only", 0))
        status = (
            f"Official {market_date} · {len(comparison):,} comparison rows · "
            f"{matched:,} matched"
        )
        if predict_only or colossus_only:
            status += (
                f" · {predict_only:,} Predict-only · {colossus_only:,} Colossus-only"
            )
        return (
            build_validate_pl_table(comparison, open_paths=effective_open),
            status,
            effective_open,
        )


__all__ = [
    "VALIDATE_PL_GROUPS",
    "VALIDATE_PL_JOIN_KEYS",
    "VALIDATE_PL_METRICS",
    "VALIDATE_PL_ROW_TOGGLE_TYPE",
    "build_validate_pl_comparison",
    "build_validate_pl_table",
    "build_validate_pl_section",
    "normalize_validate_pl_open_paths",
    "register_validate_pl_callbacks",
    "toggle_validate_pl_open_paths",
]
