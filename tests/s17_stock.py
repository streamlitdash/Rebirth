"""Contracts for dated Stock comparison, local filters, and lazy callbacks."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from threading import Event, Thread
from types import SimpleNamespace

import pandas as pd
import pytest
from dash import dash_table, html, no_update
from flask import Flask
from plotly.utils import PlotlyJSONEncoder

from adapters.s05_stock import (
    GetStock,
    STOCK_COLUMNS,
    build_stock_adapter,
    get_stock,
    validate_stock_frame,
)
from core.s01_schema import PORTFOLIO_MAPPED_COLUMN, UNMAPPED_VALUE
from core.s07_stock import (
    CURRENT_MARKET_VALUE_COLUMN,
    MAPPED_STOCK_COMPARISON_COLUMNS,
    MARKET_VALUE_CHANGE_COLUMN,
    PRIOR_QUANTITY_COLUMN,
    QUANTITY_CHANGE_COLUMN,
    STOCK_CHANGE_COLUMN,
    STOCK_HIERARCHY_DEPTH_COLUMN,
    STOCK_HIERARCHY_LABEL_COLUMN,
    STOCK_HIERARCHY_LEVEL_COLUMN,
    STOCK_HIERARCHY_PATH_COLUMN,
    STOCK_HIERARCHY_POSITION_COUNT_COLUMN,
    STOCK_IDENTITY_COLUMNS,
    STOCK_PROMOTION_BUCKET_COLUMN,
    STOCK_PROMOTION_THRESHOLD_DEFAULT,
    STOCK_TEMPORARY_GROUP_COLUMN,
    compare_stock_snapshots,
    filter_stock_comparison,
    map_stock_comparison_portfolios,
    map_stock_portfolios,
    prepare_stock_hierarchy,
    summarize_stock_hierarchy,
    summarize_visible_stock_hierarchy,
)
from core.s08_saved_views import SavedFilterView
from feeds.s01_sources import build_production_refresh_manager
from pages import PAGE_SERVICES_CONFIG_KEY
from pages.stock import layout as stock_page_layout
from ui.s02_constants import DIMENSION_FILTER_IDS, FILTER_DIMENSION_FIELDS
from ui.s07_events import STARTUP_COORDINATOR_CONFIG_KEY
from ui import s09_factory as factory_module
from ui.s09_factory import build_app
from ui.s10_stock import (
    STOCK_FILTER_FIELDS,
    STOCK_FILTER_IDS,
    STOCK_HIERARCHY_TOGGLE_TYPE,
    StockPageData,
    build_stock_hierarchy_with_state,
    build_stock_page,
    build_stock_page_from_data,
    build_stock_page_from_sources,
    build_stock_page_shell,
    build_stock_table,
    default_stock_dates,
    normalize_stock_date_pair,
    stock_hierarchy_path_token,
    toggle_stock_hierarchy_open_tokens,
)
from ui.s11_saved_views import saved_view_apply_request


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


def _large_stock_inputs(
    row_count: int = 10_000,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    portfolios = [f"BOOK_{index}" for index in range(20)]
    current = _stock(
        [
            [
                f"CRDS-{index}",
                f"CPTY-{index % 250}",
                portfolios[index % len(portfolios)],
                f"INSTRUMENT-{index}",
                ("USD", "GBP", "EUR")[index % 3],
                float(index % 100),
                60_000.0 + float(index % 100),
            ]
            for index in range(row_count)
        ]
    )
    prior = current.copy()
    prior["Quantity"] = prior["Quantity"] - 1.0
    prior["Market Value"] = prior["Market Value"] - 250.0
    config = _config(
        [
            [
                portfolio,
                "XVA",
                "Macro" if index % 2 == 0 else "Hedge",
                f"SOG-{index % 4}",
                "Core",
                "Stock",
            ]
            for index, portfolio in enumerate(portfolios)
        ]
    )
    return current, prior, config


def _large_mapped_stock(row_count: int = 10_000) -> pd.DataFrame:
    current, prior, config = _large_stock_inputs(row_count)
    return map_stock_comparison_portfolios(current, prior, config)


def _walk(component: object) -> Iterable[object]:
    yield component
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    elif children is not None:
        yield from _walk(children)


def _hierarchy_levels(component: object) -> list[str]:
    levels: list[str] = []
    for item in _walk(component):
        if not hasattr(item, "to_plotly_json"):
            continue
        value = item.to_plotly_json().get("props", {}).get("data-stock-hierarchy-level")
        if value is not None:
            levels.append(str(value))
    return levels


def _hierarchy_paths(component: object) -> set[str]:
    paths: set[str] = set()
    for item in _walk(component):
        if not hasattr(item, "to_plotly_json"):
            continue
        value = item.to_plotly_json().get("props", {}).get("data-stock-hierarchy-path")
        if value is not None:
            paths.add(str(value))
    return paths


def _callback_for_input(app, component_id: str):
    return next(
        metadata["callback"].__wrapped__
        for metadata in app.callback_map.values()
        if any(item["id"] == component_id for item in metadata["inputs"])
    )


def _callback_for_output(app, component_id: str, component_property: str):
    return next(
        metadata["callback"].__wrapped__
        for metadata in app.callback_map.values()
        if any(
            output.component_id == component_id
            and output.component_property == component_property
            for output in _callback_outputs(metadata)
        )
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


def test_stock_promotion_runs_on_the_filtered_comparison_shape() -> None:
    """A promoted source row cannot leak into a lower-value filtered view."""

    current = _stock(
        [
            ["CRDS-HIGH", "CPTY-A", "BOOK_A", "EQ-A", "USD", 1.0, 60_000.0],
            ["CRDS-LOW", "CPTY-B", "BOOK_B", "EQ-B", "USD", 1.0, 20_000.0],
        ]
    )
    prior = current.copy()
    prior["Market Value"] = 0.0
    mapped = map_stock_comparison_portfolios(current, prior, _config())

    global_hierarchy = summarize_stock_hierarchy(mapped, 50_000.0)
    filtered = filter_stock_comparison(mapped, {"activity": ["Hedge"]})
    filtered_hierarchy = summarize_stock_hierarchy(filtered, 50_000.0)

    global_paths = set(global_hierarchy[STOCK_HIERARCHY_PATH_COLUMN])
    filtered_paths = set(filtered_hierarchy[STOCK_HIERARCHY_PATH_COLUMN])
    assert ("Macro", "Promoted") in global_paths
    assert filtered[CURRENT_MARKET_VALUE_COLUMN].sum() == 20_000.0
    assert ("Hedge", "Promoted") not in filtered_paths
    assert ("Hedge", "Other") in filtered_paths
    assert all("CRDS-HIGH" not in path for path in filtered_paths)


def test_stock_promotion_aggregates_filtered_rows_at_displayed_name_identity() -> None:
    """Several Portfolio rows for one visible name share one promotion bucket."""

    current = _stock(
        [
            ["CRDS-1", "CPTY-A", "BOOK_A", "EQ-A", "USD", 1.0, 30_000.0],
            ["CRDS-1", "CPTY-A", "BOOK_B", "EQ-B", "USD", 1.0, 30_000.0],
        ]
    )
    prior = current.copy()
    prior["Market Value"] = 0.0
    config = _config(
        [
            ["BOOK_A", "XVA", "Macro", "SOG-A", "Core", "Rates"],
            ["BOOK_B", "XVA", "Macro", "SOG-A", "Core", "Rates"],
        ]
    )
    mapped = map_stock_comparison_portfolios(current, prior, config)

    prepared = prepare_stock_hierarchy(mapped, 50_000.0)
    assert prepared[CURRENT_MARKET_VALUE_COLUMN].tolist() == [30_000.0, 30_000.0]
    assert prepared[STOCK_PROMOTION_BUCKET_COLUMN].tolist() == [
        "Promoted",
        "Promoted",
    ]

    hierarchy = summarize_stock_hierarchy(mapped, 50_000.0)
    visible_leaf = hierarchy.loc[
        hierarchy[STOCK_HIERARCHY_PATH_COLUMN].map(
            lambda path: (
                path
                == (
                    "Macro",
                    "Promoted",
                    "Temporary currency group · USD",
                    "CPTY-A",
                    "CRDS-1",
                )
            )
        )
    ].iloc[0]
    assert visible_leaf[CURRENT_MARKET_VALUE_COLUMN] == 60_000.0
    assert not any(
        path[:2] == ("Macro", "Other")
        for path in hierarchy[STOCK_HIERARCHY_PATH_COLUMN]
    )


def test_stock_hierarchy_orders_absolute_current_stock_after_filtering() -> None:
    current = _stock(
        [
            ["CRDS-A", "CPTY-A", "BOOK_A", "EQ-A", "USD", 1.0, 70_000.0],
            ["CRDS-B", "CPTY-B", "BOOK_B", "EQ-B", "USD", 1.0, -65_000.0],
            ["CRDS-C", "CPTY-C", "BOOK_C", "EQ-C", "USD", 1.0, -60_000.0],
        ]
    )
    prior = current.copy()
    prior["Market Value"] = 0.0
    config = _config(
        [
            ["BOOK_A", "XVA", "Macro", "SOG-A", "Core", "Rates"],
            ["BOOK_B", "XVA", "Macro", "SOG-A", "Core", "Rates"],
            ["BOOK_C", "XVA", "Hedge", "SOG-A", "Core", "Rates"],
        ]
    )
    mapped = map_stock_comparison_portfolios(current, prior, config)

    def activity_order(frame: pd.DataFrame) -> list[str]:
        hierarchy = summarize_stock_hierarchy(frame, 50_000.0)
        return hierarchy.loc[
            hierarchy[STOCK_HIERARCHY_DEPTH_COLUMN].eq(1),
            STOCK_HIERARCHY_LABEL_COLUMN,
        ].tolist()

    # Macro nets to +5k globally, so Hedge's 60k absolute Stock ranks first.
    assert activity_order(mapped) == ["Hedge", "Macro"]

    # Removing BOOK_B changes Macro to +70k and therefore recomputes its rank.
    filtered = filter_stock_comparison(
        mapped,
        {"portfolio": ["BOOK_A", "BOOK_C"]},
    )
    assert activity_order(filtered) == ["Macro", "Hedge"]


def test_stock_promotion_and_hierarchy_preserve_identity_and_totals() -> None:
    current = _stock(
        [
            ["CRDS-1", "CPTY-A", "BOOK_A", "EQ-A", "USD", 10.0, 50_000.0],
            ["CRDS-2", "CPTY-B", "BOOK_B", "EQ-B", "GBP", 20.0, -60_000.0],
            ["CRDS-3", "CPTY-C", "BOOK_C", "EQ-C", "USD", 30.0, 49_999.0],
            [
                "CRDS-4",
                "CPTY-D",
                "BOOK_UNKNOWN",
                "EQ-D",
                "EUR",
                40.0,
                100_000.0,
            ],
        ]
    )
    prior = current.copy()
    prior["Market Value"] = 0.0
    mapped = map_stock_comparison_portfolios(current, prior, _config())

    prepared = prepare_stock_hierarchy(mapped, 50_000)
    assert prepared[STOCK_PROMOTION_BUCKET_COLUMN].tolist() == [
        "Promoted",  # equality is intentionally inclusive
        "Promoted",  # negative current MV uses its absolute value
        "Other",
        "Promoted",
    ]
    assert prepared[STOCK_TEMPORARY_GROUP_COLUMN].tolist() == [
        "Temporary currency group · USD",
        "Temporary currency group · GBP",
        "Temporary currency group · USD",
        "Temporary currency group · EUR",
    ]
    assert prepared[PORTFOLIO_MAPPED_COLUMN].tolist() == [True, True, True, False]

    hierarchy = summarize_stock_hierarchy(mapped, 50_000)
    total = hierarchy.loc[
        hierarchy[STOCK_HIERARCHY_PATH_COLUMN].map(lambda path: path == ())
    ].iloc[0]
    assert total[CURRENT_MARKET_VALUE_COLUMN] == 139_999.0
    assert total[MARKET_VALUE_CHANGE_COLUMN] == 139_999.0
    paths = set(hierarchy[STOCK_HIERARCHY_PATH_COLUMN])
    macro = hierarchy.loc[
        hierarchy[STOCK_HIERARCHY_PATH_COLUMN].map(lambda path: path == ("Macro",))
    ].iloc[0]
    assert macro[STOCK_HIERARCHY_POSITION_COUNT_COLUMN] == 2
    assert macro[CURRENT_MARKET_VALUE_COLUMN] == 99_999.0
    assert macro[MARKET_VALUE_CHANGE_COLUMN] == 99_999.0
    assert (
        "Macro",
        "Promoted",
        "Temporary currency group · USD",
        "CPTY-A",
        "CRDS-1",
    ) in paths
    assert (
        "Macro",
        "Other",
        "Temporary currency group · USD",
        "CPTY-C",
        "CRDS-3",
    ) in paths
    assert (
        UNMAPPED_VALUE,
        "Promoted",
        "Temporary currency group · EUR",
        "CPTY-D",
        "CRDS-4",
    ) in paths
    assert set(hierarchy[STOCK_HIERARCHY_LEVEL_COLUMN]) == {
        "Total",
        "Activity",
        STOCK_PROMOTION_BUCKET_COLUMN,
        STOCK_TEMPORARY_GROUP_COLUMN,
        "CPTY",
        "CRDS",
    }


def test_stock_hierarchy_progressively_renders_only_open_branches() -> None:
    current, prior = _comparison_legs()
    mapped = map_stock_comparison_portfolios(current, prior, _config())

    initial_summary = summarize_visible_stock_hierarchy(mapped, 10.0)
    assert set(initial_summary[STOCK_HIERARCHY_LEVEL_COLUMN]) == {
        "Total",
        "Activity",
    }

    tree, open_tokens = build_stock_hierarchy_with_state(
        mapped,
        promotion_threshold=10.0,
    )
    assert set(_hierarchy_levels(tree)) == {"Total", "Activity"}
    toggle_ids = {
        component_id["path"]
        for item in _walk(tree)
        if isinstance((component_id := getattr(item, "id", None)), dict)
        and component_id.get("type") == STOCK_HIERARCHY_TOGGLE_TYPE
    }
    macro = stock_hierarchy_path_token(("Macro",))
    assert macro in toggle_ids
    assert open_tokens == []

    open_tokens = toggle_stock_hierarchy_open_tokens(open_tokens, macro)
    tree, open_tokens = build_stock_hierarchy_with_state(
        mapped,
        promotion_threshold=10.0,
        open_path_tokens=open_tokens,
    )
    assert "Promotion Bucket" in _hierarchy_levels(tree)
    assert "Group (Temporary Fixture)" not in _hierarchy_levels(tree)

    promoted = stock_hierarchy_path_token(("Macro", "Promoted"))
    open_tokens = toggle_stock_hierarchy_open_tokens(open_tokens, promoted)
    tree, open_tokens = build_stock_hierarchy_with_state(
        mapped,
        promotion_threshold=10.0,
        open_path_tokens=open_tokens,
    )
    assert "Group (Temporary Fixture)" in _hierarchy_levels(tree)
    assert "CPTY" not in _hierarchy_levels(tree)
    assert "Macro / Other / Temporary currency group · GBP" not in _hierarchy_paths(
        tree
    )

    group = stock_hierarchy_path_token(
        ("Macro", "Promoted", "Temporary currency group · USD")
    )
    open_tokens = toggle_stock_hierarchy_open_tokens(open_tokens, group)
    tree, open_tokens = build_stock_hierarchy_with_state(
        mapped,
        promotion_threshold=10.0,
        open_path_tokens=open_tokens,
    )
    assert "CPTY" in _hierarchy_levels(tree)
    assert "CRDS" not in _hierarchy_levels(tree)

    counterparty = stock_hierarchy_path_token(
        (
            "Macro",
            "Promoted",
            "Temporary currency group · USD",
            "CPTY-A",
        )
    )
    open_tokens = toggle_stock_hierarchy_open_tokens(open_tokens, counterparty)
    tree, effective = build_stock_hierarchy_with_state(
        mapped,
        promotion_threshold=10.0,
        open_path_tokens=open_tokens,
    )
    assert "CRDS" in _hierarchy_levels(tree)
    assert "Macro / Promoted / Temporary currency group · USD / CPTY-A / CRDS-1" in (
        _hierarchy_paths(tree)
    )
    assert effective == open_tokens


def test_stock_hierarchy_uses_risk_explorer_semantic_table_contract() -> None:
    current, prior = _comparison_legs()
    mapped = map_stock_comparison_portfolios(current, prior, _config())

    tree, open_tokens = build_stock_hierarchy_with_state(
        mapped,
        promotion_threshold=10.0,
    )
    assert open_tokens == []
    assert {"risk-table-wrap", "stock-hierarchy-table-wrap"} <= set(
        str(tree.className).split()
    )

    tables = [item for item in _walk(tree) if isinstance(item, html.Table)]
    assert len(tables) == 1
    table = tables[0]
    assert {"risk-table", "stock-hierarchy-table"} <= set(str(table.className).split())
    assert table.role == "treegrid"
    assert any(isinstance(item, html.Caption) for item in table.children)
    assert any(isinstance(item, html.Thead) for item in table.children)
    body = next(item for item in table.children if isinstance(item, html.Tbody))
    rows = [item for item in body.children if isinstance(item, html.Tr)]
    assert rows
    assert "total-row" in str(rows[0].className).split()
    assert all(len(row.children) == 8 for row in rows)

    activity_rows = [
        row
        for row in rows
        if row.to_plotly_json()["props"].get("data-stock-hierarchy-level") == "Activity"
    ]
    assert activity_rows
    assert all(
        {"group-row", "hierarchy-total-row", "stock-hierarchy-row"}
        <= set(str(row.className).split())
        for row in activity_rows
    )
    assert all(isinstance(row.children[0], html.Th) for row in rows)
    assert all("index-cell" in str(row.children[0].className) for row in rows)
    assert not any(
        "stock-hierarchy-level-label" in str(getattr(item, "className", "")).split()
        for item in _walk(tree)
    )

    toggles = [
        item
        for item in _walk(tree)
        if isinstance(item, html.Button)
        and isinstance(item.id, dict)
        and item.id.get("type") == STOCK_HIERARCHY_TOGGLE_TYPE
    ]
    assert toggles
    assert all("row-toggle" in str(toggle.className).split() for toggle in toggles)
    assert {toggle.children for toggle in toggles} == {"▸"}
    assert all(
        toggle.to_plotly_json()["props"]["aria-label"].startswith("Expand ")
        for toggle in toggles
    )
    assert all(
        toggle.to_plotly_json()["props"]["title"]
        == toggle.to_plotly_json()["props"]["aria-label"]
        for toggle in toggles
    )

    macro_path = stock_hierarchy_path_token(("Macro",))
    opened_tree, effective_open_tokens = build_stock_hierarchy_with_state(
        mapped,
        promotion_threshold=10.0,
        open_path_tokens=[macro_path],
    )
    assert effective_open_tokens == [macro_path]
    macro_toggle = next(
        item
        for item in _walk(opened_tree)
        if isinstance(item, html.Button)
        and isinstance(item.id, dict)
        and item.id.get("path") == macro_path
    )
    assert macro_toggle.children == "−"
    assert macro_toggle.to_plotly_json()["props"]["aria-expanded"] == "true"
    assert macro_toggle.to_plotly_json()["props"]["aria-label"].startswith(
        "Collapse Activity: Macro"
    )

    metric_cells = [item for item in _walk(tree) if isinstance(item, html.Td)]
    assert any(
        "number-positive" in str(cell.className).split() for cell in metric_cells
    )
    assert any(
        "number-negative" in str(cell.className).split() for cell in metric_cells
    )


def test_closed_10k_stock_page_is_bounded_and_keeps_source_rows_lazy() -> None:
    mapped = _large_mapped_stock()

    started = time.perf_counter()
    page = build_stock_page_from_data(
        StockPageData(
            mapped_stock=mapped,
            current_date=pd.Timestamp("2026-08-14"),
            prior_date=pd.Timestamp("2026-08-13"),
            portfolio_date=pd.Timestamp("2026-08-14"),
        )
    )
    elapsed = time.perf_counter() - started
    payload = json.dumps(page, cls=PlotlyJSONEncoder, separators=(",", ":"))
    tree = next(
        item
        for item in _walk(page)
        if getattr(item, "id", None) == "stock-hierarchy-stack"
    )

    assert elapsed < 2.5
    assert len(payload.encode("utf-8")) < 250_000
    assert set(_hierarchy_levels(tree)) == {"Total", "Activity"}
    assert "CRDS-9999" not in payload
    assert not any(isinstance(item, dash_table.DataTable) for item in _walk(page))
    assert (
        sum(
            getattr(item, "id", None) == "stock-source-rows-button"
            for item in _walk(page)
        )
        == 1
    )


def test_10k_cached_hierarchy_expand_does_not_resend_source_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, prior, config = _large_stock_inputs()
    calls = 0

    def stock_source(stock_date: pd.Timestamp) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        return current if stock_date == pd.Timestamp("2026-08-14") else prior

    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=stock_source,
        stock_portfolio_source=lambda _date: config,
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
        "large-lazy-stock",
    )
    initial_payload = json.dumps(
        loaded[0],
        cls=PlotlyJSONEncoder,
        separators=(",", ":"),
    )
    assert len(initial_payload.encode("utf-8")) < 250_000
    assert not any(
        isinstance(item, dash_table.DataTable)
        for component in loaded[0]
        for item in _walk(component)
    )

    macro_path = stock_hierarchy_path_token(("Macro",))
    monkeypatch.setattr(
        factory_module,
        "ctx",
        SimpleNamespace(
            triggered_id={
                "type": STOCK_HIERARCHY_TOGGLE_TYPE,
                "path": macro_path,
            },
            triggered=[{"value": 1}],
        ),
    )
    hierarchy_callback = _callback_for_output(
        app,
        "stock-hierarchy-view",
        "children",
    )
    expanded = hierarchy_callback(
        [],
        [],
        [],
        [],
        [],
        [],
        50_000.0,
        loaded[2],
        [1],
        [],
    )
    assert expanded[:4] == (no_update, no_update, no_update, no_update)
    assert expanded[4] == [macro_path]
    assert not any(
        isinstance(item, dash_table.DataTable) for item in _walk(expanded[5])
    )
    expanded_payload = json.dumps(
        expanded[5],
        cls=PlotlyJSONEncoder,
        separators=(",", ":"),
    )
    assert len(expanded_payload.encode("utf-8")) < 250_000
    assert calls == 2


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
    assert "stock-promotion-threshold" in ids
    threshold = next(
        component
        for component in _walk(shell)
        if getattr(component, "id", None) == "stock-promotion-threshold"
    )
    filter_row = next(
        component
        for component in _walk(shell)
        if isinstance(component, html.Div)
        and {"controls", "filter-controls"}
        <= set(str(getattr(component, "className", "")).split())
    )
    saved_view_bar = next(
        component
        for component in _walk(shell)
        if isinstance(component, html.Details)
        and getattr(component, "id", None) == "stock-saved-view-bar"
    )
    compare_action = next(
        component
        for component in _walk(shell)
        if isinstance(component, html.Div)
        and "stock-compare-action"
        in set(str(getattr(component, "className", "")).split())
    )
    assert threshold.value == STOCK_PROMOTION_THRESHOLD_DEFAULT
    assert threshold.min == 0
    assert threshold.debounce is True
    assert compare_action.children[0].children == "Compare"
    assert compare_action.children[1].id == "stock-compare-button"
    assert compare_action.children[1].type == "button"
    filter_fields = filter_row.children[: len(FILTER_DIMENSION_FIELDS)]
    assert [control.children[0].children for control in filter_fields] == [
        "Activity",
        "Signoff Group",
        "Portfolio",
        "Category",
        "Sub Category",
    ]
    assert filter_row.children[-1].id == "stock-filter-exclude-selected"
    assert "filter-mode-control" in str(filter_row.children[-1].className).split()
    saved_view_notes = [
        component
        for component in _walk(saved_view_bar)
        if isinstance(component, html.Div)
        and "saved-view-filter-note"
        in set(str(getattr(component, "className", "")).split())
    ]
    assert len(saved_view_notes) == 1
    assert "Stock selections remain independent" in saved_view_notes[0].children
    assert filter_row in list(_walk(saved_view_bar))
    assert "dimension-filter-store" not in ids
    assert not (set(DIMENSION_FILTER_IDS.values()) & ids)


def test_stock_source_table_native_filters_are_case_insensitive() -> None:
    current, prior = _comparison_legs()
    mapped = map_stock_comparison_portfolios(current, prior, _config())
    table = build_stock_table(mapped)

    assert table.filter_action == "native"
    assert table.filter_options == {"case": "insensitive"}


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


def test_stock_page_keeps_source_rows_lazy_and_exposes_filtered_counts() -> None:
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
    ids = {
        component_id
        for component in components
        if isinstance((component_id := getattr(component, "id", None)), str)
    }
    assert {
        "stock-comparison-view",
        "stock-hierarchy-panel",
        "stock-hierarchy-view",
        "stock-hierarchy-stack",
        "stock-source-comparison-details",
        "stock-source-rows-button",
        "stock-table-panel",
        "stock-row-count",
        "stock-mapped-count",
        "stock-unmapped-count",
    } <= ids
    assert (
        sum(
            getattr(component, "id", None) == "stock-source-rows-button"
            for component in components
        )
        == 1
    )
    assert (
        sum(
            getattr(component, "id", None) == "stock-table-panel"
            for component in components
        )
        == 1
    )
    assert not any(
        isinstance(component, dash_table.DataTable) for component in components
    )
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
        "stock-hierarchy-open-paths",
        "stock-source-rows-state",
        "stock-load-trigger",
        "stock-request-scope",
        "stock-current-date",
        "stock-prior-date",
        "stock-compare-button",
    } <= ids
    assert calls == []
    assert (
        sum(
            getattr(component, "id", None) == "stock-source-rows-button"
            for component in components
        )
        == 1
    )
    assert (
        sum(
            getattr(component, "id", None) == "stock-table-panel"
            for component in components
        )
        == 1
    )

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


def test_initial_stock_shell_contains_every_stock_callback_output_target() -> None:
    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=lambda _date: _stock(),
        stock_portfolio_source=lambda _date: _config(),
    )
    with app.server.test_request_context("/stock"):
        page = stock_page_layout()
    shell_ids = {
        str(component_id)
        for item in _walk(page)
        if isinstance((component_id := getattr(item, "id", None)), str)
    }
    stock_inputs = {
        "stock-load-trigger",
        "stock-compare-button",
        "stock-filter-exclude-selected",
        "stock-promotion-threshold",
        "stock-source-rows-button",
        *STOCK_FILTER_IDS.values(),
    }
    callback_outputs = {
        str(output.component_id)
        for metadata in app.callback_map.values()
        if any(str(item["id"]) in stock_inputs for item in metadata["inputs"])
        for output in _callback_outputs(metadata)
        if isinstance(output.component_id, str)
        and output.component_id.startswith("stock-")
    }

    assert callback_outputs
    assert callback_outputs <= shell_ids
    assert {
        "stock-table-panel",
        "stock-row-count",
        "stock-mapped-count",
        "stock-unmapped-count",
        "stock-hierarchy-view",
        "stock-source-rows-button",
        "stock-source-rows-state",
    } <= shell_ids


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


def test_pending_saved_stock_view_survives_a_busy_load_and_applies_on_retry() -> None:
    load_started = Event()
    release_load = Event()
    calls: list[pd.Timestamp] = []

    def stock_source(stock_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(stock_date)
        if stock_date == pd.Timestamp("2026-08-14") and not load_started.is_set():
            load_started.set()
            if not release_load.wait(timeout=3):
                raise TimeoutError("test did not release the blocked Stock load")
        return _stock()

    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=stock_source,
        stock_portfolio_source=lambda _date: _config(),
    )
    callback = _callback_for_input(app, "stock-load-trigger")
    old_result: list[tuple] = []

    thread = Thread(
        target=lambda: old_result.append(
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
                "saved-view-retry",
            )
        )
    )
    thread.start()
    assert load_started.wait(timeout=3)

    target_filters = {field.key: [] for field in STOCK_FILTER_FIELDS}
    target_filters["activity"] = ("Macro",)
    view = SavedFilterView(
        identifier="macro--0123456789ab",
        scope="stock",
        name="Macro",
        filters=target_filters,
        exclude_selected=False,
    )
    request = saved_view_apply_request(
        view,
        base_filters={field.key: [] for field in STOCK_FILTER_FIELDS},
        base_exclude_selected=False,
    )
    busy = callback(
        1,
        "0",
        0,
        request,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "saved-view-retry",
        None,
    )
    assert busy[:3] == (no_update, no_update, no_update)
    assert busy[3] is False

    release_load.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert len(old_result) == 1
    assert all(value is no_update for value in old_result[0])

    retried = callback(
        2,
        "0",
        0,
        request,
        -1,
        None,
        "2026-08-14",
        "2026-08-13",
        [],
        *([[]] * len(STOCK_FILTER_FIELDS)),
        "saved-view-retry",
        None,
    )
    assert retried[1] == 0
    assert retried[3] is True
    assert retried[5] == ["Macro"]
    assert retried[14] == []
    assert calls == [pd.Timestamp("2026-08-14"), pd.Timestamp("2026-08-13")]

    later_manual_filters = [
        ["Hedge"] if field.key == "activity" else [] for field in STOCK_FILTER_FIELDS
    ]
    superseded = callback(
        3,
        "0",
        0,
        request,
        retried[1],
        retried[2],
        "2026-08-14",
        "2026-08-13",
        [],
        *later_manual_filters,
        "saved-view-retry",
        None,
    )
    assert superseded[0] is no_update
    assert superseded[5] is no_update
    assert superseded[14] is no_update


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
    assert len(owners[("stock-hierarchy-open-paths", "data")]) == 1
    assert len(owners[("stock-hierarchy-view", "children")]) == 1
    assert len(owners[("stock-table-panel", "children")]) == 1
    assert len(owners[("stock-source-rows-state", "data")]) == 1
    assert len(owners[("stock-source-rows-button", "children")]) == 1
    hierarchy_callback = next(
        metadata
        for metadata in app.callback_map.values()
        if any(
            output.component_id == "stock-hierarchy-view"
            for output in _callback_outputs(metadata)
        )
    )
    assert any(
        STOCK_HIERARCHY_TOGGLE_TYPE in item["id"]
        for item in hierarchy_callback["inputs"]
    )


def test_stock_filter_callback_uses_cache_only_and_updates_visible_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    filter_callback = _callback_for_output(
        app,
        "stock-hierarchy-view",
        "children",
    )
    rows, mapped, unmapped, store, open_paths, hierarchy = filter_callback(
        ["Macro"],
        [],
        [],
        [],
        [],
        [],
        10.0,
        loaded[2],
        [],
        [],
    )

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
        "promotion_threshold": 10.0,
    }
    assert hierarchy.id == "stock-hierarchy-stack"
    assert open_paths == []
    assert hierarchy.to_plotly_json()["props"]["data-stock-promotion-threshold"] == (
        "10.0"
    )
    macro_path = stock_hierarchy_path_token(("Macro",))
    monkeypatch.setattr(
        factory_module,
        "ctx",
        SimpleNamespace(
            triggered_id={
                "type": STOCK_HIERARCHY_TOGGLE_TYPE,
                "path": macro_path,
            },
            triggered=[{"value": 1}],
        ),
    )
    expanded = filter_callback(
        ["Macro"],
        [],
        [],
        [],
        [],
        [],
        10.0,
        loaded[2],
        [1],
        [],
    )
    assert expanded[:4] == (no_update, no_update, no_update, no_update)
    assert expanded[4] == [macro_path]
    assert "Promotion Bucket" in _hierarchy_levels(expanded[5])
    assert not any(
        isinstance(item, dash_table.DataTable) for item in _walk(expanded[5])
    )
    assert calls == 2
    monkeypatch.setattr(
        factory_module,
        "ctx",
        SimpleNamespace(
            triggered_id="stock-filter-exclude-selected",
            triggered=[{"value": ["exclude"]}],
        ),
    )
    excluded_rows, *_rest = filter_callback(
        ["Macro"],
        [],
        [],
        [],
        [],
        ["exclude"],
        50_000.0,
        loaded[2],
        [],
        [],
    )
    assert "Rows: 1 of 3" in excluded_rows
    assert calls == 2

    source_callback = _callback_for_input(app, "stock-source-rows-button")
    monkeypatch.setattr(
        factory_module,
        "ctx",
        SimpleNamespace(
            triggered_id="stock-source-rows-button",
            triggered=[{"value": 1}],
        ),
    )
    source_panel, source_state, source_label, source_open = source_callback(
        1,
        loaded[2],
        ["Macro"],
        [],
        [],
        [],
        [],
        [],
        {"requested": False, "loaded_dates": loaded[2]},
    )
    assert isinstance(source_panel, dash_table.DataTable)
    assert [column["id"] for column in source_panel.columns] == list(
        MAPPED_STOCK_COMPARISON_COLUMNS
    )
    assert source_panel.filter_action == "native"
    assert source_panel.sort_action == "native"
    assert {row["CRDS"] for row in source_panel.data} == {"CRDS-1", "CRDS-3"}
    assert source_state == {"requested": True, "loaded_dates": loaded[2]}
    assert source_label == "Hide source rows"
    assert source_open is True
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
    error_ids = {
        str(component_id)
        for item in first[0]
        for descendant in _walk(item)
        if isinstance((component_id := getattr(descendant, "id", None)), str)
    }
    assert {
        "stock-load-error",
        "stock-table-panel",
        "stock-source-rows-button",
        "stock-row-count",
        "stock-mapped-count",
        "stock-unmapped-count",
        "stock-hierarchy-view",
    } <= error_ids
    assert (
        sum(
            getattr(descendant, "id", None) == "stock-source-rows-button"
            for item in first[0]
            for descendant in _walk(item)
        )
        == 1
    )
    assert (
        sum(
            getattr(descendant, "id", None) == "stock-table-panel"
            for item in first[0]
            for descendant in _walk(item)
        )
        == 1
    )
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
        ["Macro"],
        [],
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
        ["Macro"],
        [],
        [],
        [],
        [],
        "refresh-selected-dates",
    )

    assert refreshed[1] == 1
    assert refreshed[2]["current_date"] == "2026-08-14"
    assert refreshed[2]["prior_date"] == "2026-08-13"
    assert refreshed[5] == ["Macro"]  # Activity value follows its options output.
    assert calls[-3:] == [
        ("stock", pd.Timestamp("2026-08-14")),
        ("stock", pd.Timestamp("2026-08-13")),
        ("config", pd.Timestamp("2026-08-14")),
    ]
