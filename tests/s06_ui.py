"""Stable component-level checks for collapsible search and status-aware charts."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from dash import dash_table, dcc, html

from ui.s04_components import (
    QUICK_SEARCH_HIERARCHY_DEPTH,
    build_quick_market_result,
    build_quick_market_search,
    build_quick_search,
    build_small_table,
    build_tenor_heatmap,
    build_tree_rows,
    build_unmapped_books_table,
    detail_tenor_view_state,
)
from ui.s03_aggregate import ordered_unique, row_key
from ui.s07_events import _prune_quick_search_indexes, _render_quick_search_pivot


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


def _table_headers(component: object) -> list[str]:
    head = next(item for item in _walk(component) if isinstance(item, html.Thead))
    return [str(item.children) for item in _walk(head) if isinstance(item, html.Th)]


def _table_row_values(component: object) -> list[list[str]]:
    body = next(item for item in _walk(component) if isinstance(item, html.Tbody))
    return [[str(cell.children) for cell in row.children] for row in body.children]


def test_connector_owned_groups_have_no_hard_coded_display_taxonomy() -> None:
    frame = pd.DataFrame(
        {
            "risk type": ["FX", "FX", "FX"],
            "group": ["Zulu Desk", "G10", "Anything I Want"],
        }
    )

    assert ordered_unique(frame, "group") == [
        "Anything I Want",
        "G10",
        "Zulu Desk",
    ]


def test_split_labels_follow_the_canonical_filter_order() -> None:
    frame = pd.DataFrame(
        {
            "split": ["Cross Gamma", "Gamma", "Risk", "New Position"],
        }
    )

    assert ordered_unique(frame, "split") == [
        "Risk",
        "New Position",
        "Gamma",
        "Cross Gamma",
    ]


def test_quick_risk_and_market_search_use_native_collapsible_chevrons() -> None:
    risk = build_quick_search()
    market = build_quick_market_search()

    assert isinstance(risk, html.Details)
    assert isinstance(market, html.Details)
    assert risk.id == "quick-search-details"
    assert market.id == "quick-market-details"
    assert risk.open is False
    assert market.open is False
    assert isinstance(risk.children[0], html.Summary)
    assert isinstance(market.children[0], html.Summary)
    assert risk.children[0].id == "quick-search-summary"
    assert market.children[0].id == "quick-market-summary"
    assert risk.children[0].n_clicks == 0
    assert market.children[0].n_clicks == 0
    surface_picker = next(
        item
        for item in _walk(market)
        if isinstance(item, dcc.RadioItems) and item.id == "quick-market-surface-metric"
    )
    assert surface_picker.value == "current"
    assert surface_picker.options == [
        {"label": "Open", "value": "open"},
        {"label": "Market Status", "value": "current"},
        {"label": "Move", "value": "move"},
    ]
    surface_control = next(
        item
        for item in _walk(market)
        if isinstance(item, html.Div)
        and getattr(item, "id", None) == "quick-market-surface-metric-control"
    )
    assert surface_control.hidden is True


def test_quick_search_keeps_identity_authority_separate_from_pivot_levels() -> None:
    search = build_quick_search()
    picker = next(
        item
        for item in _walk(search)
        if isinstance(item, dcc.Dropdown) and item.id == "quick-search-dimensions"
    )

    assert picker.value == [
        "Underlying",
        "Tenor Swap",
        "Tenor Option",
    ]
    values = [option["value"] for option in picker.options]
    assert "Tenor Swap" in values
    assert "Tenor Option" in values
    assert "Tnr" not in values
    assert "Tenor Opt" not in values
    identity_mode = next(
        item
        for item in _walk(search)
        if isinstance(item, dcc.RadioItems) and item.id == "quick-search-identity-mode"
    )
    assert identity_mode.value == "reported"
    assert identity_mode.options == [
        {"label": "Reported Underlying", "value": "reported"},
        {"label": "Underlying", "value": "underlying"},
    ]
    ids = {getattr(item, "id", None) for item in _walk(search)}
    assert "greek-filter" not in ids
    assert "quick-search-greek-filter" not in ids


def test_one_axis_market_chart_uses_equal_category_spacing_and_dynamic_label() -> None:
    frame = pd.DataFrame(
        [
            ["10Y", 4.0, 4.1, 0.1],
            ["11Y", 4.2, 4.4, 0.2],
            ["15Y", 4.3, 4.2, -0.1],
        ],
        columns=["Tenor Swap", "Open", "Current", "Move"],
    )

    result, selected, _options, _surface_options = build_quick_market_result(
        frame,
        combine_udl="IR | Delta | USD-SOFR",
        requested_view="auto",
        surface_metric="current",
        market_status="OFFICIAL",
        revision=3,
    )
    graphs = [
        component for component in _walk(result) if isinstance(component, dcc.Graph)
    ]
    figure = graphs[0].figure

    assert selected == "swap"
    # The translucent move bar is added first so both quote lines render over it.
    assert [trace.name for trace in figure.data] == [
        "Market Move",
        "Open",
        "OFFICIAL",
    ]
    assert [trace.yaxis for trace in figure.data] == ["y2", "y", "y"]
    assert [round(float(value), 8) for value in figure.data[0].y] == [0.1, 0.2, -0.1]
    assert figure.layout.xaxis.type == "category"
    assert list(figure.layout.xaxis.categoryarray) == ["10Y", "11Y", "15Y"]
    assert figure.layout.yaxis.title.text == "Open / OFFICIAL"
    assert figure.layout.yaxis2.title.text == "Market Move"
    assert figure.layout.yaxis2.overlaying == "y"
    assert figure.layout.yaxis2.side == "right"


def test_market_curve_and_table_follow_connector_tenor_rank() -> None:
    frame = pd.DataFrame(
        [
            ["10Y", 2, 4.0, 4.1, 0.1],
            ["1Y", 0, 4.2, 4.4, 0.2],
            ["5Y", 1, 4.3, 4.2, -0.1],
        ],
        columns=["Tenor Swap", "Tenor Swap Order", "Open", "Current", "Move"],
    )

    result, selected, _options, _surface_options = build_quick_market_result(
        frame,
        combine_udl="IR | Delta | USD-SOFR",
        requested_view="swap",
        surface_metric="current",
        market_status="LIVE",
        revision=8,
    )
    graph = next(item for item in _walk(result) if isinstance(item, dcc.Graph))

    assert selected == "swap"
    assert list(graph.figure.layout.xaxis.categoryarray) == ["1Y", "5Y", "10Y"]
    assert list(graph.figure.data[0].x) == ["1Y", "5Y", "10Y"]
    assert list(graph.figure.data[2].x) == ["1Y", "5Y", "10Y"]
    assert [row[0] for row in _table_row_values(result)] == ["1Y", "5Y", "10Y"]


def test_market_surface_is_not_hardcoded_to_three_by_three() -> None:
    rows = [
        [swap, option, 10.0 + swap_index, 11.0 + option_index, 1.0]
        for swap_index, swap in enumerate(("1Y", "5Y"))
        for option_index, option in enumerate(("1M", "6M", "2Y"))
    ]
    frame = pd.DataFrame(
        rows,
        columns=["Tenor Swap", "Tenor Option", "Open", "Current", "Move"],
    )

    result, selected, options, surface_options = build_quick_market_result(
        frame,
        combine_udl="IR | DeltaVega | USD-SWAPTION",
        requested_view="surface",
        surface_metric="current",
        market_status="Live",
        revision=4,
    )
    graphs = [
        component for component in _walk(result) if isinstance(component, dcc.Graph)
    ]
    heatmaps = list(graphs[0].figure.data)

    assert selected == "surface"
    assert (
        next(item for item in options if item["value"] == "surface")["disabled"]
        is False
    )
    assert len(heatmaps) == 1
    assert tuple(heatmaps[0].z.shape) == (3, 2)
    assert heatmaps[0].colorbar.title.text == "Live"
    assert surface_options == [
        {"label": "Open", "value": "open"},
        {"label": "Live", "value": "current"},
        {"label": "Move", "value": "move"},
    ]
    axis = graphs[0].figure.layout.xaxis
    assert axis.side == "top"
    assert axis.ticklabelposition == "outside top"
    assert axis.ticks == "outside"
    assert axis.automargin is True
    assert graphs[0].figure.layout.margin.t == 90


def test_market_surface_selector_switches_open_status_and_derived_move() -> None:
    frame = pd.DataFrame(
        [
            ["1Y", "1M", 10.0, 11.5, 999.0],
            ["5Y", "1M", 20.0, 18.0, 999.0],
        ],
        columns=["Tenor Swap", "Tenor Option", "Open", "Current", "Move"],
    )
    expectations = {
        "open": ("Open", [[10.0, 20.0]]),
        "current": ("OFFICIAL", [[11.5, 18.0]]),
        "move": ("Move", [[1.5, -2.0]]),
    }

    for metric, (label, expected_values) in expectations.items():
        result, selected, _options, surface_options = build_quick_market_result(
            frame,
            combine_udl="IR | DeltaVega | USD-SWAPTION",
            requested_view="surface",
            surface_metric=metric,
            market_status="OFFICIAL",
            revision=6,
        )
        graph = next(item for item in _walk(result) if isinstance(item, dcc.Graph))
        trace = graph.figure.data[0]

        assert selected == "surface"
        assert trace.colorbar.title.text == label
        assert trace.z.tolist() == expected_values
        assert surface_options[1] == {
            "label": "OFFICIAL",
            "value": "current",
        }

    move_trace = next(
        item
        for item in _walk(
            build_quick_market_result(
                frame,
                combine_udl="IR | DeltaVega | USD-SWAPTION",
                requested_view="surface",
                surface_metric="move",
                market_status="OFFICIAL",
                revision=6,
            )[0]
        )
        if isinstance(item, dcc.Graph)
    ).figure.data[0]
    assert move_trace.zmid == 0.0
    assert move_trace.zmin == -2.0
    assert move_trace.zmax == 2.0


def test_detail_surface_places_swap_tenors_above_and_outside_the_heatmap() -> None:
    detail = pd.DataFrame(
        {
            "tenor swap": ["1Y", "5Y", "1Y", "5Y"],
            "tenor option": ["1M", "1M", "6M", "6M"],
            "risk": [1.0, 2.0, 3.0, 4.0],
        }
    )

    figure = build_tenor_heatmap(detail, "risk").figure
    axis = figure.layout.xaxis

    assert axis.side == "top"
    assert axis.ticklabelposition == "outside top"
    assert axis.ticks == "outside"
    assert axis.ticklen == 5
    assert axis.automargin is True
    assert axis.title.text == "Tenor Swap"
    assert axis.title.standoff == 10
    assert figure.layout.margin.t == 90


def test_market_surface_and_table_follow_both_connector_rank_axes() -> None:
    swaps = (("10Y", 2), ("1Y", 0), ("5Y", 1))
    options = (("2Y", 2), ("1M", 0), ("6M", 1))
    rows = [
        [swap, swap_rank, option, option_rank, 1.0, 1.1, 0.1]
        for swap, swap_rank in swaps
        for option, option_rank in options
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "Tenor Swap",
            "Tenor Swap Order",
            "Tenor Option",
            "Tenor Option Order",
            "Open",
            "Current",
            "Move",
        ],
    ).sample(frac=1.0, random_state=23)

    result, selected, _options, _surface_options = build_quick_market_result(
        frame,
        combine_udl="IR | DeltaVega | USD-SWAPTION",
        requested_view="surface",
        surface_metric="current",
        market_status="OFFICIAL",
        revision=9,
    )
    graph = next(item for item in _walk(result) if isinstance(item, dcc.Graph))
    traces = list(graph.figure.data)

    assert selected == "surface"
    assert list(traces[0].x) == ["1Y", "5Y", "10Y"]
    # Heatmaps reverse the visible Y axis so the lowest connector rank appears
    # nearest the X axis; the matrix retains forward connector rank order.
    assert list(traces[0].y) == ["2Y", "6M", "1M"]
    assert _table_headers(result) == [
        "Tenor Option / Tenor Swap",
        "1Y",
        "5Y",
        "10Y",
    ]
    matrix_rows = _table_row_values(result)
    assert [row[0] for row in matrix_rows] == ["1M", "6M", "2Y"]
    assert all(row[1:] == ["1.1000", "1.1000", "1.1000"] for row in matrix_rows)


def test_detail_picker_enables_swap_for_curves_and_surface_for_paired_axes() -> None:
    curve = pd.DataFrame(
        {
            "tenor swap": ["2Y", "10Y"],
            "tenor option": ["N/A", "N/A"],
        }
    )
    surface = pd.DataFrame(
        {
            "tenor swap": ["1Y", "5Y"],
            "tenor option": ["1M", "6M"],
        }
    )

    curve_options, curve_selected = detail_tenor_view_state(curve, "swap")
    surface_options, surface_selected = detail_tenor_view_state(surface, "surface")

    assert curve_selected == "swap"
    assert (
        next(item for item in curve_options if item["value"] == "surface")["disabled"]
        is True
    )
    assert surface_selected == "surface"
    assert (
        next(item for item in surface_options if item["value"] == "surface")["disabled"]
        is False
    )


def test_quick_risk_prunes_only_tenor_axes_absent_from_selected_identity() -> None:
    defaults = ("Underlying", "Tenor Swap", "Tenor Option")
    curve = pd.DataFrame(
        [
            {
                QUICK_SEARCH_HIERARCHY_DEPTH: 3,
                "Underlying": "USD-SOFR",
                "Tenor Swap": "5Y",
                "Tenor Option": "N/A",
            }
        ]
    )
    surface = pd.DataFrame(
        [
            {
                QUICK_SEARCH_HIERARCHY_DEPTH: 3,
                "Underlying": "USD-SWAPTION",
                "Tenor Swap": "10Y",
                "Tenor Option": "6M",
            }
        ]
    )

    assert _prune_quick_search_indexes(curve, defaults) == (
        "Underlying",
        "Tenor Swap",
    )
    assert _prune_quick_search_indexes(surface, defaults) == (
        "Underlying",
        "Tenor Swap",
        "Tenor Option",
    )


def test_quick_risk_pruning_preserves_user_reporting_dimensions() -> None:
    selected = ("Portfolio", "Tenor Swap", "Tenor Option")
    hierarchy = pd.DataFrame(
        [
            {
                QUICK_SEARCH_HIERARCHY_DEPTH: 3,
                "Portfolio": "BOOK-A",
                "Tenor Swap": "2Y",
                "Tenor Option": "N/A",
            }
        ]
    )

    assert _prune_quick_search_indexes(hierarchy, selected) == (
        "Portfolio",
        "Tenor Swap",
    )


def test_quick_risk_reruns_with_effective_axes_and_syncs_picker() -> None:
    class Manager:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], str, dict[str, list[str]]]] = []

        def pivot_combined_hierarchy(
            self,
            _identity: str,
            *,
            index_columns: tuple[str, ...],
            leaf_limit: int,
            identity_mode: str,
            risk_filters: dict[str, list[str]],
        ) -> SimpleNamespace:
            assert leaf_limit > 0
            selected = tuple(index_columns)
            self.calls.append((selected, identity_mode, dict(risk_filters)))
            values = {
                "Underlying": "USD-SOFR",
                "Tenor Swap": "5Y",
                "Tenor Option": "N/A",
            }
            rows = []
            for depth in range(1, len(selected) + 1):
                row = {
                    QUICK_SEARCH_HIERARCHY_DEPTH: depth,
                    **{
                        column: values[column] if position < depth else pd.NA
                        for position, column in enumerate(selected)
                    },
                    "Risk": 10.0,
                    "dRisk": 2.0,
                    "PL": 1.0,
                    "Open": 3.0,
                    "Current": 3.5,
                    "Move": 0.5,
                }
                rows.append(row)
            return SimpleNamespace(
                frame=pd.DataFrame(rows),
                total=1,
                revision=12,
            )

    manager = Manager()
    rendered, index_update = _render_quick_search_pivot(
        manager,
        combine_udl="IR | Gamma | USD-SOFR",
        identity_mode="underlying",
        index_columns=("Underlying", "Tenor Swap", "Tenor Option"),
        is_open=True,
        risk_filters={"Split": ["Gamma"]},
    )

    assert isinstance(rendered, html.Div)
    assert index_update == ["Underlying", "Tenor Swap"]
    assert manager.calls == [
        (
            ("Underlying", "Tenor Swap", "Tenor Option"),
            "underlying",
            {"Split": ["Gamma"]},
        ),
        (
            ("Underlying", "Tenor Swap"),
            "underlying",
            {"Split": ["Gamma"]},
        ),
    ]


def test_small_detail_table_only_shows_meaningful_tenor_axes() -> None:
    frame = pd.DataFrame(
        {
            "tenor swap": ["2Y"],
            "tenor option": ["N/A"],
            "risk": [12.0],
            "risk expo": [15.0],
            "risk hedges": [-3.0],
            "rows": [2],
        }
    )

    assert _table_headers(build_small_table(frame, "risk")) == [
        "Tenor Swap",
        "Risk",
        "Risk XVA",
        "Risk Hedges",
        "Rows",
    ]


def test_unmapped_inspector_exposes_tenor_ranks() -> None:
    row = {
        "Portfolio": "BOOK-A",
        "Risk Type": "IR",
        "Risk Greek": "Delta",
        "Tenor Swap": "10Y",
        "Tenor Swap Order": 2,
        "Tenor Option": "6M",
        "Tenor Option Order": 0,
        "Risk": 10.0,
        "dRisk": 1.0,
        "PL": 0.5,
    }
    frame = pd.DataFrame([row])
    expected = {
        "Tenor Swap",
        "Tenor Swap Order",
        "Tenor Option",
        "Tenor Option Order",
    }

    table = next(
        item
        for item in _walk(build_unmapped_books_table(frame))
        if isinstance(item, dash_table.DataTable)
    )
    assert expected <= {column["id"] for column in table.columns}
    assert table.page_size == 25


def test_unmapped_note_distinguishes_rows_from_portfolios() -> None:
    frame = pd.DataFrame(
        {
            "Portfolio": ["BOOK-MISSING-A", "BOOK-MISSING-A", "BOOK-MISSING-B"],
            "Risk Type": ["IR", "IR", "FX"],
            "Risk Greek": ["Delta", "Gamma", "Delta"],
            "Risk": [10.0, 20.0, 30.0],
            "dRisk": [1.0, 2.0, 3.0],
            "PL": [0.5, 1.0, 1.5],
        }
    )

    note = next(
        item
        for item in _walk(build_unmapped_books_table(frame))
        if getattr(item, "className", None) == "unmapped-note"
    )

    assert note.children == (
        "3 normalized P&L rows across 2 portfolios are excluded from mapped "
        "dashboard totals because their Portfolio value has no matching config "
        "entry. They remain visible here for remediation. "
    )


def test_semantic_total_rows_are_bold_divided_across_the_full_row() -> None:
    frame = pd.DataFrame(
        {
            "label": ["Big Risk"],
            "risk type": ["IR"],
            "risk greek": ["Delta"],
            "underlying": ["USD-SOFR"],
            "risk": [1.0],
        }
    )
    open_rows = [
        row_key({"label": "Big Risk"}),
        row_key({"label": "Big Risk", "risk type": "IR"}),
        row_key(
            {
                "label": "Big Risk",
                "risk type": "IR",
                "risk greek": "Delta",
            }
        ),
    ]
    rows = build_tree_rows(
        frame,
        [],
        open_rows,
        [],
        groups=["label", "risk type", "risk greek", "underlying"],
        cell_builder=lambda _frame, _context: [html.Td("1")],
    )

    assert len(rows) == 4
    assert all("hierarchy-total-row" in str(row.className) for row in rows[:3])
    assert "hierarchy-total-row" not in str(rows[3].className)

    stylesheet = (
        Path(__file__).resolve().parents[1] / "assets" / "s01_style.css"
    ).read_text(encoding="utf-8")
    selector = stylesheet.split(".hierarchy-total-row > *", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "border-top: 1px dotted #111111" in selector
    assert "border-bottom: 1px dotted #111111" in selector
    assert "font-weight: 850" in selector
