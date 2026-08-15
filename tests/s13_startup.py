"""Cold-start shell, worker ownership, watchdog, and failure tests."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from dash import page_registry
from dash.exceptions import UnsupportedRelativePath

from feeds.s01_sources import build_production_refresh_manager
from ui import s07_events as events
from ui import s09_factory as factory
from ui.s05_staticdata import (
    STATIC_FILE_OPTIONS,
    build_static_data_page,
    build_static_data_table,
)
from ui.s07_events import STARTUP_COORDINATOR_CONFIG_KEY, StartupCoordinator
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


def _component_id_key(component_id: object) -> str:
    if isinstance(component_id, dict):
        return json.dumps(
            component_id,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return str(component_id)


def _callback_outputs(metadata: dict) -> list[object]:
    output = metadata["output"]
    return list(output) if isinstance(output, (list, tuple)) else [output]


def _callback_for_output(app, component_id: str, component_property: str):
    metadata = next(
        metadata
        for metadata in app.callback_map.values()
        if any(
            output.component_id == component_id
            and output.component_property == component_property
            for output in _callback_outputs(metadata)
        )
    )
    return metadata["callback"].__wrapped__


def _native_page(
    app,
    page_path: str = "/",
    *,
    browser_path: str | None = None,
):
    """Materialize one native page using this app's Flask service context."""
    routes_prefix = app.config.routes_pathname_prefix
    layout_path = f"{routes_prefix}_dash-layout"
    response = app.server.test_client().get(layout_path)
    assert response.status_code == 200

    route = _callback_for_output(app, "_pages_content", "children")
    pathname = (
        app.get_relative_path(page_path) if browser_path is None else browser_path
    )
    with app.server.test_request_context(layout_path):
        return route(pathname, "")


