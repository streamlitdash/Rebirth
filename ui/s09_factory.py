"""Dash application factory and HTTP boundary configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from dash import Dash, Input, Output, State, dcc, html, no_update
from flask import jsonify, request

from .s03_aggregate import prepare_risk_data
from .s07_events import (
    STARTUP_COORDINATOR_CONFIG_KEY,
    STARTUP_UI_ERROR_CONFIG_KEY,
    StartupCoordinator,
    register_callbacks,
)
from .s04_components import build_initial_load_layout, build_layout
from .s05_staticdata import build_static_data_page
from .s08_plevents import PLSendConfig, register_pl_send_callbacks
from .s01_contracts import RefreshManagerProtocol


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
    app = Dash(
        __name__,
        assets_folder=str(Path(__file__).resolve().parent.parent / "assets"),
        **dash_options,
    )
    app.title = "Cube"
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
        if (
            startup_coordinator is not None
            and response.status_code < 400
            and request.path.endswith("_dash-layout")
            and startup_coordinator.status().phase == "idle"
        ):
            # The short delay lets the shell reach the browser before the
            # background worker begins.  start() is process-wide and
            # idempotent, so simultaneous visitors cannot create two writers.
            startup_coordinator.schedule_start(delay_seconds=0.25)
        return response

    stage_delays = refresh_manager.stage_delays if refresh_manager is not None else None
    cube_href = request_prefix
    static_data_href = f"{request_prefix.rstrip('/')}/static-data"
    cube_path = cube_href.rstrip("/") or "/"
    static_data_path = static_data_href.rstrip("/") or "/"

    def normalize_browser_path(pathname: object) -> str:
        """Return one exact browser pathname without changing its prefix."""
        value = str(pathname or cube_path).strip()
        if not value.startswith("/"):
            value = f"/{value}"
        return value.rstrip("/") or "/"

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
                    )
            except Exception as error:
                app.logger.exception(
                    "Could not materialize the committed startup snapshot: %s",
                    type(error).__name__,
                )
                return build_initial_load_layout(
                    stage_delays=stage_delays,
                    error=(
                        "The validated data loaded, but the dashboard could not be "
                        "rendered. Check the server log and retry."
                    ),
                )
            return build_initial_load_layout(stage_delays=stage_delays)
        return build_layout(
            risk_data,
            initial_snapshot,
            refresh_enabled=False,
            pl_enabled=False,
            stage_delays=stage_delays,
        )

    def cube_page_body() -> html.Main:
        """Mount the revision-aware Risk page under its stable callback owner."""
        return html.Main(current_cube_page(), id="cube-page-container")

    def not_found_page(pathname: str) -> html.Main:
        """Return an explicit page for paths outside the configured catalogue."""
        return html.Main(
            html.Section(
                [
                    html.H1("Page not found"),
                    html.P(
                        f"Cube has no page at {pathname}.",
                        className="static-data-page-note",
                    ),
                    dcc.Link(
                        "Return to Risk",
                        href=cube_href,
                        className="app-nav-link cube-nav-link",
                    ),
                ],
                id="not-found-page",
                className="static-data-page",
                role="alert",
            ),
            id="not-found-page-container",
        )

    def serve_layout():
        """Build a request-fresh router so reconnecting browsers recover cleanly."""
        return html.Div(
            [
                dcc.Location(id="app-location", refresh="callback-nav"),
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
                dcc.Store(id="active-page-path", data=cube_path),
                html.Div(
                    cube_page_body(),
                    id="app-page-container",
                ),
            ],
            className="app-router-shell",
        )

    app.layout = (
        serve_layout
        if refresh_manager is not None and initial_snapshot is None
        else serve_layout()
    )

    @app.callback(
        Output("app-page-container", "children"),
        Output("active-page-path", "data"),
        Output("cube-nav-link", "className"),
        Output("static-data-nav-link", "className"),
        Input("app-location", "pathname"),
        State("active-page-path", "data"),
    )
    def route_page(pathname, active_path):
        """Mount exactly one page body for one exact, prefix-safe URL."""
        selected_path = normalize_browser_path(pathname)
        current_path = normalize_browser_path(active_path)
        if selected_path == cube_path:
            cube_class = "app-nav-link cube-nav-link is-active"
            static_class = "app-nav-link cube-nav-link"
        elif selected_path == static_data_path:
            cube_class = "app-nav-link cube-nav-link"
            static_class = "app-nav-link cube-nav-link is-active"
        else:
            cube_class = "app-nav-link cube-nav-link"
            static_class = "app-nav-link cube-nav-link"

        # The request-fresh layout already contains the root Risk page. Avoid
        # replacing that tree on the initial Location callback: doing so would
        # restart its intervals and remount the entire callback graph.
        if selected_path == current_path:
            return no_update, no_update, cube_class, static_class
        if selected_path == cube_path:
            page = cube_page_body()
        elif selected_path == static_data_path:
            page = build_static_data_page()
        else:
            page = not_found_page(selected_path)
        return page, selected_path, cube_class, static_class

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
