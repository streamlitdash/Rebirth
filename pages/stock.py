"""Native Dash Pages entry for the mapped Stock table."""

from __future__ import annotations

from typing import Any

from . import page_services


def layout(**_kwargs: Any):
    """Build Stock only when its native URL is active."""

    builder = page_services()["stock_page_builder"]
    if not callable(builder):
        raise RuntimeError("The Stock page builder is not callable")
    return builder()


__all__ = ["layout"]
