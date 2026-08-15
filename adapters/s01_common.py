"""Shared adapter helpers with an inline, comment-only recovered implementation."""

from __future__ import annotations

# === REAL CONNECTOR IMPLEMENTATION (COMMENTED OUT) ============================
# SWITCH TO REAL: uncomment the required imports and ``run_async`` below, then
# switch the matching registration in ``feeds/s01_sources.py`` from CSV to REAL.
# Leave the recovered ``from __future__ import annotations`` line commented;
# this module already enables it above so the inline switch remains compilable.
# The recovered helper is preserved verbatim as comments; it is not executable.
# === END SWITCH INSTRUCTIONS ==================================================
# """Strict helpers shared by the example product adapters."""
#
# from __future__ import annotations
#
# import asyncio
# import concurrent.futures
# import threading
# from typing import Any, TypeVar
# from typing import Protocol
#
# import pandas as pd
#
# from core.s02_pipeline import LIVE, OFFICIAL
#
# T = TypeVar("T")
#
# # Shared thread pool for running async coroutines outside the main event loop.
# # A single shared executor avoids the overhead of creating threads per call.
# _async_executor = concurrent.futures.ThreadPoolExecutor(
#     max_workers=1, thread_name_prefix="async-runner"
# )
#
#
# def run_async(coro: Any) -> Any:
#     """Run an async coroutine safely from a synchronous context.
#
#     Uses a dedicated single-threaded executor so the coroutine always runs
#     in a *fresh* thread with its own event loop -- never inside Dash's
#     callback loop.  This avoids the "asyncio.run() cannot be called from
#     a running event loop" RuntimeError entirely.
#     """
#     loop_factory = lambda: asyncio.new_event_loop()
#
#     def _run():
#         loop = loop_factory()
#         try:
#             asyncio.set_event_loop(loop)
#             return loop.run_until_complete(coro)
#         finally:
#             loop.close()
#             try:
#                 asyncio.get_event_loop().close()
#             except Exception:
#                 pass
#
#     return _async_executor.submit(_run).result()
#
#
# class RiskSource(Protocol):
#     def __call__(self, risk_date: pd.Timestamp) -> pd.DataFrame: ...
#
#
# class MarketSource(Protocol):
#     def __call__(
#         self,
#         market_date: pd.Timestamp,
#         underlying: str,
#         *,
#         market_status: str,
#     ) -> pd.DataFrame: ...
#
#
# def exact_frame(
#     value: object,
#     *,
#     columns: tuple[str, ...],
#     label: str,
# ) -> pd.DataFrame:
#     """Copy a DataFrame only when its ordered public schema is exact."""
#
#     if not isinstance(value, pd.DataFrame):
#         raise TypeError(f"{label} must return a pandas DataFrame")
#     actual = tuple(value.columns)
#     if actual != columns:
#         raise ValueError(
#             f"{label} columns must be exactly {list(columns)} in that order; "
#             f"found {list(actual)}"
#         )
#     return value.copy()
#
#
# def exact_status(value: object) -> str:
#     """Require the manager-owned Live/OFFICIAL routing instruction."""
#
#     if value not in (LIVE, OFFICIAL):
#         raise ValueError("market_status must be exactly 'Live' or 'OFFICIAL'")
#     return str(value)
#
#
# def exact_underlying(value: object) -> str:
#     """Require one nonblank Underlying; batching belongs to the framework."""
#
#     if not isinstance(value, str) or not value.strip():
#         raise ValueError("underlying must be nonblank text")
#     return value.strip()
#
#
# def market_frame(
#     source: MarketSource,
#     market_date: pd.Timestamp,
#     underlying: str,
#     *,
#     market_status: str,
#     columns: tuple[str, ...],
#     label: str,
#     attach_status: bool = False,
# ) -> pd.DataFrame:
#     """Call one personal market function and validate identity and routing."""
#
#     selected_underlying = exact_underlying(underlying)
#     selected_status = exact_status(market_status)
#     frame = exact_frame(
#         source(
#             market_date,
#             selected_underlying,
#             market_status=selected_status,
#         ),
#         columns=columns,
#         label=f"{label} for {selected_underlying!r}",
#     )
#     if not frame.empty and not frame["Underlying"].eq(selected_underlying).all():
#         raise ValueError(
#             f"{label} for {selected_underlying!r} returned another Underlying"
#         )
#     if attach_status:
#         frame["Market Status"] = selected_status
#     return frame
#
#
# __all__ = [
#     "MarketSource",
#     "RiskSource",
#     "exact_frame",
#     "exact_status",
#     "exact_underlying",
#     "market_frame",
# ]
#
#
# def exact_status(value: object) -> str:
#     """Require the manager-owned Live/OFFICIAL routing instruction."""
#
#     if value not in (LIVE, OFFICIAL):
#         raise ValueError("market_status must be exactly 'Live' or 'OFFICIAL'")
#     return str(value)
#
#
# def exact_underlying(value: object) -> str:
#     """Require one nonblank Underlying; batching belongs to the framework."""
#
#     if not isinstance(value, str) or not value.strip():
#         raise ValueError("underlying must be nonblank text")
#     return value.strip()
#
#
# def market_frame(
#     source: MarketSource,
#     market_date: pd.Timestamp,
#     underlying: str,
#     *,
#     market_status: str,
#     columns: tuple[str, ...],
#     label: str,
#     attach_status: bool = False,
# ) -> pd.DataFrame:
#     """Call one personal market function and validate identity and routing."""
#
#     selected_underlying = exact_underlying(underlying)
#     selected_status = exact_status(market_status)
#     frame = exact_frame(
#         source(
#             market_date,
#             selected_underlying,
#             market_status=selected_status,
#         ),
#         columns=columns,
#         label=f"{label} for {selected_underlying!r}",
#     )
#     if not frame.empty and not frame["Underlying"].eq(selected_underlying).all():
#         raise ValueError(
#             f"{label} for {selected_underlying!r} returned another Underlying"
#         )
#     if attach_status:
#         frame["Market Status"] = selected_status
#     return frame
#
#
# __all__ = [
#     "MarketSource",
#     "RiskSource",
#     "exact_frame",
#     "exact_status",
#     "exact_underlying",
#     "market_frame",
# ]


