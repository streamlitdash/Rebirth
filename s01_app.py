"""Compose the Dash application from its connector and storage boundaries."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from core.s05_storage import LocalCsvAdjustmentRepository
from feeds.s01_sources import (
    build_production_refresh_manager,
    send_portfolio_pl,
    send_sog_pl,
)
from s02_config import RuntimeSettings, resolve_data_path
from ui.s08_plevents import PLSendConfig
from ui.s09_factory import build_app


_parser = argparse.ArgumentParser(description="Risk Cube Dashboard")
_parser.add_argument(
    "--port",
    type=int,
    default=None,
    help="Port to bind (default: PORT or 8050).",
)
_parser.add_argument(
    "--host",
    type=str,
    default=None,
    help="Host to bind (default: HOST or 127.0.0.1).",
)
_parser.add_argument(
    "--debug",
    action="store_true",
    help="Enable Dash debug mode.",
)


def create_app(settings: RuntimeSettings | None = None):
    """Build the application without loading connector data.

    The checked-in feeds read clearly labelled fake CSV fixtures. Replace the
    connector bodies in ``feeds/s01_sources.py`` before production use.
    """
    settings = settings or RuntimeSettings.from_env()
    manager = build_production_refresh_manager(
        stage_delays={
            "risk_product": float(os.getenv("RISK_PRODUCT_DELAY_SECONDS", "1")),
        }
    )

    project_root = Path(__file__).resolve().parent
    mapping_path = resolve_data_path(
        os.getenv("CONCERTO_MAPPING_PATH"),
        Path("data/s08_concerto.csv"),
        root=project_root,
    )
    adjustment_path = resolve_data_path(
        os.getenv("PL_ADJUSTMENT_PATH"),
        Path("adjustments"),
        root=project_root,
    )
    saved_pl_path = resolve_data_path(
        os.getenv("PL_LOCAL_FALLBACK_PATH"),
        Path("saved_pl"),
        root=project_root,
    )
    historical_pl_path = resolve_data_path(
        os.getenv("PL_HISTORICAL_PATH"),
        Path("data/s10_historical_pl.csv"),
        root=project_root,
    )

    pl_send_config = PLSendConfig(
        mapping_source=mapping_path,
        adjustment_repository=LocalCsvAdjustmentRepository(adjustment_path),
        saved_directory=saved_pl_path,
        send_sog_pl=send_sog_pl,
        send_portfolio_pl=send_portfolio_pl,
        history_source=historical_pl_path,
    )
    return build_app(
        refresh_manager=manager,
        pl_send_config=pl_send_config,
        dash_kwargs=settings.dash_kwargs,
    )


def parse_args():
    """Parse the optional local/JupyterHub launch overrides."""
    return _parser.parse_args()


def _configure_cli_environment(args) -> None:
    """Apply command-line overrides before constructing the one Dash app."""
    if args.port is not None:
        os.environ["PORT"] = str(args.port)
    if args.host is not None:
        os.environ["HOST"] = args.host
    if args.debug:
        os.environ["DASH_DEBUG"] = "1"


if __name__ == "__main__":
    _configure_cli_environment(parse_args())


SETTINGS = RuntimeSettings.from_env()
app = create_app(SETTINGS)
server = app.server


def run_app() -> None:
    """Run the already-constructed local app without enabling a reloader."""
    app.run(
        debug=SETTINGS.debug,
        host=SETTINGS.host,
        port=SETTINGS.port,
        use_reloader=False,
    )


if __name__ == "__main__":
    run_app()
