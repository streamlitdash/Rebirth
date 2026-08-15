"""Contracts for dated Stock comparison, local filters, and lazy callbacks."""

from __future__ import annotations

from collections.abc import Iterable
from threading import Event, Thread

import pandas as pd
import pytest
from dash import dash_table, no_update
from flask import Flask

from adapters.s05_stock import (
    GetStock,
    STOCK_COLUMNS,
    build_stock_adapter,
    get_stock,
    validate_stock_frame,
)
from core.s01_schema import PORTFOLIO_MAPPED_COLUMN, UNMAPPED_VALUE
from core.s08_stock import (
    CURRENT_MARKET_VALUE_COLUMN,
    MAPPED_STOCK_COMPARISON_COLUMNS,
    MARKET_VALUE_CHANGE_COLUMN,
    PRIOR_QUANTITY_COLUMN,
    QUANTITY_CHANGE_COLUMN,
    STOCK_CHANGE_COLUMN,
    STOCK_IDENTITY_COLUMNS,
    compare_stock_snapshots,
    filter_stock_comparison,
    map_stock_comparison_portfolios,
    map_stock_portfolios,
)
from feeds.s01_sources import build_production_refresh_manager
from pages import PAGE_SERVICES_CONFIG_KEY
from pages.stock import layout as stock_page_layout
from ui.s02_constants import DIMENSION_FILTER_IDS, FILTER_DIMENSION_FIELDS
from ui.s07_events import STARTUP_COORDINATOR_CONFIG_KEY
from ui.s09_factory import build_app
from ui.s10_stock import (
    STOCK_FILTER_FIELDS,
    STOCK_FILTER_IDS,
    build_stock_page,
    build_stock_page_from_sources,
    build_stock_page_shell,
    default_stock_dates,
    normalize_stock_date_pair,
)


def _stock(rows: list[list[object]] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        rows
        if rows is not None
        else [
            ["CRDS-1", "CPTY-A", "BOOK_A", "EURUSD", "USD", 100.0, 25.5],
            ["CRDS-2", "CPTY-B", "BOOK_UNKNOWN", "CDX", "USD", -50.0, -12.0],
        ],
        columns=list(STOCK_COLUMNS),
    )


def _config(rows: list[list[object]] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        rows
        if rows is not None
        else [
            ["BOOK_A", "XVA", "Macro", "SOG-A", "Core", "Rates"],
            ["BOOK_B", "Hedges", "Hedge", "SOG-B", "Hedge", "Credit"],
            ["BOOK_C", "XVA", "Macro", "SOG-C", "Other", "FX"],
        ],
        columns=[
            "Portfolio",
            "Product",
            "Activity",
            "SignoffGroup",
            "Category",
            "Sub Category",
        ],
    )


def _comparison_legs() -> tuple[pd.DataFrame, pd.DataFrame]:
    current = _stock(
        [
            ["CRDS-1", "CPTY-A", "BOOK_A", "EURUSD", "USD", 110.0, 30.0],
            ["CRDS-2", "CPTY-B", "BOOK_B", "CDX", "USD", 50.0, 12.0],
            ["CRDS-3", "CPTY-C", "BOOK_C", "GILT", "GBP", 20.0, 8.0],
        ]
    )
    prior = _stock(
        [
            ["CRDS-1", "CPTY-A", "BOOK_A", "EURUSD", "USD", 100.0, 25.0],
            ["CRDS-2", "CPTY-B", "BOOK_B", "CDX", "USD", 50.0, 12.0],
            ["CRDS-4", "CPTY-D", "BOOK_UNKNOWN", "UST", "USD", 7.0, 4.0],
        ]
    )
    return current, prior


def _walk(component: object) -> Iterable[object]:
    yield component
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    elif children is not None:
        yield from _walk(children)


def _callback_for_input(app, component_id: str):
    return next(
        metadata["callback"].__wrapped__
        for metadata in app.callback_map.values()
        if any(item["id"] == component_id for item in metadata["inputs"])
    )


def _callback_outputs(metadata: dict) -> list[object]:
    output = metadata["output"]
    return list(output) if isinstance(output, (list, tuple)) else [output]


