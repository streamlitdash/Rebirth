"""Native Dash Pages entry for the lazily mounted Statics catalogue."""

from __future__ import annotations

from typing import Any

from ui.s05_staticdata import build_static_data_page


def layout(**_kwargs: Any):
    """Build Statics only when its URL is active."""
    return build_static_data_page()


__all__ = ["layout"]
