"""Native Dash Pages entry for the Risk dashboard."""

from __future__ import annotations

from typing import Any

from . import page_services


def layout(**_kwargs: Any):
    """Build the current app's cold shell or committed Risk dashboard."""
    builder = page_services()["risk_page_builder"]
    if not callable(builder):
        raise RuntimeError("The Risk page builder is not callable")
    return builder()


__all__ = ["layout"]