def test_stock_adapter_normalizes_dates_and_returns_a_defensive_copy() -> None:
    calls: list[pd.Timestamp] = []
    source_frame = _stock()

    def source(stock_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(stock_date)
        return source_frame

    result = build_stock_adapter(stock=source).get_stock("2026-08-15 13:45")
    result.loc[0, "CPTY"] = "changed"

    assert calls == [pd.Timestamp("2026-08-15")]
    assert tuple(result.columns) == STOCK_COLUMNS
    assert source_frame.loc[0, "CPTY"] == "CPTY-A"


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (_stock()[list(reversed(STOCK_COLUMNS))], "columns must be exactly"),
        (
            _stock([["CRDS-1", "", "BOOK_A", "EURUSD", "USD", 1.0, 2.0]]),
            "CPTY.*nonblank text",
        ),
        (
            _stock([["CRDS-1", "CPTY-A", "BOOK_A", "EURUSD", "USD", True, 2.0]]),
            "Quantity.*finite numbers",
        ),
    ],
)
def test_stock_adapter_rejects_schema_and_value_contract_failures(
    frame: pd.DataFrame,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_stock_frame(frame)


def test_checked_in_getstock_is_fake_validated_and_varies_by_date() -> None:
    prior = get_stock("2026-08-14")
    current = GetStock("2026-08-15")

    assert tuple(current.columns) == STOCK_COLUMNS
    assert len(current) == 3
    assert current["CRDS"].str.startswith("FAKE_REPLACE_ME").all()
    assert current[list(STOCK_IDENTITY_COLUMNS)].equals(
        prior[list(STOCK_IDENTITY_COLUMNS)]
    )
    assert not current[["Quantity", "Market Value"]].equals(
        prior[["Quantity", "Market Value"]]
    )


def test_stock_mapping_remains_left_many_to_one_and_preserves_unmapped() -> None:
    mapped = map_stock_portfolios(_stock(), _config())

    assert mapped["CRDS"].tolist() == ["CRDS-1", "CRDS-2"]
    assert mapped[PORTFOLIO_MAPPED_COLUMN].tolist() == [True, False]
    assert mapped.loc[0, "SignoffGroup"] == "SOG-A"
    assert mapped.loc[1, "SignoffGroup"] == UNMAPPED_VALUE


def test_stock_comparison_is_full_outer_with_visible_deltas_and_status() -> None:
    current, prior = _comparison_legs()
    compared = compare_stock_snapshots(current, prior).set_index("CRDS")

    assert compared.index.tolist() == ["CRDS-1", "CRDS-2", "CRDS-3", "CRDS-4"]
    assert compared[STOCK_CHANGE_COLUMN].to_dict() == {
        "CRDS-1": "Changed",
        "CRDS-2": "Unchanged",
        "CRDS-3": "Added",
        "CRDS-4": "Removed",
    }
    assert compared.loc["CRDS-1", QUANTITY_CHANGE_COLUMN] == 10.0
    assert compared.loc["CRDS-1", MARKET_VALUE_CHANGE_COLUMN] == 5.0
    assert pd.isna(compared.loc["CRDS-3", PRIOR_QUANTITY_COLUMN])
    assert compared.loc["CRDS-3", QUANTITY_CHANGE_COLUMN] == 20.0
    assert pd.isna(compared.loc["CRDS-4", CURRENT_MARKET_VALUE_COLUMN])
    assert compared.loc["CRDS-4", MARKET_VALUE_CHANGE_COLUMN] == -4.0


def test_stock_comparison_rejects_ambiguous_duplicate_identity() -> None:
    current, prior = _comparison_legs()
    duplicate = pd.concat([current, current.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="duplicate Stock identities"):
        compare_stock_snapshots(duplicate, prior)


def test_stock_comparison_mapping_and_filters_are_or_within_and_across() -> None:
    current, prior = _comparison_legs()
    mapped = map_stock_comparison_portfolios(current, prior, _config())

    assert tuple(mapped.columns) == MAPPED_STOCK_COMPARISON_COLUMNS
    assert mapped.set_index("CRDS").loc["CRDS-4", "Activity"] == UNMAPPED_VALUE
    filtered = filter_stock_comparison(
        mapped,
        {
            "portfolio": ["BOOK_A", "BOOK_B"],
            "activity": ["Macro", "Hedge"],
            "category": ["Core"],
            "signoffgroup": [],
            "subcategory": None,
        },
    )
    assert filtered["CRDS"].tolist() == ["CRDS-1"]
    excluded = filter_stock_comparison(
        mapped,
        {"portfolio": ["BOOK_A", "BOOK_UNKNOWN"]},
        exclude_selected=True,
    )
    assert excluded["CRDS"].tolist() == ["CRDS-2", "CRDS-3"]
    with pytest.raises(ValueError, match="Unknown Stock"):
        filter_stock_comparison(mapped, {"risk-only-filter": ["x"]})


def test_stock_filter_ids_and_store_are_independent_from_risk() -> None:
    assert [field.key for field in STOCK_FILTER_FIELDS] == [
        field.key for field in FILTER_DIMENSION_FIELDS
    ]
    assert set(STOCK_FILTER_IDS.values()).isdisjoint(DIMENSION_FILTER_IDS.values())
    shell = build_stock_page_shell(
        current_date="2026-08-14",
        prior_date="2026-08-13",
    )
    ids = {getattr(component, "id", None) for component in _walk(shell)}
    assert set(STOCK_FILTER_IDS.values()) <= ids
    assert "stock-dimension-filter-store" in ids
    assert "stock-filter-exclude-selected" in ids
    assert "dimension-filter-store" not in ids
    assert not (set(DIMENSION_FILTER_IDS.values()) & ids)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("2026-08-14", ("2026-08-13", "2026-08-12")),  # Friday
        ("2026-08-17", ("2026-08-14", "2026-08-13")),  # Monday
        ("2026-08-15", ("2026-08-14", "2026-08-13")),  # Saturday
        ("2026-08-16", ("2026-08-14", "2026-08-13")),  # Sunday
    ],
)
def test_stock_default_dates_use_business_day_offsets(
    reference: str,
    expected: tuple[str, str],
) -> None:
    current, prior = default_stock_dates(reference)
    assert (current.date().isoformat(), prior.date().isoformat()) == expected


