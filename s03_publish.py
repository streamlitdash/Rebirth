"""Stage the numbered source tree and publish it with the Plotly Cloud CLI.

The repository keeps ``s01_app.py`` and ``s04_server.py`` as its canonical
sources.  Plotly's conventional filenames are created only inside a temporary
deployment directory; no forwarding modules are checked in.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parent
CONFIG = PROJECT / "plotly-cloud.toml"

RUNTIME_FILES = {
    "s01_app.py": "app.py",
    "s02_config.py": "s02_config.py",
    "s04_server.py": "gunicorn.conf.py",
    "requirements.txt": "requirements.txt",
}
RUNTIME_DIRECTORIES = (
    "adapters",
    "assets",
    "core",
    "data",
    "feeds",
    "pages",
    "ui",
)
IGNORED_NAMES = (
    "__pycache__",
    "_disabled",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".write.lock",
    ".*.tmp",
)
_OFFICIAL_HISTORY_ARTIFACTS = {"risk.csv", "colossus.csv", "_SUCCESS"}
_PENDING_HISTORY_LEAF = re.compile(
    r"\.\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\.pending-.+"
)


def _deployment_ignore(directory: str, names: list[str]) -> set[str]:
    """Exclude caches and runtime-only official history date directories."""

    ignored = set(shutil.ignore_patterns(*IGNORED_NAMES)(directory, names))
    current = Path(directory).resolve()
    history_root = (PROJECT / "data" / "histo").resolve()
    try:
        relative = current.relative_to(history_root)
    except ValueError:
        return ignored
    if relative.parts:
        return ignored
    for name in names:
        candidate = current / name
        if _PENDING_HISTORY_LEAF.fullmatch(name):
            ignored.add(name)
            continue
        if not candidate.is_dir():
            continue
        try:
            child_names = {path.name for path in candidate.iterdir()}
        except OSError:
            # A scheduler may atomically rename its temporary leaf while the
            # bundle is staged. Omitting that transient entry is always safe.
            ignored.add(name)
            continue
        if child_names & _OFFICIAL_HISTORY_ARTIFACTS:
            ignored.add(name)
    return ignored


def _require_file(relative_path: str) -> Path:
    source = PROJECT / relative_path
    if not source.is_file():
        raise FileNotFoundError(f"required deployment file is missing: {relative_path}")
    return source


def _require_directory(relative_path: str) -> Path:
    source = PROJECT / relative_path
    if not source.is_dir():
        raise FileNotFoundError(
            f"required deployment directory is missing: {relative_path}"
        )
    return source


def stage_bundle(destination: Path) -> Path:
    """Create a minimal, self-contained Plotly runtime tree at *destination*."""
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"deployment destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    for source_name, staged_name in RUNTIME_FILES.items():
        shutil.copy2(_require_file(source_name), destination / staged_name)

    for directory_name in RUNTIME_DIRECTORIES:
        shutil.copytree(
            _require_directory(directory_name),
            destination / directory_name,
            ignore=_deployment_ignore,
        )

    return destination


def publish(*, keep_bundle: Path | None = None) -> None:
    """Publish a new app or update the app recorded by Plotly after first use."""
    _require_file(CONFIG.name)

    if keep_bundle is None:
        context = tempfile.TemporaryDirectory(prefix="rebirth-plotly-")
        temporary_root = Path(context.__enter__())
    else:
        context = None
        temporary_root = keep_bundle.resolve()

    try:
        staged = stage_bundle(temporary_root / "runtime")
        command = [
            sys.executable,
            "-m",
            "plotly_cloud.cli",
            "app",
            "publish",
            "--project-path",
            str(staged),
            "--config",
            str(CONFIG),
            "--name",
            "rebirth",
            "--poll-timeout",
            "300",
        ]
        subprocess.run(command, cwd=PROJECT, check=True)
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-bundle",
        type=Path,
        help="keep the staged runtime in this directory for inspection",
    )
    args = parser.parse_args()
    publish(keep_bundle=args.keep_bundle)


if __name__ == "__main__":
    main()
