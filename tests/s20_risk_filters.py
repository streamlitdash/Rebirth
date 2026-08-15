"""Risk-local Portfolio and include/exclude filter regressions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from types import SimpleNamespace

import pandas as pd
from dash import dcc, html

from core.s03_search import MARKET_RESULT_COLUMNS, SearchCatalog
from ui.s02_constants import (
    DEFAULT_VIEW_DIMENSION,
    DIMENSION_FILTER_IDS,
    FILTER_DIMENSION_FIELDS,
    VIEW_DIMENSION_FIELDS,
)
from ui.s03_aggregate import (
    apply_filters,
    dimension_title,
    prepare_risk_data,
    selected_dimension,
)
from ui.s04_components import build_aggregate_pl_table, build_layout
from ui.s07_events import (
    _RiskDataCache,
    _render_quick_search_pivot,
    _top_book_action_view_token,
    filter_unmapped_portfolios,
)
from ui.s09_factory import build_app


def _raw_risk_frame() -> pd.DataFrame:
    base = {
        "Source Type": "ir/delta",
        "Risk Type": "IR",
        "Risk Greek": "Delta",
        "Display Bucket": "Other",
        "Region": "Americas",
        "Group": "G10",
        "Reported Underlying": "USD-SOFR",
        "Underlying": "USD-SOFR",
        "Tenor Swap": "1Y",
        "Tenor Option": "N/A",
        "Split": "Risk",
        "Product": "XVA",
        "Activity": "1111",
        "SignoffGroup": "SOG-A",
        "Sub Category": "Rates",
        "Open": 3.0,
        "Current": 4.0,
        "Risk Threshold": 1_000.0,
        "dRisk Threshold": 1_000.0,
        "PL Threshold": 1_000.0,
    }
    return pd.DataFrame(
        [
            {
                **base,
                "Portfolio": "BOOK-A",
                "Category": "Core",
                "Risk": 10.0,
                "dRisk": 1.0,
                "PL": 4.0,
            },
            {
                **base,
                "Portfolio": "BOOK-B",
                "Category": "Hedge",
                "Risk": 20.0,
                "dRisk": 2.0,
                "PL": 6.0,
            },
        ]
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


def _snapshot() -> SimpleNamespace:
    risk_status = pd.DataFrame(
        [
            {
                "Source Type": "ir/delta",
                "Suggested Risk Date": pd.Timestamp("2026-08-13"),
                "Effective Risk Date": pd.Timestamp("2026-08-13"),
                "Age": 0,
                "Age Defaulted": False,
                "Force Risk": False,
            }
        ]
    )
    return SimpleNamespace(
        revision=1,
        refreshed_at=pd.Timestamp("2026-08-14 08:00", tz="UTC").to_pydatetime(),
        system_date=pd.Timestamp("2026-08-14"),
        market_date=pd.Timestamp("2026-08-14"),
        market_status="OFFICIAL",
        checker_date=pd.Timestamp("2026-08-13"),
        risk_dates={"ir/delta": pd.Timestamp("2026-08-13")},
        risk_status=risk_status,
        forced_dates={},
        forced_view_date=None,
        commodity_market_enabled=False,
        risk_checker_enabled=True,
        dashboard_frame=_raw_risk_frame(),
    )


def _warm_manager() -> SimpleNamespace:
    snapshot = _snapshot()
    return SimpleNamespace(
        snapshot=snapshot,
        stage_delays={},
        health=SimpleNamespace(
            revision=1,
            refreshed_at=snapshot.refreshed_at,
            last_attempt_at=snapshot.refreshed_at,
            active_error_count=0,
        ),
    )


def _callback_outputs(metadata: dict) -> list[object]:
    output = metadata["output"]
    return list(output) if isinstance(output, (list, tuple)) else [output]


def _callback_inputs_for_output(
    app, component_id: str, component_property: str
) -> set[tuple[object, str]]:
    metadata = next(
        item
        for item in app.callback_map.values()
        if any(
            output.component_id == component_id
            and output.component_property == component_property
            for output in _callback_outputs(item)
        )
    )
    return {(item["id"], item["property"]) for item in metadata["inputs"]}


def test_portfolio_is_a_ui_view_and_filter_without_changing_the_core_registry() -> None:
    assert [field.key for field in FILTER_DIMENSION_FIELDS] == [
        "portfolio",
        "activity",
        "signoffgroup",
        "category",
        "subcategory",
    ]
    assert [field.key for field in VIEW_DIMENSION_FIELDS] == [
        "portfolio",
        "product",
        "activity",
        "signoffgroup",
        "category",
        "subcategory",
    ]
    assert DIMENSION_FILTER_IDS["portfolio"] == "portfolio-filter"
    assert DEFAULT_VIEW_DIMENSION == "activity"
    assert selected_dimension("portfolio") == "portfolio"
    assert dimension_title("portfolio") == "Portfolio"


def test_prepare_retains_real_portfolio_and_filter_modes_use_position_grain() -> None:
    prepared = prepare_risk_data(_raw_risk_frame())

    assert prepared["portfolio"].tolist() == ["BOOK-A", "BOOK-B"]
    included = apply_filters(
        prepared,
        ["IR"],
        ["Risk"],
        {"portfolio": ["BOOK-A"], "activity": ["1111"]},
    )
    excluded = apply_filters(
        prepared,
        ["IR"],
        ["Risk"],
        {"portfolio": ["BOOK-A"], "category": ["Core"]},
        exclude_selected=True,
    )
    unrestricted = apply_filters(
        prepared,
        ["IR"],
        ["Risk"],
        {"portfolio": [], "category": None},
        exclude_selected=True,
    )

    assert included["portfolio"].tolist() == ["BOOK-A"]
    # Exclusion is AND across the per-dimension complements.
    assert excluded["portfolio"].tolist() == ["BOOK-B"]
    assert unrestricted["portfolio"].tolist() == ["BOOK-A", "BOOK-B"]


def test_filtered_cache_distinguishes_include_and_exclude_generations() -> None:
    prepared = prepare_risk_data(_raw_risk_frame())
    cache = _RiskDataCache(prepared, revision=7)
    selected = {"portfolio": ["BOOK-A"]}

    included = cache.filtered(None, "IR", None, ["Risk"], selected)
    excluded = cache.filtered(
        None,
        "IR",
        None,
        ["Risk"],
        selected,
        exclude_selected=True,
    )

    assert included["portfolio"].tolist() == ["BOOK-A"]
    assert excluded["portfolio"].tolist() == ["BOOK-B"]


def test_portfolio_is_rendered_as_a_filter_and_a_view_by_dimension() -> None:
    prepared = prepare_risk_data(_raw_risk_frame())
    layout = build_layout(prepared, _snapshot(), refresh_enabled=True)
    components = list(_walk(layout))

    portfolio_filter = next(
        item
        for item in components
        if isinstance(item, dcc.Dropdown) and item.id == "portfolio-filter"
    )
    exclude_mode = next(
        item
        for item in components
        if isinstance(item, dcc.Checklist) and item.id == "risk-filter-exclude-selected"
    )
    aggregate_dimension = next(
        item
        for item in components
        if isinstance(item, dcc.RadioItems) and item.id == "aggregate-pl-dimension"
    )
    table_dimension = next(
        item
        for item in components
        if isinstance(item, dcc.RadioItems) and item.id == "table-dimension"
    )

    assert [option["value"] for option in portfolio_filter.options] == [
        "BOOK-A",
        "BOOK-B",
    ]
    assert exclude_mode.options == [
        {"label": "Exclude selected values", "value": "exclude"}
    ]
    assert exclude_mode.value == []
    for selector in (aggregate_dimension, table_dimension):
        assert {option["value"] for option in selector.options} >= {"portfolio"}
        assert selector.value == "activity"


def test_warm_risk_layout_pre_renders_default_tables_and_filter_state() -> None:
    prepared = prepare_risk_data(_raw_risk_frame())
    layout = build_layout(prepared, _snapshot(), refresh_enabled=True)
    components = list(_walk(layout))

    risk_grid = next(
        item for item in components if getattr(item, "id", None) == "risk-grid"
    )
    aggregate_grid = next(
        item for item in components if getattr(item, "id", None) == "aggregate-pl-grid"
    )
    filter_values = next(
        item
        for item in components
        if getattr(item, "id", None) == "dimension-filter-values-store"
    )

    assert any(isinstance(item, html.Table) for item in _walk(risk_grid.children))
    assert any(isinstance(item, html.Table) for item in _walk(aggregate_grid.children))
    assert filter_values.data == [None] * len(FILTER_DIMENSION_FIELDS)


def test_aggregate_toggle_ids_match_the_registered_pattern_callback() -> None:
    prepared = prepare_risk_data(_raw_risk_frame())
    table = build_aggregate_pl_table(prepared, "activity", [])
    toggle_ids = [
        item.id
        for item in _walk(table)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == "aggregate-row-toggle"
    ]

    assert toggle_ids
    assert all(
        set(component_id) == {"type", "risk_type"} for component_id in toggle_ids
    )

    app = build_app(refresh_manager=_warm_manager())
    aggregate_inputs = _callback_inputs_for_output(
        app,
        "aggregate-pl-grid",
        "children",
    )
    assert (
        '{"risk_type":["ALL"],"type":"aggregate-row-toggle"}',
        "n_clicks",
    ) in aggregate_inputs


def test_aggregate_pl_can_group_by_portfolio() -> None:
    prepared = prepare_risk_data(_raw_risk_frame())
    component = build_aggregate_pl_table(prepared, "portfolio", [])
    header = next(item for item in _walk(component) if isinstance(item, html.Thead))
    labels = [str(item.children) for item in _walk(header) if isinstance(item, html.Th)]

    assert labels == ["Index", "BOOK-A", "BOOK-B", "Total"]


def test_every_applicable_risk_consumer_is_wired_to_portfolio_and_filter_mode() -> None:
    app = build_app(refresh_manager=_warm_manager())
    exclusion = ("risk-filter-exclude-selected", "value")
    portfolio = ("portfolio-filter", "value")

    for output_id in (
        "aggregate-pl-grid",
        "top-book-grid",
        "unmapped-books-grid",
        "quick-search-results",
    ):
        inputs = _callback_inputs_for_output(app, output_id, "children")
        assert exclusion in inputs
        assert portfolio in inputs

    explorer_inputs = _callback_inputs_for_output(app, "risk-grid", "children")
    assert exclusion in explorer_inputs
    assert ("dimension-filter-values-store", "data") in explorer_inputs
    sync_inputs = _callback_inputs_for_output(
        app, "dimension-filter-values-store", "data"
    )
    assert portfolio in sync_inputs


def test_unmapped_inventory_applies_only_its_meaningful_portfolio_dimension() -> None:
    frame = pd.DataFrame(
        {
            "Portfolio": ["BOOK-A", "BOOK-B"],
            "Activity": ["Unmapped", "Unmapped"],
        }
    )

    included = filter_unmapped_portfolios(frame, ["BOOK-A"])
    excluded = filter_unmapped_portfolios(
        frame,
        ["BOOK-A"],
        exclude_selected=True,
    )

    assert included["Portfolio"].tolist() == ["BOOK-A"]
    assert excluded["Portfolio"].tolist() == ["BOOK-B"]


def _quick_catalog() -> SearchCatalog:
    market = pd.DataFrame(
        [
            [
                "ir/delta",
                "IR",
                "Delta",
                "USD-SOFR",
                "1Y",
                "N/A",
                0,
                pd.NA,
                pd.Timestamp("2026-08-14"),
                3.0,
                4.0,
                1.0,
                "OFFICIAL",
                "Available",
            ]
        ],
        columns=list(MARKET_RESULT_COLUMNS),
    )
    risk = pd.DataFrame(
        [
            ["BOOK-A", 10.0, 1.0, 4.0],
            ["BOOK-B", 20.0, 2.0, 6.0],
        ],
        columns=["Portfolio", "Risk", "dRisk", "PL"],
    )
    for column, value in {
        "Source Type": "ir/delta",
        "Risk Type": "IR",
        "Risk Greek": "Delta",
        "Split": "Risk",
        "Reported Underlying": "USD-SOFR",
        "Underlying": "USD-SOFR",
        "Tenor Swap": "1Y",
        "Tenor Option": "N/A",
        "Tenor Swap Order": 0,
        "Tenor Option Order": pd.NA,
        "Activity": "1111",
    }.items():
        risk[column] = value
    return SearchCatalog(
        revision=3,
        risk_dates={"ir/delta": pd.Timestamp("2026-08-13")},
        market_date=pd.Timestamp("2026-08-14"),
        market_frame=market,
        risk_pivot_frame=risk,
    )


def test_quick_risk_filters_portfolio_in_both_modes_at_position_grain() -> None:
    catalog = _quick_catalog()
    kwargs = {
        "index_columns": ("Portfolio",),
        "risk_filters": {"Split": ["Risk"], "Portfolio": ["BOOK-A"]},
    }

    included = catalog.pivot_combined_hierarchy(
        "IR | Delta | USD-SOFR",
        **kwargs,
    ).frame
    excluded = catalog.pivot_combined_hierarchy(
        "IR | Delta | USD-SOFR",
        exclude_selected=True,
        **kwargs,
    ).frame
    prepared = prepare_risk_data(_raw_risk_frame())
    main_included = apply_filters(
        prepared,
        ["IR"],
        ["Risk"],
        {"portfolio": ["BOOK-A"]},
    )
    main_excluded = apply_filters(
        prepared,
        ["IR"],
        ["Risk"],
        {"portfolio": ["BOOK-A"]},
        exclude_selected=True,
    )

    assert included[["Portfolio", "Risk"]].values.tolist() == [["BOOK-A", 10.0]]
    assert excluded[["Portfolio", "Risk"]].values.tolist() == [["BOOK-B", 20.0]]
    assert main_included[["portfolio", "risk"]].values.tolist() == [["BOOK-A", 10.0]]
    assert main_excluded[["portfolio", "risk"]].values.tolist() == [["BOOK-B", 20.0]]


def test_quick_risk_helper_forwards_exclusion_and_action_tokens_bind_the_mode() -> None:
    class Manager:
        def __init__(self) -> None:
            self.exclude_values: list[bool] = []

        def pivot_combined_hierarchy(
            self,
            _identity: str,
            *,
            index_columns,
            leaf_limit: int,
            identity_mode: str,
            risk_filters,
            exclude_selected: bool,
        ) -> SimpleNamespace:
            assert leaf_limit > 0
            assert identity_mode == "reported"
            assert risk_filters == {"Portfolio": ["BOOK-A"]}
            self.exclude_values.append(exclude_selected)
            return SimpleNamespace(
                frame=pd.DataFrame(
                    [
                        {
                            "__Hierarchy Depth__": 1,
                            "Portfolio": "BOOK-B",
                            "Risk": 20.0,
                            "dRisk": 2.0,
                            "PL": 6.0,
                            "Open": pd.NA,
                            "Current": pd.NA,
                            "Move": pd.NA,
                        }
                    ]
                ),
                total=1,
                revision=9,
            )

    manager = Manager()
    rendered, index_update = _render_quick_search_pivot(
        manager,
        combine_udl="IR | Delta | USD-SOFR",
        index_columns=("Portfolio",),
        is_open=True,
        risk_filters={"Portfolio": ["BOOK-A"]},
        exclude_selected=True,
    )
    include_token = _top_book_action_view_token(
        9,
        dimension_filters={"portfolio": ["BOOK-A"]},
    )
    exclude_token = _top_book_action_view_token(
        9,
        dimension_filters={"portfolio": ["BOOK-A"]},
        exclude_selected=True,
    )

    assert isinstance(rendered, html.Div)
    assert index_update is not None
    assert manager.exclude_values == [True]
    assert include_token != exclude_token
    assert json.loads(exclude_token)["exclude_selected"] is True