@pytest.mark.parametrize(
    ("current", "prior"),
    [("2026-08-14", "2026-08-14"), ("2026-08-14", "2026-08-15")],
)
def test_stock_date_pair_requires_prior_before_current(
    current: str, prior: str
) -> None:
    with pytest.raises(ValueError, match="must be earlier"):
        normalize_stock_date_pair(current, prior)


def test_stock_page_exposes_comparison_table_and_filtered_counts() -> None:
    current, prior = _comparison_legs()
    page = build_stock_page(
        current,
        prior,
        _config(),
        current_date="2026-08-14",
        prior_date="2026-08-13",
        selected_filters={"activity": ["Macro"]},
    )
    components = list(_walk(page))
    ids = {getattr(component, "id", None) for component in components}
    table = next(
        component
        for component in components
        if isinstance(component, dash_table.DataTable)
    )

    assert {
        "stock-comparison-view",
        "stock-table",
        "stock-row-count",
        "stock-mapped-count",
        "stock-unmapped-count",
    } <= ids
    assert [column["id"] for column in table.columns] == list(
        MAPPED_STOCK_COMPARISON_COLUMNS
    )
    assert table.filter_action == "native"
    assert table.sort_action == "native"
    assert {row["CRDS"] for row in table.data} == {"CRDS-1", "CRDS-3"}
    row_count = next(
        item for item in components if getattr(item, "id", None) == "stock-row-count"
    )
    assert "Rows: 2 of 4" in row_count.children