def _wait_for_phase(
    coordinator: StartupCoordinator,
    phase: str,
    *,
    timeout: float = 3.0,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = coordinator.status()
        if status.phase == phase:
            return status
        time.sleep(0.01)
    raise AssertionError(
        f"startup phase did not become {phase!r}; last={coordinator.status()}"
    )


class _StartupManager:
    def __init__(self, *, blocker: Event | None = None, error: Exception | None = None):
        self._blocker = blocker
        self._error = error
        self.calls = 0
        self.stage_delays = {}
        self.health = SimpleNamespace(
            revision=0,
            refreshed_at=None,
            last_attempt_at=None,
            active_error_count=0,
        )
        self.progress = SimpleNamespace(
            attempt_id="refresh-attempt-1",
            function_name="get_ir_delta_market_status",
            source_type="ir/delta",
            underlying="USD SOFR",
            product_label="IR Delta",
            product_index=1,
            product_total=16,
            hold_seconds=0.0,
            stage="market_status",
            current=1,
            total=16,
            message="Waiting for current market connector.",
            running=True,
            error=None,
            started_at=None,
            updated_at=None,
            finished_at=None,
        )

    def refresh(self, **_kwargs):
        self.calls += 1
        if self._blocker is not None:
            assert self._blocker.wait(timeout=2.0)
        if self._error is not None:
            raise self._error
        self.health.revision = 1


def test_manager_backed_risk_page_mounts_loading_shell_before_source_io() -> None:
    manager = build_production_refresh_manager()

    app = build_app(refresh_manager=manager)
    response = app.server.test_client().get("/_dash-layout")
    health = app.server.test_client().get("/healthz").get_json()
    base_layout = app.layout() if callable(app.layout) else app.layout
    base_components = list(_walk(base_layout))
    base_ids = {
        component_id
        for item in base_components
        if isinstance((component_id := getattr(item, "id", None)), str)
    }
    risk_page, _metadata = _native_page(app)
    risk_ids = {getattr(item, "id", None) for item in _walk(risk_page)}
    shared_shell = next(
        item
        for item in base_components
        if getattr(item, "id", None) == "shared-refresh-shell"
    )
    refresh_progress = next(
        item
        for item in base_components
        if getattr(item, "id", None) == "refresh-progress"
    )
    mounted_ids = [
        _component_id_key(component_id)
        for item in [*base_components, *_walk(risk_page)]
        if (component_id := getattr(item, "id", None)) is not None
    ]

    assert response.status_code == 200
    assert b'"id":"_pages_content"' in response.data
    assert b"initial-load-trigger" not in response.data
    assert b'"id":"static-data-page"' not in response.data
    assert {
        "_pages_location",
        "shared-refresh-shell",
        "refresh-progress-function",
    } <= base_ids
    assert "app-location" not in base_ids
    assert shared_shell.style == {"display": "none"}
    assert refresh_progress.hidden is True
    assert refresh_progress.to_plotly_json()["props"]["data-initial-load"] == "false"
    assert {"cube-page-container", "initial-load-trigger"} <= risk_ids
    assert "refresh-progress-function" not in risk_ids
    assert len(mounted_ids) == len(set(mounted_ids))
    assert manager.health.revision == 0
    assert health["status"] == "starting"
    coordinator = app.server.config[STARTUP_COORDINATOR_CONFIG_KEY]
    assert coordinator.status().phase == "idle"


def test_startup_coordinator_deduplicates_visitors_and_commits_once() -> None:
    manager = _StartupManager()
    coordinator = StartupCoordinator(manager)

    assert coordinator.start() is True
    assert coordinator.start() is False
    status = _wait_for_phase(coordinator, "succeeded")

    assert status.attempt == 1
    assert manager.calls == 1
    assert manager.health.revision == 1


def test_startup_watchdog_names_active_call_without_starting_second_writer(
    monkeypatch,
) -> None:
    blocker = Event()
    manager = _StartupManager(blocker=blocker)
    clock = [10.0]
    monkeypatch.setattr(events, "monotonic", lambda: clock[0])
    coordinator = StartupCoordinator(manager, timeout_seconds=1.0)

    assert coordinator.start() is True
    clock[0] = 12.0
    status = coordinator.status()

    assert status.phase == "stalled"
    assert status.retryable is False
    assert "get_ir_delta_market_status" in str(status.error)
    assert coordinator.start(retry=True) is False
    assert manager.calls == 1

    blocker.set()
    _wait_for_phase(coordinator, "succeeded")


def test_startup_schedule_allows_only_one_pending_timer() -> None:
    blocker = Event()
    manager = _StartupManager(blocker=blocker)
    coordinator = StartupCoordinator(manager)

    assert coordinator.schedule_start(delay_seconds=0.01) is True
    assert all(
        coordinator.schedule_start(delay_seconds=0.01) is False for _ in range(50)
    )

    blocker.set()
    _wait_for_phase(coordinator, "succeeded")
    assert manager.calls == 1


def test_startup_failure_is_visible_and_retryable() -> None:
    manager = _StartupManager(error=RuntimeError("checker service unavailable"))
    coordinator = StartupCoordinator(manager)

    assert coordinator.start() is True
    status = _wait_for_phase(coordinator, "failed")

    assert status.retryable is True
    assert "RuntimeError" in str(status.error)
    assert "checker service unavailable" in str(status.error)


def test_only_mounted_risk_shell_requests_start_and_warm_page_skips_shell() -> None:
    manager = build_production_refresh_manager()
    app = build_app(refresh_manager=manager)
    client = app.server.test_client()

    coordinator = app.server.config[STARTUP_COORDINATOR_CONFIG_KEY]
    base = client.get("/_dash-layout")
    static_page, _metadata = _native_page(app, "/static-data")
    static_ids = {getattr(item, "id", None) for item in _walk(static_page)}

    assert base.status_code == 200
    assert b"initial-load-trigger" not in base.data
    assert "static-data-page" in static_ids
    assert "initial-load-trigger" not in static_ids
    assert coordinator.status().phase == "idle"
    assert manager.health.revision == 0

    cold_page, _metadata = _native_page(app)
    cold_ids = {getattr(item, "id", None) for item in _walk(cold_page)}
    assert "initial-load-trigger" in cold_ids
    assert coordinator.status().phase == "idle"

    start = client.post("/startz")
    assert start.status_code == 200
    assert start.get_json()["started_new_worker"] is True
    _wait_for_phase(coordinator, "succeeded")
    warm_page, _metadata = _native_page(app)
    warm_ids = {
        component_id
        for item in _walk(warm_page)
        if isinstance((component_id := getattr(item, "id", None)), str)
    }

    assert manager.health.revision == 1
    assert "initial-load-trigger" not in warm_ids
    assert "risk-type-tabs" in warm_ids


def test_start_endpoint_is_idempotent_and_progress_has_attempt_identity() -> None:
    blocker = Event()
    manager = _StartupManager(blocker=blocker)
    app = build_app(refresh_manager=manager)
    client = app.server.test_client()

    first = client.post("/startz")
    second = client.post("/startz")
    first_payload = first.get_json()
    second_payload = second.get_json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first_payload["started_new_worker"] is True
    assert second_payload["started_new_worker"] is False
    assert first_payload["revision"] == 0
    assert first_payload["startup_phase"] == "running"
    assert first_payload["startup_attempt_id"]
    assert first_payload["server_boot_id"]
    assert second_payload["startup_attempt_id"] == first_payload["startup_attempt_id"]
    assert second_payload["server_boot_id"] == first_payload["server_boot_id"]

    blocker.set()
    coordinator = app.server.config[STARTUP_COORDINATOR_CONFIG_KEY]
    _wait_for_phase(coordinator, "succeeded")
    complete = client.get("/progressz").get_json()
    assert complete["revision"] == 1
    assert complete["startup_phase"] == "succeeded"
    assert complete["attempt_id"] == "refresh-attempt-1"


def test_public_endpoint_urls_do_not_reuse_internal_route_prefix() -> None:
    manager = build_production_refresh_manager()
    app = build_app(
        refresh_manager=manager,
        dash_kwargs={
            "routes_pathname_prefix": "/internal/",
            "requests_pathname_prefix": "/proxy/internal/",
        },
    )
    client = app.server.test_client()

    layout = client.get("/internal/_dash-layout")

    assert layout.status_code == 200
    layout_component = app.layout() if callable(app.layout) else app.layout
    endpoint = next(
        item
        for item in _walk(layout_component)
        if getattr(item, "id", None) == "backend-endpoints"
    )
    endpoint_props = endpoint.to_plotly_json()["props"]
    assert endpoint_props["data-progress-url"] == "/proxy/internal/progressz"
    assert endpoint_props["data-start-url"] == "/proxy/internal/startz"
    assert client.get("/internal/progressz").status_code == 200
    assert client.post("/internal/startz").status_code == 200


def test_browser_progress_copy_never_claims_an_unconfirmed_refresh() -> None:
    source = (
        Path(__file__).resolve().parent.parent / "assets" / "s02_app.js"
    ).read_text(encoding="utf-8")

    assert "the refresh is still being followed" not in source
    assert "Refresh state is not confirmed" in source
    assert "startup_attempt_id" in source
    assert "baselineRefreshAttemptId" in source
    assert "refreshAttemptMatches" in source
    assert "revisionAdvanced" in source
    assert "progressStartedDuringAttempt" in source
    assert "server_boot_id" in source
    assert 'refreshProgressState.mode === "bootstrap"' in source
    assert "const startupAttemptMatches" in source
    assert "attributeOldValue: true" in source
    assert "transitionedFromRunning" in source
    assert 'state.mode === "bootstrap"' in source
    assert "hasNewError ? 5000 : 300" in source
    assert "revision <= renderedDataRevisionFloor()" in source
    assert 'setProps("data-revision-store", { data: revision })' in source
    revision_sync = source.index("const syncCommittedDataRevision")
    assert (
        'refreshProgressState?.mode === "bootstrap"'
        in source[revision_sync : revision_sync + 500]
    )
    revision_sync_source = source[revision_sync : revision_sync + 1_300]
    assert (
        'const commitNode = document.getElementById("refresh-commit-revision")'
        in revision_sync_source
    )
    assert "normalizedRevision(commitNode?.textContent)" in revision_sync_source
    assert "progress?.running === false" in revision_sync_source
    assert "|| !commitNode" in revision_sync_source
    assert "Math.max" in revision_sync_source
    assert 'document.getElementById("data-revision-store")' not in (
        revision_sync_source
    )
    assert "const claimSessionReload" in source
    assert "dashIsLoading() || Date.now() < handoffDeadline" in source
    assert "cube-bootstrap-ready-reload:${bootId}:${revision}" in source
    assert "cube-progress-transport-reload:${bootId}" in source
    assert "cube-progress-recovery-reload-at" not in source
    assert "Date.now() - lastRecovery >= 60000" not in source
    reload_guard = source.index("disconnectedFor >= 45000")
    assert (
        'refreshProgressState.mode === "bootstrap"'
        in source[reload_guard : reload_guard + 180]
    )
    completion_guard = source.index(
        "// Only the refresh callback's running state gates this"
    )
    assert (
        "if (!running) finishRefreshProgress();"
        in source[completion_guard : completion_guard + 400]
    )


def test_warm_manager_keeps_the_shell_recovery_callback_registered() -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)

    app = build_app(refresh_manager=manager)
    coordinator = app.server.config[STARTUP_COORDINATOR_CONFIG_KEY]
    operating_dates = _callback_for_output(app, "operating-date-banner", "children")(
        manager.health.revision
    )
    risk_page, _metadata = _native_page(app)
    base_layout = app.layout() if callable(app.layout) else app.layout
    components = [*list(_walk(base_layout)), *list(_walk(risk_page))]
    ids = {
        component_id
        for item in components
        if isinstance((component_id := getattr(item, "id", None)), str)
    }
    auto_interval = next(
        item
        for item in components
        if getattr(item, "id", None) == "auto-refresh-interval"
    )

    assert coordinator is not None
    assert coordinator.status().phase == "succeeded"
    assert "cube-page-container.children" in app.callback_map
    assert manager.snapshot.market_date.date().isoformat() in str(operating_dates)
    assert auto_interval.interval == 15 * 60_000
    assert "operating-date-banner" in ids
    assert "unmapped-books-summary" in ids
    assert "raw-data-summary" not in ids


