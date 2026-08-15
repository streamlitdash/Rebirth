"""Force-date browser lifecycle and readiness-label regressions."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from dash import html

from core.s02_pipeline import PRODUCT_SPECS_BY_SOURCE_TYPE, RiskRefreshManager
from tools.s01_fixtures import build_datasets, validate_datasets
from ui.s04_components import build_risk_date_editor


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
    return SimpleNamespace(
        system_date=pd.Timestamp("2026-08-15"),
        market_date=pd.Timestamp("2026-08-14"),
        market_status="OFFICIAL",
        checker_date=pd.Timestamp("2026-08-13"),
        forced_view_date=None,
        forced_dates={},
        risk_checker_enabled=True,
        risk_status=pd.DataFrame(
            [
                {
                    "Source Type": "fx/delta",
                    "Age": 0,
                    "Age Defaulted": False,
                    "Suggested Risk Date": pd.Timestamp("2026-08-13"),
                    "Effective Risk Date": pd.Timestamp("2026-08-13"),
                },
                {
                    "Source Type": "fx/gamma",
                    "Age": 0,
                    "Age Defaulted": True,
                    "Suggested Risk Date": pd.Timestamp("2026-08-13"),
                    "Effective Risk Date": pd.Timestamp("2026-08-13"),
                },
            ]
        ),
    )


def test_force_date_lock_waits_for_dash_to_receive_the_click() -> None:
    source = (Path(__file__).parents[1] / "assets" / "s02_app.js").read_text(
        encoding="utf-8"
    )
    start = source.index("const startRefreshProgress = (mode) =>")
    state_created = source.index("refreshProgressState = {", start)
    deferred_lock = source.index("const stateForDateActionLock", state_created)
    lifecycle_sync = source.index("syncRefreshLifecycleNodes();", deferred_lock)
    section = source[start:lifecycle_sync]

    assert "capture phase" in section
    assert state_created < deferred_lock
    assert "setTimeout(() => {" in source[deferred_lock:lifecycle_sync]
    assert "if (refreshProgressState !== stateForDateActionLock) return;" in section
    assert "setProps(id, { disabled: true })" in section
    assert "setProps(id, { disabled: true })" not in source[start:state_created]


def test_readiness_fallback_label_explains_why_age_zero_was_synthesised() -> None:
    editor = build_risk_date_editor(_snapshot(), {}, {})
    age_cells = [
        component
        for component in _walk(editor)
        if isinstance(component, html.Td)
        and str(getattr(component, "children", "")).startswith("0")
    ]

    assert any(cell.children == "0" for cell in age_cells)
    fallback = next(cell for cell in age_cells if cell.children == "0 (T-1 fallback)")
    assert "did not report" in fallback.title

    visible_text = " ".join(
        str(component) for component in _walk(editor) if isinstance(component, str)
    )
    assert (
        "T-1 fallback appears only when RiskChecker omits a configured pair"
        in visible_text
    )


def test_fake_readiness_explicitly_reports_fx_gamma_age_zero() -> None:
    datasets = build_datasets()
    validate_datasets(datasets)
    rows = datasets["s01_readiness.csv"]

    assert len(rows) == len(PRODUCT_SPECS_BY_SOURCE_TYPE)
    assert [
        row for row in rows if (row["Risk Type"], row["Risk Greek"]) == ("FX", "Gamma")
    ] == [{"Risk Type": "FX", "Risk Greek": "Gamma", "Age": "0"}]

    checked_in = pd.read_csv(Path(__file__).parents[1] / "data" / "s01_readiness.csv")
    validated = RiskRefreshManager._validate_risk_readiness(checked_in)
    fx_gamma = validated.loc[validated["Source Type"].eq("fx/gamma")].iloc[0]
    assert int(fx_gamma["Age"]) == 0
    assert bool(fx_gamma["Age Defaulted"]) is False