def test_stock_page_source_boundary_receives_both_dates_and_current_mapping_date() -> (
    None
):
    calls: list[tuple[str, pd.Timestamp]] = []

    def stock_source(stock_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(("stock", stock_date))
        return _stock()

    def config_source(portfolio_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(("config", portfolio_date))
        return _config()

    page = build_stock_page_from_sources(
        stock_source=stock_source,
        portfolio_config_source=config_source,
        current_date="2026-08-14 12:00",
        prior_date="2026-08-13",
    )

    assert page.id == "stock-comparison-view"
    assert calls == [
        ("stock", pd.Timestamp("2026-08-14")),
        ("stock", pd.Timestamp("2026-08-13")),
        ("config", pd.Timestamp("2026-08-14")),
    ]


def test_native_stock_page_resolves_the_active_flask_service() -> None:
    first = Flask("first-stock-app")
    second = Flask("second-stock-app")
    first.server_name = "first.test"
    second.server_name = "second.test"
    first.config[PAGE_SERVICES_CONFIG_KEY] = {"stock_page_builder": lambda: "first"}
    second.config[PAGE_SERVICES_CONFIG_KEY] = {"stock_page_builder": lambda: "second"}

    with first.app_context():
        assert stock_page_layout() == "first"
    with second.app_context():
        assert stock_page_layout() == "second"


def test_factory_is_lazy_and_loads_default_two_business_day_snapshots() -> None:
    calls: list[tuple[str, pd.Timestamp]] = []

    def stock_source(stock_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(("stock", stock_date))
        return _stock()

    def config_source(portfolio_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(("config", portfolio_date))
        return _config()

    manager = build_production_refresh_manager()
    app = build_app(
        refresh_manager=manager,
        stock_source=stock_source,
        stock_portfolio_source=config_source,
    )
    base_layout = app.layout() if callable(app.layout) else app.layout
    assert base_layout is not None
    assert calls == []

    with app.server.test_request_context("/_dash-layout"):
        page = stock_page_layout()
    components = list(_walk(page))
    ids = {getattr(component, "id", None) for component in components}
    assert {
        "stock-page-content",
        "stock-loaded-revision",
        "stock-loaded-dates",
        "stock-load-trigger",
        "stock-request-scope",
        "stock-current-date",
        "stock-prior-date",
        "stock-compare-button",
    } <= ids
    assert calls == []

    current_picker = next(
        item for item in components if getattr(item, "id", None) == "stock-current-date"
    )
    prior_picker = next(
        item for item in components if getattr(item, "id", None) == "stock-prior-date"
    )
    callback = _callback_for_input(app, "stock-load-trigger")
    result = callback(
        1,
        "0",
        0,
        -1,
        None,
        current_picker.date,
        prior_picker.date,
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "lazy-defaults",
    )
    children, loaded_revision, token, timer_disabled = result[:4]

    assert children
    assert loaded_revision == 0
    assert timer_disabled is True
    assert token["current_date"] == current_picker.date
    assert token["prior_date"] == prior_picker.date
    assert calls == [
        ("stock", pd.Timestamp(current_picker.date)),
        ("stock", pd.Timestamp(prior_picker.date)),
        ("config", pd.Timestamp(current_picker.date)),
    ]
    coordinator = app.server.config[STARTUP_COORDINATOR_CONFIG_KEY]
    assert coordinator.status().phase == "idle"
    assert manager.health.revision == 0


def test_factory_warm_shell_defaults_from_committed_market_date() -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)
    app = build_app(
        refresh_manager=manager,
        stock_source=lambda _date: _stock(),
        stock_portfolio_source=lambda _date: _config(),
    )
    with app.server.test_request_context("/stock"):
        page = stock_page_layout()
    components = list(_walk(page))
    current_picker = next(
        item for item in components if getattr(item, "id", None) == "stock-current-date"
    )
    prior_picker = next(
        item for item in components if getattr(item, "id", None) == "stock-prior-date"
    )
    expected_current, expected_prior = default_stock_dates(manager.snapshot.market_date)
    assert current_picker.date == expected_current.date().isoformat()
    assert prior_picker.date == expected_prior.date().isoformat()


def test_compare_callback_uses_selected_dates_without_reloading_same_key() -> None:
    calls: list[pd.Timestamp] = []

    def stock_source(stock_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(stock_date)
        return _stock()

    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=stock_source,
        stock_portfolio_source=lambda date: _config(),
    )
    callback = _callback_for_input(app, "stock-compare-button")
    result = callback(
        0,
        "0",
        1,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "selected-dates",
    )
    loaded_revision, token = result[1:3]
    assert calls == [pd.Timestamp("2026-08-14"), pd.Timestamp("2026-08-13")]

    unchanged = callback(
        0,
        "0",
        2,
        loaded_revision,
        token,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "selected-dates",
    )
    assert unchanged[0] is no_update
    assert unchanged[3] is True
    assert calls == [pd.Timestamp("2026-08-14"), pd.Timestamp("2026-08-13")]


def test_newer_date_intent_supersedes_a_blocked_stock_load() -> None:
    old_started = Event()
    release_old = Event()
    calls: list[pd.Timestamp] = []

    def stock_source(stock_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(stock_date)
        if stock_date == pd.Timestamp("2026-08-14") and not old_started.is_set():
            old_started.set()
            if not release_old.wait(timeout=3):
                raise TimeoutError("test did not release the blocked Stock load")
        return _stock()

    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=stock_source,
        stock_portfolio_source=lambda _date: _config(),
    )
    callback = _callback_for_input(app, "stock-load-trigger")
    old_result: list[tuple] = []
    old_errors: list[BaseException] = []

    def run_old_request() -> None:
        try:
            old_result.append(
                callback(
                    1,
                    "0",
                    0,
                    -1,
                    None,
                    "2026-08-14",
                    "2026-08-13",
                    [],
                    *([[]] * len(STOCK_FILTER_FIELDS)),
                    "one-mounted-stock-page",
                )
            )
        except BaseException as error:  # pragma: no cover - assertion handoff
            old_errors.append(error)

    old_thread = Thread(target=run_old_request)
    old_thread.start()
    assert old_started.wait(timeout=3)

    newer_busy = callback(
        1,
        "0",
        1,
        -1,
        None,
        "2026-08-12",
        "2026-08-11",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "one-mounted-stock-page",
    )
    assert newer_busy[:3] == (no_update, no_update, no_update)
    assert newer_busy[3] is False

    release_old.set()
    old_thread.join(timeout=3)
    assert not old_thread.is_alive()
    assert old_errors == []
    assert len(old_result) == 1
    assert all(value is no_update for value in old_result[0])

    newer_loaded = callback(
        2,
        "0",
        1,
        -1,
        None,
        "2026-08-12",
        "2026-08-11",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "one-mounted-stock-page",
    )
    assert newer_loaded[0]
    assert newer_loaded[2]["current_date"] == "2026-08-12"
    assert newer_loaded[2]["prior_date"] == "2026-08-11"
    assert newer_loaded[3] is True
    assert calls == [
        pd.Timestamp("2026-08-14"),
        pd.Timestamp("2026-08-13"),
        pd.Timestamp("2026-08-12"),
        pd.Timestamp("2026-08-11"),
    ]


def test_stock_enabled_callback_map_has_single_output_owners() -> None:
    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=lambda _date: _stock(),
        stock_portfolio_source=lambda _date: _config(),
    )
    owners: dict[tuple[str, str], list[str]] = {}

    for callback_key, metadata in app.callback_map.items():
        for output in _callback_outputs(metadata):
            identity = (str(output.component_id), output.component_property)
            owners.setdefault(identity, []).append(callback_key)
            assert output.allow_duplicate is False

    duplicates = {
        identity: callbacks
        for identity, callbacks in owners.items()
        if len(callbacks) != 1
    }
    assert duplicates == {}
    assert len(owners[("stock-page-content", "children")]) == 1
    assert len(owners[("stock-loaded-dates", "data")]) == 1
    assert len(owners[("stock-load-trigger", "disabled")]) == 1


def test_stock_filter_callback_uses_cache_only_and_updates_visible_counts() -> None:
    calls = 0

    def stock_source(_date: pd.Timestamp) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        current, _prior = _comparison_legs()
        return current

    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=stock_source,
        stock_portfolio_source=lambda _date: _config(),
    )
    load = _callback_for_input(app, "stock-load-trigger")
    loaded = load(
        1,
        "0",
        0,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "filter-cache",
    )
    assert calls == 2
    filter_callback = _callback_for_input(app, STOCK_FILTER_IDS["activity"])
    panel, rows, mapped, unmapped, store = filter_callback(
        [],
        ["Macro"],
        [],
        [],
        [],
        [],
        loaded[2],
    )

    assert isinstance(panel, dash_table.DataTable)
    assert {row["CRDS"] for row in panel.data} == {"CRDS-1", "CRDS-3"}
    assert "Rows: 2 of 3" in rows
    assert mapped == "Mapped: 2"
    assert unmapped == "Unmapped: 0"
    assert store == {
        "filters": {
            "portfolio": [],
            "activity": ["Macro"],
            "signoffgroup": [],
            "category": [],
            "subcategory": [],
        },
        "exclude_selected": False,
    }
    excluded_panel, excluded_rows, *_rest = filter_callback(
        [],
        ["Macro"],
        [],
        [],
        [],
        ["exclude"],
        loaded[2],
    )
    assert isinstance(excluded_panel, dash_table.DataTable)
    assert [row["CRDS"] for row in excluded_panel.data] == ["CRDS-2"]
    assert "Rows: 1 of 3" in excluded_rows
    assert calls == 2


def test_stock_load_retries_a_transient_source_failure() -> None:
    attempts = 0

    def flaky_stock(_stock_date: pd.Timestamp) -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("temporary GetStock timeout")
        return _stock()

    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=flaky_stock,
        stock_portfolio_source=lambda _date: _config(),
    )
    callback = _callback_for_input(app, "stock-load-trigger")
    first = callback(
        1,
        "0",
        0,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "transient-retry",
    )
    assert any(getattr(item, "id", None) == "stock-load-error" for item in first[0])
    assert first[1] is no_update
    assert first[2] is None
    assert first[3] is False

    second = callback(
        2,
        "0",
        0,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "transient-retry",
    )
    assert second[1] == 0
    assert second[3] is True
    assert attempts == 3