def test_long_financial_callback_cannot_own_live_revision() -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)
    app = build_app(refresh_manager=manager)
    metadata = next(
        metadata
        for metadata in app.callback_map.values()
        if any(
            output.component_id == "refresh-commit-revision"
            for output in _callback_outputs(metadata)
        )
    )
    inputs = {(item["id"], item["property"]) for item in metadata["inputs"]}
    outputs = {
        (output.component_id, output.component_property)
        for output in _callback_outputs(metadata)
    }

    assert inputs == {
        ("auto-refresh-interval", "n_intervals"),
        ("refresh-portfolios-button", "n_clicks"),
        ("refresh-pl-button", "n_clicks"),
        ("reload-risk-button", "n_clicks"),
        ("force-risk-apply-button", "n_clicks"),
        ("commo-market-toggle", "n_clicks"),
        ("risk-checker-toggle", "n_clicks"),
    }
    assert (
        "perspective-risk-cube-commodity-market-v1",
        "data",
    ) not in outputs
    assert ("perspective-risk-cube-risk-checker-v1", "data") not in outputs
    assert ("refresh-commit-revision", "children") in outputs
    assert ("data-revision-store", "data") not in outputs

    layout = app.layout() if callable(app.layout) else app.layout
    commit_revision = next(
        item
        for item in _walk(layout)
        if getattr(item, "id", None) == "refresh-commit-revision"
    )
    assert commit_revision.hidden is True
    assert commit_revision.children == manager.health.revision