# === ACTIVE FIXTURE/CSV COMPATIBILITY HELPERS ================================
# Keep this section active while ``feeds/s01_sources.py`` uses its CSV fallback.
# Strict helpers shared by the active fixture-compatible adapter contracts.

from typing import Protocol

import pandas as pd

from core.s02_pipeline import LIVE, OFFICIAL


class RiskSource(Protocol):
    def __call__(self, risk_date: pd.Timestamp) -> pd.DataFrame: ...


class MarketSource(Protocol):
    def __call__(
        self,
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame: ...


def exact_frame(
    value: object,
    *,
    columns: tuple[str, ...],
    label: str,
) -> pd.DataFrame:
    """Copy a DataFrame only when its ordered public schema is exact."""

    if not isinstance(value, pd.DataFrame):
        raise TypeError(f"{label} must return a pandas DataFrame")
    actual = tuple(value.columns)
    if actual != columns:
        raise ValueError(
            f"{label} columns must be exactly {list(columns)} in that order; "
            f"found {list(actual)}"
        )
    return value.copy()


def exact_status(value: object) -> str:
    """Require the manager-owned Live/OFFICIAL routing instruction."""

    if value not in {LIVE, OFFICIAL}:
        raise ValueError("market_status must be exactly 'Live' or 'OFFICIAL'")
    return str(value)


def exact_underlying(value: object) -> str:
    """Require one nonblank Underlying; batching belongs to the framework."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("underlying must be nonblank text")
    return value.strip()


def market_frame(
    source: MarketSource,
    market_date: pd.Timestamp,
    underlying: str,
    *,
    market_status: str,
    columns: tuple[str, ...],
    label: str,
    attach_status: bool = False,
) -> pd.DataFrame:
    """Call one personal market function and validate identity and routing."""

    selected_underlying = exact_underlying(underlying)
    selected_status = exact_status(market_status)
    frame = exact_frame(
        source(
            market_date,
            selected_underlying,
            market_status=selected_status,
        ),
        columns=columns,
        label=f"{label} for {selected_underlying!r}",
    )
    if not frame.empty and not frame["Underlying"].eq(selected_underlying).all():
        raise ValueError(
            f"{label} for {selected_underlying!r} returned another Underlying"
        )
    if attach_status:
        frame["Market Status"] = selected_status
    return frame


__all__ = [
    "MarketSource",
    "RiskSource",
    "exact_frame",
    "exact_status",
    "exact_underlying",
    "market_frame",
]
