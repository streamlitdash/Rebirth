"""Dash application factory and HTTP boundary configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from dash import (
    Dash,
    Input,
    Output,
    dcc,
    html,
    page_container,
    page_registry,
    register_page,
)
from flask import jsonify, request

from pages import PAGE_SERVICES_CONFIG_KEY
from pages.not_found_404 import layout as not_found_page_layout
from pages.risk import layout as risk_page_layout
from pages.static_data import layout as static_data_page_layout

from .s03_aggregate import prepare_risk_data
from .s07_events import (
    STARTUP_COORDINATOR_CONFIG_KEY,
    STARTUP_UI_ERROR_CONFIG_KEY,
    StartupCoordinator,
    register_callbacks,
)
from .s04_components import (
    build_initial_load_layout,
    build_layout,
    build_shared_refresh_shell,
)
from .s08_plevents import PLSendConfig, register_pl_send_callbacks
from .s01_contracts import RefreshManagerProtocol


def _register_native_pages() -> None:
    """Install one deterministic page catalogue with stable layout callables."""
    page_registry.clear()
    register_page(
        "pages.risk",
        path="/",
        name="Risk",
        title="Cube — Risk",
        order=0,
        layout=risk_page_layout,
    )
    register_page(
        "pages.static_data",
        path="/static-data",
        name="Static Data",
        title="Cube — Static Data",
        order=1,
        layout=static_data_page_layout,
    )
    register_page(
        "pages.not_found_404",
        path="/404",
        name="Page not found",
        title="Cube — Page not found",
        order=99,
        layout=not_found_page_layout,
    )


def _progress_payload(
    refresh_manager: RefreshManagerProtocol | None,
    startup_coordinator: StartupCoordinator | None = None,
) -> dict[str, Any]:
    """Serialize optional manager progress without touching its snapshot lock."""
    progress: Any = None
    if refresh_manager is not None:
        try:
            progress = refresh_manager.progress
        except Exception:
            progress = None
    try:
        revision = (
            int(refresh_manager.health.revision) if refresh_manager is not None else 0
        )
    except Exception:
        revision = 0

    def timestamp(name: str) -> str | None:
        value = getattr(progress, name, None)
        return value.isoformat() if value is not None else None

    payload = {
        "attempt_id": getattr(progress, "attempt_id", None),
        "function_name": getattr(progress, "function_name", None),
        "source_type": getattr(progress, "source_type", None),
        "underlying": getattr(progress, "underlying", None),
        "product_label": getattr(progress, "product_label", None),
        "product_index": int(getattr(progress, "product_index", 0)),
        "product_total": int(getattr(progress, "product_total", 0)),
        "hold_seconds": float(getattr(progress, "hold_seconds", 0.0)),
        "stage": getattr(progress, "stage", "idle"),
        "current": int(getattr(progress, "current", 0)),
        "total": int(getattr(progress, "total", 0)),
        "message": getattr(
            progress, "message", "No live refresh progress is available."
        ),
        "running": bool(getattr(progress, "running", False)),
        "error": getattr(progress, "error", None),
        "started_at": timestamp("started_at"),
        "updated_at": timestamp("updated_at"),
        "finished_at": timestamp("finished_at"),
        "revision": revision,
        "server_time": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    if startup_coordinator is not None:
        startup = startup_coordinator.status()
        payload.update(
            startup_phase=startup.phase,
            startup_attempt=startup.attempt,
            startup_attempt_id=startup.attempt_id,
            server_boot_id=startup.server_boot_id,
            startup_elapsed_seconds=startup.elapsed_seconds,
            startup_timeout_seconds=startup_coordinator.timeout_seconds,
            startup_retryable=startup.retryable,
        )
        if startup.error:
            payload["error"] = startup.error
    return payload


def build_app(
    data: pd.DataFrame | None = None,
    refresh_manager: RefreshManagerProtocol | None = None,
    *,
    pl_send_config: PLSendConfig | None = None,
    dash_kwargs: Mapping[str, Any] | None = None,
) -> Dash:
    """Create the Dash app from static data or a server-side refresh manager."""
    if data is not None and refresh_manager is not None:
        raise ValueError("Pass either data or refresh_manager, not both")
    if data is None and refresh_manager is None:
        raise ValueError(
            "No real dashboard data source was supplied. Pass a validated DataFrame "
            "or a refresh manager backed by configured real connectors."
        )
    if pl_send_config is not None and refresh_manager is None:
        raise ValueError("PL send configuration requires a refresh manager")

    # A manager-backed app must become reachable before it calls any source.
    # Reuse an already committed snapshot (for example in a warm worker), but
    # leave a cold manager untouched until the browser mounts the loading shell.
    initial_snapshot = None
    if refresh_manager is not None and refresh_manager.health.revision > 0:
        initial_snapshot = refresh_manager.snapshot
        risk_data = prepare_risk_data(initial_snapshot.dashboard_frame)
    elif refresh_manager is not None:
        risk_data = pd.DataFrame()
    else:
        if data is None:
            raise RuntimeError("A static app requires a DataFrame")
        risk_data = prepare_risk_data(data)

    dash_options = dict(dash_kwargs or {})
    # Only the active URL's page body is mounted. Page-specific callback
    # targets therefore enter and leave the layout as navigation occurs.
    dash_options["suppress_callback_exceptions"] = True
    dash_options["use_pages"] = True
    dash_options["pages_folder"] = ""
    app = Dash(
        __name__,
        assets_folder=str(Path(__file__).resolve().parent.parent / "assets"),
        **dash_options,
    )
    app.title = "Cube"
    _register_native_pages()
    app.server.config.setdefault(STARTUP_UI_ERROR_CONFIG_KEY, None)
    startup_coordinator: StartupCoordinator | None = None
    if refresh_manager is not None:
        raw_timeout = os.getenv("CUBE_STARTUP_TIMEOUT_SECONDS", "2400")
        try:
            startup_timeout = float(raw_timeout)
            if startup_timeout <= 0:
                raise ValueError
        except (TypeError, ValueError):
            app.logger.warning(
                "Invalid CUBE_STARTUP_TIMEOUT_SECONDS=%r; using 2400 seconds.",
                raw_timeout,
            )
            startup_timeout = 2400.0
        startup_coordinator = StartupCoordinator(
            refresh_manager,
            timeout_seconds=startup_timeout,
            logger=app.logger,
        )
    app.server.config[STARTUP_COORDINATOR_CONFIG_KEY] = startup_coordinator

    route_prefix = app.config.routes_pathname_prefix or "/"
    request_prefix = app.config.requests_pathname_prefix or route_prefix
    health_path = f"{route_prefix.rstrip('/')}/healthz" or "/healthz"
    progress_path = f"{route_prefix.rstrip('/')}/progressz" or "/progressz"
    start_path = f"{route_prefix.rstrip('/')}/startz" or "/startz"
    public_progress_path = f"{request_prefix.rstrip('/')}/progressz" or "/progressz"
    public_start_path = f"{request_prefix.rstrip('/')}/startz" or "/startz"

    @app.server.get(health_path)
    def health_check():
        health = refresh_manager.health if refresh_manager is not None else None
        progress = _progress_payload(refresh_manager, startup_coordinator)
        startup_ui_error = app.server.config.get(STARTUP_UI_ERROR_CONFIG_KEY)
        startup_phase = progress.get("startup_phase")
        if health is not None and health.revision == 0:
            health_status = (
                "degraded"
                if progress["error"] or startup_phase in {"failed", "stalled"}
                else "starting"
            )
        elif startup_ui_error or (health is not None and health.active_error_count):
            health_status = "degraded"
        else:
            health_status = "ok"
        return jsonify(
            status=health_status,
            revision=health.revision if health is not None else 0,
            last_success=(
                health.refreshed_at.isoformat()
                if health is not None and health.refreshed_at is not None
                else None
            ),
            last_attempt=(
                health.last_attempt_at.isoformat()
                if health is not None and health.last_attempt_at is not None
                else progress["started_at"]
            ),
            active_error_count=(
                (
                    1
                    if health is not None and health.revision == 0 and progress["error"]
                    else health.active_error_count + int(bool(startup_ui_error))
                )
                if health is not None
                else 0
            ),
        )

    @app.server.get(progress_path)
    def refresh_progress():
        return jsonify(_progress_payload(refresh_manager, startup_coordinator))

    @app.server.post(start_path)
    def start_initial_refresh():
        """Idempotently recover a cold worker after first paint or a pod restart."""
        started = False
        if startup_coordinator is not None:
            started = startup_coordinator.start()
        payload = _progress_payload(refresh_manager, startup_coordinator)
        payload["start_requested"] = True
        payload["started_new_worker"] = started
        return jsonify(payload)

    @app.server.after_request
    def secure_dashboard_responses(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if (
            request.path in {health_path, progress_path, start_path}
            or request.path.endswith("_dash-layout")
            or request.path.endswith("_dash-update-component")
            or response.mimetype == "application/json"
        ):
            response.headers["Cache-Control"] = "no-store, private"
        return response

    stage_delays = refresh_manager.stage_delays if refresh_manager is not None else None
    cube_href = app.get_relative_path("/")
    static_data_href = app.get_relative_path("/static-data")

    def current_cube_page():
        """Serve the shell cold and the complete dashboard after revision 1."""
        if refresh_manager is not None:
            try:
                if refresh_manager.health.revision > 0:
                    snapshot = refresh_manager.snapshot
                    prepared = prepare_risk_data(snapshot.dashboard_frame)
                    return build_layout(
                        prepared,
                        snapshot,
                        refresh_enabled=True,
                        pl_enabled=pl_send_config is not None,
                        stage_delays=stage_delays,
                        include_shared_refresh_shell=False,
                    )
            except Exception as error:
                app.logger.exception(
                    "Could not materialize the committed startup snapshot: %s",
                    type(error).__name__,
                )
                return build_initial_load_layout(
                    stage_delays=stage_delays,
                    include_shared_refresh_shell=False,
                    error=(
                        "The validated data loaded, but the dashboard could not be "
                        "rendered. Check the server log and retry."
                    ),
                )
            return build_initial_load_layout(
                stage_delays=stage_delays,
                include_shared_refresh_shell=False,
            )
        return build_layout(
            risk_data,
            initial_snapshot,
            refresh_enabled=False,
            pl_enabled=False,
            stage_delays=stage_delays,
            include_shared_refresh_shell=False,
        )

    def cube_page_body() -> html.Main:
        """Mount the revision-aware Risk page under its stable callback owner."""
        return html.Main(current_cube_page(), id="cube-page-container")

    def current_shared_snapshot():
        """Return only a snapshot already committed by this app's manager."""
        if refresh_manager is not None:
            try:
                if refresh_manager.health.revision > 0:
                    return refresh_manager.snapshot
            except Exception:
                return initial_snapshot
        return initial_snapshot

    app.server.config[PAGE_SERVICES_CONFIG_KEY] = {
        "cube_href": cube_href,
        "risk_page_builder": cube_page_body,
    }

    def serve_layout():
        """Build a request-fresh router so reconnecting browsers recover cleanly."""
        return html.Div(
            [
                html.Div(
                    id="backend-endpoints",
                    hidden=True,
                    **{
                        "data-progress-url": public_progress_path,
                        "data-start-url": public_start_path,
                    },
                ),
                html.Header(
                    [
                        dcc.Link(
                            [
                                html.Span("Cube", className="cube-wordmark"),
                                html.Span("Risk & PL", className="cube-wordmark-note"),
                            ],
                            href=cube_href,
                            className="cube-brand",
                            title="Cube Risk and PL home",
                        ),
                        html.Nav(
                            [
                                dcc.Link(
                                    "Risk",
                                    href=cube_href,
                                    id="cube-nav-link",
                                    className="app-nav-link cube-nav-link is-active",
                                ),
                                dcc.Link(
                                    "Static Data",
                                    href=static_data_href,
                                    id="static-data-nav-link",
                                    className="app-nav-link cube-nav-link",
                                ),
                            ],
                            className="cube-nav",
                            **{"aria-label": "Primary navigation"},
                        ),
                    ],
                    className="cube-app-header",
                ),
                build_shared_refresh_shell(
                    current_shared_snapshot(),
                    refresh_enabled=refresh_manager is not None,
                    stage_delays=stage_delays,
                    initial_loading=False,
                    style={"display": "none"},
                ),
                page_container,
            ],
            className="app-router-shell",
        )

    app.layout = serve_layout

    @app.callback(
        Output("cube-nav-link", "className"),
        Output("static-data-nav-link", "className"),
        Output("shared-refresh-shell", "style"),
        Input("_pages_location", "pathname"),
    )
    def update_navigation(pathname):
        """Reflect the native page route without taking ownership of content."""
        selected_path = app.strip_relative_path(pathname)
        cube_class = "app-nav-link cube-nav-link"
        static_class = "app-nav-link cube-nav-link"
        shared_shell_style = {"display": "none"}
        if selected_path == "":
            cube_class = f"{cube_class} is-active"
            shared_shell_style = {}
        elif selected_path == "static-data":
            static_class = f"{static_class} is-active"
        return cube_class, static_class, shared_shell_style

    register_callbacks(
        app,
        refresh_manager,
        initial_snapshot,
        risk_data,
        route_prefix=request_prefix,
        startup_coordinator=startup_coordinator,
        pl_enabled=pl_send_config is not None,
    )
    if refresh_manager is not None and pl_send_config is not None:
        register_pl_send_callbacks(app, refresh_manager, pl_send_config)
    return app


__all__ = ["build_app"]
