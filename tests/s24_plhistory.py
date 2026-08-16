"""Expandable Colossus/Predict P&L history component contracts."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
from dash import dcc, html

from core.s04_pl import (
    ACTIVITY,
    CATEGORY,
    COLOSSUS_TYPE,
    HISTORY_FILE_COLUMNS,
    HISTORY_MAPPING_STATUS,
    HISTORY_TYPE,
    PL_HISTORY_COLUMNS,
    PORTFOLIO,
    PREDICT_TYPE,
    SIGNOFF_GROUP,
    SUB_CATEGORY,
    select_pl_history_series,
)
from ui.s12_plhistory import (
    DAILY_P_PERIOD,
    MTD_PERIOD,
    PL_HISTORY_METRIC_CELL_TYPE,
    PL_HISTORY_PERIOD_HEADER_TYPE,
    PL_HISTORY_ROW_TOGGLE_TYPE,
    YTD_PERIOD,
    build_pl_history_figure,
    build_pl_history_series_selector,
    build_pl_history_table_with_state,
    pl_history_path_token,
    summarize_visible_pl_history,
    toggle_pl_history_expanded_periods,
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
    history = pd.DataFrame(
        rows,
        columns=["Market Date", HISTORY_TYPE, *HISTORY_FILE_COLUMNS],
    )
    history = history.rename(columns={"Book": PORTFOLIO})
    history[ACTIVITY] = history[PORTFOLIO].map({"BOOK-A": "Rates", "BOOK-B": "FX"})
    history[SIGNOFF_GROUP] = history[PORTFOLIO].map(
        {"BOOK-A": "SOG-A", "BOOK-B": "SOG-B"}
    )
    history[CATEGORY] = "Core"
    history[SUB_CATEGORY] = "Synthetic"
    history[HISTORY_MAPPING_STATUS] = "Mapped"
    return history.loc[:, list(PL_HISTORY_COLUMNS)]


def _metric_button(
    component: object,
    path: tuple[str, ...],
    period: str,
    history_type: str,
) -> object:
    token = pl_history_path_token(path)
    return next(
        item
        for item in _walk(component)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_METRIC_CELL_TYPE
        and item.id.get("path") == token
        and item.id.get("period") == period
        and item.id.get("series") == history_type
    )


def test_history_tree_is_lazy_and_uses_global_latest_date_for_stale_nodes() -> None:
    history = _history()
    summary = summarize_visible_pl_history(history)
    table, open_paths, comparisons, selection = build_pl_history_table_with_state(
        history
    )

    assert summary["Hierarchy Path"].tolist() == [(), ("SOG-A",), ("SOG-B",)]
    assert isinstance(table, html.Div)
    assert open_paths == []
    assert comparisons == []
    assert selection == {"path": []}
    tree = next(item for item in _walk(table) if isinstance(item, html.Table))
    assert tree.role == "treegrid"
    headers = [
        _text(item)
        for item in _walk(tree)
        if isinstance(item, html.Th) and "header" in str(item.className or "")
    ]
    assert headers == ["Index", DAILY_P_PERIOD, "▸ MTD", "▸ YTD"]
    row_toggles = [
        item
        for item in _walk(tree)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_ROW_TOGGLE_TYPE
    ]
    assert {item.id["path"] for item in row_toggles} == {
        pl_history_path_token(("SOG-A",)),
        pl_history_path_token(("SOG-B",)),
    }
    assert {item.children for item in row_toggles} == {"▸"}
    assert all(
        item.to_plotly_json()["props"]["aria-expanded"] == "false"
        for item in row_toggles
    )
    assert all(
        item.to_plotly_json()["props"]["aria-label"].startswith("Expand SignoffGroup: ")
        for item in row_toggles
    )

    # IR remains available even though only FX exists on the global latest day.
    assert _text(_metric_button(table, ("SOG-A",), DAILY_P_PERIOD, PREDICT_TYPE)) == "—"
    assert _text(_metric_button(table, ("SOG-A",), MTD_PERIOD, COLOSSUS_TYPE)) == "21"
    assert _text(_metric_button(table, ("SOG-A",), YTD_PERIOD, COLOSSUS_TYPE)) == "22"
    assert _text(_metric_button(table, (), DAILY_P_PERIOD, PREDICT_TYPE)) == "8"
    assert not any(isinstance(item, html.Small) for item in _walk(table))
    assert "Risk Type" not in _text(table)


def test_history_tree_matches_risk_explorer_and_period_headers_toggle() -> None:
    history = _history()
    sog = pl_history_path_token(("SOG-A",))
    ir = pl_history_path_token(("SOG-A", "IR"))
    open_paths = toggle_pl_history_open_tokens([], sog)
    table, effective_open, comparisons, selection = build_pl_history_table_with_state(
        history, open_path_tokens=open_paths
    )

    assert effective_open == [sog]
    assert comparisons == []
    assert selection == {"path": []}
    visible_toggles = {
        item.id["path"]
        for item in _walk(table)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_ROW_TOGGLE_TYPE
    }
    assert visible_toggles == {
        sog,
        ir,
        pl_history_path_token(("SOG-B",)),
    }
    ir_toggle = next(
        item
        for item in _walk(table)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_ROW_TOGGLE_TYPE
        and item.id.get("path") == sog
    )
    assert ir_toggle.children == "−"
    assert ir_toggle.to_plotly_json()["props"]["aria-label"] == (
        "Collapse SignoffGroup: SOG-A"
    )
    assert "Risk Greek" not in _text(table)
    assert "Underlying" not in _text(table)

    open_paths = toggle_pl_history_open_tokens(open_paths, ir)
    expanded, effective_open, _comparisons, _selection = (
        build_pl_history_table_with_state(history, open_path_tokens=open_paths)
    )
    assert effective_open == [sog, ir]
    expanded_toggle_paths = {
        item.id["path"]
        for item in _walk(expanded)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_ROW_TOGGLE_TYPE
    }
    assert pl_history_path_token(("SOG-A", "IR", "Delta")) in expanded_toggle_paths

    comparison_state = toggle_pl_history_expanded_periods([], MTD_PERIOD)
    compared, _open, effective_comparisons, effective_selection = (
        build_pl_history_table_with_state(
            history,
            open_path_tokens=open_paths,
            open_comparison_tokens=comparison_state,
            selection={"path": ["SOG-A", "IR"]},
        )
    )
    assert effective_comparisons == [MTD_PERIOD]
    assert effective_selection == {"path": ["SOG-A", "IR"]}
    headers = [
        _text(item)
        for item in _walk(compared)
        if isinstance(item, html.Th) and "header" in str(item.className or "")
    ]
    assert headers == ["Index", DAILY_P_PERIOD, "− MTD (C)", "MTD (P)", "▸ YTD"]
    period_headers = [
        item
        for item in _walk(compared)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == PL_HISTORY_PERIOD_HEADER_TYPE
    ]
    assert {item.id["period"] for item in period_headers} == {MTD_PERIOD, YTD_PERIOD}
    assert (
        _text(_metric_button(compared, ("SOG-A", "IR"), MTD_PERIOD, COLOSSUS_TYPE))
        == "21"
    )
    assert (
        _text(_metric_button(compared, ("SOG-A", "IR"), MTD_PERIOD, PREDICT_TYPE))
        == "20"
    )
    assert not any(isinstance(item, html.Small) for item in _walk(compared))

    both_periods = toggle_pl_history_expanded_periods(comparison_state, YTD_PERIOD)
    both_expanded, _open, effective_periods, _selection = (
        build_pl_history_table_with_state(
            history,
            open_path_tokens=open_paths,
            open_comparison_tokens=both_periods,
        )
    )
    assert effective_periods == [MTD_PERIOD, YTD_PERIOD]
    assert [
        _text(item)
        for item in _walk(both_expanded)
        if isinstance(item, html.Th) and "header" in str(item.className or "")
    ] == [
        "Index",
        DAILY_P_PERIOD,
        "− MTD (C)",
        "MTD (P)",
        "− YTD (C)",
        "YTD (P)",
    ]

    leaf_open: list[str] = []
    for path in (
        ("SOG-A",),
        ("SOG-A", "IR"),
        ("SOG-A", "IR", "Delta"),
        ("SOG-A", "IR", "Delta", "EUR"),
        ("SOG-A", "IR", "Delta", "EUR", "XVA"),
    ):
        leaf_open = toggle_pl_history_open_tokens(
            leaf_open,
            pl_history_path_token(path),
        )
    leaf_table, _open, _periods, _selection = build_pl_history_table_with_state(
        history,
        open_path_tokens=leaf_open,
    )
    leaf_spacers = [
        item
        for item in _walk(leaf_table)
        if isinstance(item, html.Button)
        and "pl-history-row-toggle" in str(item.className or "").split()
        and bool(item.to_plotly_json()["props"].get("disabled"))
    ]
    assert len(leaf_spacers) == 1
    leaf_props = leaf_spacers[0].to_plotly_json()["props"]
    assert leaf_props["children"] == ""
    assert leaf_props["tabIndex"] == -1
    assert leaf_props["aria-hidden"] == "true"
    assert "aria-expanded" not in leaf_props

    closed = toggle_pl_history_expanded_periods(comparison_state, MTD_PERIOD)
    assert closed == []
    assert toggle_pl_history_open_tokens(open_paths, sog) == []


def test_history_figure_plots_only_observed_colossus_and_predict_rows() -> None:
    history = _history()
    series = select_pl_history_series(history, ("SOG-A", "IR"))
    figure = build_pl_history_figure(series, path=("SOG-A", "IR"))

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

    predict = select_pl_history_series(history, ("SOG-A", "IR"), PREDICT_TYPE)
    predict_figure = build_pl_history_figure(predict, path=("SOG-A", "IR"))
    assert [trace.name for trace in predict_figure.data] == [PREDICT_TYPE]
    assert list(predict_figure.data[0].y) == [1.5, 2.5, 11.0, 6.0]

    empty = build_pl_history_figure(pd.DataFrame(), path=("SOG-A", "IR"))
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
