"""Native Dash Pages entry for lazily mounted static reference data."""

from __future__ import annotations

from typing import Any

from ui.s05_staticdata import build_static_data_page


def layout(**_kwargs: Any):
    """Build Static Data only when its URL is active."""
    return build_static_data_page()


__all__ = ["layout"]
