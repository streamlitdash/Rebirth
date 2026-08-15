"""Cold-start shell, worker ownership, watchdog, and failure tests."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from feeds.s01_sources import build_production_refresh_manager
from ui import s07_events as events
from ui.s05_staticdata import STATIC_FILE_OPTIONS, build_static_data_page
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


def test_manager_backed_app_paints_loading_shell_before_source_io() -> None:
    manager = build_production_refresh_manager()

    app = build_app(refresh_manager=manager)
    response = app.server.test_client().get("/_dash-layout")
    health = app.server.test_client().get("/healthz").get_json()

    assert response.status_code == 200
    assert b"initial-load-trigger" in response.data
    assert b"refresh-progress-function" in response.data
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


def test_layout_response_schedules_refresh_and_warm_layout_recovers() -> None:
    manager = build_production_refresh_manager()
    app = build_app(refresh_manager=manager)
    client = app.server.test_client()

    cold = client.get("/_dash-layout")
    assert cold.status_code == 200
    assert b"initial-load-trigger" in cold.data

    coordinator = app.server.config[STARTUP_COORDINATOR_CONFIG_KEY]
    _wait_for_phase(coordinator, "succeeded")
    warm = client.get("/_dash-layout")

    assert manager.health.revision == 1
    assert warm.status_code == 200
    assert b"initial-load-trigger" not in warm.data
    assert b"risk-type-tabs" in warm.data


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
    endpoint_props = layout.get_json()["props"]["children"][1]["props"]
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

    assert coordinator is not None
    assert coordinator.status().phase == "succeeded"
    assert "cube-page-container.children" in app.callback_map


def test_every_callback_output_has_one_nonduplicate_owner() -> None:
    app = build_app(refresh_manager=build_production_refresh_manager())
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
    assert len(owners[("data-revision-store", "data")]) == 1
    assert len(owners[("cube-page-container", "children")]) == 1


def test_static_data_route_owns_a_separate_page_and_concerto_catalog() -> None:
    app = build_app(refresh_manager=build_production_refresh_manager())
    route_metadata = next(
        metadata
        for metadata in app.callback_map.values()
        if any(
            output.component_id == "static-data-page-container"
            and output.component_property == "style"
            for output in _callback_outputs(metadata)
        )
    )
    route = route_metadata["callback"].__wrapped__

    assert route("/") == (
        {},
        {"display": "none"},
        "app-nav-link cube-nav-link is-active",
        "app-nav-link cube-nav-link",
    )
    assert route("/static-data") == (
        {"display": "none"},
        {},
        "app-nav-link cube-nav-link",
        "app-nav-link cube-nav-link is-active",
    )

    options = {option["value"] for option in STATIC_FILE_OPTIONS}
    assert "s08_concerto.csv" in options
    assert "s08_plsend.csv" not in options
    page_ids = {getattr(item, "id", None) for item in _walk(build_static_data_page())}
    assert {"static-data-page", "static-data-file-selector"} <= page_ids