def test_composed_app_defaults_to_one_second_risk_product_hold(monkeypatch) -> None:
    monkeypatch.delenv("RISK_PRODUCT_DELAY_SECONDS", raising=False)
    from s01_app import create_app

    app = create_app()
    coordinator = app.server.config[STARTUP_COORDINATOR_CONFIG_KEY]

    assert coordinator._manager.stage_delays == {"risk_product": 1.0}


def test_every_callback_output_has_one_nonduplicate_owner() -> None:
    app = build_app(refresh_manager=build_production_refresh_manager())
    _native_page(app)
    owners: dict[tuple[str, str], list[str]] = defaultdict(list)

    for callback_key, metadata in app.callback_map.items():
        for output in _callback_outputs(metadata):
            identity = (
                _component_id_key(output.component_id),
                output.component_property,
            )
            owners[identity].append(callback_key)
            assert output.allow_duplicate is False

    duplicates = {
        f"{component_id}.{component_property}": callbacks
        for (component_id, component_property), callbacks in owners.items()
        if len(callbacks) != 1
    }
    assert duplicates == {}
    assert len(owners[("risk-grid", "children")]) == 1
    assert ("data-revision-store", "data") not in owners
    assert len(owners[("refresh-commit-revision", "children")]) == 1
    assert len(owners[("cube-page-container", "children")]) == 1
    assert len(owners[("_pages_content", "children")]) == 1
    assert len(owners[("shared-refresh-shell", "children")]) == 1
    assert len(owners[("shared-refresh-shell", "style")]) == 1
    assert ("app-page-container", "children") not in owners
    assert ("cube-page-container", "style") not in owners
    assert ("static-data-page-container", "style") not in owners


