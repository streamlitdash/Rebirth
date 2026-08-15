"""PL disclosure laziness and application-factory boundary checks."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from dash import Dash, dcc, html, no_update

from core.s04_pl import CONCERTO_FIELD
from core.s05_storage import LocalCsvAdjustmentRepository
from feeds.s01_sources import build_production_refresh_manager
from ui import s08_plevents as pl_events
from ui.s06_plview import build_pl_send_sections
from ui.s08_plevents import PLSendConfig
from ui.s09_factory import build_app


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
            ["2026-07-18", "BOOK-A", "irdeltaeffect", 10.0],
            ["2026-07-19", "BOOK-A", "irdeltaeffect", 12.0],
            ["2026-07-19", "BOOK-A", "fxdeltaeffect", -3.0],
            ["2026-07-19", "BOOK-B", "irdeltaeffect", 7.0],
        ],
        columns=["Market Date", "Portfolio", CONCERTO_FIELD, "PL"],
    )


def _config(tmp_path: Path) -> PLSendConfig:
    history_source = tmp_path / "historical.csv"
    _history_frame().to_csv(history_source, index=False)
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
                CONCERTO_FIELD: "irdeltaeffect",
                "PL": 20.0,
                "Adjustment": False,
            },
            {
                "Market Date": "2026-07-20",
                "Risk Type": "FX",
                "Risk Greek": "Delta",
                "Portfolio": "BOOK-A",
                "SignoffGroup": "SOG-A",
                CONCERTO_FIELD: "fxdeltaeffect",
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
        "P&L Preview",
        "SOG P&L",
        "Portfolio P&L",
        "Write PL to S3",
        "Histo Data",
    ]
    assert all(
        [item for item in _walk(detail) if isinstance(item, html.Details)] == [detail]
        for detail in details
    )
    assert "pl-workflow-summary" not in _string_ids(html.Div(sections))


def test_histo_data_is_lazy_and_filters_chart_and_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _manager = _registered_pl_app(tmp_path)
    history = _callback(app, "pl-history-grid.data")
    real_loader = pl_events.load_historical_pl

    def forbidden(*_args, **_kwargs):
        raise AssertionError("closed Histo Data performed file work")

    monkeypatch.setattr(pl_events, "load_historical_pl", forbidden)
    closed = history(0, [], [])
    assert closed[3] == []
    assert closed[4] == "Open Histo Data to load its validated rows."

    monkeypatch.setattr(pl_events, "load_historical_pl", real_loader)
    portfolio_options, concerto_options, figure, rows, status = history(
        1,
        ["BOOK-A"],
        ["irdeltaeffect"],
    )
    assert [option["value"] for option in portfolio_options] == ["BOOK-A", "BOOK-B"]
    assert [option["value"] for option in concerto_options] == [
        "fxdeltaeffect",
        "irdeltaeffect",
    ]
    assert len(rows) == 2
    assert {row["Portfolio"] for row in rows} == {"BOOK-A"}
    assert {row[CONCERTO_FIELD] for row in rows} == {"irdeltaeffect"}
    assert len(figure.data) == 1
    assert "2 of 4 validated daily rows" in status


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

    config = PLSendConfig(
        mapping_source=Path("data/s08_concerto.csv"),
        adjustment_repository=LocalCsvAdjustmentRepository(tmp_path / "adjustments"),
        saved_directory=tmp_path / "saved",
        send_sog_pl=lambda _frame: None,
        send_portfolio_pl=lambda _frame: None,
    )
    with_pl = build_app(refresh_manager=manager, pl_send_config=config)
    with_page = _native_page(with_pl)
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


def test_static_app_rejects_inert_pl_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PL send configuration requires"):
        build_app(data=pd.DataFrame(), pl_send_config=_config(tmp_path))