def test_stock_load_retries_when_financial_revision_advances_during_io() -> None:
    manager = build_production_refresh_manager()
    attempts = 0

    def committing_stock(_stock_date: pd.Timestamp) -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            manager.refresh(force_risk=True, force_pl=True)
        return _stock()

    app = build_app(
        refresh_manager=manager,
        stock_source=committing_stock,
        stock_portfolio_source=lambda _date: _config(),
    )
    callback = _callback_for_input(app, "stock-load-trigger")
    first = callback(
        1,
        "0",
        0,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "revision-retry",
    )
    assert first[0]
    assert first[1] is no_update
    assert first[2] is no_update
    assert first[3] is False
    assert manager.health.revision == 1

    second = callback(
        2,
        "1",
        0,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "revision-retry",
    )
    assert second[1] == 1
    assert second[2]["revision"] == 1
    assert second[3] is True
    assert attempts == 4


def test_invalid_compare_dates_do_not_call_sources() -> None:
    calls = 0

    def stock_source(_date: pd.Timestamp) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        return _stock()

    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=stock_source,
        stock_portfolio_source=lambda _date: _config(),
    )
    callback = _callback_for_input(app, "stock-compare-button")
    result = callback(
        0,
        "0",
        1,
        -1,
        None,
        "2026-08-14",
        "2026-08-14",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "invalid-dates",
    )

    assert calls == 0
    assert any(getattr(item, "id", None) == "stock-load-error" for item in result[0])
    assert result[2] is None
    assert result[3] is True