def test_native_pages_mount_one_exact_page_and_explicit_404() -> None:
    app = build_app(refresh_manager=build_production_refresh_manager())
    nav = _callback_for_output(app, "cube-nav-link", "className")
    app_layout = app.layout() if callable(app.layout) else app.layout
    primary_navigation = next(
        item
        for item in _walk(app_layout)
        if getattr(item, "className", None) == "cube-nav"
    )

    assert [link.children for link in primary_navigation.children] == [
        "Risk",
        "Stock",
        "P&L",
        "Statics",
    ]

    static_page, metadata = _native_page(app, "/static-data")
    static_ids = {getattr(item, "id", None) for item in _walk(static_page)}
    cube_class, pnl_class, stock_class, static_class, shell_style = nav(
        app.get_relative_path("/static-data/")
    )
    assert metadata == {"title": "Cube — Statics"}
    assert cube_class == "app-nav-link cube-nav-link"
    assert pnl_class == "app-nav-link cube-nav-link"
    assert stock_class == "app-nav-link cube-nav-link"
    assert static_class == "app-nav-link cube-nav-link is-active"
    assert shell_style == {"display": "none"}
    assert {"static-data-page", "static-data-file-selector"} <= static_ids
    assert any(
        getattr(item, "children", None) == "Statics" for item in _walk(static_page)
    )
    assert "cube-page-container" not in static_ids
    assert "initial-load-trigger" not in static_ids

    cube_page, metadata = _native_page(app)
    cube_ids = {getattr(item, "id", None) for item in _walk(cube_page)}
    cube_class, pnl_class, stock_class, static_class, shell_style = nav(
        app.get_relative_path("/")
    )
    assert metadata == {"title": "Cube — Risk"}
    assert cube_class == "app-nav-link cube-nav-link is-active"
    assert pnl_class == "app-nav-link cube-nav-link"
    assert stock_class == "app-nav-link cube-nav-link"
    assert static_class == "app-nav-link cube-nav-link"
    assert shell_style == {}
    assert {"cube-page-container", "initial-load-trigger"} <= cube_ids
    assert "static-data-page" not in cube_ids

    pnl_page, metadata = _native_page(app, "/pnl")
    pnl_ids = {getattr(item, "id", None) for item in _walk(pnl_page)}
    cube_class, pnl_class, stock_class, static_class, shell_style = nav(
        app.get_relative_path("/pnl")
    )
    assert metadata == {"title": "Cube — P&L Sender"}
    assert cube_class == "app-nav-link cube-nav-link"
    assert pnl_class == "app-nav-link cube-nav-link is-active"
    assert stock_class == "app-nav-link cube-nav-link"
    assert static_class == "app-nav-link cube-nav-link"
    assert shell_style == {}
    assert {"pnl-page", "pnl-unavailable"} <= pnl_ids
    assert "cube-page-container" not in pnl_ids

    stock_page, metadata = _native_page(app, "/stock")
    stock_ids = {getattr(item, "id", None) for item in _walk(stock_page)}
    cube_class, pnl_class, stock_class, static_class, shell_style = nav(
        app.get_relative_path("/stock")
    )
    assert metadata == {"title": "Cube — Stock"}
    assert cube_class == "app-nav-link cube-nav-link"
    assert pnl_class == "app-nav-link cube-nav-link"
    assert stock_class == "app-nav-link cube-nav-link is-active"
    assert static_class == "app-nav-link cube-nav-link"
    assert shell_style == {"display": "none"}
    assert {"stock-page", "stock-unavailable"} <= stock_ids
    assert "cube-page-container" not in stock_ids

    not_found, metadata = _native_page(app, "/nested/static-data")
    not_found_ids = {getattr(item, "id", None) for item in _walk(not_found)}
    cube_class, pnl_class, stock_class, static_class, shell_style = nav(
        app.get_relative_path("/nested/static-data")
    )
    return_link = next(
        item
        for item in _walk(not_found)
        if getattr(item, "children", None) == "Return to Risk"
    )
    assert metadata == {"title": "Cube — Page not found"}
    assert cube_class == "app-nav-link cube-nav-link"
    assert pnl_class == "app-nav-link cube-nav-link"
    assert stock_class == "app-nav-link cube-nav-link"
    assert static_class == "app-nav-link cube-nav-link"
    assert shell_style == {"display": "none"}
    assert {"not-found-page", "not-found-page-container"} <= not_found_ids
    assert return_link.href == app.get_relative_path("/")


