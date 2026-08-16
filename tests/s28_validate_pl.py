"""Official historical Risk comparison and Validate P&L regressions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from dash import Dash, dcc, html

from core.s11_risk_archive import archive_official_snapshot
from core.s04_pl import HISTORY_MAPPING_STATUS
from core.s01_schema import UNMAPPED_VALUE
from ui import s13_validate_pl as validate_pl_module
from ui.s06_plview import build_pl_send_sections
from ui.s13_validate_pl import (
    build_validate_pl_comparison,
    build_validate_pl_table,
    build_validate_pl_section,
    normalize_validate_pl_open_paths,
    register_validate_pl_callbacks,
    toggle_validate_pl_open_paths,
)
from ui.s14_pl_explorer import (
    PL_EXPLORER_FILTER_IDS,
    apply_pl_explorer_filters,
    pl_explorer_filter_map,
    pl_explorer_filter_options,
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


def _raw_risk() -> pd.DataFrame:
    base = {
        "Source Type": "ir/delta",
        "Risk Type": "IR",
        "Risk Greek": "Delta",
        "Display Bucket": "Other",
        "Region": "Americas",
        "Group": "G10",
        "Reported Underlying": "USD-SOFR",
        "Underlying": "USD-SOFR",
        "Tenor Option": "N/A",
        "Split": "Risk",
        "Product": "XVA",
        "Portfolio": "BOOK-A",
        "Activity": "1111",
        "SignoffGroup": "SOG-A",
        "Category": "Core",
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
                "Tenor Swap": "1Y",
                "Risk": 10.0,
                "dRisk": 1.0,
                "PL": 4.0,
            },
            {
                **base,
                "Tenor Swap": "2Y",
                "Risk": 20.0,
                "dRisk": 2.0,
                "PL": 6.0,
            },
        ]
    )


def _colossus(*, duplicate: bool = False) -> pd.DataFrame:
    rows = [["BOOK-A", "USD-SOFR", "IR", "Delta", 12.0]]
    if duplicate:
        rows.append(rows[0])
    return pd.DataFrame(
        rows,
        columns=["Portfolio", "Underlying", "Risk Type", "Risk Greek", "PL"],
    )


def _token(**values: str) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def test_comparison_aggregates_predict_before_one_to_one_colossus_join() -> None:
    comparison = build_validate_pl_comparison(_raw_risk(), _colossus())

    assert len(comparison) == 1
    assert comparison.loc[0, ["risk", "drisk", "pl", "colossus"]].tolist() == [
        30.0,
        3.0,
        10.0,
        12.0,
    ]
    assert comparison.loc[0, "comparison status"] == "Matched"


def test_comparison_rejects_duplicate_colossus_grain_instead_of_multiplying_it() -> (
    None
):
    with pytest.raises(ValueError, match="duplicate comparison keys"):
        build_validate_pl_comparison(_raw_risk(), _colossus(duplicate=True))


def test_comparison_never_presents_a_partial_predict_total() -> None:
    risk = _raw_risk()
    risk.loc[1, "PL"] = float("nan")

    comparison = build_validate_pl_comparison(risk, _colossus())

    assert pd.isna(comparison.loc[0, "pl"])
    assert comparison.loc[0, "colossus"] == 12.0


def test_comparison_maps_known_colossus_only_and_audits_unknown_once() -> None:
    colossus = pd.concat(
        [
            _colossus(),
            pd.DataFrame(
                [
                    ["BOOK-A", "JPY-SOFR", "IR", "Delta", 3.0],
                    ["BOOK-Z", "GBP-SONIA", "IR", "Delta", 7.0],
                ],
                columns=["Portfolio", "Underlying", "Risk Type", "Risk Greek", "PL"],
            ),
        ],
        ignore_index=True,
    )

    comparison = build_validate_pl_comparison(_raw_risk(), colossus)

    known = comparison.loc[comparison["Underlying"].eq("JPY-SOFR")].iloc[0]
    assert known["SignoffGroup"] == "SOG-A"
    assert known["Product"] == "XVA"
    assert known["comparison status"] == "Colossus only"
    assert pd.isna(known["pl"])
    unknown = comparison.loc[comparison["Portfolio"].eq("BOOK-Z")]
    assert len(unknown) == 1
    assert unknown.iloc[0][HISTORY_MAPPING_STATUS] == UNMAPPED_VALUE
    assert unknown.iloc[0]["comparison status"] == "Colossus unmapped"
    assert unknown.iloc[0]["colossus"] == 7.0


def test_comparison_never_copies_colossus_across_ambiguous_products() -> None:
    ambiguous_risk = pd.concat(
        [
            _raw_risk(),
            _raw_risk().iloc[[0]].assign(Product="Hedges", PL=1.0),
        ],
        ignore_index=True,
    )

    comparison = build_validate_pl_comparison(ambiguous_risk, _colossus())

    colossus_rows = comparison.loc[comparison["colossus"].notna()]
    assert len(colossus_rows) == 1
    assert colossus_rows.iloc[0][HISTORY_MAPPING_STATUS] == UNMAPPED_VALUE
    assert colossus_rows.iloc[0]["Product"] == UNMAPPED_VALUE
    assert comparison.loc[comparison["pl"].notna(), "Product"].tolist() == [
        "Hedges",
        "XVA",
    ]


def test_shared_explorer_filters_are_case_insensitive_with_documented_boolean_logic() -> (
    None
):
    frame = pd.DataFrame(
        [
            ["Credit", "SOG-A", "BOOK-A", "Core", "IG"],
            ["credit", "SOG-B", "BOOK-B", "Core", "HY"],
            ["Rates", "SOG-A", "BOOK-C", "Macro", "G10"],
        ],
        columns=[
            "Activity",
            "SignoffGroup",
            "Portfolio",
            "Category",
            "Sub Category",
        ],
    )
    original = frame.copy(deep=True)
    options = pl_explorer_filter_options(
        frame,
        frame.iloc[[0]].assign(Activity="CREDIT"),
    )
    assert [option["value"] for option in options["Activity"]] == ["Credit", "Rates"]

    selections = pl_explorer_filter_map([["CREDIT", "rates"], ["sog-a"], [], [], []])
    included = apply_pl_explorer_filters(frame, selections)
    excluded = apply_pl_explorer_filters(
        frame,
        pl_explorer_filter_map([["credit"], ["sog-a"], [], [], []]),
        exclude_selected=True,
    )

    # Include is OR inside Activity, then AND with Signoff Group.
    assert included["Portfolio"].tolist() == ["BOOK-A", "BOOK-C"]
    # Exclude removes a row matching Activity OR Signoff Group.
    assert excluded["Portfolio"].tolist() == []
    pd.testing.assert_frame_equal(
        apply_pl_explorer_filters(frame, pl_explorer_filter_map([[], [], [], [], []])),
        original,
    )
    pd.testing.assert_frame_equal(frame, original)


def test_validate_pl_table_uses_risk_explorer_chevrons_at_truthful_comparison_grain() -> (
    None
):
    comparison = build_validate_pl_comparison(_raw_risk(), _colossus())
    open_paths = [
        _token(**{"SignoffGroup": "SOG-A"}),
        _token(**{"SignoffGroup": "SOG-A", "Risk Type": "IR"}),
        _token(
            **{
                "SignoffGroup": "SOG-A",
                "Risk Type": "IR",
                "Risk Greek": "Delta",
            }
        ),
        _token(
            **{
                "SignoffGroup": "SOG-A",
                "Risk Type": "IR",
                "Risk Greek": "Delta",
                "Underlying": "USD-SOFR",
            }
        ),
        _token(
            **{
                "SignoffGroup": "SOG-A",
                "Risk Type": "IR",
                "Risk Greek": "Delta",
                "Underlying": "USD-SOFR",
                "Product": "XVA",
            }
        ),
    ]

    table = build_validate_pl_table(comparison, open_paths=open_paths)
    headers = [
        component.children
        for component in _walk(table)
        if isinstance(component, html.Th) and component.scope == "col"
    ]
    row_labels = [
        component.children
        for component in _walk(table)
        if isinstance(component, html.Span)
        and getattr(component, "className", None) == "row-label-text"
    ]
    toggles = [
        component
        for component in _walk(table)
        if isinstance(component, html.Button)
        and getattr(component, "className", None) == "row-toggle"
    ]

    assert headers == ["Index", "Risk", "dRisk", "P", "C"]
    assert row_labels == [
        "TOTAL",
        "SOG-A",
        "IR",
        "Delta",
        "USD-SOFR",
        "XVA",
        "BOOK-A",
    ]
    assert [toggle.children for toggle in toggles] == ["−"] * 5 + [""]
    colossus_cells = [
        component.to_plotly_json()["props"]
        for component in _walk(table)
        if isinstance(component, html.Td)
        and component.to_plotly_json()["props"].get("data-metric") == "colossus"
    ]
    assert [float(props["data-copy-value"]) for props in colossus_cells] == [12.0] * 7
    # C appears once at each visible hierarchy level, never once per archived
    # 1Y and 2Y tenor row.
    assert not any(label in {"1Y", "2Y"} for label in row_labels)


def test_validate_pl_table_keeps_unmapped_colossus_out_of_mapped_total() -> None:
    colossus = pd.concat(
        [
            _colossus(),
            pd.DataFrame(
                [["BOOK-Z", "GBP-SONIA", "IR", "Delta", 7.0]],
                columns=["Portfolio", "Underlying", "Risk Type", "Risk Greek", "PL"],
            ),
        ],
        ignore_index=True,
    )
    table = build_validate_pl_table(build_validate_pl_comparison(_raw_risk(), colossus))

    summaries = [
        item.children for item in _walk(table) if isinstance(item, html.Summary)
    ]
    assert summaries == ["Unmapped Colossus (1)"]
    mapped_table = next(
        item
        for item in _walk(table)
        if isinstance(item, html.Table)
        and getattr(item, "className", None) == "risk-table validate-pl-table"
    )
    mapped_total_c = next(
        item
        for item in _walk(mapped_table)
        if isinstance(item, html.Td)
        and item.to_plotly_json()["props"].get("data-metric") == "colossus"
    )
    assert float(mapped_total_c.to_plotly_json()["props"]["data-copy-value"]) == 12.0
    unmapped_table = next(
        item
        for item in _walk(table)
        if isinstance(item, html.Table)
        and getattr(item, "className", None) == "risk-table validate-pl-unmapped-table"
    )
    unmapped_metrics = [
        item.to_plotly_json()["props"]
        for item in _walk(unmapped_table)
        if isinstance(item, html.Td)
        and item.to_plotly_json()["props"].get("data-metric") in {"pl", "colossus"}
    ]
    assert [item["data-copy-value"] for item in unmapped_metrics] == ["", "7.0"]


def test_validate_pl_open_state_is_page_local_normalized_and_prunes_descendants() -> (
    None
):
    risk_type = _token(**{"SignoffGroup": "SOG-A", "Risk Type": "IR"})
    greek = _token(
        **{
            "SignoffGroup": "SOG-A",
            "Risk Type": "IR",
            "Risk Greek": "Delta",
        }
    )
    malformed = '{"tenor swap":"1Y"}'

    assert normalize_validate_pl_open_paths([greek, malformed, greek]) == [greek]
    assert toggle_validate_pl_open_paths([risk_type, greek], risk_type) == []


def test_validate_pl_is_a_lazy_section_immediately_above_histo_pl() -> None:
    section = build_validate_pl_section()
    picker = next(
        component
        for component in _walk(section)
        if isinstance(component, dcc.Dropdown) and component.id == "pl-validate-date"
    )
    summaries = [
        component.children
        for component in _walk(html.Div(build_pl_send_sections()))
        if isinstance(component, html.Summary)
    ]

    assert isinstance(section, html.Details)
    assert section.children[0].children == "Validate P&L"
    assert picker.options == []
    assert picker.value is None
    assert picker.disabled is True
    assert summaries.index("Validate P&L") + 1 == summaries.index("Histo P&L")


def test_validate_pl_discovers_and_renders_only_completed_dates_when_opened(
    tmp_path,
    monkeypatch,
) -> None:
    snapshot = SimpleNamespace(
        revision=1,
        refreshed_at=datetime(2026, 8, 14, 22, 5, tzinfo=timezone.utc),
        system_date=pd.Timestamp("2026-08-14"),
        market_date=pd.Timestamp("2026-08-14"),
        market_status="OFFICIAL",
        dashboard_frame=_raw_risk(),
        errors=(),
    )
    archive_official_snapshot(snapshot, lambda _date: _colossus(), tmp_path)
    incomplete = tmp_path / "2026-08-15"
    incomplete.mkdir(parents=True)

    app = Dash(__name__)
    app.layout = build_validate_pl_section()
    register_validate_pl_callbacks(app, tmp_path)
    key = next(key for key in app.callback_map if "pl-validate-date.options" in key)
    discover = app.callback_map[key]["callback"].__wrapped__

    options, selected, disabled, status = discover(1, None)

    assert options == [{"label": "2026-08-14", "value": "2026-08-14"}]
    assert selected == "2026-08-14"
    assert disabled is False
    assert status == ""

    render_key = next(
        key for key in app.callback_map if "pl-validate-table.children" in key
    )
    render = app.callback_map[render_key]["callback"].__wrapped__
    monkeypatch.setattr(
        validate_pl_module,
        "ctx",
        SimpleNamespace(triggered_id="pl-validate-date"),
    )

    table, render_status, open_paths = render(
        selected,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )

    assert "Official 2026-08-14" in render_status
    assert open_paths == []
    assert any(
        isinstance(component, html.Table)
        and getattr(component, "className", None) == "risk-table validate-pl-table"
        for component in _walk(table)
    )


def test_checked_in_synthetic_archive_is_discoverable_and_renders_validate_pl(
    monkeypatch,
) -> None:
    history_root = Path(__file__).resolve().parents[1] / "data" / "histo"
    demo_date = "2026-08-10"
    app = Dash(__name__)
    app.layout = build_validate_pl_section()
    register_validate_pl_callbacks(app, history_root)
    catalog_key = next(
        key for key in app.callback_map if "pl-validate-date.options" in key
    )
    discover = app.callback_map[catalog_key]["callback"].__wrapped__

    options, selected, disabled, status = discover(1, None)

    assert {option["value"] for option in options} == {demo_date}
    assert selected == demo_date
    assert disabled is False
    assert status == ""

    render_key = next(
        key for key in app.callback_map if "pl-validate-table.children" in key
    )
    render = app.callback_map[render_key]["callback"].__wrapped__
    monkeypatch.setattr(
        validate_pl_module,
        "ctx",
        SimpleNamespace(triggered_id="pl-validate-date"),
    )

    table, render_status, open_paths = render(
        selected,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )

    assert "Official 2026-08-10" in render_status
    assert "matched" in render_status
    assert open_paths == []
    labels = [
        component.children
        for component in _walk(table)
        if isinstance(component, html.Span)
        and getattr(component, "className", None) == "row-label-text"
    ]
    assert labels == ["TOTAL", "DEMO-SOG"]


def test_validate_callback_uses_explorer_filter_and_resets_open_paths(
    tmp_path,
    monkeypatch,
) -> None:
    snapshot = SimpleNamespace(
        revision=1,
        refreshed_at=datetime(2026, 8, 14, 22, 5, tzinfo=timezone.utc),
        system_date=pd.Timestamp("2026-08-14"),
        market_date=pd.Timestamp("2026-08-14"),
        market_status="OFFICIAL",
        dashboard_frame=_raw_risk(),
        errors=(),
    )
    colossus = pd.concat(
        [
            _colossus(),
            pd.DataFrame(
                [["BOOK-Z", "GBP-SONIA", "IR", "Delta", 7.0]],
                columns=["Portfolio", "Underlying", "Risk Type", "Risk Greek", "PL"],
            ),
        ],
        ignore_index=True,
    )
    archive_official_snapshot(snapshot, lambda _date: colossus, tmp_path)
    app = Dash(__name__)
    app.layout = build_validate_pl_section()
    register_validate_pl_callbacks(app, tmp_path)
    render = next(
        metadata["callback"].__wrapped__
        for metadata in app.callback_map.values()
        if "pl-validate-table.children" in str(metadata["output"])
    )
    monkeypatch.setattr(
        validate_pl_module,
        "ctx",
        SimpleNamespace(triggered_id=PL_EXPLORER_FILTER_IDS["portfolio"]),
    )

    table, status, open_paths = render(
        "2026-08-14",
        [],
        [],
        ["book-z"],
        [],
        [],
        [],
        [],
        [],
        [_token(**{"SignoffGroup": "SOG-A"})],
    )

    assert "1 filtered rows" in status
    assert "0 mapped" in status
    assert "1 Unmapped Colossus" in status
    assert open_paths == []
    assert "Unmapped Colossus (1)" in [
        item.children for item in _walk(table) if isinstance(item, html.Summary)
    ]
