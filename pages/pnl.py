"""Native Dash Pages entry for the governed P&L Sender workflow."""

from __future__ import annotations

from typing import Any

from . import page_services


def layout(**_kwargs: Any):
    """Build the P&L page through this Dash app's injected page service."""
    builder = page_services()["pnl_page_builder"]
    if not callable(builder):
        raise RuntimeError("The P&L page builder is not callable")
    return builder()


__all__ = ["layout"]
