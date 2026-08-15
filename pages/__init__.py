"""Stable Dash Pages layouts backed by the active Flask application."""

from __future__ import annotations

from typing import Any, Mapping

from flask import current_app

PAGE_SERVICES_CONFIG_KEY = "CUBE_PAGE_SERVICES"


def page_services() -> Mapping[str, Any]:
    """Return page builders belonging to the Dash app serving this request."""
    services = current_app.config.get(PAGE_SERVICES_CONFIG_KEY)
    if not isinstance(services, Mapping):
        raise RuntimeError("Cube page services are not configured for this app")
    return services


__all__ = ["PAGE_SERVICES_CONFIG_KEY", "page_services"]
