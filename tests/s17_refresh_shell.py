"""Persistent refresh lifecycle checks for native Dash Pages."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from dash import no_update
from feeds.s01_sources import build_production_refresh_manager
from ui.s04_components import (
    build_initial_load_layout,
    build_shared_refresh_shell,
)
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


def _by_id(component: object, component_id: str) -> object:
    return next(
        item for item in _walk(component) if getattr(item, "id", None) == component_id
    )


def _callback_outputs(metadata: dict) -> list[object]:
    output = metadata["output"]
    return list(output) if isinstance(output, (list, tuple)) else [output]


def _callback_for_output(app, component_id: str, component_property: str) -> dict:
    return next(
        metadata
        for metadata in app.callback_map.values()
        if any(
            output.component_id == component_id
            and output.component_property == component_property
            for output in _callback_outputs(metadata)
        )
    )


def test_shared_shell_has_neutral_bootstrap_and_error_modes() -> None:
    neutral = build_shared_refresh_shell(
        None,
        refresh_enabled=True,
        initial_loading=False,
        style={"display": "none"},
    )
    assert neutral.id == "shared-refresh-shell"
    assert neutral.style == {"display": "none"}
    assert _by_id(neutral, "refresh-status").className == "refresh-status"
    assert _by_id(neutral, "refresh-progress").hidden is True
    assert _by_id(neutral, "shared-refresh-bootstrap-interval").disabled is True

    loading = build_shared_refresh_shell(
        None,
        refresh_enabled=True,
        initial_loading=True,
    )
    assert "is-refreshing" in _by_id(loading, "refresh-status").className
    assert _by_id(loading, "refresh-progress").hidden is False
    assert _by_id(loading, "shared-refresh-bootstrap-interval").disabled is False

    stalled = build_shared_refresh_shell(
        None,
        refresh_enabled=True,
        initial_error="Connector is still running",
        keep_polling=True,
    )
    assert "is-error" in _by_id(stalled, "refresh-status").className
    assert _by_id(stalled, "error-log").children == "Connector is still running"
    assert _by_id(stalled, "shared-refresh-bootstrap-interval").disabled is False

    committed = SimpleNamespace(
        revision=4,
        refreshed_at=datetime.now(timezone.utc),
        forced_dates={},
        forced_view_date=None,
        market_date=pd.Timestamp("2026-08-14"),
        market_status="ready",
        risk_dates={"ir/delta": pd.Timestamp("2026-08-14")},
        commodity_market_enabled=False,
        risk_checker_enabled=True,
        risk_status=pd.DataFrame({"Age": [0], "Force Risk": [False]}),
    )
    deferred = build_shared_refresh_shell(
        committed,
        refresh_enabled=True,
        data_revision=2,
    )
    assert _by_id(deferred, "data-revision-store").data == 2
    assert _by_id(deferred, "refresh-commit-revision").children == 4


def test_cold_risk_body_can_exclude_every_shared_lifecycle_id() -> None:
    page = build_initial_load_layout(include_shared_refresh_shell=False)
    page_ids = {getattr(item, "id", None) for item in _walk(page)}
    assert {
        "initial-load-trigger",
        "initial-load-retry",
        "initial-load-message",
    } <= page_ids
    assert {
        "shared-refresh-shell",
        "data-revision-store",
        "refresh-commit-revision",
        "refresh-control-strip",
        "refresh-progress",
        "error-log",
        "auto-refresh-interval",
        "shared-refresh-bootstrap-interval",
    }.isdisjoint(page_ids)

    # The default remains a standalone-compatible composition for existing
    # callers that do not mount a factory-level shell.
    standalone = build_initial_load_layout()
    standalone_ids = [getattr(item, "id", None) for item in _walk(standalone)]
    assert standalone_ids.count("shared-refresh-shell") == 1
    assert standalone_ids.count("refresh-progress") == 1


def test_startup_page_and_shared_shell_have_independent_callback_outputs() -> None:
    app = build_app(refresh_manager=build_production_refresh_manager())
    page_callback = _callback_for_output(app, "cube-page-container", "children")
    shell_callback = _callback_for_output(app, "shared-refresh-shell", "children")
    refresh_callback = _callback_for_output(app, "refresh-commit-revision", "children")

    assert page_callback is not shell_callback
    assert [
        (output.component_id, output.component_property)
        for output in _callback_outputs(page_callback)
    ] == [("cube-page-container", "children")]
    assert [
        (output.component_id, output.component_property)
        for output in _callback_outputs(shell_callback)
    ] == [("shared-refresh-shell", "children")]
    assert {(item["id"], item["property"]) for item in shell_callback["inputs"]} == {
        ("initial-load-trigger", "n_intervals"),
        ("initial-load-retry", "n_clicks"),
        ("shared-refresh-bootstrap-interval", "n_intervals"),
    }
    assert all(
        item.get("allow_optional") is True
        for item in shell_callback["inputs"]
        if item["id"].startswith("initial-load-")
    )
    force_apply = next(
        item
        for item in refresh_callback["inputs"]
        if item["id"] == "force-risk-apply-button"
    )
    assert force_apply.get("allow_optional") is True

    draft_callback = _callback_for_output(app, "force-risk-draft-store", "data")
    draft_mount = next(
        item for item in draft_callback["inputs"] if item["id"] == "risk-date-editor"
    )
    assert draft_mount.get("allow_optional") is True

    actions_callback = _callback_for_output(app, "force-risk-edit-status", "children")
    actions_mount = next(
        item
        for item in actions_callback["inputs"]
        if item["id"] == "force-risk-apply-button" and item["property"] == "id"
    )
    assert actions_mount.get("allow_optional") is True


def test_operating_dates_stay_neutral_before_the_cold_start_commits() -> None:
    app = build_app(refresh_manager=build_production_refresh_manager())
    metadata = _callback_for_output(app, "operating-date-banner", "children")

    assert metadata["callback"].__wrapped__(0) is no_update


def test_browser_defers_start_and_revision_signals_off_risk_page() -> None:
    source = (Path(__file__).parents[1] / "assets" / "s02_app.js").read_text(
        encoding="utf-8"
    )
    assert 'document.getElementById("shared-refresh-shell")' in source
    assert "shell.getClientRects().length > 0" in source
    assert "running && !refreshProgressState && lifecycleVisible" in source
    assert '!document.getElementById("cube-page-container")' in source
    assert '!document.getElementById("risk-type-tabs")' in source
    assert 'document.getElementById("cube-page-container")' in source
    assert '&& document.getElementById("risk-type-tabs")' in source
    assert "syncCommittedDataRevision(lastBackendProgress);" in source
