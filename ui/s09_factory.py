"""Dash application factory and HTTP boundary configuration."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence
from uuid import uuid4

import pandas as pd
from dash import (
    ALL,
    Dash,
    Input,
    Output,
    State,
    dcc,
    ctx,
    html,
    no_update,
    page_container,
    page_registry,
    register_page,
)
from flask import jsonify, request
from dash.exceptions import MissingCallbackContextException
from core.s09_saved_views import SavedFilterViewRepository

from pages import PAGE_SERVICES_CONFIG_KEY
from pages.not_found_404 import layout as not_found_page_layout
from pages.pnl import layout as pnl_page_layout
from pages.risk import layout as risk_page_layout
from pages.static_data import layout as static_data_page_layout
from pages.stock import layout as stock_page_layout

from .s03_aggregate import prepare_risk_data
from .s07_events import (
    STARTUP_COORDINATOR_CONFIG_KEY,
    STARTUP_UI_ERROR_CONFIG_KEY,
    StartupCoordinator,
    register_callbacks,
)
from .s04_components import (
    RISK_SAVED_VIEW_CONTROLS,
    build_initial_load_layout,
    build_layout,
    build_shared_refresh_shell,
)
from .s08_plevents import (
    PLSendConfig,
    register_pl_aggregate_callbacks,
    register_pl_send_callbacks,
)
from .s06_plview import PL_SAVED_VIEW_CONTROLS, build_pl_page
from .s02_constants import FILTER_DIMENSION_FIELDS
from .s01_contracts import RefreshManagerProtocol
from .s10_stock import (
    STOCK_FILTER_FIELDS,
    STOCK_FILTER_IDS,
    STOCK_HIERARCHY_TOGGLE_TYPE,
    STOCK_SAVED_VIEW_CONTROLS,
    StockPageData,
    build_stock_hierarchy_panel_with_state,
    build_stock_page_from_data,
    build_stock_page_placeholder,
    build_stock_page_shell,
    build_stock_table_panel,
    default_stock_dates,
    filter_stock_comparison,
    load_stock_page_data,
    normalize_stock_date_pair,
    normalize_stock_promotion_threshold,
    normalize_stock_hierarchy_open_tokens,
    stock_exclude_selected,
    stock_filter_map,
    stock_filter_options,
    stock_summary_text,
    toggle_stock_hierarchy_open_tokens,
)
from .s11_saved_views import (
    build_saved_filter_view_bar,
    register_saved_filter_view_callbacks,
    saved_view_request_id,
    saved_view_request_matches_base,
    saved_view_request_values,
)


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
        "pages.stock",
        path="/stock",
        name="Stock",
        title="Cube — Stock",
        order=1,
        layout=stock_page_layout,
    )
    register_page(
        "pages.pnl",
        path="/pnl",
        name="P&L",
        title="Cube — P&L Sender",
        order=2,
        layout=pnl_page_layout,
    )
    register_page(
        "pages.static_data",
        path="/static-data",
        name="Statics",
        title="Cube — Statics",
        order=3,
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
    stock_source: Any | None = None,
    stock_portfolio_source: Any | None = None,
    saved_view_root: str | Path | None = None,
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
    if (stock_source is None) != (stock_portfolio_source is None):
        raise ValueError(
            "Stock requires both stock_source and stock_portfolio_source, or neither"
        )
    saved_view_repository = SavedFilterViewRepository(
        saved_view_root
        if saved_view_root is not None
        else Path(__file__).resolve().parent.parent / "data" / "saved_views",
        tuple(field.key for field in FILTER_DIMENSION_FIELDS),
    )
    stock_load_lock = Lock()
    stock_cached_pages: dict[tuple[int, str, str, str], StockPageData] = {}
    stock_intent_lock = Lock()
    stock_intent_sequence = 0
    stock_latest_intent: dict[str, int] = {}

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

    prepared_dashboard_lock = Lock()
    prepared_dashboard_revision = (
        int(initial_snapshot.revision) if initial_snapshot is not None else -1
    )
    prepared_dashboard_frame: pd.DataFrame | None = (
        risk_data if initial_snapshot is not None else None
    )
    risk_snapshot_lock = Lock()
    risk_snapshot_revision = prepared_dashboard_revision
    risk_snapshot_cache = initial_snapshot

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
    pnl_href = app.get_relative_path("/pnl")
    stock_href = app.get_relative_path("/stock")
    static_data_href = app.get_relative_path("/static-data")

    def prepared_committed_dashboard(
        *,
        revision: int | None = None,
        frame: pd.DataFrame | None = None,
    ) -> pd.DataFrame | None:
        """Prepare the mapped dashboard at most once per committed revision."""

        nonlocal prepared_dashboard_frame, prepared_dashboard_revision
        if refresh_manager is None:
            return risk_data
        if frame is None:
            try:
                requested_revision = int(refresh_manager.health.revision)
            except Exception:
                requested_revision = -1
            if requested_revision <= 0:
                return None
            with prepared_dashboard_lock:
                if (
                    prepared_dashboard_frame is not None
                    and prepared_dashboard_revision == requested_revision
                ):
                    return prepared_dashboard_frame
            dashboard_read = refresh_manager.read_frame("dashboard_frame")
            revision = int(dashboard_read.revision)
            frame = dashboard_read.frame
        elif revision is None:
            raise ValueError("revision is required when a dashboard frame is supplied")

        selected_revision = int(revision)
        with prepared_dashboard_lock:
            if (
                prepared_dashboard_frame is not None
                and prepared_dashboard_revision == selected_revision
            ):
                return prepared_dashboard_frame
            prepared = prepare_risk_data(frame) if not frame.empty else frame.copy()
            if selected_revision >= prepared_dashboard_revision:
                prepared_dashboard_revision = selected_revision
                prepared_dashboard_frame = prepared
            return prepared_dashboard_frame

    def current_cube_page():
        """Serve the shell cold and the complete dashboard after revision 1."""
        nonlocal risk_snapshot_cache, risk_snapshot_revision
        if refresh_manager is not None:
            try:
                revision = int(refresh_manager.health.revision)
                if revision > 0:
                    with risk_snapshot_lock:
                        if (
                            risk_snapshot_cache is None
                            or risk_snapshot_revision != revision
                        ):
                            risk_snapshot_cache = refresh_manager.snapshot
                            risk_snapshot_revision = int(risk_snapshot_cache.revision)
                        snapshot = risk_snapshot_cache
                    prepared = prepared_committed_dashboard(
                        revision=int(snapshot.revision),
                        frame=snapshot.dashboard_frame,
                    )
                    if prepared is None:
                        raise RuntimeError("Committed dashboard frame is unavailable")
                    return build_layout(
                        prepared,
                        snapshot,
                        refresh_enabled=True,
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
            stage_delays=stage_delays,
            include_shared_refresh_shell=False,
        )

    def cube_page_body() -> html.Main:
        """Mount the revision-aware Risk page under its stable callback owner."""
        return html.Main(current_cube_page(), id="cube-page-container")

    def current_shared_snapshot():
        """Return the compact committed view used by the shared page shell."""
        if refresh_manager is not None:
            try:
                if refresh_manager.health.revision > 0:
                    return refresh_manager.control_snapshot
            except Exception:
                return initial_snapshot
        return initial_snapshot

    def pnl_page_body():
        """Mount Aggregate P&L and the optional sender on the native P&L route."""
        if refresh_manager is not None:
            initial_aggregate_frame = None
            try:
                start_initial_load = int(refresh_manager.health.revision) <= 0
                if not start_initial_load:
                    initial_aggregate_frame = prepared_committed_dashboard()
            except Exception:
                start_initial_load = True
                app.logger.exception(
                    "Could not pre-render committed Aggregate P&L on the P&L page"
                )
            return build_pl_page(
                start_initial_load=start_initial_load,
                send_workflow_available=pl_send_config is not None,
                initial_aggregate_frame=initial_aggregate_frame,
                saved_view_bar=build_saved_filter_view_bar(PL_SAVED_VIEW_CONTROLS),
            )
        return html.Main(
            [
                html.H1("P&L Sender", className="static-data-page-title"),
                html.P(
                    "P&L sending is not configured for this application.",
                    id="pnl-unavailable",
                    className="static-data-empty",
                ),
            ],
            id="pnl-page",
            className="static-data-page",
        )

    def stock_page_body():
        """Paint Stock immediately; its page-local callback owns source I/O."""
        if stock_source is None or stock_portfolio_source is None:
            return html.Main(
                [
                    html.H1("Stock", className="static-data-page-title"),
                    html.P(
                        "GetStock and its Portfolio mapping are not configured.",
                        id="stock-unavailable",
                        className="static-data-empty",
                    ),
                ],
                id="stock-page",
                className="static-data-page",
            )

        snapshot = current_shared_snapshot()
        reference_date = (
            snapshot.market_date
            if snapshot is not None
            else pd.Timestamp.now().normalize()
        )
        current_date, prior_date = default_stock_dates(reference_date)
        page = build_stock_page_shell(
            current_date=current_date,
            prior_date=prior_date,
        )
        page.children = [
            dcc.Store(id="stock-request-scope", data=uuid4().hex),
            *page.children,
        ]
        return page

    def loaded_stock_page(current_date: object, prior_date: object) -> StockPageData:
        """Resolve two dated Stock legs behind the mounted page shell."""

        return load_stock_page_data(
            stock_source=stock_source,
            portfolio_config_source=stock_portfolio_source,
            current_date=current_date,
            prior_date=prior_date,
            # The current selected Stock date owns the mapping authority.
            portfolio_date=current_date,
        )

    app.server.config[PAGE_SERVICES_CONFIG_KEY] = {
        "cube_href": cube_href,
        "risk_page_builder": cube_page_body,
        "pnl_page_builder": pnl_page_body,
        "stock_page_builder": stock_page_body,
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
                                    "Stock",
                                    href=stock_href,
                                    id="stock-nav-link",
                                    className="app-nav-link cube-nav-link",
                                ),
                                dcc.Link(
                                    "P&L",
                                    href=pnl_href,
                                    id="pnl-nav-link",
                                    className="app-nav-link cube-nav-link",
                                ),
                                dcc.Link(
                                    "Statics",
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
        Output("pnl-nav-link", "className"),
        Output("stock-nav-link", "className"),
        Output("static-data-nav-link", "className"),
        Output("shared-refresh-shell", "style"),
        Input("_pages_location", "pathname"),
    )
    def update_navigation(pathname):
        """Reflect the native page route without taking ownership of content."""
        selected_path = app.strip_relative_path(pathname)
        cube_class = "app-nav-link cube-nav-link"
        pnl_class = "app-nav-link cube-nav-link"
        stock_class = "app-nav-link cube-nav-link"
        static_class = "app-nav-link cube-nav-link"
        shared_shell_style = {"display": "none"}
        if selected_path == "":
            cube_class = f"{cube_class} is-active"
            shared_shell_style = {}
        elif selected_path == "pnl":
            pnl_class = f"{pnl_class} is-active"
            if refresh_manager is not None:
                shared_shell_style = {}
        elif selected_path == "stock":
            stock_class = f"{stock_class} is-active"
        elif selected_path == "static-data":
            static_class = f"{static_class} is-active"
        return (
            cube_class,
            pnl_class,
            stock_class,
            static_class,
            shared_shell_style,
        )

    register_callbacks(
        app,
        refresh_manager,
        initial_snapshot,
        risk_data,
        route_prefix=request_prefix,
        startup_coordinator=startup_coordinator,
    )
    if refresh_manager is not None:
        register_pl_aggregate_callbacks(
            app,
            refresh_manager,
            prepared_frame_loader=prepared_committed_dashboard,
            saved_view_controls=PL_SAVED_VIEW_CONTROLS,
        )
        register_saved_filter_view_callbacks(
            app,
            saved_view_repository,
            RISK_SAVED_VIEW_CONTROLS,
        )
        register_saved_filter_view_callbacks(
            app,
            saved_view_repository,
            PL_SAVED_VIEW_CONTROLS,
        )
    if refresh_manager is not None and pl_send_config is not None:
        register_pl_send_callbacks(app, refresh_manager, pl_send_config)
    if stock_source is not None and stock_portfolio_source is not None:
        register_saved_filter_view_callbacks(
            app,
            saved_view_repository,
            STOCK_SAVED_VIEW_CONTROLS,
        )

        def committed_stock_revision() -> int:
            try:
                return (
                    int(refresh_manager.health.revision)
                    if refresh_manager is not None
                    else 0
                )
            except Exception:
                return 0

        def stock_filter_outputs():
            outputs = [
                output
                for field in STOCK_FILTER_FIELDS
                for output in (
                    Output(STOCK_FILTER_IDS[field.key], "options"),
                    Output(STOCK_FILTER_IDS[field.key], "value"),
                )
            ]
            outputs.append(Output("stock-filter-exclude-selected", "value"))
            return outputs

        def stock_filter_states():
            return [
                State(STOCK_FILTER_IDS[field.key], "value")
                for field in STOCK_FILTER_FIELDS
            ]

        def stock_cache_token(
            revision: int,
            current_date: pd.Timestamp,
            prior_date: pd.Timestamp,
            portfolio_date: pd.Timestamp,
        ) -> dict[str, Any]:
            return {
                "revision": revision,
                "current_date": current_date.date().isoformat(),
                "prior_date": prior_date.date().isoformat(),
                "portfolio_date": portfolio_date.date().isoformat(),
            }

        def stock_cache_key(token: object) -> tuple[int, str, str, str] | None:
            if not isinstance(token, Mapping):
                return None
            try:
                return (
                    int(token["revision"]),
                    str(token["current_date"]),
                    str(token["prior_date"]),
                    str(token["portfolio_date"]),
                )
            except (KeyError, TypeError, ValueError):
                return None

        def stock_error_result(error: Exception, *, retryable: bool):
            return (
                build_stock_page_placeholder(
                    f"Stock could not be loaded: {error}",
                    error=True,
                ),
                no_update,
                None,
                not retryable,
                *([no_update] * ((2 * len(STOCK_FILTER_FIELDS)) + 1)),
            )

        def claim_stock_intent(request_scope: object) -> tuple[str, int]:
            """Record the newest load request for one mounted Stock page."""

            nonlocal stock_intent_sequence
            scope = str(request_scope or "stock-unscoped")
            with stock_intent_lock:
                stock_intent_sequence += 1
                sequence = stock_intent_sequence
                stock_latest_intent[scope] = sequence
            return scope, sequence

        def stock_intent_is_current(scope: str, sequence: int) -> bool:
            with stock_intent_lock:
                return stock_latest_intent.get(scope) == sequence

        def finish_stock_intent(scope: str, sequence: int) -> None:
            with stock_intent_lock:
                if stock_latest_intent.get(scope) == sequence:
                    stock_latest_intent.pop(scope, None)

        def stale_stock_result():
            """Ignore a response superseded by newer date intent in the browser."""

            return (no_update,) * (5 + (2 * len(STOCK_FILTER_FIELDS)))

        def render_stock_result(
            page_data: StockPageData,
            selected_filter_values: Sequence[Sequence[str] | None],
            exclude_value: Sequence[str] | None,
        ) -> tuple[Any, list[Any]]:
            selected_filters = stock_filter_map(selected_filter_values)
            options, valid = stock_filter_options(
                page_data.mapped_stock,
                selected_filters,
            )
            page = build_stock_page_from_data(
                page_data,
                selected_filters=valid,
                exclude_selected=stock_exclude_selected(exclude_value),
            )
            filter_payload: list[Any] = []
            for field in STOCK_FILTER_FIELDS:
                filter_payload.extend((options[field.key], valid[field.key]))
            filter_payload.append(list(exclude_value or []))
            return page.children, filter_payload

        def load_stock_revision(
            current_date: object,
            prior_date: object,
            loaded_revision: object,
            loaded_dates: object,
            selected_filter_values: Sequence[Sequence[str] | None],
            exclude_value: Sequence[str] | None,
            request_scope: object,
            *,
            force_render: bool = False,
        ):
            """Coalesce dated loads and retain retryability after failures."""

            scope, intent_sequence = claim_stock_intent(request_scope)
            try:
                current, prior = normalize_stock_date_pair(current_date, prior_date)
            except Exception as error:
                # Invalid picker state needs a user edit, not a one-second
                # automatic retry loop.
                finish_stock_intent(scope, intent_sequence)
                return stock_error_result(error, retryable=False)
            committed_revision = committed_stock_revision()
            token = stock_cache_token(
                committed_revision,
                current,
                prior,
                current,
            )
            key = stock_cache_key(token)
            if (
                loaded_revision == committed_revision
                and stock_cache_key(loaded_dates) == key
                and key in stock_cached_pages
                and not force_render
            ):
                finish_stock_intent(scope, intent_sequence)
                return (
                    no_update,
                    no_update,
                    no_update,
                    True,
                    *([no_update] * ((2 * len(STOCK_FILTER_FIELDS)) + 1)),
                )
            if not stock_load_lock.acquire(blocking=False):
                return (
                    no_update,
                    no_update,
                    no_update,
                    False,
                    *([no_update] * ((2 * len(STOCK_FILTER_FIELDS)) + 1)),
                )
            try:
                page_data = stock_cached_pages.get(key)
                if page_data is None:
                    try:
                        page_data = loaded_stock_page(current, prior)
                    except Exception as error:
                        app.logger.exception("Could not load the Stock page")
                        if not stock_intent_is_current(scope, intent_sequence):
                            return stale_stock_result()
                        finish_stock_intent(scope, intent_sequence)
                        return stock_error_result(error, retryable=True)
                    if key is None:
                        raise RuntimeError("Stock cache key could not be constructed")
                    stock_cached_pages[key] = page_data
                    if len(stock_cached_pages) > 8:
                        stock_cached_pages.pop(next(iter(stock_cached_pages)))
                if not stock_intent_is_current(scope, intent_sequence):
                    return stale_stock_result()
                children, filter_payload = render_stock_result(
                    page_data,
                    selected_filter_values,
                    exclude_value,
                )
                if committed_stock_revision() != committed_revision:
                    # A commit landed while a dated connector was in flight.
                    # The completed result may paint, but its revision/date
                    # token is not released and the timer remains retryable.
                    return (
                        children,
                        no_update,
                        no_update,
                        False,
                        *filter_payload,
                    )
                finish_stock_intent(scope, intent_sequence)
                return (
                    children,
                    committed_revision,
                    token,
                    True,
                    *filter_payload,
                )
            finally:
                stock_load_lock.release()

        @app.callback(
            Output("stock-page-content", "children"),
            Output("stock-loaded-revision", "data"),
            Output("stock-loaded-dates", "data"),
            Output("stock-load-trigger", "disabled"),
            *stock_filter_outputs(),
            Input("stock-load-trigger", "n_intervals"),
            Input("refresh-commit-revision", "children"),
            Input("stock-compare-button", "n_clicks"),
            Input(STOCK_SAVED_VIEW_CONTROLS.apply_request_id, "data"),
            State("stock-loaded-revision", "data"),
            State("stock-loaded-dates", "data"),
            State("stock-current-date", "date"),
            State("stock-prior-date", "date"),
            State("stock-filter-exclude-selected", "value"),
            *stock_filter_states(),
            State("stock-request-scope", "data"),
            State(STOCK_SAVED_VIEW_CONTROLS.applied_request_id, "data"),
            prevent_initial_call=True,
        )
        def coordinate_stock_load(
            _ticks,
            _committed_revision,
            _compare_clicks,
            *callback_values,
        ):
            """Own mount, Compare, retry, and financial-commit Stock loads."""

            # The optional fallback keeps direct-library callers from before
            # saved views source-compatible; Dash always supplies the request
            # Input in the new callback graph.
            legacy_value_count = 6 + len(STOCK_FILTER_FIELDS)
            if len(callback_values) == legacy_value_count:
                saved_view_request = None
                state_values = callback_values
                applied_saved_view_request = None
            else:
                saved_view_request = callback_values[0]
                state_values = callback_values[1:-1]
                applied_saved_view_request = callback_values[-1]
            (
                loaded_revision,
                loaded_dates,
                current_date,
                prior_date,
                exclude_value,
                *filter_values_and_scope,
            ) = state_values

            selected_filter_values = filter_values_and_scope[: len(STOCK_FILTER_FIELDS)]
            request_scope = filter_values_and_scope[-1]
            try:
                saved_view_triggered = (
                    ctx.triggered_id == STOCK_SAVED_VIEW_CONTROLS.apply_request_id
                )
            except (LookupError, MissingCallbackContextException):
                saved_view_triggered = False
            request_id = saved_view_request_id(saved_view_request)
            saved_view_pending = bool(
                request_id and request_id != applied_saved_view_request
            )
            request_matches_base = False
            if saved_view_pending:
                try:
                    request_matches_base = saved_view_request_matches_base(
                        saved_view_request,
                        STOCK_SAVED_VIEW_CONTROLS,
                        selected_filter_values,
                        exclude_value,
                    )
                except ValueError:
                    request_matches_base = False
            apply_pending = saved_view_pending and (
                saved_view_triggered or request_matches_base
            )
            if apply_pending:
                try:
                    requested = saved_view_request_values(
                        saved_view_request,
                        STOCK_SAVED_VIEW_CONTROLS,
                    )
                except ValueError:
                    requested = None
                if requested is not None:
                    selected_filter_values, exclude_value = requested
            return load_stock_revision(
                current_date,
                prior_date,
                loaded_revision,
                loaded_dates,
                selected_filter_values,
                exclude_value,
                request_scope,
                force_render=apply_pending,
            )

        @app.callback(
            Output("stock-row-count", "children"),
            Output("stock-mapped-count", "children"),
            Output("stock-unmapped-count", "children"),
            Output("stock-dimension-filter-store", "data"),
            Output("stock-hierarchy-open-paths", "data"),
            Output("stock-hierarchy-view", "children"),
            *[
                Input(STOCK_FILTER_IDS[field.key], "value")
                for field in STOCK_FILTER_FIELDS
            ],
            Input("stock-filter-exclude-selected", "value"),
            Input("stock-promotion-threshold", "value"),
            Input("stock-loaded-dates", "data"),
            Input(
                {"type": STOCK_HIERARCHY_TOGGLE_TYPE, "path": ALL},
                "n_clicks",
            ),
            State("stock-hierarchy-open-paths", "data"),
            prevent_initial_call=True,
        )
        def filter_stock_table(*values):
            """Rebuild the Stock stack and source rows from the server cache."""
            selected_filter_values = values[: len(STOCK_FILTER_FIELDS)]
            exclude_value = values[len(STOCK_FILTER_FIELDS)]
            promotion_threshold = values[len(STOCK_FILTER_FIELDS) + 1]
            loaded_dates = values[len(STOCK_FILTER_FIELDS) + 2]
            _row_clicks = values[len(STOCK_FILTER_FIELDS) + 3]
            current_open_paths = values[-1]
            selected_filters = stock_filter_map(selected_filter_values)
            key = stock_cache_key(loaded_dates)
            page_data = stock_cached_pages.get(key)
            if page_data is None:
                return (no_update,) * 6
            filtered = filter_stock_comparison(
                page_data.mapped_stock,
                selected_filters,
                exclude_selected=stock_exclude_selected(exclude_value),
            )
            effective_threshold = normalize_stock_promotion_threshold(
                promotion_threshold
            )
            rows, mapped, unmapped = stock_summary_text(
                filtered,
                total_rows=len(page_data.mapped_stock),
                current_date=page_data.current_date,
                prior_date=page_data.prior_date,
            )
            requested_open_paths = normalize_stock_hierarchy_open_tokens(
                current_open_paths
            )
            try:
                triggered = ctx.triggered_id
                triggered_clicks = ctx.triggered[0].get("value") if ctx.triggered else 0
            except MissingCallbackContextException:
                triggered = None
                triggered_clicks = 0
            if (
                isinstance(triggered, dict)
                and triggered.get("type") == STOCK_HIERARCHY_TOGGLE_TYPE
                and int(triggered_clicks or 0) > 0
            ):
                requested_open_paths = toggle_stock_hierarchy_open_tokens(
                    requested_open_paths,
                    triggered.get("path"),
                )
            hierarchy, effective_open_paths = build_stock_hierarchy_panel_with_state(
                filtered,
                has_unfiltered_rows=not page_data.mapped_stock.empty,
                promotion_threshold=effective_threshold,
                open_path_tokens=requested_open_paths,
            )
            hierarchy_triggered = (
                isinstance(triggered, dict)
                and triggered.get("type") == STOCK_HIERARCHY_TOGGLE_TYPE
                and int(triggered_clicks or 0) > 0
            )
            if hierarchy_triggered:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    effective_open_paths,
                    hierarchy,
                )
            return (
                rows,
                mapped,
                unmapped,
                {
                    "filters": selected_filters,
                    "exclude_selected": stock_exclude_selected(exclude_value),
                    "promotion_threshold": effective_threshold,
                },
                effective_open_paths,
                hierarchy,
            )

        @app.callback(
            Output("stock-table-panel", "children"),
            Output("stock-source-rows-state", "data"),
            Output("stock-source-rows-button", "children"),
            Output("stock-source-comparison-details", "open"),
            Input("stock-source-rows-button", "n_clicks"),
            Input("stock-loaded-dates", "data"),
            *[
                Input(STOCK_FILTER_IDS[field.key], "value")
                for field in STOCK_FILTER_FIELDS
            ],
            Input("stock-filter-exclude-selected", "value"),
            State("stock-source-rows-state", "data"),
            prevent_initial_call=True,
        )
        def render_stock_source_rows(
            _button_clicks,
            loaded_dates,
            *filter_values_exclude_and_state,
        ):
            """Load filtered raw rows only after an explicit page-local request."""

            selected_filter_values = filter_values_exclude_and_state[
                : len(STOCK_FILTER_FIELDS)
            ]
            exclude_value = filter_values_exclude_and_state[len(STOCK_FILTER_FIELDS)]
            current_state = filter_values_exclude_and_state[-1]
            key = stock_cache_key(loaded_dates)
            page_data = stock_cached_pages.get(key)
            state = current_state if isinstance(current_state, Mapping) else {}
            same_snapshot = stock_cache_key(state.get("loaded_dates")) == key
            requested = bool(state.get("requested")) and same_snapshot
            try:
                triggered = ctx.triggered_id
                triggered_clicks = ctx.triggered[0].get("value") if ctx.triggered else 0
            except MissingCallbackContextException:
                triggered = None
                triggered_clicks = 0
            if (
                triggered == "stock-source-rows-button"
                and int(triggered_clicks or 0) > 0
            ):
                requested = not requested

            state_payload = {
                "requested": requested,
                "loaded_dates": loaded_dates if key is not None else None,
            }
            if page_data is None or not requested:
                return (
                    html.P(
                        "Source comparison rows are not loaded. Load them only when needed.",
                        className="static-data-page-note",
                    ),
                    {**state_payload, "requested": False},
                    "Load filtered source rows",
                    False,
                )

            selected_filters = stock_filter_map(selected_filter_values)
            filtered = filter_stock_comparison(
                page_data.mapped_stock,
                selected_filters,
                exclude_selected=stock_exclude_selected(exclude_value),
            )
            return (
                build_stock_table_panel(
                    filtered,
                    has_unfiltered_rows=not page_data.mapped_stock.empty,
                ),
                state_payload,
                "Hide source rows",
                True,
            )

    return app


__all__ = ["build_app"]
