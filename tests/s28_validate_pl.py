"""Official historical Risk comparison and Validate P&L regressions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from dash import Dash, dcc, html

from core.s11_risk_archive import archive_official_snapshot
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


def test_validate_pl_table_uses_risk_explorer_chevrons_at_truthful_comparison_grain() -> (
    None
):
    comparison = build_validate_pl_comparison(_raw_risk(), _colossus())
    open_paths = [
        _token(**{"risk type": "IR"}),
        _token(**{"risk type": "IR", "risk greek": "Delta"}),
        _token(
            **{
                "risk type": "IR",
                "risk greek": "Delta",
                "underlying": "USD-SOFR",
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
    assert row_labels == ["TOTAL", "IR", "Delta", "USD-SOFR", "BOOK-A"]
    assert [toggle.children for toggle in toggles] == ["−", "−", "−", ""]
    colossus_cells = [
        component.to_plotly_json()["props"]
        for component in _walk(table)
        if isinstance(component, html.Td)
        and component.to_plotly_json()["props"].get("data-metric") == "colossus"
    ]
    assert [float(props["data-copy-value"]) for props in colossus_cells] == [12.0] * 5
    # C appears once at each visible aggregate level (total/type/Greek/
    # underlying/Portfolio), never once per archived 1Y and 2Y tenor row.
    assert not any(label in {"1Y", "2Y"} for label in row_labels)


def test_validate_pl_open_state_is_page_local_normalized_and_prunes_descendants() -> (
    None
):
    risk_type = _token(**{"risk type": "IR"})
    greek = _token(**{"risk type": "IR", "risk greek": "Delta"})
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

    table, render_status, open_paths = render(selected, [], [], [])

    assert "Official 2026-08-14" in render_status
    assert open_paths == []
    assert any(
        isinstance(component, html.Table)
        and getattr(component, "className", None) == "risk-table validate-pl-table"
        for component in _walk(table)
    )
