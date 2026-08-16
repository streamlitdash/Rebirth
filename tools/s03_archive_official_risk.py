"""Jupyter Scheduler entry point for the daily official Risk archive."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from core.s11_risk_archive import (
    ArchiveResult,
    ColossusLoader,
    archive_from_manager,
)


DEFAULT_COLOSSUS_LOADER = "feeds.s01_sources:get_colossus_pl"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE_ROOT = PROJECT_ROOT / "data" / "histo"


def resolve_archive_root(
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve ``PL_HISTORICAL_PATH`` against the repository, never the job CWD."""

    values = os.environ if environ is None else environ
    configured = str(values.get("PL_HISTORICAL_PATH", "")).strip()
    candidate = Path(configured).expanduser() if configured else DEFAULT_ARCHIVE_ROOT
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def resolve_colossus_loader(
    value: str | None,
) -> ColossusLoader:
    """Resolve one explicit ``module:function`` Colossus integration boundary."""

    reference = str(value or DEFAULT_COLOSSUS_LOADER).strip()
    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name.strip() or not attribute_name.strip():
        raise ValueError("COLOSSUS_LOADER must use the form 'module:function'")
    module = importlib.import_module(module_name.strip())
    loader = getattr(module, attribute_name.strip(), None)
    if not callable(loader):
        raise TypeError(f"Configured Colossus loader is not callable: {reference}")
    return loader


def _default_manager_factory() -> object:
    from feeds.s01_sources import build_production_refresh_manager

    return build_production_refresh_manager(stage_delays={})


def run_scheduled_archive(
    *,
    environ: Mapping[str, str] | None = None,
    manager_factory: Callable[[], object] | None = None,
    colossus_loader: ColossusLoader | None = None,
) -> ArchiveResult:
    """Run one idempotent scheduled attempt using environment configuration."""

    values = os.environ if environ is None else environ
    root = resolve_archive_root(values)
    loader = colossus_loader or resolve_colossus_loader(values.get("COLOSSUS_LOADER"))
    manager = (manager_factory or _default_manager_factory)()
    return archive_from_manager(manager, loader, root, refresh=True)


def run_from_env() -> ArchiveResult:
    """Zero-argument API used by the checked-in Jupyter Scheduler notebook."""

    return run_scheduled_archive()


def main() -> None:
    result = run_from_env()
    print(
        f"{result.status}: {result.reason} date={result.market_date} "
        f"risk_rows={result.risk_rows} colossus_rows={result.colossus_rows} "
        f"path={result.path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_ARCHIVE_ROOT",
    "DEFAULT_COLOSSUS_LOADER",
    "resolve_archive_root",
    "resolve_colossus_loader",
    "run_from_env",
    "run_scheduled_archive",
]
