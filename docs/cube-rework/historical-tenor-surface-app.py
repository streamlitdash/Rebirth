"""Historical market-tenor viewer using Rebirth's app lifecycle.

Files may be named DDMMYYYY.csv or YYYYMMDD.csv. Required columns are:

    Underlying, Tenor Swap, Tenor Option, OFFICIAL

Recommended additional columns are:

    Risk Type, Risk Greek

When either identity column is absent it is loaded as ``Unspecified``.

Install:
    python -m pip install dash pandas numpy plotly

Run:
    python historical-tenor-surface-app.py --data-dir data --port 8050

The server follows Rebirth's startup order:

    argparse -> environment -> RuntimeSettings -> create_app
    -> app/server -> run_app(use_reloader=False)

The Dash shell is available before source data is read. One background loader
publishes an immutable history snapshot. Exact Risk Type / Risk Greek /
Underlying bundles are then built lazily and cached.
"""

from __future__ import annotations

import argparse
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Final, Mapping

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html
from flask import jsonify, request


# ---------------------------------------------------------------------------
# Rebirth-style runtime setup
# ---------------------------------------------------------------------------

TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


def env_flag(
    name: str,
    default: bool = False,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    raw = values.get(name)
    return default if raw is None else raw.strip().casefold() in TRUTHY


def normalize_path_prefix(value: str | None, *, name: str) -> str:
    prefix = (value or "/").strip() or "/"
    if "://" in prefix or "?" in prefix or "#" in prefix:
        raise ValueError(f"{name} must be a URL path, not a complete URL")
    return f"/{prefix.strip('/')}/" if prefix.strip("/") else "/"


def resolve_data_path(value: str | None, default: Path, *, root: Path) -> Path:
    candidate = default if value is None or not value.strip() else Path(value).expanduser()
    return (root / candidate if not candidate.is_absolute() else candidate).resolve()


def discover_project_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    for candidate in (script_dir, *script_dir.parents):
        if (candidate / "s01_app.py").is_file():
            return candidate
    return script_dir


@dataclass(frozen=True)
class RuntimeSettings:
    host: str
    port: int
    debug: bool
    requests_pathname_prefix: str
    routes_pathname_prefix: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "RuntimeSettings":
        values = dict(os.environ if environ is None else environ)
        try:
            port = int(values.get("PORT", "8050"))
        except ValueError as exc:
            raise ValueError("PORT must be an integer between 1 and 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be an integer between 1 and 65535")

        request_override = values.get("DASH_REQUESTS_PATHNAME_PREFIX")
        route_override = values.get("DASH_ROUTES_PATHNAME_PREFIX")
        hub_prefix = values.get("JUPYTERHUB_SERVICE_PREFIX")
        hub_mode = values.get("DASH_JUPYTERHUB_MODE", "proxy").strip().casefold()
        if hub_mode not in {"proxy", "service"}:
            raise ValueError("DASH_JUPYTERHUB_MODE must be 'proxy' or 'service'")

        if request_override:
            requests_prefix = normalize_path_prefix(
                request_override,
                name="DASH_REQUESTS_PATHNAME_PREFIX",
            )
        elif hub_prefix and hub_mode == "service":
            requests_prefix = normalize_path_prefix(
                hub_prefix,
                name="JUPYTERHUB_SERVICE_PREFIX",
            )
        elif hub_prefix:
            hub_base = normalize_path_prefix(
                hub_prefix,
                name="JUPYTERHUB_SERVICE_PREFIX",
            ).rstrip("/")
            requests_prefix = normalize_path_prefix(
                f"{hub_base}/proxy/{port}/",
                name="derived JupyterHub proxy prefix",
            )
        else:
            requests_prefix = "/"

        if route_override:
            routes_prefix = normalize_path_prefix(
                route_override,
                name="DASH_ROUTES_PATHNAME_PREFIX",
            )
        elif hub_prefix and hub_mode == "service":
            routes_prefix = normalize_path_prefix(
                hub_prefix,
                name="JUPYTERHUB_SERVICE_PREFIX",
            )
        else:
            routes_prefix = "/"

        return cls(
            host=values.get("HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=port,
            debug=env_flag("DASH_DEBUG", False, environ=values),
            requests_pathname_prefix=requests_prefix,
            routes_pathname_prefix=routes_prefix,
        )

    @property
    def dash_kwargs(self) -> dict[str, str]:
        return {
            "requests_pathname_prefix": self.requests_pathname_prefix,
            "routes_pathname_prefix": self.routes_pathname_prefix,
        }


_parser = argparse.ArgumentParser(description="Historical market-tenor viewer")
_parser.add_argument("--port", type=int, default=None)
_parser.add_argument("--host", type=str, default=None)
_parser.add_argument("--debug", action="store_true")
_parser.add_argument("--data-dir", type=str, default=None)
_parser.add_argument(
    "--jupyterhub-mode",
    choices=("proxy", "service"),
    default=None,
)


def parse_args() -> argparse.Namespace:
    return _parser.parse_args()


def _configure_cli_environment(args: argparse.Namespace) -> None:
    if args.port is not None:
        os.environ["PORT"] = str(args.port)
    if args.host is not None:
        os.environ["HOST"] = args.host
    if args.debug:
        os.environ["DASH_DEBUG"] = "1"
    if args.data_dir:
        os.environ["CUBE_HISTORY_DATA_DIR"] = args.data_dir
    if args.jupyterhub_mode:
        os.environ["DASH_JUPYTERHUB_MODE"] = args.jupyterhub_mode


if __name__ == "__main__":
    _configure_cli_environment(parse_args())


# ---------------------------------------------------------------------------
# History schema and ordering
# ---------------------------------------------------------------------------

DATE_FILE_RE: Final = re.compile(r"^(?P<date>\d{8})\.csv$", re.IGNORECASE)
CORE_COLUMNS: Final = ("Underlying", "Tenor Swap", "Tenor Option", "OFFICIAL")
IDENTITY_COLUMNS: Final = ("Risk Type", "Risk Greek")
ALL_COLUMNS: Final = (*IDENTITY_COLUMNS, *CORE_COLUMNS)
CANONICAL_BY_CASEFOLD: Final = {
    column.casefold(): column for column in ALL_COLUMNS
}
TOKEN_RE: Final = re.compile(r"(\d+(?:\.\d+)?)")
TENOR_RE: Final = re.compile(r"^(\d+(?:\.\d+)?)([DWMY])$", re.IGNORECASE)
UNIT_DAYS: Final = {"D": 1.0, "W": 7.0, "M": 30.4375, "Y": 365.25}
SPECIAL_TENORS: Final = {
    "ON": 0.1,
    "O/N": 0.1,
    "TN": 0.2,
    "T/N": 0.2,
    "SN": 0.3,
    "S/N": 0.3,
    "SPOT": 0.4,
}
ABSENT_TENORS: Final = frozenset(
    {"", "n/a", "na", "none", "null", "unspecified", "spot"}
)
NO_TENOR: Final = "No Tenor"


def parse_market_date(path: Path) -> pd.Timestamp:
    match = DATE_FILE_RE.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Expected an 8-digit dated CSV, found {path.name}")

    token = match.group("date")
    formats = (
        ("%Y%m%d", "%d%m%Y")
        if 1900 <= int(token[:4]) <= 2200
        else ("%d%m%Y", "%Y%m%d")
    )
    for date_format in formats:
        try:
            return pd.Timestamp(datetime.strptime(token, date_format)).normalize()
        except ValueError:
            pass
    raise ValueError(f"Invalid date filename: {path.name}")


def natural_tokens(value: str) -> tuple[object, ...]:
    return tuple(
        float(part) if part.replace(".", "", 1).isdigit() else part
        for part in TOKEN_RE.split(value.casefold())
    )


def tenor_sort_key(value: object) -> tuple[object, ...]:
    label = str(value).strip()
    normalized = label.upper().replace(" ", "")
    if normalized.casefold() in ABSENT_TENORS or label == NO_TENOR:
        return (3, float("inf"), natural_tokens(label))
    if normalized in SPECIAL_TENORS:
        return (0, SPECIAL_TENORS[normalized], natural_tokens(label))
    match = TENOR_RE.fullmatch(normalized)
    if match:
        return (
            1,
            float(match.group(1)) * UNIT_DAYS[match.group(2).upper()],
            natural_tokens(label),
        )
    return (2, float("inf"), natural_tokens(label))


def meaningful_tenor(value: object) -> bool:
    return (
        value is not None
        and not pd.isna(value)
        and str(value).strip().casefold() not in ABSENT_TENORS
    )


def ordered_tenors(values: pd.Series) -> tuple[str, ...]:
    labels = (
        values.loc[values.map(meaningful_tenor)]
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .tolist()
    )
    return tuple(sorted(labels, key=tenor_sort_key))


def read_history_file(path: Path) -> tuple[pd.DataFrame, tuple[str, ...]]:
    header = pd.read_csv(path, nrows=0)
    actual: dict[str, str] = {}
    for raw_column in header.columns:
        canonical = CANONICAL_BY_CASEFOLD.get(str(raw_column).strip().casefold())
        if canonical:
            actual[canonical] = str(raw_column)

    missing_core = [column for column in CORE_COLUMNS if column not in actual]
    if missing_core:
        raise ValueError(
            f"{path.name} is missing {missing_core}; found {list(header.columns)}"
        )

    frame = pd.read_csv(
        path,
        usecols=[actual[column] for column in ALL_COLUMNS if column in actual],
        low_memory=False,
    ).rename(
        columns={actual[column]: column for column in ALL_COLUMNS if column in actual}
    )

    missing_identity = tuple(
        column for column in IDENTITY_COLUMNS if column not in frame
    )
    for column in missing_identity:
        frame[column] = "Unspecified"

    for column in (*IDENTITY_COLUMNS, "Underlying"):
        values = frame[column].astype("string").str.strip()
        if column == "Underlying" and (
            values.isna() | values.eq("")
        ).any():
            raise ValueError(f"{path.name}: Underlying contains blank values")
        frame[column] = (
            values.fillna("Unspecified")
            .mask(values.fillna("").eq(""), "Unspecified")
            .astype(str)
        )

    for column in ("Tenor Swap", "Tenor Option"):
        values = frame[column].astype("string").str.strip().fillna("N/A")
        frame[column] = values.mask(values.eq(""), "N/A").astype(str)

    raw_official = frame["OFFICIAL"]
    official = pd.to_numeric(raw_official, errors="coerce")
    nonblank = (
        raw_official.notna()
        & raw_official.astype("string").str.strip().ne("")
    )
    invalid = nonblank & official.isna()
    invalid |= official.notna() & ~np.isfinite(official)
    if invalid.any():
        raise ValueError(
            f"{path.name}: OFFICIAL is invalid at rows "
            f"{frame.index[invalid].tolist()[:5]}"
        )

    frame["OFFICIAL"] = official.astype(float)
    frame = frame.loc[frame["OFFICIAL"].notna()].copy()
    frame.insert(0, "Market Date", parse_market_date(path).date().isoformat())

    return (
        frame.groupby(
            [
                "Market Date",
                "Risk Type",
                "Risk Greek",
                "Underlying",
                "Tenor Swap",
                "Tenor Option",
            ],
            as_index=False,
            dropna=False,
            sort=False,
        )
        .agg(
            OFFICIAL=("OFFICIAL", "mean"),
            Source_Rows=("OFFICIAL", "size"),
        )
        .reset_index(drop=True),
        missing_identity,
    )


def colour_scale(values: pd.Series) -> tuple[float, float, str, float | None]:
    finite = pd.to_numeric(values, errors="coerce")
    finite = finite[np.isfinite(finite)]
    if finite.empty:
        return -1.0, 1.0, "RdBu", 0.0
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        padding = max(abs(low) * 0.05, 1.0)
        low, high = low - padding, high + padding
    if low < 0.0 < high:
        bound = max(abs(low), abs(high))
        return -bound, bound, "RdBu", 0.0
    return low, high, "Viridis", None


# ---------------------------------------------------------------------------
# Immutable catalogue and lazy bundles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdentityKey:
    risk_type: str
    risk_greek: str
    underlying: str


@dataclass(frozen=True)
class IdentityBundle:
    key: IdentityKey
    dates: tuple[str, ...]
    swaps: tuple[str, ...]
    options: tuple[str, ...]
    surfaces: tuple[np.ndarray, ...]
    counts: tuple[np.ndarray, ...]
    axis_count: int
    single_axis: str | None
    cmin: float
    cmax: float
    colorscale: str
    cmid: float | None
    rows: int
    duplicate_cells: int


@dataclass(frozen=True)
class HistorySnapshot:
    generation: int
    loaded_at: datetime
    file_count: int
    quote_rows: int
    history: pd.DataFrame
    positions: dict[IdentityKey, np.ndarray]
    risk_types: tuple[str, ...]
    greeks: dict[str, tuple[str, ...]]
    underlyings: dict[tuple[str, str], tuple[str, ...]]
    missing_identity_files: tuple[str, ...]


@dataclass(frozen=True)
class LoadStatus:
    phase: str
    stage: str
    message: str
    current: int
    total: int
    generation: int
    error: str | None
    has_snapshot: bool


def build_bundle(key: IdentityKey, frame: pd.DataFrame) -> IdentityBundle:
    dates = tuple(sorted(frame["Market Date"].astype(str).drop_duplicates()))
    swaps_found = ordered_tenors(frame["Tenor Swap"])
    options_found = ordered_tenors(frame["Tenor Option"])
    has_swap, has_option = bool(swaps_found), bool(options_found)
    axis_count = int(has_swap) + int(has_option)
    single_axis = (
        "swap" if has_swap and not has_option
        else "option" if has_option and not has_swap
        else None
    )
    swaps = swaps_found if has_swap else (NO_TENOR,)
    options = options_found if has_option else (NO_TENOR,)

    work = frame.copy()
    work["Display Swap"] = work["Tenor Swap"].where(
        work["Tenor Swap"].map(meaningful_tenor),
        NO_TENOR,
    )
    work["Display Option"] = work["Tenor Option"].where(
        work["Tenor Option"].map(meaningful_tenor),
        NO_TENOR,
    )

    surfaces: list[np.ndarray] = []
    counts: list[np.ndarray] = []
    for selected_date in dates:
        day = work.loc[work["Market Date"].eq(selected_date)]
        z = (
            day.pivot_table(
                index="Display Option",
                columns="Display Swap",
                values="OFFICIAL",
                aggfunc="mean",
                sort=False,
            )
            .reindex(index=options, columns=swaps)
            .to_numpy(dtype=float)
        )
        source_counts = (
            day.pivot_table(
                index="Display Option",
                columns="Display Swap",
                values="Source_Rows",
                aggfunc="sum",
                sort=False,
            )
            .reindex(index=options, columns=swaps)
            .to_numpy(dtype=float)
        )
        z.flags.writeable = False
        source_counts.flags.writeable = False
        surfaces.append(z)
        counts.append(source_counts)

    cmin, cmax, colorscale, cmid = colour_scale(work["OFFICIAL"])
    return IdentityBundle(
        key=key,
        dates=dates,
        swaps=swaps,
        options=options,
        surfaces=tuple(surfaces),
        counts=tuple(counts),
        axis_count=axis_count,
        single_axis=single_axis,
        cmin=cmin,
        cmax=cmax,
        colorscale=colorscale,
        cmid=cmid,
        rows=len(work),
        duplicate_cells=int((work["Source_Rows"] > 1).sum()),
    )


class HistoryCoordinator:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = Lock()
        self._phase = "idle"
        self._stage = "Waiting"
        self._message = "Waiting for browser-triggered startup."
        self._current = 0
        self._total = 0
        self._generation = 0
        self._error: str | None = None
        self._snapshot: HistorySnapshot | None = None
        self._bundles: OrderedDict[
            tuple[int, IdentityKey], IdentityBundle
        ] = OrderedDict()

    def start(self, *, force: bool = False) -> bool:
        with self._lock:
            if self._phase == "loading":
                return False
            if self._snapshot is not None and not force:
                return False
            self._phase = "loading"
            self._stage = "Starting"
            self._message = f"Scanning {self.data_dir}"
            self._current = 0
            self._total = 0
            self._error = None
            generation = self._generation + 1
        Thread(
            target=self._load,
            args=(generation,),
            name="historical-market-loader",
            daemon=True,
        ).start()
        return True

    def _progress(
        self,
        stage: str,
        message: str,
        current: int,
        total: int,
    ) -> None:
        with self._lock:
            self._stage = stage
            self._message = message
            self._current = current
            self._total = total

    def _load(self, generation: int) -> None:
        try:
            if not self.data_dir.is_dir():
                raise FileNotFoundError(
                    f"Data directory does not exist: {self.data_dir}"
                )
            files = sorted(
                (
                    path
                    for path in self.data_dir.iterdir()
                    if path.is_file() and DATE_FILE_RE.fullmatch(path.name)
                ),
                key=parse_market_date,
            )
            if not files:
                raise FileNotFoundError(
                    f"No 8-digit dated CSV files found in {self.data_dir}"
                )

            frames: list[pd.DataFrame] = []
            missing_identity_files: list[str] = []
            for index, path in enumerate(files, start=1):
                frame, missing = read_history_file(path)
                frames.append(frame)
                if missing:
                    missing_identity_files.append(
                        f"{path.name}: {', '.join(missing)}"
                    )
                self._progress(
                    "Reading CSV history",
                    f"Read {path.name}",
                    index,
                    len(files),
                )

            self._progress(
                "Publishing catalogue",
                "Combining quote cells and indexing exact identities.",
                len(files),
                len(files),
            )
            history = (
                pd.concat(frames, ignore_index=True, sort=False)
                .groupby(
                    [
                        "Market Date",
                        "Risk Type",
                        "Risk Greek",
                        "Underlying",
                        "Tenor Swap",
                        "Tenor Option",
                    ],
                    as_index=False,
                    dropna=False,
                    sort=False,
                )
                .agg(
                    OFFICIAL=("OFFICIAL", "mean"),
                    Source_Rows=("Source_Rows", "sum"),
                )
                .reset_index(drop=True)
            )

            positions: dict[IdentityKey, np.ndarray] = {}
            grouped = history.groupby(
                ["Risk Type", "Risk Greek", "Underlying"],
                sort=False,
                dropna=False,
            ).indices
            for raw_key, raw_positions in grouped.items():
                key = IdentityKey(*(str(value) for value in raw_key))
                compact = np.asarray(raw_positions, dtype=np.int32)
                compact.flags.writeable = False
                positions[key] = compact

            risk_types = tuple(
                sorted(
                    history["Risk Type"].astype(str).drop_duplicates(),
                    key=str.casefold,
                )
            )
            greeks = {
                risk_type: tuple(
                    sorted(
                        history.loc[
                            history["Risk Type"].eq(risk_type),
                            "Risk Greek",
                        ]
                        .astype(str)
                        .drop_duplicates(),
                        key=str.casefold,
                    )
                )
                for risk_type in risk_types
            }
            underlyings = {
                (risk_type, greek): tuple(
                    sorted(
                        history.loc[
                            history["Risk Type"].eq(risk_type)
                            & history["Risk Greek"].eq(greek),
                            "Underlying",
                        ]
                        .astype(str)
                        .drop_duplicates(),
                        key=str.casefold,
                    )
                )
                for risk_type in risk_types
                for greek in greeks[risk_type]
            }
            snapshot = HistorySnapshot(
                generation=generation,
                loaded_at=datetime.now(timezone.utc),
                file_count=len(files),
                quote_rows=len(history),
                history=history,
                positions=positions,
                risk_types=risk_types,
                greeks=greeks,
                underlyings=underlyings,
                missing_identity_files=tuple(missing_identity_files),
            )
        except Exception as exc:
            with self._lock:
                self._phase = "failed"
                self._stage = "Failed"
                self._message = (
                    "Reload failed; the last complete snapshot remains available."
                    if self._snapshot is not None
                    else "Historical data could not be loaded."
                )
                self._error = f"{type(exc).__name__}: {exc}"
            return

        with self._lock:
            self._snapshot = snapshot
            self._bundles.clear()
            self._generation = generation
            self._phase = "ready"
            self._stage = "Ready"
            suffix = (
                f" {len(snapshot.missing_identity_files):,} files used "
                "Unspecified Risk Type/Greek."
                if snapshot.missing_identity_files
                else ""
            )
            self._message = (
                f"Loaded {snapshot.file_count:,} files, "
                f"{snapshot.quote_rows:,} quote cells, and "
                f"{len(snapshot.positions):,} exact identities.{suffix}"
            )
            self._current = self._total
            self._error = None

    def status(self) -> LoadStatus:
        with self._lock:
            return LoadStatus(
                phase=self._phase,
                stage=self._stage,
                message=self._message,
                current=self._current,
                total=self._total,
                generation=self._generation,
                error=self._error,
                has_snapshot=self._snapshot is not None,
            )

    def snapshot(self) -> HistorySnapshot | None:
        with self._lock:
            return self._snapshot

    def bundle(
        self,
        risk_type: str,
        risk_greek: str,
        underlying: str,
    ) -> IdentityBundle:
        snapshot = self.snapshot()
        if snapshot is None:
            raise RuntimeError("Historical snapshot is not ready")

        key = IdentityKey(risk_type, risk_greek, underlying)
        positions = snapshot.positions.get(key)
        if positions is None:
            raise ValueError(
                f"No history for {risk_type} | {risk_greek} | {underlying}"
            )

        cache_key = (snapshot.generation, key)
        with self._lock:
            cached = self._bundles.get(cache_key)
            if cached is not None:
                self._bundles.move_to_end(cache_key)
                return cached

        bundle = build_bundle(key, snapshot.history.iloc[positions].copy())
        with self._lock:
            self._bundles[cache_key] = bundle
            self._bundles.move_to_end(cache_key)
            while len(self._bundles) > 12:
                self._bundles.popitem(last=False)
        return bundle


# ---------------------------------------------------------------------------
# Plot construction
# ---------------------------------------------------------------------------

def axis_ticks(labels: tuple[str, ...], maximum: int = 9) -> dict[str, Any]:
    if len(labels) <= maximum:
        positions = list(range(len(labels)))
    else:
        positions = sorted(
            set(
                np.linspace(
                    0,
                    len(labels) - 1,
                    maximum,
                    dtype=int,
                ).tolist()
            )
        )
    return {
        "tickmode": "array",
        "tickvals": positions,
        "ticktext": [labels[position] for position in positions],
        "tickfont": {"size": 10},
    }


def scene_layout(
    *,
    x_title: str,
    x_labels: tuple[str, ...],
    y_title: str,
    y_labels: tuple[str, ...],
    bundle: IdentityBundle,
    revision: str,
) -> dict[str, Any]:
    return {
        "domain": {"x": [0.05, 0.95], "y": [0.12, 0.98]},
        "camera": {"eye": {"x": 1.55, "y": 1.55, "z": 1.1}},
        "aspectmode": "manual",
        "aspectratio": {"x": 1.32, "y": 1.08, "z": 0.82},
        "xaxis": {
            "title": {"text": x_title, "font": {"size": 12}},
            **axis_ticks(x_labels),
            "showspikes": False,
        },
        "yaxis": {
            "title": {"text": y_title, "font": {"size": 12}},
            **axis_ticks(y_labels),
            "showspikes": False,
        },
        "zaxis": {
            "title": {"text": "OFFICIAL", "font": {"size": 12}},
            "tickfont": {"size": 10},
            "range": [bundle.cmin, bundle.cmax],
            "showspikes": False,
        },
        "uirevision": revision,
    }


def surface_trace(
    *,
    z: np.ndarray | list[list[float | None]],
    text: list[list[str]],
    bundle: IdentityBundle,
) -> go.Surface:
    settings: dict[str, Any] = {
        "z": z,
        "text": text,
        "hovertemplate": (
            "%{text}<br>OFFICIAL: %{z:,.6g}<extra></extra>"
        ),
        "colorscale": bundle.colorscale,
        "reversescale": bundle.colorscale == "RdBu",
        "cmin": bundle.cmin,
        "cmax": bundle.cmax,
        "connectgaps": False,
        "colorbar": {
            "title": "OFFICIAL",
            "thickness": 17,
            "len": 0.68,
            "x": 0.97,
        },
    }
    if bundle.cmid is not None:
        settings["cmid"] = bundle.cmid
    return go.Surface(**settings)


def figure_shell(*, revision: str, bottom: int = 45) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        template="plotly_white",
        height=650,
        autosize=True,
        margin={"l": 52, "r": 58, "t": 8, "b": bottom},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        uirevision=revision,
    )
    return figure


def date_labels(dates: tuple[str, ...], maximum: int = 8) -> set[int]:
    return set(
        np.linspace(
            0,
            len(dates) - 1,
            min(maximum, len(dates)),
            dtype=int,
        ).tolist()
    )


def animated_heatmap(bundle: IdentityBundle) -> go.Figure:
    latest = len(bundle.dates) - 1
    x = tuple(range(len(bundle.swaps)))
    y = tuple(range(len(bundle.options)))

    def hover(date_index: int) -> list[list[str]]:
        counts = bundle.counts[date_index]
        return [
            [
                (
                    f"<b>{swap} swap</b><br>{option} option<br>"
                    f"Source rows: "
                    f"{int(counts[option_index, swap_index]) if np.isfinite(counts[option_index, swap_index]) else 0}"
                )
                for swap_index, swap in enumerate(bundle.swaps)
            ]
            for option_index, option in enumerate(bundle.options)
        ]

    figure = figure_shell(
        revision=f"heatmap::{bundle.key}",
        bottom=118,
    )
    initial = surface_trace(
        z=bundle.surfaces[latest],
        text=hover(latest),
        bundle=bundle,
    )
    initial.x = x
    initial.y = y
    initial.contours = {
        "z": {
            "show": True,
            "usecolormap": True,
            "project": {"z": True},
        }
    }
    figure.add_trace(initial)

    figure.frames = tuple(
        go.Frame(
            name=date,
            data=(
                surface_trace(
                    z=bundle.surfaces[index],
                    text=hover(index),
                    bundle=bundle,
                ),
            ),
        )
        for index, date in enumerate(bundle.dates)
    )

    marked = date_labels(bundle.dates)
    steps = [
        {
            "method": "animate",
            "label": (
                pd.Timestamp(date).strftime("%d %b")
                if index in marked
                else ""
            ),
            "args": [
                [date],
                {
                    "mode": "immediate",
                    "frame": {"duration": 0, "redraw": True},
                    "transition": {"duration": 0},
                },
            ],
        }
        for index, date in enumerate(bundle.dates)
    ]
    figure.update_layout(
        scene=scene_layout(
            x_title="Tenor Swap",
            x_labels=bundle.swaps,
            y_title="Tenor Option",
            y_labels=bundle.options,
            bundle=bundle,
            revision=f"heatmap::{bundle.key}",
        ),
        updatemenus=[
            {
                "type": "buttons",
                "direction": "left",
                "showactive": False,
                "x": 0.01,
                "xanchor": "left",
                "y": -0.11,
                "yanchor": "top",
                "pad": {"r": 8, "t": 0},
                "buttons": [
                    {
                        "label": "▶ Play",
                        "method": "animate",
                        "args": [
                            None,
                            {
                                "fromcurrent": True,
                                "mode": "immediate",
                                "frame": {
                                    "duration": 425,
                                    "redraw": True,
                                },
                                "transition": {"duration": 0},
                            },
                        ],
                    },
                    {
                        "label": "❚❚ Pause",
                        "method": "animate",
                        "args": [
                            [None],
                            {
                                "mode": "immediate",
                                "frame": {
                                    "duration": 0,
                                    "redraw": False,
                                },
                                "transition": {"duration": 0},
                            },
                        ],
                    },
                ],
            }
        ],
        sliders=[
            {
                "active": latest,
                "x": 0.18,
                "len": 0.78,
                "y": -0.09,
                "yanchor": "top",
                "pad": {"t": 4, "b": 0},
                "currentvalue": {
                    "prefix": "Market date: ",
                    "visible": True,
                    "xanchor": "right",
                    "font": {"size": 12},
                },
                "transition": {"duration": 0},
                "steps": steps,
            }
        ],
    )
    return figure


def history_surface(
    bundle: IdentityBundle,
    *,
    fixed_axis: str | None,
    tenor: str | None,
) -> tuple[go.Figure, str]:
    dates = bundle.dates

    if fixed_axis == "swap":
        swap_index = max(0, bundle.swaps.index(str(tenor)))
        labels = bundle.options
        z = np.asarray(
            [
                [
                    bundle.surfaces[date_index][option_index, swap_index]
                    for date_index in range(len(dates))
                ]
                for option_index in range(len(labels))
            ],
            dtype=float,
        )
        text = [
            [
                f"<b>{date}</b><br>Swap {tenor}<br>Option {option}"
                for date in dates
            ]
            for option in labels
        ]
        y_title = "Tenor Option"
        title = f"Swap {tenor} through time"
    elif fixed_axis == "option":
        option_index = max(0, bundle.options.index(str(tenor)))
        labels = bundle.swaps
        z = np.asarray(
            [
                [
                    bundle.surfaces[date_index][option_index, swap_index]
                    for date_index in range(len(dates))
                ]
                for swap_index in range(len(labels))
            ],
            dtype=float,
        )
        text = [
            [
                f"<b>{date}</b><br>Option {tenor}<br>Swap {swap}"
                for date in dates
            ]
            for swap in labels
        ]
        y_title = "Tenor Swap"
        title = f"Option {tenor} through time"
    elif bundle.axis_count == 1:
        labels = bundle.swaps if bundle.single_axis == "swap" else bundle.options
        z = np.asarray(
            [
                [
                    (
                        bundle.surfaces[date_index][0, axis_index]
                        if bundle.single_axis == "swap"
                        else bundle.surfaces[date_index][axis_index, 0]
                    )
                    for date_index in range(len(dates))
                ]
                for axis_index in range(len(labels))
            ],
            dtype=float,
        )
        text = [
            [
                (
                    f"<b>{date}</b><br>"
                    f"{'Swap' if bundle.single_axis == 'swap' else 'Option'} {label}"
                )
                for date in dates
            ]
            for label in labels
        ]
        y_title = "Tenor Swap" if bundle.single_axis == "swap" else "Tenor Option"
        title = "No tenor selection"
    else:
        labels_list: list[str] = []
        values: list[list[float]] = []
        text = []
        for option_index, option in enumerate(bundle.options):
            for swap_index, swap in enumerate(bundle.swaps):
                row = [
                    bundle.surfaces[date_index][option_index, swap_index]
                    for date_index in range(len(dates))
                ]
                if any(np.isfinite(value) for value in row):
                    label = f"Option {option} · Swap {swap}"
                    labels_list.append(label)
                    values.append(row)
                    text.append(
                        [f"<b>{date}</b><br>{label}" for date in dates]
                    )
        labels = tuple(labels_list)
        z = np.asarray(values, dtype=float)
        y_title = "Tenor cell"
        title = "No tenor selection"

    figure = figure_shell(
        revision=f"history::{bundle.key}::{fixed_axis}::{tenor}",
    )
    trace = surface_trace(z=z, text=text, bundle=bundle)
    trace.x = tuple(range(len(dates)))
    trace.y = tuple(range(len(labels)))
    figure.add_trace(trace)
    figure.update_layout(
        scene=scene_layout(
            x_title="Market Date",
            x_labels=dates,
            y_title=y_title,
            y_labels=labels,
            bundle=bundle,
            revision=f"history::{bundle.key}::{fixed_axis}::{tenor}",
        )
    )
    return figure, title


def flat_history(
    bundle: IdentityBundle,
    *,
    tenor: str | None,
) -> tuple[go.Figure, str]:
    if bundle.axis_count == 0:
        values = [
            float(np.nanmean(surface))
            if np.isfinite(surface).any()
            else np.nan
            for surface in bundle.surfaces
        ]
        title = "No tenor"
    elif bundle.single_axis == "swap":
        index = max(0, bundle.swaps.index(str(tenor)))
        values = [surface[0, index] for surface in bundle.surfaces]
        title = f"Tenor Swap {tenor}"
    else:
        index = max(0, bundle.options.index(str(tenor)))
        values = [surface[index, 0] for surface in bundle.surfaces]
        title = f"Tenor Option {tenor}"

    figure = figure_shell(
        revision=f"line::{bundle.key}::{tenor}",
        bottom=72,
    )
    figure.add_trace(
        go.Scatter(
            x=bundle.dates,
            y=values,
            name="OFFICIAL",
            mode="lines+markers",
            line={"width": 3},
            marker={"size": 6},
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "OFFICIAL: %{y:,.6g}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        hovermode="x unified",
        xaxis={
            "title": "Market Date",
            "type": "date",
            "automargin": True,
            "tickformat": "%d %b\n%Y",
        },
        yaxis={"title": "OFFICIAL", "automargin": True},
    )
    return figure, title


def empty_figure(message: str) -> go.Figure:
    figure = figure_shell(revision="empty")
    figure.add_annotation(
        text=message,
        showarrow=False,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
    )
    return figure


# ---------------------------------------------------------------------------
# Dash app
# ---------------------------------------------------------------------------

APP_CSS = """
*{box-sizing:border-box}html,body{margin:0;background:#f4f6f8}
body{font-family:"Segoe UI Variable Text","Segoe UI",Arial,sans-serif;color:#111}
.page{min-height:100vh;padding:1px 16px 32px}
.card{width:min(100%,1400px);margin:22px auto;overflow:hidden;border:1px solid #d9dee5;border-radius:14px;background:#fff;box-shadow:0 8px 28px rgba(16,24,40,.08)}
.header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 22px 14px;border-bottom:1px solid #d9dee5}
.header h1{margin:0;font-size:22px}.header p{margin:6px 0 0;color:#626875;font-size:13px}
.reload{min-height:38px;padding:8px 14px;border:1px solid #b7c0ca;border-radius:8px;background:#fff;font-weight:800}
.load{padding:12px 22px;border-bottom:1px solid #d9dee5;background:#f7f8fa}
.loadtop,.charthead{display:flex;justify-content:space-between;gap:12px}
.track{height:7px;margin-top:7px;overflow:hidden;border-radius:999px;background:#e8ebef}
.controls{display:grid;grid-template-columns:repeat(5,minmax(160px,1fr));align-items:end;gap:13px;padding:15px 22px;border-bottom:1px solid #d9dee5}
.field{min-width:0}.field label{display:block;margin-bottom:6px;font-size:12px;font-weight:800}
.charthead{align-items:baseline;padding:12px 22px 0}.charttitle{font-size:15px;font-weight:850}.chartsub{color:#626875;font-size:11px}
.graph{height:650px;min-height:650px}.status{padding:11px 22px 16px;color:#626875;font-size:12px}
@media(max-width:1050px){.controls{grid-template-columns:repeat(2,minmax(180px,1fr))}}
@media(max-width:680px){.page{padding:1px 8px 24px}.header,.charthead{align-items:flex-start;flex-direction:column}.controls{grid-template-columns:1fr;padding:13px}.graph{height:560px;min-height:560px}}
"""


def build_layout(data_dir: Path) -> html.Main:
    dropdown = lambda component_id, placeholder="": dcc.Dropdown(
        id=component_id,
        options=[],
        clearable=False,
        searchable=True,
        disabled=True,
        placeholder=placeholder,
    )
    return html.Main(
        [
            dcc.Interval(
                id="startup-trigger",
                interval=300,
                n_intervals=0,
                max_intervals=1,
            ),
            dcc.Interval(
                id="status-poll",
                interval=500,
                n_intervals=0,
                disabled=False,
            ),
            dcc.Store(id="generation-store", data=0),
            dcc.Store(id="bundle-token"),
            html.Section(
                [
                    html.Header(
                        [
                            html.Div(
                                [
                                    html.H1("Historical market tenor viewer"),
                                    html.P(
                                        "Cube-style Risk Type, Risk Greek, Underlying and tenor views through time."
                                    ),
                                ]
                            ),
                            html.Button(
                                "Reload history",
                                id="reload-button",
                                n_clicks=0,
                                disabled=True,
                                className="reload",
                            ),
                        ],
                        className="header",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Strong(id="load-stage", children="Waiting"),
                                    html.Span(id="load-percent", children="0%"),
                                ],
                                className="loadtop",
                            ),
                            html.Div(
                                html.Div(
                                    id="progress-fill",
                                    style={
                                        "height": "100%",
                                        "width": "0%",
                                        "background": "#111",
                                    },
                                ),
                                className="track",
                            ),
                            html.Div(
                                id="load-status",
                                children="Waiting for browser-triggered startup.",
                                style={
                                    "marginTop": "8px",
                                    "color": "#626875",
                                    "fontSize": "12px",
                                },
                            ),
                            html.Code(
                                str(data_dir),
                                style={
                                    "display": "block",
                                    "marginTop": "4px",
                                    "color": "#7a838d",
                                    "fontSize": "10px",
                                    "overflowWrap": "anywhere",
                                },
                            ),
                        ],
                        className="load",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Risk Type"),
                                    dropdown("risk-type", "History is loading…"),
                                ],
                                className="field",
                            ),
                            html.Div(
                                [
                                    html.Label("Risk Greek"),
                                    dropdown("risk-greek"),
                                ],
                                className="field",
                            ),
                            html.Div(
                                [
                                    html.Label("Underlying"),
                                    dropdown("underlying"),
                                ],
                                className="field",
                            ),
                            html.Div(
                                [
                                    html.Label("Tenor view"),
                                    dropdown("tenor-view"),
                                ],
                                className="field",
                            ),
                            html.Div(
                                [
                                    html.Label(
                                        id="tenor-label",
                                        children="Specific tenor",
                                    ),
                                    dropdown("tenor-choice"),
                                ],
                                id="tenor-field",
                                className="field",
                            ),
                        ],
                        className="controls",
                    ),
                    html.Div(
                        [
                            html.Div(
                                id="chart-title",
                                children="Historical market",
                                className="charttitle",
                            ),
                            html.Div(
                                id="chart-subtitle",
                                children="Choose an exact identity.",
                                className="chartsub",
                            ),
                        ],
                        className="charthead",
                    ),
                    dcc.Loading(
                        dcc.Graph(
                            id="chart",
                            figure=empty_figure("Historical data is loading…"),
                            responsive=True,
                            config={
                                "displaylogo": False,
                                "responsive": True,
                                "scrollZoom": True,
                            },
                            className="graph",
                        ),
                        type="dot",
                        delay_show=120,
                    ),
                    html.Div(
                        id="chart-status",
                        children="Waiting for a complete history snapshot.",
                        className="status",
                    ),
                ],
                className="card",
            ),
        ],
        className="page",
    )