def test_risk_and_pnl_navigation_share_one_prepared_frame_per_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = build_production_refresh_manager()
    app = build_app(refresh_manager=manager)
    manager.refresh(force_risk=True, force_pl=True)
    calls = 0
    original = factory.prepare_risk_data

    def counted_prepare(frame):
        nonlocal calls
        calls += 1
        return original(frame)

    monkeypatch.setattr(factory, "prepare_risk_data", counted_prepare)

    _native_page(app, "/")
    _native_page(app, "/pnl")
    _native_page(app, "/")
    _native_page(app, "/pnl")
    aggregate = _callback_for_output(app, "pnl-aggregate-pl-grid", "children")
    aggregate("activity", manager.health.revision, [], [])

    assert calls == 1


def test_native_pages_match_the_public_prefix_exactly() -> None:
    app = build_app(
        refresh_manager=build_production_refresh_manager(),
        dash_kwargs={
            "routes_pathname_prefix": "/internal/",
            "requests_pathname_prefix": "/proxy/internal/",
        },
    )
    nav = _callback_for_output(app, "cube-nav-link", "className")

    static_page, _metadata = _native_page(app, "/static-data")
    static_ids = {getattr(item, "id", None) for item in _walk(static_page)}
    cube_class, pnl_class, stock_class, static_class, shell_style = nav(
        "/proxy/internal/static-data/"
    )
    assert cube_class == "app-nav-link cube-nav-link"
    assert pnl_class == "app-nav-link cube-nav-link"
    assert stock_class == "app-nav-link cube-nav-link"
    assert static_class == "app-nav-link cube-nav-link is-active"
    assert shell_style == {"display": "none"}
    assert "static-data-page" in static_ids

    not_found, _metadata = _native_page(app, "/nested/static-data")
    not_found_ids = {getattr(item, "id", None) for item in _walk(not_found)}
    cube_class, pnl_class, stock_class, static_class, shell_style = nav(
        "/proxy/internal/nested/static-data"
    )
    assert cube_class == "app-nav-link cube-nav-link"
    assert pnl_class == "app-nav-link cube-nav-link"
    assert stock_class == "app-nav-link cube-nav-link"
    assert static_class == "app-nav-link cube-nav-link"
    assert shell_style == {"display": "none"}
    assert "not-found-page" in not_found_ids

    with pytest.raises(UnsupportedRelativePath):
        _native_page(app, browser_path="/internal/static-data")


