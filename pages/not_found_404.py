"""Explicit native Dash Pages fallback."""

from __future__ import annotations

from typing import Any

from dash import dcc, html

from . import page_services


def layout(**_kwargs: Any):
    """Build a prefix-safe page for URLs outside the page catalogue."""
    cube_href = str(page_services()["cube_href"])
    return html.Main(
        html.Section(
            [
                html.H1("Page not found"),
                html.P(
                    "Cube has no page at this address.",
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


__all__ = ["layout"]