def create_app(settings: RuntimeSettings | None = None) -> Dash:
    settings = settings or RuntimeSettings.from_env()
    project_root = discover_project_root()
    data_dir = resolve_data_path(
        os.getenv("CUBE_HISTORY_DATA_DIR"),
        Path("data"),
        root=project_root,
    )
    coordinator = HistoryCoordinator(data_dir)

    app = Dash(__name__, **settings.dash_kwargs)
    app.title = "Historical market tenor viewer"
    app.layout = lambda: build_layout(data_dir)
    app.index_string = f"""<!DOCTYPE html>
<html><head>{{%metas%}}<title>{{%title%}}</title>{{%favicon%}}{{%css%}}
<style>{APP_CSS}</style></head>
<body>{{%app_entry%}}<footer>{{%config%}}{{%scripts%}}{{%renderer%}}</footer></body>
</html>"""

    prefix = app.config.routes_pathname_prefix or "/"
    health_path = f"{prefix.rstrip('/')}/healthz" or "/healthz"
    progress_path = f"{prefix.rstrip('/')}/progressz" or "/progressz"

    @app.server.get(health_path)
    def health():
        status = coordinator.status()
        return jsonify(
            status=(
                "ok"
                if status.has_snapshot and status.phase != "failed"
                else "degraded"
                if status.phase == "failed"
                else "starting"
            ),
            phase=status.phase,
            generation=status.generation,
            error=status.error,
        )

    @app.server.get(progress_path)
    def progress():
        return jsonify(**coordinator.status().__dict__)

    @app.server.after_request
    def response_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if (
            request.path in {health_path, progress_path}
            or request.path.endswith("_dash-layout")
            or request.path.endswith("_dash-update-component")
            or response.mimetype == "application/json"
        ):
            response.headers["Cache-Control"] = "no-store, private"
        return response

    @app.callback(
        Output("load-stage", "children"),
        Output("load-percent", "children"),
        Output("progress-fill", "style"),
        Output("load-status", "children"),
        Output("risk-type", "options"),
        Output("risk-type", "value"),
        Output("risk-type", "disabled"),
        Output("generation-store", "data"),
        Output("status-poll", "disabled"),
        Output("reload-button", "children"),
        Output("reload-button", "disabled"),
        Input("startup-trigger", "n_intervals"),
        Input("status-poll", "n_intervals"),
        Input("reload-button", "n_clicks"),
        State("risk-type", "value"),
    )
    def startup(_startup, _poll, _reload, current):
        if ctx.triggered_id == "reload-button":
            coordinator.start(force=True)
        elif ctx.triggered_id == "startup-trigger" or coordinator.status().phase == "idle":
            coordinator.start()

        status = coordinator.status()
        snapshot = coordinator.snapshot()
        percent = (
            100
            if status.phase == "ready"
            else min(100, round(status.current / max(status.total, 1) * 100))
        )
        values = snapshot.risk_types if snapshot else ()
        selected = current if current in values else (values[0] if values else None)
        fill = {
            "height": "100%",
            "width": f"{percent}%",
            "borderRadius": "999px",
            "background": (
                "#b42318"
                if status.phase == "failed"
                else "#297a43"
                if status.phase == "ready"
                else "#111"
            ),
            "transition": "width 180ms linear",
        }
        message = status.message + (f" {status.error}" if status.error else "")
        return (
            status.stage,
            f"{percent}%",
            fill,
            message,
            [{"label": value, "value": value} for value in values],
            selected,
            snapshot is None,
            status.generation,
            status.phase in {"ready", "failed"},
            (
                "Loading…"
                if status.phase == "loading"
                else "Retry history"
                if status.phase == "failed"
                else "Reload history"
            ),
            status.phase == "loading",
        )

    @app.callback(
        Output("risk-greek", "options"),
        Output("risk-greek", "value"),
        Output("risk-greek", "disabled"),
        Input("risk-type", "value"),
        Input("generation-store", "data"),
        State("risk-greek", "value"),
    )
    def select_greek(risk_type, _generation, current):
        snapshot = coordinator.snapshot()
        values = snapshot.greeks.get(risk_type, ()) if snapshot and risk_type else ()
        selected = current if current in values else (values[0] if values else None)
        return (
            [{"label": value, "value": value} for value in values],
            selected,
            not bool(values),
        )

    @app.callback(
        Output("underlying", "options"),
        Output("underlying", "value"),
        Output("underlying", "disabled"),
        Input("risk-type", "value"),
        Input("risk-greek", "value"),
        Input("generation-store", "data"),
        State("underlying", "value"),
    )
    def select_underlying(risk_type, greek, _generation, current):
        snapshot = coordinator.snapshot()
        values = (
            snapshot.underlyings.get((risk_type, greek), ())
            if snapshot and risk_type and greek
            else ()
        )
        selected = current if current in values else (values[0] if values else None)
        return (
            [{"label": value, "value": value} for value in values],
            selected,
            not bool(values),
        )

    @app.callback(
        Output("tenor-view", "options"),
        Output("tenor-view", "value"),
        Output("tenor-view", "disabled"),
        Output("tenor-label", "children"),
        Output("tenor-choice", "options"),
        Output("tenor-choice", "value"),
        Output("tenor-choice", "disabled"),
        Output("tenor-field", "style"),
        Output("bundle-token", "data"),
        Input("risk-type", "value"),
        Input("risk-greek", "value"),
        Input("underlying", "value"),
        Input("generation-store", "data"),
        State("tenor-view", "value"),
        State("tenor-choice", "value"),
    )
    def configure_view(
        risk_type,
        greek,
        underlying,
        generation,
        requested_view,
        requested_tenor,
    ):
        if not risk_type or not greek or not underlying:
            return [], None, True, "Specific tenor", [], None, True, {"display": "none"}, None

        try:
            bundle = coordinator.bundle(risk_type, greek, underlying)
        except Exception as exc:
            return [], None, True, "Specific tenor", [], None, True, {"display": "none"}, {
                "error": f"{type(exc).__name__}: {exc}"
            }

        if bundle.axis_count == 2:
            view_options = [
                ("Heatmap (3-D surface)", "heatmap"),
                ("Tenor Swap through time", "swap_history"),
                ("Tenor Option through time", "option_history"),
                ("No tenor selection (3-D history)", "all_history"),
            ]
            default_view = "heatmap"
        elif bundle.axis_count == 1:
            axis_label = "Tenor Swap" if bundle.single_axis == "swap" else "Tenor Option"
            view_options = [
                (f"{axis_label} time series", "tenor_line"),
                ("No tenor selection (3-D history)", "all_history"),
            ]
            default_view = "tenor_line"
        else:
            view_options = [("No tenor (time series)", "flat")]
            default_view = "flat"

        valid_views = {value for _label, value in view_options}
        view = requested_view if requested_view in valid_views else default_view

        tenor_label = "Specific tenor"
        tenor_values: tuple[str, ...] = ()
        if view == "swap_history":
            tenor_label, tenor_values = "Tenor Swap", bundle.swaps
        elif view == "option_history":
            tenor_label, tenor_values = "Tenor Option", bundle.options
        elif view == "tenor_line":
            if bundle.single_axis == "swap":
                tenor_label, tenor_values = "Tenor Swap", bundle.swaps
            else:
                tenor_label, tenor_values = "Tenor Option", bundle.options

        tenor = (
            requested_tenor
            if requested_tenor in tenor_values
            else tenor_values[0]
            if tenor_values
            else None
        )
        token = {
            "generation": generation,
            "risk_type": risk_type,
            "risk_greek": greek,
            "underlying": underlying,
        }
        return (
            [{"label": label, "value": value} for label, value in view_options],
            view,
            False,
            tenor_label,
            [{"label": value, "value": value} for value in tenor_values],
            tenor,
            not bool(tenor_values),
            {} if tenor_values else {"display": "none"},
            token,
        )

    @app.callback(
        Output("chart", "figure"),
        Output("chart-title", "children"),
        Output("chart-subtitle", "children"),
        Output("chart-status", "children"),
        Input("bundle-token", "data"),
        Input("tenor-view", "value"),
        Input("tenor-choice", "value"),
    )
    def render_chart(token, view, tenor):
        if not token:
            return (
                empty_figure("Choose Risk Type, Risk Greek and Underlying"),
                "Historical market",
                "Choose an exact identity.",
                "No identity is selected.",
            )
        if token.get("error"):
            return (
                empty_figure(token["error"]),
                "Historical market",
                "The selected identity could not be loaded.",
                token["error"],
            )

        bundle = coordinator.bundle(
            token["risk_type"],
            token["risk_greek"],
            token["underlying"],
        )
        identity = (
            f"{bundle.key.risk_type} · {bundle.key.risk_greek} · "
            f"{bundle.key.underlying}"
        )
        if view == "heatmap":
            figure = animated_heatmap(bundle)
            title = f"{identity} · Heatmap"
            subtitle = "Drag the date control, or use Play / Pause below the surface."
        elif view == "swap_history":
            figure, suffix = history_surface(
                bundle,
                fixed_axis="swap",
                tenor=tenor,
            )
            title = f"{identity} · {suffix}"
            subtitle = "3-D Market Date × Tenor Option surface."
        elif view == "option_history":
            figure, suffix = history_surface(
                bundle,
                fixed_axis="option",
                tenor=tenor,
            )
            title = f"{identity} · {suffix}"
            subtitle = "3-D Market Date × Tenor Swap surface."
        elif view == "all_history":
            figure, suffix = history_surface(
                bundle,
                fixed_axis=None,
                tenor=None,
            )
            title = f"{identity} · {suffix}"
            subtitle = "3-D historical surface with no specific tenor fixed."
        else:
            figure, suffix = flat_history(bundle, tenor=tenor)
            title = f"{identity} · {suffix}"
            subtitle = "Flat historical time series."

        status = (
            f"{bundle.rows:,} quote cells across {len(bundle.dates):,} dates"
            + (
                f" · {bundle.duplicate_cells:,} duplicate cells averaged"
                if bundle.duplicate_cells
                else ""
            )
        )
        return figure, title, subtitle, status

    return app


SETTINGS = RuntimeSettings.from_env()
app = create_app(SETTINGS)
server = app.server


def run_app() -> None:
    app.run(
        debug=SETTINGS.debug,
        host=SETTINGS.host,
        port=SETTINGS.port,
        use_reloader=False,
    )


if __name__ == "__main__":
    run_app()
