"""Risk-local Portfolio and include/exclude filter regressions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from types import SimpleNamespace

import pandas as pd
from dash import dcc, html

from core.s08_saved_views import SavedFilterView
from core.s03_search import MARKET_RESULT_COLUMNS, SearchCatalog
from ui import s07_events as events_module
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
from ui.s04_components import (
    RISK_SAVED_VIEW_CONTROLS,
    build_aggregate_pl_table,
    build_layout,
)
from ui.s07_events import (
    _RiskDataCache,
    _render_quick_search_pivot,
    _top_book_action_view_token,
    filter_unmapped_portfolios,
)
from ui.s09_factory import build_app
from ui.s11_saved_views import saved_view_apply_request


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
        "activity",
        "signoffgroup",
        "portfolio",
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


def test_include_and_exclude_modes_have_explicit_boolean_semantics() -> None:
    frame = pd.DataFrame(
        {
            "row": [
                "Credit / B",
                "Credit / D",
                "Credit / C",
                "Rates / B",
                "Rates / C",
            ],
            "risk type": ["IR"] * 5,
            "split": ["Risk"] * 5,
            "activity": ["Credit", "Credit", "Credit", "Rates", "Rates"],
            "portfolio": ["B", "D", "C", "B", "C"],
            "pl": [1.0] * 5,
        }
    )
    selections = {"activity": ["Credit"], "portfolio": ["B", "D"]}

    included = apply_filters(frame, None, None, selections)
    excluded = apply_filters(
        frame,
        None,
        None,
        selections,
        exclude_selected=True,
    )

    assert included["row"].tolist() == ["Credit / B", "Credit / D"]
    # Exclude mode is (NOT Credit) AND (NOT B or D), not merely the inverse
    # of the included intersection.
    assert excluded["row"].tolist() == ["Rates / C"]


def test_ir_family_tabs_keep_xgamma_sources_inside_delta_and_vega() -> None:
    prepared = prepare_risk_data(_raw_risk_frame())
    layout = build_layout(prepared, _snapshot(), refresh_enabled=True)
    tabs = next(
        item
        for item in _walk(layout)
        if isinstance(item, dcc.Tabs) and item.id == "ir-family-tabs"
    )

    assert [(tab.label, tab.value) for tab in tabs.children] == [
        ("Delta", "delta"),
        ("Basis", "basis"),
        ("Vega", "vega"),
    ]


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


def test_risk_promotion_is_recomputed_after_position_filters() -> None:
    """A globally promoted exposure can fall below threshold in one view."""

    raw = _raw_risk_frame()
    raw["Risk"] = [600.0, 600.0]
    raw["dRisk"] = [0.0, 0.0]
    raw["PL"] = [0.0, 0.0]
    cache = _RiskDataCache(prepare_risk_data(raw), revision=7)

    global_view = cache.filtered(None, "IR", "delta", ["Risk"], {})
    book_view = cache.filtered(
        None,
        "IR",
        "delta",
        ["Risk"],
        {"portfolio": ["BOOK-A"]},
    )

    assert global_view["risk"].sum() == 1_200.0
    assert global_view["display bucket"].eq("USD-SOFR").all()
    assert global_view["promotion reason"].eq("Big Risk").all()
    assert book_view["risk"].sum() == 600.0
    assert book_view["display bucket"].eq("Other").all()
    assert book_view["promotion reason"].eq("").all()


def test_risk_filter_owner_applies_pending_saved_view_without_losing_manual_edits(
    monkeypatch,
) -> None:
    app = build_app(refresh_manager=_warm_manager())
    metadata = next(
        item
        for item in app.callback_map.values()
        if any(
            output.component_id == DIMENSION_FILTER_IDS["activity"]
            and output.component_property == "value"
            for output in _callback_outputs(item)
        )
    )
    callback = metadata["callback"].__wrapped__
    assert (
        RISK_SAVED_VIEW_CONTROLS.applied_request_id,
        "data",
    ) in {(item["id"], item["property"]) for item in metadata["state"]}
    view = SavedFilterView(
        identifier="morning--0123456789ab",
        scope="risk",
        name="Morning",
        filters={
            "activity": ("1111",),
            "signoffgroup": ("SOG-A",),
            "portfolio": ("BOOK-A",),
            "category": ("Core",),
            "subcategory": ("Rates",),
        },
        exclude_selected=True,
    )
    request = saved_view_apply_request(
        view,
        base_filters={field.key: [] for field in FILTER_DIMENSION_FIELDS},
        base_exclude_selected=False,
    )
    monkeypatch.setattr(
        events_module,
        "ctx",
        SimpleNamespace(triggered_id=RISK_SAVED_VIEW_CONTROLS.apply_request_id),
    )

    result = callback(
        1,
        request,
        *([[]] * len(FILTER_DIMENSION_FIELDS)),
        [],
        None,
    )

    assert result[1::2][:5] == (
        ["1111"],
        ["SOG-A"],
        ["BOOK-A"],
        ["Core"],
        ["Rates"],
    )
    assert result[-1] == ["exclude"]

    # Dash may coalesce the request Store and a financial revision update, in
    # which case triggered_id is the revision. The pending request still owns
    # the unchanged base controls and must not be stranded forever.
    monkeypatch.setattr(
        events_module,
        "ctx",
        SimpleNamespace(triggered_id="data-revision-store"),
    )
    coalesced = callback(
        2,
        request,
        *([[]] * len(FILTER_DIMENSION_FIELDS)),
        [],
        None,
    )
    assert coalesced[1::2][:5] == result[1::2][:5]
    assert coalesced[-1] == ["exclude"]

    manual = [[] for _field in FILTER_DIMENSION_FIELDS]
    manual[2] = ["BOOK-B"]
    superseded = callback(3, request, *manual, [], None)
    assert superseded[5] == ["BOOK-B"]
    assert superseded[-1] == []

    acknowledged = callback(
        4,
        request,
        *([[]] * len(FILTER_DIMENSION_FIELDS)),
        [],
        request["request_id"],
    )
    assert acknowledged[1::2][:5] == ([], [], [], [], [])
    assert acknowledged[-1] == []


def test_risk_filter_values_and_mode_have_one_callback_owner() -> None:
    app = build_app(refresh_manager=_warm_manager())
    governed = {
        *((component_id, "value") for component_id in DIMENSION_FILTER_IDS.values()),
        ("risk-filter-exclude-selected", "value"),
    }
    owners = {identity: 0 for identity in governed}
    for metadata in app.callback_map.values():
        for output in _callback_outputs(metadata):
            identity = (str(output.component_id), output.component_property)
            if identity in owners:
                owners[identity] += 1

    assert set(owners.values()) == {1}


def test_quick_market_history_targets_are_mounted_and_have_single_owners() -> None:
    app = build_app(
        refresh_manager=_warm_manager(),
        market_history_loader=lambda _risk_type, _risk_greek, _underlying: (
            pd.DataFrame()
        ),
    )
    governed = {
        ("quick-market-history-cell", "options"),
        ("quick-market-history-cell", "value"),
        ("quick-market-history-cell", "disabled"),
        ("quick-market-history-chart", "children"),
        ("quick-market-history-status", "children"),
    }
    owners = {identity: 0 for identity in governed}
    for metadata in app.callback_map.values():
        for output in _callback_outputs(metadata):
            identity = (str(output.component_id), output.component_property)
            if identity in owners:
                owners[identity] += 1

    assert set(owners.values()) == {1}
    layout_ids = {
        str(getattr(component, "id", ""))
        for component in _walk(
            build_layout(
                prepare_risk_data(_raw_risk_frame()),
                _snapshot(),
                refresh_enabled=True,
            )
        )
    }
    assert {component_id for component_id, _property in governed} <= layout_ids

    history_inputs = _callback_inputs_for_output(
        app,
        "quick-market-history-chart",
        "children",
    )
    assert history_inputs == {
        ("quick-market-combine-udl", "value"),
        ("quick-market-history-cell", "value"),
        ("quick-market-history-summary", "n_clicks"),
        ("quick-market-history-period", "value"),
        ("quick-market-history-date-range", "start_date"),
        ("quick-market-history-date-range", "end_date"),
        ("quick-market-summary", "n_clicks"),
        ("data-revision-store", "data"),
    }


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
    filter_row = next(
        item
        for item in components
        if isinstance(item, html.Div)
        and {"controls", "filter-controls"}
        <= set(str(getattr(item, "className", "")).split())
    )
    saved_view_bar = next(
        item
        for item in components
        if isinstance(item, html.Details)
        and getattr(item, "id", None) == "risk-saved-view-bar"
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
        {
            "label": "Exclude rows matching any selected value",
            "value": "exclude",
        }
    ]
    assert exclude_mode.value == []
    filter_fields = filter_row.children[: len(FILTER_DIMENSION_FIELDS)]
    assert [control.children[0].children for control in filter_fields] == [
        "Activity",
        "Signoff Group",
        "Portfolio",
        "Category",
        "Sub Category",
    ]
    assert filter_row.children[-1].id == "risk-filter-exclude-selected"
    assert "filter-mode-control" in str(filter_row.children[-1].className).split()
    saved_view_notes = [
        item
        for item in _walk(saved_view_bar)
        if isinstance(item, html.Div)
        and "saved-view-filter-note" in set(str(getattr(item, "className", "")).split())
    ]
    assert len(saved_view_notes) == 1
    assert "Risk selections remain independent" in saved_view_notes[0].children
    assert filter_row in list(_walk(saved_view_bar))
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
    toggles = [
        item
        for item in _walk(table)
        if isinstance(getattr(item, "id", None), dict)
        and item.id.get("type") == "aggregate-row-toggle"
    ]
    toggle_ids = [item.id for item in toggles]

    assert toggle_ids
    assert all(
        set(component_id) == {"type", "risk_type"} for component_id in toggle_ids
    )
    assert all(toggle.children == "▸" for toggle in toggles)
    assert all(
        {"row-toggle", "aggregate-row-toggle"} <= set(str(toggle.className).split())
        for toggle in toggles
    )
    assert all(
        toggle.to_plotly_json()["props"]["aria-expanded"] == "false"
        for toggle in toggles
    )
    assert all(
        toggle.title == toggle.to_plotly_json()["props"]["aria-label"]
        and str(toggle.title).startswith("Expand ")
        for toggle in toggles
    )
    aggregate_table = next(
        item for item in _walk(table) if isinstance(item, html.Table)
    )
    risk_rows = [
        item
        for item in _walk(aggregate_table)
        if isinstance(item, html.Tr)
        and getattr(item, "className", None) == "aggregate-risk-row"
    ]
    assert aggregate_table.role == "treegrid"
    assert all(
        row.to_plotly_json()["props"]["aria-level"] == "1"
        and row.to_plotly_json()["props"]["aria-expanded"] == "false"
        for row in risk_rows
    )

    expanded_table = build_aggregate_pl_table(prepared, "activity", ["IR"])
    ir_toggle = next(
        item
        for item in _walk(expanded_table)
        if isinstance(getattr(item, "id", None), dict)
        and item.id == {"type": "aggregate-row-toggle", "risk_type": "IR"}
    )
    assert ir_toggle.title == "Collapse IR greeks"
    assert ir_toggle.to_plotly_json()["props"]["aria-label"] == ir_toggle.title
    greek_rows = [
        item
        for item in _walk(expanded_table)
        if isinstance(item, html.Tr)
        and getattr(item, "className", None) == "aggregate-greek-row"
    ]
    assert ir_toggle.children == "−"
    assert ir_toggle.to_plotly_json()["props"]["aria-expanded"] == "true"
    assert greek_rows
    assert all(
        row.to_plotly_json()["props"]["aria-level"] == "2"
        and "aria-expanded" not in row.to_plotly_json()["props"]
        for row in greek_rows
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
