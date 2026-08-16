"""Expandable Colossus/Predict P&L history component contracts."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
from dash import dcc, html

from core.s04_pl import (
    COLOSSUS_TYPE,
    HISTORY_FILE_COLUMNS,
    HISTORY_TYPE,
    PREDICT_TYPE,
    select_pl_history_series,
)
from ui.s12_plhistory import (
    DAILY_P_PERIOD,
    MTD_PERIOD,
    PL_HISTORY_METRIC_CELL_TYPE,
    PL_HISTORY_ROW_TOGGLE_TYPE,
    YTD_PERIOD,
    build_pl_history_figure,
    build_pl_history_series_selector,
    build_pl_history_table_with_state,
    pl_history_comparison_token,
    pl_history_path_token,
    summarize_visible_pl_history,
    toggle_pl_history_comparison_tokens,
    toggle_pl_history_open_tokens,
)


def _walk(component: object) -> Iterable[object]:
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    else:
        yield from _walk(children)


def _text(component: object) -> str:
    if component is None:
        return ""
    if isinstance(component, (str, int, float)):
        return str(component)
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        return "".join(_text(child) for child in children)
    return _text(children)


def _history() -> pd.DataFrame:
    rows = [
        ["2026-01-05", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 1.0],
        ["2026-01-05", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 1.5],
        ["2026-08-03", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 2.0],
        ["2026-08-03", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 2.5],
        ["2026-08-10", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 10.0],
        ["2026-08-10", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 11.0],
        ["2026-08-12", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 4.0],
        ["2026-08-13", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 5.0],
        ["2026-08-13", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 6.0],
        [
            "2026-08-14",
            COLOSSUS_TYPE,
            "FX",
            "Delta",
            "EUR/USD",
            "Spot",
            "BOOK-B",
            7.0,
        ],
        [
            "2026-08-14",
            PREDICT_TYPE,
            "FX",
            "Delta",
            "EUR/USD",
            "Spot",
            "BOOK-B",
            8.0,
        ],
    ]
    return pd.DataFrame(
        rows,
        columns=["Market Date", HISTORY_TYPE, *HISTORY_FILE_COLUMNS],
    )


def _metric_button(component: object, path: tuple[str, ...], period: str) -> object:
    token = pl_history_path_token(path)
    return next(
        item
        for item in _walk(component)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_METRIC_CELL_TYPE
        and item.id.get("path") == token
        and item.id.get("period") == period
    )


def test_history_tree_is_lazy_and_uses_global_latest_date_for_stale_nodes() -> None:
    history = _history()
    summary = summarize_visible_pl_history(history)
    table, open_paths, comparisons, selection = build_pl_history_table_with_state(
        history
    )

    assert summary["Hierarchy Path"].tolist() == [(), ("IR",), ("FX",)]
    assert isinstance(table, html.Div)
    assert open_paths == []
    assert comparisons == []
    assert selection == {"path": []}
    tree = next(item for item in _walk(table) if isinstance(item, html.Table))
    assert tree.role == "treegrid"
    headers = [
        item.children
        for item in _walk(tree)
        if isinstance(item, html.Th) and "header" in str(item.className or "")
    ]
    assert headers == ["P&L hierarchy", DAILY_P_PERIOD, MTD_PERIOD, YTD_PERIOD]
    row_toggle_paths = {
        item.id["path"]
        for item in _walk(tree)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_ROW_TOGGLE_TYPE
    }
    assert row_toggle_paths == {
        pl_history_path_token(("IR",)),
        pl_history_path_token(("FX",)),
    }

    # IR remains available even though only FX exists on the global latest day.
    assert _text(_metric_button(table, ("IR",), DAILY_P_PERIOD)) == "—P"
    assert _text(_metric_button(table, ("IR",), MTD_PERIOD)) == "21C▸"
    assert _text(_metric_button(table, ("IR",), YTD_PERIOD)) == "22C▸"
    assert _text(_metric_button(table, (), DAILY_P_PERIOD)) == "8P"


def test_history_tree_expands_one_level_at_a_time_and_comparison_cells_toggle() -> None:
    history = _history()
    ir = pl_history_path_token(("IR",))
    ir_delta = pl_history_path_token(("IR", "Delta"))
    open_paths = toggle_pl_history_open_tokens([], ir)
    table, effective_open, comparisons, selection = build_pl_history_table_with_state(
        history, open_path_tokens=open_paths
    )

    assert effective_open == [ir]
    assert comparisons == []
    assert selection == {"path": []}
    visible_toggles = {
        item.id["path"]
        for item in _walk(table)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_ROW_TOGGLE_TYPE
    }
    assert visible_toggles == {
        ir,
        ir_delta,
        pl_history_path_token(("FX",)),
    }
    assert "Risk Greek" in _text(table)
    assert "Underlying" not in _text(table)

    open_paths = toggle_pl_history_open_tokens(open_paths, ir_delta)
    expanded, effective_open, _comparisons, _selection = (
        build_pl_history_table_with_state(history, open_path_tokens=open_paths)
    )
    assert effective_open == [ir, ir_delta]
    assert "Underlying" in _text(expanded)
    assert "Product" not in _text(expanded)

    comparison = pl_history_comparison_token(("IR",), MTD_PERIOD)
    comparison_state = toggle_pl_history_comparison_tokens([], comparison)
    compared, _open, effective_comparisons, effective_selection = (
        build_pl_history_table_with_state(
            history,
            open_path_tokens=open_paths,
            open_comparison_tokens=comparison_state,
            selection={"path": ["IR"]},
        )
    )
    assert effective_comparisons == [comparison]
    assert effective_selection == {"path": ["IR"]}
    metric = _metric_button(compared, ("IR",), MTD_PERIOD)
    assert "is-expanded" in metric.className
    assert _text(metric) == "C21P20−"

    closed = toggle_pl_history_comparison_tokens(comparison_state, comparison)
    assert closed == []
    assert toggle_pl_history_open_tokens(open_paths, ir) == []


def test_history_figure_plots_only_observed_colossus_and_predict_rows() -> None:
    history = _history()
    series = select_pl_history_series(history, ("IR",))
    figure = build_pl_history_figure(series, path=("IR",))

    assert [trace.name for trace in figure.data] == [COLOSSUS_TYPE, PREDICT_TYPE]
    assert list(figure.data[0].x) == [
        "2026-01-05",
        "2026-08-03",
        "2026-08-10",
        "2026-08-12",
        "2026-08-13",
    ]
    assert list(figure.data[1].x) == [
        "2026-01-05",
        "2026-08-03",
        "2026-08-10",
        "2026-08-13",
    ]
    assert "2026-08-11" not in {
        str(value) for trace in figure.data for value in trace.x
    }
    assert "2026-08-14" not in {
        str(value) for trace in figure.data for value in trace.x
    }
    assert all(value != 0 for trace in figure.data for value in trace.y)

    predict = select_pl_history_series(history, ("IR",), PREDICT_TYPE)
    predict_figure = build_pl_history_figure(predict, path=("IR",))
    assert [trace.name for trace in predict_figure.data] == [PREDICT_TYPE]
    assert list(predict_figure.data[0].y) == [1.5, 2.5, 11.0, 6.0]

    empty = build_pl_history_figure(pd.DataFrame(), path=("IR",))
    assert not empty.data
    assert empty.layout.annotations[0].text.startswith("Select a P&L cell")


def test_history_series_selector_exposes_both_and_each_named_source() -> None:
    selector = build_pl_history_series_selector()

    assert isinstance(selector, dcc.RadioItems)
    assert selector.value == "both"
    assert selector.inline is True
    assert selector.options == [
        {"label": "Both", "value": "both"},
        {"label": COLOSSUS_TYPE, "value": "colossus"},
        {"label": PREDICT_TYPE, "value": "predict"},
    ]