def test_repeated_apps_keep_native_page_services_isolated() -> None:
    cold_manager = build_production_refresh_manager()
    warm_manager = build_production_refresh_manager()
    warm_manager.refresh(force_risk=True, force_pl=True)

    cold_app = build_app(
        refresh_manager=cold_manager,
        dash_kwargs={
            "routes_pathname_prefix": "/cold-internal/",
            "requests_pathname_prefix": "/cold/",
        },
    )
    warm_app = build_app(
        refresh_manager=warm_manager,
        dash_kwargs={
            "routes_pathname_prefix": "/warm-internal/",
            "requests_pathname_prefix": "/warm/",
        },
    )

    assert tuple(page_registry) == (
        "pages.risk",
        "pages.stock",
        "pages.pnl",
        "pages.static_data",
        "pages.not_found_404",
    )
    assert page_registry["pages.static_data"]["name"] == "Statics"
    assert page_registry["pages.static_data"]["title"] == "Cube — Statics"
    assert page_registry["pages.pnl"]["relative_path"] == "/warm/pnl"
    assert page_registry["pages.stock"]["relative_path"] == "/warm/stock"
    assert page_registry["pages.static_data"]["relative_path"] == "/warm/static-data"
    assert cold_app.get_relative_path("/pnl") == "/cold/pnl"
    assert warm_app.get_relative_path("/stock") == "/warm/stock"
    assert cold_app.get_relative_path("/static-data") == "/cold/static-data"
    assert warm_app.get_relative_path("/static-data") == "/warm/static-data"

    # Route the older app after the newer factory reset/re-registered Dash's
    # process-global catalogue. Stable layouts must still resolve the active
    # Flask app's manager rather than capture the latest factory closure.
    cold_page, _metadata = _native_page(cold_app)
    warm_page, _metadata = _native_page(warm_app)
    cold_ids = {
        component_id
        for item in _walk(cold_page)
        if isinstance((component_id := getattr(item, "id", None)), str)
    }
    warm_ids = {
        component_id
        for item in _walk(warm_page)
        if isinstance((component_id := getattr(item, "id", None)), str)
    }

    assert "initial-load-trigger" in cold_ids
    assert "risk-type-tabs" not in cold_ids
    assert "initial-load-trigger" not in warm_ids
    assert "risk-type-tabs" in warm_ids
    assert cold_manager.health.revision == 0
    assert warm_manager.health.revision == 1


def test_static_data_page_defers_its_default_csv_until_callback_mount() -> None:
    page = build_static_data_page()
    page_ids = {getattr(item, "id", None) for item in _walk(page)}
    table_container = next(
        item
        for item in _walk(page)
        if getattr(item, "id", None) == "static-data-table-container"
    )

    assert {"static-data-page", "static-data-file-selector"} <= page_ids
    assert table_container.children is None
    assert not any(
        str(component_id).startswith("static-data-table-")
        and component_id != "static-data-table-container"
        for component_id in page_ids
        if component_id is not None
    )

    options = {option["value"] for option in STATIC_FILE_OPTIONS}
    assert "s08_concerto.csv" in options
    assert "s10_historical_pl.csv" not in options
    assert "s08_plsend.csv" not in options


def test_static_data_columns_use_only_supported_dash_properties() -> None:
    table_layout = build_static_data_table("s01_readiness.csv")
    table = next(
        item
        for item in _walk(table_layout)
        if getattr(item, "id", None) == "static-data-table-s01_readiness"
    )

    assert table.columns
    assert all("resizable" not in column for column in table.columns)