def test_refresh_commit_reloads_selected_dates_and_preserves_filters() -> None:
    calls: list[tuple[str, pd.Timestamp]] = []

    def stock_source(date: pd.Timestamp) -> pd.DataFrame:
        calls.append(("stock", date))
        return _stock()

    def config_source(date: pd.Timestamp) -> pd.DataFrame:
        calls.append(("config", date))
        return _config()

    manager = build_production_refresh_manager()
    app = build_app(
        refresh_manager=manager,
        stock_source=stock_source,
        stock_portfolio_source=config_source,
    )
    load = _callback_for_input(app, "stock-load-trigger")
    loaded = load(
        1,
        "0",
        0,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        [],
        ["Macro"],
        [],
        [],
        [],
        "refresh-selected-dates",
    )
    manager.refresh(force_risk=True, force_pl=True)
    refresh = _callback_for_input(app, "refresh-commit-revision")
    refreshed = refresh(
        1,
        "1",
        0,
        loaded[1],
        loaded[2],
        "2026-08-14",
        "2026-08-13",
        [],
        [],
        ["Macro"],
        [],
        [],
        [],
        "refresh-selected-dates",
    )

    assert refreshed[1] == 1
    assert refreshed[2]["current_date"] == "2026-08-14"
    assert refreshed[2]["prior_date"] == "2026-08-13"
    assert refreshed[7] == ["Macro"]  # Activity value follows its options output.
    assert calls[-3:] == [
        ("stock", pd.Timestamp("2026-08-14")),
        ("stock", pd.Timestamp("2026-08-13")),
        ("config", pd.Timestamp("2026-08-14")),
    ]
