"""PL disclosure laziness and application-factory boundary checks."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from dash import Dash, dcc, html, no_update

from core.s04_pl import (
    BOOK,
    HISTO_TYPE,
    HISTORY_FILE_COLUMNS,
    PREDICTED_TYPE,
)
from core.s05_storage import LocalCsvAdjustmentRepository
from feeds.s01_sources import build_production_refresh_manager
from ui import s08_plevents as pl_events
from ui.s03_aggregate import format_number, prepare_risk_data
from ui.s06_plview import (
    PL_AGGREGATE_TOGGLE_TYPE,
    PL_FILTER_FIELDS,
    PL_FILTER_IDS,
    PL_SAVED_VIEW_CONTROLS,
    build_pl_aggregate_table,
    build_pl_page,
    build_pl_send_sections,
)
from ui.s08_plevents import (
    PLSendConfig,
    _historical_pl_figure,
    history_range_bounds,
    history_selection_from_cell,
    register_pl_aggregate_callbacks,
    select_pl_history_series,
)
from ui.s09_factory import build_app
from ui.s11_saved_views import build_saved_filter_view_bar


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


def _history_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["2026-07-18", "IR", "Delta", "EUR", "XVA", "BOOK-A", 10.0],
            ["2026-07-19", "IR", "Delta", "EUR", "XVA", "BOOK-A", 12.0],
            ["2026-07-19", "FX", "Delta", "EUR/USD", "XVA", "BOOK-A", -3.0],
            ["2026-07-19", "IR", "Delta", "EUR", "XVA", "BOOK-B", 7.0],
        ],
        columns=["Market Date", *HISTORY_FILE_COLUMNS],
    )


def _config(tmp_path: Path) -> PLSendConfig:
    history_source = tmp_path / "histo"
    for market_date, daily in _history_frame().groupby("Market Date", sort=True):
        year, month, day = str(market_date).split("-")
        leaf = history_source / year / f"{month}-{day}"
        leaf.mkdir(parents=True)
        actual = daily[list(HISTORY_FILE_COLUMNS)]
        predicted = actual.copy()
        predicted["PL"] = predicted["PL"] * 0.9
        actual.to_csv(leaf / "histo.csv", index=False)
        predicted.to_csv(leaf / "predicted.csv", index=False)
    return PLSendConfig(
        mapping_source=tmp_path / "mapping.csv",
        adjustment_repository=LocalCsvAdjustmentRepository(tmp_path / "adjustments"),
        saved_directory=tmp_path / "saved",
        send_sog_pl=lambda _frame: None,
        send_portfolio_pl=lambda _frame: None,
        history_source=history_source,
    )


def _registered_pl_app(tmp_path: Path) -> tuple[Dash, SimpleNamespace]:
    snapshot = SimpleNamespace(revision=7, market_date=pd.Timestamp("2026-07-20"))
    manager = SimpleNamespace(pl_snapshot=snapshot)
    app = Dash(__name__)
    app.layout = html.Div(
        [
            dcc.Store(id="data-revision-store", data=7),
            dcc.Store(id="pl-adjustment-revision-store", data=0),
            *build_pl_send_sections(),
        ]
    )
    pl_events.register_pl_send_callbacks(app, manager, _config(tmp_path))
    return app, manager


def _callback(app: Dash, output_fragment: str):
    key = next(key for key in app.callback_map if output_fragment in key)
    return app.callback_map[key]["callback"].__wrapped__


def _native_page(app: Dash, pathname: str = "/"):
    """Materialize one Dash Pages route through its registered router."""
    routes_prefix = app.config.routes_pathname_prefix
    layout_path = f"{routes_prefix}_dash-layout"
    response = app.server.test_client().get(layout_path)
    assert response.status_code == 200

    route = _callback(app, "_pages_content.children")
    with app.server.test_request_context(layout_path):
        page, _metadata = route(app.get_relative_path(pathname), "")
    return page


def _string_ids(component: object) -> set[str]:
    return {
        component_id
        for item in _walk(component)
        if isinstance((component_id := getattr(item, "id", None)), str)
    }


def _effective_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Market Date": "2026-07-20",
                "Risk Type": "IR",
                "Risk Greek": "Delta",
                "Portfolio": "BOOK-B",
                "SignoffGroup": "SOG-B",
                "ConcertoField": "irdeltaeffect",
                "PL": 20.0,
                "Adjustment": False,
            },
            {
                "Market Date": "2026-07-20",
                "Risk Type": "FX",
                "Risk Greek": "Delta",
                "Portfolio": "BOOK-A",
                "SignoffGroup": "SOG-A",
                "ConcertoField": "fxdeltaeffect",
                "PL": -5.0,
                "Adjustment": True,
            },
        ]
    )


def test_closed_pl_sections_never_build_or_serialize_effective_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _manager = _registered_pl_app(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("closed PL disclosure performed row work")

    monkeypatch.setattr(pl_events, "_effective_rows", forbidden)
    monkeypatch.setattr(pl_events, "_effective_store", forbidden)
    monkeypatch.setattr(pl_events, "_display_records", forbidden)

    preview = _callback(app, "pl-send-preview-grid.data")
    sog = _callback(app, "pl-send-sog-effective-store.data")
    portfolio = _callback(app, "pl-send-portfolio-effective-store.data")

    assert preview(0, 7, [], 0) == (
        [],
        "Open P&L Preview to load its current rows.",
    )
    assert preview(2, 8, ["include"], 1)[0] == []
    for callback in (sog, portfolio):
        store, options, selected = callback(0, 7, [], 0, None)
        assert store == {}
        assert options is no_update
        assert selected is no_update
        store, options, selected = callback(2, 8, ["include"], 1, "stale")
        assert store == {}
        assert options is no_update
        assert selected is no_update


def test_open_pl_sections_load_on_odd_parity_and_initialize_filters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _manager = _registered_pl_app(tmp_path)
    effective = _effective_frame()
    calls: list[bool] = []

    def effective_rows(_snapshot, _config, *, include_adjustments: bool):
        calls.append(include_adjustments)
        return effective.copy(deep=True), pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(pl_events, "_effective_rows", effective_rows)
    preview = _callback(app, "pl-send-preview-grid.data")
    sog = _callback(app, "pl-send-sog-effective-store.data")
    portfolio = _callback(app, "pl-send-portfolio-effective-store.data")

    preview_rows, preview_status = preview(3, 7, ["include"], 0)
    assert len(preview_rows) == 2
    assert preview_status == "2 unique Portfolio + ConcertoField rows."

    sog_store, sog_options, selected_sog = sog(1, 7, [], 4, None)
    assert [option["value"] for option in sog_options] == ["SOG-A", "SOG-B"]
    assert selected_sog == "SOG-A"
    assert len(sog_store["rows"]) == 2
    assert sog_store["include_adjustments"] is False
    assert sog_store["editor_epoch"] == 4

    portfolio_store, portfolio_options, selected_portfolio = portfolio(
        1,
        7,
        ["include"],
        5,
        "BOOK-B",
    )
    assert [option["value"] for option in portfolio_options] == [
        "BOOK-A",
        "BOOK-B",
    ]
    assert selected_portfolio == "BOOK-B"
    assert len(portfolio_store["rows"]) == 2
    assert portfolio_store["include_adjustments"] is True
    assert portfolio_store["editor_epoch"] == 5
    assert calls == [True, False, True]


def test_pl_sections_are_independent_top_level_disclosures() -> None:
    sections = build_pl_send_sections()
    details = [section for section in sections if isinstance(section, html.Details)]

    assert getattr(sections[0], "id", None) == "pl-workflow-state"
    assert [
        next(item for item in _walk(detail) if isinstance(item, html.Summary)).children
        for detail in details
    ] == [
        "SOG P&L",
        "Portfolio P&L",
        "Write PL to S3",
        "P&L Preview",
        "Histo P&L",
    ]
    assert all(
        [item for item in _walk(detail) if isinstance(item, html.Details)] == [detail]
        for detail in details
    )
    assert "pl-workflow-summary" not in _string_ids(html.Div(sections))


def test_native_pl_page_owns_workflow_and_adjustment_state() -> None:
    page = build_pl_page()
    ids = _string_ids(page)

    assert getattr(page, "id", None) == "pnl-page-container"
    assert {
        "pnl-page",
        "pnl-aggregate-open-risk-types",
        "pnl-aggregate-pl-dimension",
        "pnl-aggregate-pl-grid",
        "pnl-filter-bar",
        "pnl-filter-exclude-selected",
        "pl-adjustment-revision-store",
        "pl-workflow-state",
        "pl-preview-summary",
        "pl-sog-summary",
        "pl-portfolio-summary",
        "pl-history-summary",
    } <= ids
    filters = [
        item.id
        for item in _walk(page)
        if isinstance(item, dcc.Dropdown) and item.id in set(PL_FILTER_IDS.values())
    ]
    assert filters == [PL_FILTER_IDS[field.key] for field in PL_FILTER_FIELDS]
    assert PL_SAVED_VIEW_CONTROLS.scope == "pnl"
    assert PL_SAVED_VIEW_CONTROLS.apply_request_id == "pnl-saved-view-apply-request"
    aggregate_heading = next(
        item
        for item in _walk(page)
        if isinstance(item, html.H2) and item.children == "Aggregate P&L"
    )
    assert not any(
        isinstance(item, html.Summary) and item.children == "Aggregate P&L"
        for item in _walk(page)
    )
    assert aggregate_heading is not None
    history_grid = next(
        item for item in _walk(page) if getattr(item, "id", None) == "pl-history-grid"
    )
    assert [column["id"] for column in history_grid.columns] == [
        "Risk Type",
        "Risk Greek",
        "Underlying",
        "Product",
        "Book",
        HISTO_TYPE,
        PREDICTED_TYPE,
    ]
    assert history_grid.page_action == "none"
    assert history_grid.sort_action == "none"
    assert history_grid.virtualization is True

    cold_page = build_pl_page(start_initial_load=True)
    assert "pnl-initial-load-trigger" in _string_ids(cold_page)


def test_pl_aggregate_table_restores_page_owned_collapsible_chevrons() -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)
    prepared = prepare_risk_data(manager.read_frame("dashboard_frame").frame)

    table = build_pl_aggregate_table(prepared, "activity", ["IR"])
    toggle_ids = [
        component_id
        for item in _walk(table)
        if isinstance((component_id := getattr(item, "id", None)), dict)
    ]

    assert toggle_ids
    assert all(
        component_id["type"] == PL_AGGREGATE_TOGGLE_TYPE
        and set(component_id) == {"type", "risk_type"}
        for component_id in toggle_ids
    )
    assert any(isinstance(item, html.Button) for item in _walk(table))
    assert (
        sum(
            getattr(item, "className", None) == "aggregate-greek-row"
            for item in _walk(table)
        )
        == prepared.loc[prepared["risk type"].eq("IR"), ["risk type", "risk greek"]]
        .drop_duplicates()
        .shape[0]
    )


def test_pl_aggregate_callback_renders_all_mapped_rows_independently() -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)
    app = build_app(refresh_manager=manager)
    page = _native_page(app, "/pnl")
    page_ids = _string_ids(page)
    selector = next(
        item
        for item in _walk(page)
        if isinstance(item, dcc.RadioItems) and item.id == "pnl-aggregate-pl-dimension"
    )

    assert {
        "pnl-aggregate-open-risk-types",
        "pnl-aggregate-pl-dimension",
        "pnl-aggregate-pl-grid",
        "pnl-unavailable",
    } <= page_ids
    assert selector.value == "activity"
    assert {option["value"] for option in selector.options} >= {
        "activity",
        "portfolio",
    }
    assert "aggregate-pl-grid" not in page_ids
    assert "aggregate-pl-dimension" not in page_ids
    initial_grid = next(
        item
        for item in _walk(page)
        if getattr(item, "id", None) == "pnl-aggregate-pl-grid"
    )
    assert any(
        getattr(item, "className", None) == "aggregate-risk-row"
        for item in _walk(initial_grid)
    )

    aggregate = _callback(app, "pnl-aggregate-pl-grid.children")
    open_state, table = aggregate(
        "activity",
        manager.health.revision,
        [],
        *([[]] * len(PL_FILTER_FIELDS)),
        [],
        [],
    )
    prepared = prepare_risk_data(manager.read_frame("dashboard_frame").frame)
    risk_rows = [
        item
        for item in _walk(table)
        if getattr(item, "className", None) == "aggregate-risk-row"
    ]
    total_row = next(
        item
        for item in _walk(table)
        if getattr(item, "className", None) == "aggregate-total-row"
    )

    assert open_state is no_update
    assert len(risk_rows) == prepared["risk type"].nunique()
    assert prepared["portfolio"].nunique() > 0
    assert total_row.children[-1].children.children == format_number(
        prepared["pl"].sum(min_count=1)
    )

    activity = sorted(prepared["activity"].astype(str).unique())[0]
    selected = [[] for _field in PL_FILTER_FIELDS]
    activity_index = [field.key for field in PL_FILTER_FIELDS].index("activity")
    selected[activity_index] = [activity]
    _included_open, included = aggregate(
        "activity",
        manager.health.revision,
        [],
        *selected,
        [],
        [],
    )
    included_total = next(
        item
        for item in _walk(included)
        if getattr(item, "className", None) == "aggregate-total-row"
    )
    assert included_total.children[-1].children.children == format_number(
        prepared.loc[prepared["activity"].eq(activity), "pl"].sum(min_count=1)
    )
    _excluded_open, excluded = aggregate(
        "activity",
        manager.health.revision,
        [],
        *selected,
        ["exclude"],
        [],
    )
    excluded_total = next(
        item
        for item in _walk(excluded)
        if getattr(item, "className", None) == "aggregate-total-row"
    )
    assert excluded_total.children[-1].children.children == format_number(
        prepared.loc[prepared["activity"].ne(activity), "pl"].sum(min_count=1)
    )

    metadata = next(
        value
        for value in app.callback_map.values()
        if "pnl-aggregate-pl-grid.children" in str(value["output"])
    )
    assert any(PL_AGGREGATE_TOGGLE_TYPE in item["id"] for item in metadata["inputs"])
    assert {(PL_FILTER_IDS[field.key], "value") for field in PL_FILTER_FIELDS} <= {
        (item["id"], item["property"]) for item in metadata["inputs"]
    }


def test_pl_filter_owner_applies_pending_saved_view_after_coalesced_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)
    prepared = prepare_risk_data(manager.read_frame("dashboard_frame").frame)
    app = Dash(__name__)
    app.layout = html.Div(
        [
            dcc.Store(id="data-revision-store", data=manager.health.revision),
            build_pl_page(
                initial_aggregate_frame=prepared,
                saved_view_bar=build_saved_filter_view_bar(PL_SAVED_VIEW_CONTROLS),
            ),
        ]
    )
    register_pl_aggregate_callbacks(
        app,
        manager,
        prepared_frame_loader=lambda: prepared,
        saved_view_controls=PL_SAVED_VIEW_CONTROLS,
    )
    owner_key = next(
        key for key in app.callback_map if f"{PL_FILTER_IDS['activity']}.options" in key
    )
    owner_metadata = app.callback_map[owner_key]
    owner = owner_metadata["callback"].__wrapped__
    assert (
        PL_SAVED_VIEW_CONTROLS.applied_request_id,
        "data",
    ) in {(item["id"], item["property"]) for item in owner_metadata["state"]}
    activities = sorted(prepared["activity"].astype(str).unique())
    saved_activity, manual_activity = activities[:2]
    request = {
        "request_id": "a" * 32,
        "view_id": "saved-view",
        "scope": "pnl",
        "filters": {
            field.key: ([saved_activity] if field.key == "activity" else [])
            for field in PL_FILTER_FIELDS
        },
        "exclude_selected": True,
        "base_filters": {field.key: [] for field in PL_FILTER_FIELDS},
        "base_exclude_selected": False,
    }
    blank = [[] for _field in PL_FILTER_FIELDS]
    monkeypatch.setattr(
        pl_events,
        "ctx",
        SimpleNamespace(triggered_id=PL_SAVED_VIEW_CONTROLS.apply_request_id),
    )
    applied = owner(manager.health.revision, request, *blank, [], None)
    assert applied[1] == [saved_activity]
    assert applied[-1] == ["exclude"]

    monkeypatch.setattr(
        pl_events,
        "ctx",
        SimpleNamespace(triggered_id="data-revision-store"),
    )
    coalesced = owner(manager.health.revision + 1, request, *blank, [], None)
    assert coalesced[1] == [saved_activity]
    assert coalesced[-1] == ["exclude"]

    manual = [[] for _field in PL_FILTER_FIELDS]
    manual[0] = [manual_activity]
    refreshed = owner(manager.health.revision + 2, request, *manual, [], None)
    assert refreshed[1] == [manual_activity]
    assert refreshed[-1] == []

    acknowledged = owner(
        manager.health.revision + 3,
        request,
        *blank,
        [],
        request["request_id"],
    )
    assert acknowledged[1::2][:5] == ([], [], [], [], [])
    assert acknowledged[-1] == []


def test_histo_data_is_lazy_fully_expanded_and_cell_selection_plots_daily_series(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _manager = _registered_pl_app(tmp_path)
    history_callback = _callback(app, "pl-history-grid.data")
    real_loader = pl_events.load_pl_history

    def forbidden(*_args, **_kwargs):
        raise AssertionError("closed Histo Data performed file work")

    monkeypatch.setattr(pl_events, "load_pl_history", forbidden)
    closed = history_callback(0)
    assert closed[0] == []
    assert closed[1] == "Open Histo P&L to load its validated hierarchy."

    monkeypatch.setattr(pl_events, "load_pl_history", real_loader)
    rows, status, minimum, maximum = history_callback(1)
    assert len(rows) == 11
    assert "fully expanded hierarchy rows" in status
    assert (minimum, maximum) == (
        "2026-07-18",
        "2026-07-19",
    )
    assert rows[0]["Risk Type"] == "IR"
    assert rows[0][HISTO_TYPE] == 19.0
    assert rows[0][PREDICTED_TYPE] == pytest.approx(17.1)
    assert {row[BOOK] for row in rows if row[BOOK]} == {"BOOK-A", "BOOK-B"}

    selection = history_selection_from_cell(
        {"row": 0, "column_id": HISTO_TYPE},
        rows,
    )
    assert selection == {"history_type": HISTO_TYPE, "path": ["IR"]}
    history = real_loader(tmp_path / "histo")
    series = select_pl_history_series(history, selection)
    assert series[["Market Date", "PL"]].values.tolist() == [
        ["2026-07-18", 10.0],
        ["2026-07-19", 19.0],
    ]
    assert history_range_bounds(series, "1w") == (
        "2026-07-18",
        "2026-07-19",
    )
    assert history_range_bounds(series, "mtd") == (
        "2026-07-18",
        "2026-07-19",
    )
    assert history_range_bounds(series, "ytd") == (
        "2026-07-18",
        "2026-07-19",
    )
    assert history_range_bounds(series, "all") == (
        "2026-07-18",
        "2026-07-19",
    )
    assert history_range_bounds(
        series,
        "custom",
        start_date="2026-07-19",
        end_date="2026-07-18",
    ) == ("2026-07-18", "2026-07-19")
    assert history_range_bounds(
        series,
        "custom",
        start_date="2026-08-01",
        end_date="2026-08-02",
    ) == ("2026-07-19", "2026-07-19")
    assert history_range_bounds(
        series,
        "custom",
        start_date="2026-01-01",
        end_date="2026-01-02",
    ) == ("2026-07-18", "2026-07-18")
    figure = _historical_pl_figure(series, selection)
    assert len(figure.data) == 1
    assert figure.data[0].name == HISTO_TYPE
    assert list(figure.data[0].y) == [10.0, 19.0]

    # The disclosure owns disk refresh. Subsequent cell/range interactions
    # reuse that validated frame and synchronize the visible picker bounds.
    chart_callback = _callback(app, "pl-history-chart.figure")
    monkeypatch.setattr(pl_events, "load_pl_history", forbidden)
    monkeypatch.setattr(
        pl_events,
        "ctx",
        SimpleNamespace(triggered_id="pl-history-range-1w"),
    )
    chart_result = chart_callback(
        rows,
        {"row": 0, "column_id": HISTO_TYPE},
        1,
        0,
        0,
        0,
        minimum,
        maximum,
        {"preset": "all", "start_date": minimum, "end_date": maximum},
        {},
    )
    assert chart_result[1] == {
        "preset": "1w",
        "start_date": minimum,
        "end_date": maximum,
    }
    assert chart_result[-2:] == (minimum, maximum)
    assert "is-active" in chart_result[4]


def test_histo_hierarchy_keeps_identities_absent_from_the_latest_day() -> None:
    history = pd.DataFrame(
        [
            ["2026-08-14", HISTO_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 10.0],
            ["2026-08-14", PREDICTED_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK-A", 9.0],
            ["2026-08-15", HISTO_TYPE, "FX", "Delta", "EUR/USD", "XVA", "BOOK-B", 3.0],
            [
                "2026-08-15",
                PREDICTED_TYPE,
                "FX",
                "Delta",
                "EUR/USD",
                "XVA",
                "BOOK-B",
                2.0,
            ],
        ],
        columns=["Market Date", "P&L Type", *HISTORY_FILE_COLUMNS],
    )

    rows = pl_events.build_pl_history_hierarchy(history)
    ir_index = next(
        index
        for index, row in enumerate(rows)
        if row["Risk Type"] == "IR" and not row["Risk Greek"]
    )
    fx_row = next(
        row for row in rows if row["Risk Type"] == "FX" and not row["Risk Greek"]
    )

    assert rows[ir_index][HISTO_TYPE] is None
    assert rows[ir_index][PREDICTED_TYPE] is None
    assert fx_row[HISTO_TYPE] == 3.0
    selection = history_selection_from_cell(
        {"row": ir_index, "column_id": HISTO_TYPE},
        rows,
    )
    series = select_pl_history_series(history, selection)
    assert series[["Market Date", "PL"]].values.tolist() == [["2026-08-14", 10.0]]


def test_manager_app_without_pl_config_omits_inert_workflow(tmp_path: Path) -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)

    without_pl = build_app(refresh_manager=manager)
    without_page = _native_page(without_pl)
    without_ids = _string_ids(without_page)
    assert "pl-preview-summary" not in without_ids
    assert "pl-sog-summary" not in without_ids
    assert "pl-portfolio-summary" not in without_ids
    assert "pl-history-summary" not in without_ids
    assert "pl-adjustment-revision-store" not in without_ids
    assert not any(
        "pl-send-preview-grid.data" in key for key in without_pl.callback_map
    )
    without_pnl_page = _native_page(without_pl, "/pnl")
    without_pnl_ids = _string_ids(without_pnl_page)
    assert {
        "pnl-aggregate-pl-dimension",
        "pnl-aggregate-pl-grid",
        "pnl-unavailable",
    } <= without_pnl_ids
    assert any(
        "pnl-aggregate-pl-grid.children" in key for key in without_pl.callback_map
    )

    config = PLSendConfig(
        mapping_source=Path("data/s08_concerto.csv"),
        adjustment_repository=LocalCsvAdjustmentRepository(tmp_path / "adjustments"),
        saved_directory=tmp_path / "saved",
        send_sog_pl=lambda _frame: None,
        send_portfolio_pl=lambda _frame: None,
    )
    with_pl = build_app(refresh_manager=manager, pl_send_config=config)
    with_risk_page = _native_page(with_pl)
    with_risk_ids = _string_ids(with_risk_page)
    assert "pl-preview-summary" not in with_risk_ids
    assert "pl-adjustment-revision-store" not in with_risk_ids

    with_page = _native_page(with_pl, "/pnl")
    with_ids = _string_ids(with_page)
    assert {
        "pl-preview-summary",
        "pl-sog-summary",
        "pl-portfolio-summary",
        "pl-history-summary",
        "pl-adjustment-revision-store",
    } <= with_ids
    assert "pl-workflow-summary" not in with_ids
    assert any("pl-send-preview-grid.data" in key for key in with_pl.callback_map)


def test_cold_native_pnl_is_safe_before_commit_and_recovers_at_revision_one(
    tmp_path: Path,
) -> None:
    manager = build_production_refresh_manager()
    config = PLSendConfig(
        mapping_source=Path("data/s08_concerto.csv"),
        adjustment_repository=LocalCsvAdjustmentRepository(tmp_path / "adjustments"),
        saved_directory=tmp_path / "saved",
        send_sog_pl=lambda _frame: None,
        send_portfolio_pl=lambda _frame: None,
    )
    app = build_app(refresh_manager=manager, pl_send_config=config)
    page = _native_page(app, "/pnl")

    assert "pnl-initial-load-trigger" in _string_ids(page)
    assert manager.health.revision == 0
    aggregate = _callback(app, "pnl-aggregate-pl-grid.children")
    _open_state, aggregate_view = aggregate(
        "activity",
        0,
        [],
        *([[]] * len(PL_FILTER_FIELDS)),
        [],
        [],
    )
    assert "still loading" in str(aggregate_view.children)
    preview = _callback(app, "pl-send-preview-grid.data")
    rows, status = preview(1, 0, [], 0)
    assert rows == []
    assert status == "P&L data is still loading. This preview will update shortly."

    sog = _callback(app, "pl-send-sog-effective-store.data")
    store, options, selected = sog(1, 0, [], 0, None)
    assert (store, options, selected) == ({}, [], None)

    manager.refresh(force_risk=True, force_pl=True)
    _open_state, aggregate_view = aggregate(
        "activity",
        manager.health.revision,
        [],
        *([[]] * len(PL_FILTER_FIELDS)),
        [],
        [],
    )
    assert any(
        getattr(item, "className", None) == "aggregate-risk-row"
        for item in _walk(aggregate_view)
    )
    rows, status = preview(1, manager.health.revision, [], 0)
    assert rows
    assert "unique Portfolio + ConcertoField rows" in status


def test_static_app_rejects_inert_pl_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PL send configuration requires"):
        build_app(data=pd.DataFrame(), pl_send_config=_config(tmp_path))
