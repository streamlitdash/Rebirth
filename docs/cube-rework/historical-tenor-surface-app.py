"""
Single-file Dash viewer for DDMMYYYY.csv double-tenor history.

Expected data
-------------
data/
    01082026.csv
    04082026.csv
    ...

Columns:
    Underlying, Tenor Swap, Tenor Option, OFFICIAL

This follows Rebirth's s01_app.py lifecycle:
    CLI -> environment -> RuntimeSettings -> create_app -> app/server -> run_app

The Dash server is constructed without reading the CSV history. The first
browser paint starts one daemon loader, and the last complete history snapshot
is published atomically. Date surfaces are precomputed once, so slider dragging
does not run pandas groupby/pivot work.

Install:
    python -m pip install dash pandas numpy plotly

Run:
    python historical_tenor_surface_app.py
    python historical_tenor_surface_app.py --data-dir data
    python historical_tenor_surface_app.py --port 8050 --debug
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Final, Mapping

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html
from flask import jsonify, request


# ---------------------------------------------------------------------------
# Runtime setup — mirrors Rebirth s01_app.py / s02_config.py.
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
    candidate = (
        default if value is None or not value.strip() else Path(value).expanduser()
    )
    return (root / candidate if not candidate.is_absolute() else candidate).resolve()


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
            raise ValueError(
                "DASH_JUPYTERHUB_MODE must be either 'proxy' or 'service'"
            )

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

        host = values.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
        return cls(
            host=host,
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


_parser = argparse.ArgumentParser(
    description="Historical double-tenor market surface"
)
_parser.add_argument("--port", type=int, default=None)
_parser.add_argument("--host", type=str, default=None)
_parser.add_argument("--debug", action="store_true")
_parser.add_argument(
    "--data-dir",
    type=str,
    default=None,
    help="Folder containing DDMMYYYY.csv files (default: ./data).",
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
    if args.data_dir is not None:
        os.environ["CUBE_HISTORY_DATA_DIR"] = args.data_dir


# ---------------------------------------------------------------------------
# Historical source and precomputed display index.
# ---------------------------------------------------------------------------

APP_ROOT: Final = Path(__file__).resolve().parent
DATE_FILE_RE: Final = re.compile(r"^(?P<date>\d{8})\.csv$")
REQUIRED_COLUMNS: Final = (
    "Underlying",
    "Tenor Swap",
    "Tenor Option",
    "OFFICIAL",
)
CANONICAL_BY_CASEFOLD: Final = {
    column.casefold(): column for column in REQUIRED_COLUMNS
}
REQUIRED_CASEFOLDED: Final = frozenset(CANONICAL_BY_CASEFOLD)

NATURAL_TOKEN_RE: Final = re.compile(r"(\d+(?:\.\d+)?)")
TENOR_RE: Final = re.compile(
    r"^(?P<number>\d+(?:\.\d+)?)(?P<unit>[DWMY])$",
    re.IGNORECASE,
)
UNIT_DAYS: Final = {"D": 1.0, "W": 7.0, "M": 30.4375, "Y": 365.25}
SPECIAL_TENORS: Final = {
    "ON": 0.10,
    "O/N": 0.10,
    "TN": 0.20,
    "T/N": 0.20,
    "SN": 0.30,
    "S/N": 0.30,
    "SPOT": 0.40,
}


def parse_market_date(path: Path) -> pd.Timestamp:
    match = DATE_FILE_RE.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Not a DDMMYYYY.csv file: {path.name}")
    try:
        value = datetime.strptime(match.group("date"), "%d%m%Y")
    except ValueError as exc:
        raise ValueError(f"Invalid date filename: {path.name}") from exc
    return pd.Timestamp(value).normalize()


def use_history_column(column: object) -> bool:
    return str(column).strip().casefold() in REQUIRED_CASEFOLDED


def clean_file(path: Path) -> pd.DataFrame:
    date = parse_market_date(path)
    raw = pd.read_csv(path, usecols=use_history_column, low_memory=False)

    rename = {}
    for column in raw.columns:
        canonical = CANONICAL_BY_CASEFOLD.get(str(column).strip().casefold())
        if canonical:
            rename[column] = canonical
    frame = raw.rename(columns=rename)

    missing = [column for column in REQUIRED_COLUMNS if column not in frame]
    if missing:
        raise ValueError(
            f"{path.name} is missing {missing}; found {list(raw.columns)}"
        )
    frame = frame.loc[:, list(REQUIRED_COLUMNS)].copy()

    for column in ("Underlying", "Tenor Swap", "Tenor Option"):
        values = frame[column].astype("string").str.strip()
        if column == "Underlying":
            bad = values.isna() | values.eq("")
            if bad.any():
                raise ValueError(
                    f"{path.name}: blank Underlying rows "
                    f"{frame.index[bad].tolist()[:5]}"
                )
        else:
            values = values.fillna("N/A").mask(values.eq(""), "N/A")
        frame[column] = values.astype(str)

    raw_official = frame["OFFICIAL"]
    official = pd.to_numeric(raw_official, errors="coerce")
    nonblank = raw_official.notna() & raw_official.astype("string").str.strip().ne("")
    bad = nonblank & official.isna()
    bad |= official.notna() & ~np.isfinite(official)
    if bad.any():
        raise ValueError(
            f"{path.name}: invalid OFFICIAL rows "
            f"{frame.index[bad].tolist()[:5]}"
        )

    frame["OFFICIAL"] = official.astype(float)
    frame = frame.loc[frame["OFFICIAL"].notna()].copy()
    frame.insert(0, "Market Date", date.date().isoformat())

    return (
        frame.groupby(
            ["Market Date", "Underlying", "Tenor Swap", "Tenor Option"],
            as_index=False,
            dropna=False,
            sort=False,
        )
        .agg(
            OFFICIAL=("OFFICIAL", "mean"),
            Source_Rows=("OFFICIAL", "size"),
        )
        .reset_index(drop=True)
    )


def natural_tokens(value: str) -> tuple[object, ...]:
    return tuple(
        float(part) if part.replace(".", "", 1).isdigit() else part
        for part in NATURAL_TOKEN_RE.split(value.casefold())
    )


def tenor_sort_key(value: object) -> tuple[object, ...]:
    label = str(value).strip()
    normalized = label.upper().replace(" ", "")
    if normalized in SPECIAL_TENORS:
        return (0, SPECIAL_TENORS[normalized], natural_tokens(label))
    match = TENOR_RE.fullmatch(normalized)
    if match:
        days = float(match.group("number")) * UNIT_DAYS[match.group("unit").upper()]
        return (1, days, natural_tokens(label))
    return (2, float("inf"), natural_tokens(label))


def ordered_tenors(values: pd.Series) -> tuple[str, ...]:
    labels = values.dropna().astype(str).str.strip().drop_duplicates().tolist()
    return tuple(sorted(labels, key=tenor_sort_key))


@dataclass(frozen=True)
class SurfaceFrame:
    z: np.ndarray
    counts: np.ndarray
    quote_rows: int
    duplicate_cells: int


@dataclass(frozen=True)
class UnderlyingData:
    dates: tuple[str, ...]
    swap_tenors: tuple[str, ...]
    option_tenors: tuple[str, ...]
    surfaces: dict[str, SurfaceFrame]
    cmin: float
    cmax: float
    colorscale: str
    cmid: float | None


@dataclass(frozen=True)
class HistorySnapshot:
    generation: int
    loaded_at: datetime
    file_count: int
    quote_rows: int
    underlyings: tuple[str, ...]
    data: dict[str, UnderlyingData]


def color_range(values: pd.Series) -> tuple[float, float, str, float | None]:
    finite = pd.to_numeric(values, errors="coerce")
    finite = finite[np.isfinite(finite)]
    if finite.empty:
        return -1.0, 1.0, "RdBu_r", 0.0

    low, high = float(finite.min()), float(finite.max())
    if low == high:
        pad = max(abs(low) * 0.05, 1.0)
        low, high = low - pad, high + pad
    if low < 0 < high:
        bound = max(abs(low), abs(high))
        return -bound, bound, "RdBu_r", 0.0
    return low, high, "Viridis", None


def build_underlying_data(frame: pd.DataFrame) -> UnderlyingData:
    dates = tuple(sorted(frame["Market Date"].drop_duplicates().astype(str)))
    swap_tenors = ordered_tenors(frame["Tenor Swap"])
    option_tenors = ordered_tenors(frame["Tenor Option"])
    cmin, cmax, colorscale, cmid = color_range(frame["OFFICIAL"])

    surfaces = {}
    for date in dates:
        day = frame.loc[frame["Market Date"].eq(date)]
        z = (
            day.pivot_table(
                index="Tenor Option",
                columns="Tenor Swap",
                values="OFFICIAL",
                aggfunc="mean",
                sort=False,
            )
            .reindex(index=option_tenors, columns=swap_tenors)
            .to_numpy(dtype=float)
        )
        counts = (
            day.pivot_table(
                index="Tenor Option",
                columns="Tenor Swap",
                values="Source_Rows",
                aggfunc="sum",
                sort=False,
            )
            .reindex(index=option_tenors, columns=swap_tenors)
            .to_numpy(dtype=float)
        )
        z.flags.writeable = False
        counts.flags.writeable = False
        surfaces[date] = SurfaceFrame(
            z=z,
            counts=counts,
            quote_rows=len(day),
            duplicate_cells=int((day["Source_Rows"] > 1).sum()),
        )

    return UnderlyingData(
        dates=dates,
        swap_tenors=swap_tenors,
        option_tenors=option_tenors,
        surfaces=surfaces,
        cmin=cmin,
        cmax=cmax,
        colorscale=colorscale,
        cmid=cmid,
    )


# ---------------------------------------------------------------------------
# Startup coordinator: the server paints before CSV source work starts.
# ---------------------------------------------------------------------------

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


class HistoryCoordinator:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = Lock()
        self._phase = "idle"
        self._stage = "Waiting"
        self._message = "Waiting for the browser-triggered history load."
        self._current = 0
        self._total = 0
        self._generation = 0
        self._error = None
        self._snapshot = None

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
            name="history-loader",
            daemon=True,
        ).start()
        return True

    def progress(self, stage: str, current: int, total: int, message: str) -> None:
        with self._lock:
            self._stage = stage
            self._current = int(current)
            self._total = int(total)
            self._message = message

    def _load(self, generation: int) -> None:
        try:
            if not self.data_dir.is_dir():
                raise FileNotFoundError(
                    f"Data folder does not exist: {self.data_dir}"
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
                    f"No DDMMYYYY.csv files found in {self.data_dir}"
                )

            frames = []
            for index, path in enumerate(files, start=1):
                frames.append(clean_file(path))
                self.progress(
                    "Reading CSV history",
                    index,
                    len(files),
                    f"Read {path.name}",
                )

            self.progress(
                "Combining files",
                len(files),
                len(files),
                "Combining validated quote cells.",
            )
            history = pd.concat(frames, ignore_index=True, sort=False)
            history = (
                history.groupby(
                    ["Market Date", "Underlying", "Tenor Swap", "Tenor Option"],
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

            underlyings = tuple(
                sorted(
                    history["Underlying"].drop_duplicates().astype(str),
                    key=str.casefold,
                )
            )
            indexed = {}
            for index, underlying in enumerate(underlyings, start=1):
                self.progress(
                    "Building slider index",
                    index,
                    len(underlyings),
                    f"Indexing {underlying}",
                )
                indexed[underlying] = build_underlying_data(
                    history.loc[history["Underlying"].eq(underlying)]
                )

            snapshot = HistorySnapshot(
                generation=generation,
                loaded_at=datetime.now(timezone.utc),
                file_count=len(files),
                quote_rows=len(history),
                underlyings=underlyings,
                data=indexed,
            )
        except Exception as exc:
            with self._lock:
                self._phase = "failed"
                self._stage = "Failed"
                self._message = (
                    "Load failed; the last complete snapshot remains available."
                    if self._snapshot is not None
                    else "Historical data could not be loaded."
                )
                self._error = f"{type(exc).__name__}: {exc}"
            return

        with self._lock:
            self._snapshot = snapshot
            self._generation = generation
            self._phase = "ready"
            self._stage = "Ready"
            self._message = (
                f"Loaded {snapshot.file_count:,} files, "
                f"{snapshot.quote_rows:,} quote cells, and "
                f"{len(snapshot.underlyings):,} underlyings."
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


# ---------------------------------------------------------------------------
# Figure and layout.
# ---------------------------------------------------------------------------


def empty_figure(message: str) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
        annotations=[
            {
                "text": message,
                "showarrow": False,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "font": {"size": 15},
            }
        ],
        scene={
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
            "zaxis": {"visible": False},
        },
    )
    return figure


def date_marks(dates: tuple[str, ...]) -> dict[int, str]:
    if not dates:
        return {}
    indexes = sorted(
        set(np.linspace(0, len(dates) - 1, min(8, len(dates)), dtype=int))
    )
    return {
        int(index): pd.Timestamp(dates[int(index)]).strftime("%d %b")
        for index in indexes
    }


def surface_figure(
    underlying: str,
    data: UnderlyingData,
    date: str,
) -> tuple[go.Figure, str]:
    surface = data.surfaces[date]
    custom = np.empty(
        (len(data.option_tenors), len(data.swap_tenors), 3),
        dtype=object,
    )
    for option_index, option in enumerate(data.option_tenors):
        for swap_index, swap in enumerate(data.swap_tenors):
            count = surface.counts[option_index, swap_index]
            custom[option_index, swap_index] = [
                swap,
                option,
                int(count) if np.isfinite(count) else 0,
            ]

    trace_args = {
        "x": np.arange(len(data.swap_tenors)),
        "y": np.arange(len(data.option_tenors)),
        "z": surface.z,
        "customdata": custom,
        "colorscale": data.colorscale,
        "cmin": data.cmin,
        "cmax": data.cmax,
        "connectgaps": False,
        "colorbar": {"title": "OFFICIAL", "thickness": 18, "len": 0.72},
        "contours": {
            "z": {
                "show": True,
                "usecolormap": True,
                "project": {"z": True},
            }
        },
        "hovertemplate": (
            "<b>%{customdata[0]} swap</b><br>"
            "%{customdata[1]} option<br>"
            "OFFICIAL: %{z:,.6g}<br>"
            "Source rows: %{customdata[2]}<extra></extra>"
        ),
    }
    if data.cmid is not None:
        trace_args["cmid"] = data.cmid

    figure = go.Figure(go.Surface(**trace_args))
    figure.update_layout(
        template="plotly_white",
        title={
            "text": f"{underlying} · OFFICIAL surface · {date}",
            "x": 0.02,
            "xanchor": "left",
        },
        height=680,
        margin={"l": 20, "r": 30, "t": 65, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        uirevision=f"camera::{underlying}",
        scene={
            "camera": {"eye": {"x": 1.55, "y": 1.55, "z": 1.1}},
            "aspectmode": "manual",
            "aspectratio": {"x": 1.3, "y": 1.1, "z": 0.8},
            "xaxis": {
                "title": "Tenor Swap",
                "tickmode": "array",
                "tickvals": list(range(len(data.swap_tenors))),
                "ticktext": list(data.swap_tenors),
            },
            "yaxis": {
                "title": "Tenor Option",
                "tickmode": "array",
                "tickvals": list(range(len(data.option_tenors))),
                "ticktext": list(data.option_tenors),
            },
            "zaxis": {"title": "OFFICIAL", "range": [data.cmin, data.cmax]},
        },
    )

    populated = int(np.isfinite(surface.z).sum())
    status = (
        f"{surface.quote_rows:,} quote cells · "
        f"{populated:,}/{surface.z.size:,} surface cells populated"
    )
    if surface.duplicate_cells:
        status += (
            f" · {surface.duplicate_cells:,} duplicate cells averaged"
        )
    return figure, status


def build_layout(data_dir: Path) -> html.Main:
    return html.Main(
        [
            dcc.Interval(
                id="startup-trigger",
                interval=250,
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
            dcc.Store(id="date-store", data=[]),
            html.Section(
                [
                    html.Header(
                        [
                            html.Div(
                                [
                                    html.H1(
                                        "Historical double-tenor market surface",
                                        style={"margin": 0, "fontSize": "22px"},
                                    ),
                                    html.P(
                                        (
                                            "The server starts immediately. "
                                            "History loads after first paint, "
                                            "then the slider reads precomputed surfaces."
                                        ),
                                        style={
                                            "margin": "6px 0 0",
                                            "color": "#626875",
                                            "fontSize": "13px",
                                        },
                                    ),
                                ]
                            ),
                            html.Button(
                                "Reload history",
                                id="reload-button",
                                n_clicks=0,
                                disabled=True,
                                style={
                                    "minHeight": "38px",
                                    "padding": "8px 14px",
                                    "border": "1px solid #B7C0CA",
                                    "borderRadius": "8px",
                                    "background": "#FFFFFF",
                                    "fontWeight": 800,
                                },
                            ),
                        ],
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "justifyContent": "space-between",
                            "gap": "16px",
                            "padding": "18px 22px 14px",
                            "borderBottom": "1px solid #D9DEE5",
                        },
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Strong(id="load-stage", children="Waiting"),
                                    html.Span(id="load-percent", children="0%"),
                                ],
                                style={
                                    "display": "flex",
                                    "justifyContent": "space-between",
                                },
                            ),
                            html.Div(
                                html.Div(
                                    id="progress-fill",
                                    style={
                                        "height": "100%",
                                        "width": "0%",
                                        "background": "#111111",
                                    },
                                ),
                                style={
                                    "height": "7px",
                                    "marginTop": "7px",
                                    "overflow": "hidden",
                                    "borderRadius": "999px",
                                    "background": "#E8EBEF",
                                },
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
                                    "color": "#7A838D",
                                    "fontSize": "10px",
                                    "overflowWrap": "anywhere",
                                },
                            ),
                        ],
                        style={
                            "padding": "12px 22px",
                            "borderBottom": "1px solid #D9DEE5",
                            "background": "#F7F8FA",
                        },
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Underlying",
                                htmlFor="underlying",
                                style={
                                    "display": "block",
                                    "marginBottom": "6px",
                                    "fontSize": "12px",
                                    "fontWeight": 800,
                                },
                            ),
                            dcc.Dropdown(
                                id="underlying",
                                options=[],
                                value=None,
                                clearable=False,
                                searchable=True,
                                disabled=True,
                                persistence=True,
                                persistence_type="local",
                                placeholder="History is loading…",
                            ),
                        ],
                        style={
                            "maxWidth": "560px",
                            "padding": "14px 22px 4px",
                        },
                    ),
                    dcc.Loading(
                        dcc.Graph(
                            id="surface",
                            figure=empty_figure("Historical data is loading…"),
                            responsive=True,
                            config={
                                "displaylogo": False,
                                "responsive": True,
                                "scrollZoom": True,
                            },
                            style={"height": "680px"},
                        ),
                        type="dot",
                        delay_show=120,
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Strong("Historical date"),
                                    html.Strong(
                                        id="date-label",
                                        children="—",
                                        style={
                                            "padding": "6px 10px",
                                            "border": "1px solid #D9DEE5",
                                            "borderRadius": "999px",
                                            "background": "#FFFFFF",
                                        },
                                    ),
                                ],
                                style={
                                    "display": "flex",
                                    "justifyContent": "space-between",
                                    "gap": "12px",
                                    "marginBottom": "8px",
                                },
                            ),
                            dcc.Slider(
                                id="date-slider",
                                min=0,
                                max=0,
                                step=1,
                                value=0,
                                marks={},
                                disabled=True,
                                updatemode="drag",
                                tooltip={
                                    "placement": "bottom",
                                    "always_visible": False,
                                },
                            ),
                            html.Div(
                                id="surface-status",
                                children="Waiting for a complete history snapshot.",
                                style={
                                    "marginTop": "16px",
                                    "color": "#626875",
                                    "fontSize": "12px",
                                },
                            ),
                        ],
                        style={
                            "padding": "14px 24px 22px",
                            "borderTop": "1px solid #D9DEE5",
                            "background": "#F7F8FA",
                        },
                    ),
                ],
                style={
                    "maxWidth": "1380px",
                    "margin": "22px auto",
                    "border": "1px solid #D9DEE5",
                    "borderRadius": "14px",
                    "background": "#FFFFFF",
                    "boxShadow": "0 8px 28px rgba(16,24,40,.08)",
                    "overflow": "hidden",
                },
            ),
        ],
        style={
            "minHeight": "100vh",
            "padding": "1px 16px",
            "background": "#F4F6F8",
            "fontFamily": '"Segoe UI Variable Text","Segoe UI",Arial,sans-serif',
            "color": "#111111",
        },
    )


# ---------------------------------------------------------------------------
# App factory.
# ---------------------------------------------------------------------------


def create_app(settings: RuntimeSettings | None = None) -> Dash:
    """Create the Dash server without touching the historical CSV files."""
    settings = settings or RuntimeSettings.from_env()
    data_dir = resolve_data_path(
        os.getenv("CUBE_HISTORY_DATA_DIR"),
        Path("data"),
        root=APP_ROOT,
    )
    coordinator = HistoryCoordinator(data_dir)

    dash_app = Dash(__name__, **settings.dash_kwargs)
    dash_app.title = "Historical tenor surface"
    dash_app.layout = lambda: build_layout(data_dir)

    route_prefix = dash_app.config.routes_pathname_prefix or "/"
    health_path = f"{route_prefix.rstrip('/')}/healthz" or "/healthz"
    progress_path = f"{route_prefix.rstrip('/')}/progressz" or "/progressz"

    @dash_app.server.get(health_path)
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

    @dash_app.server.get(progress_path)
    def progress():
        status = coordinator.status()
        return jsonify(
            phase=status.phase,
            stage=status.stage,
            message=status.message,
            current=status.current,
            total=status.total,
            generation=status.generation,
            error=status.error,
            has_snapshot=status.has_snapshot,
            data_dir=str(data_dir),
        )

    @dash_app.server.after_request
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

    @dash_app.callback(
        Output("load-stage", "children"),
        Output("load-percent", "children"),
        Output("progress-fill", "style"),
        Output("load-status", "children"),
        Output("underlying", "options"),
        Output("underlying", "value"),
        Output("underlying", "disabled"),
        Output("generation-store", "data"),
        Output("status-poll", "disabled"),
        Output("reload-button", "children"),
        Output("reload-button", "disabled"),
        Input("startup-trigger", "n_intervals"),
        Input("status-poll", "n_intervals"),
        Input("reload-button", "n_clicks"),
        State("underlying", "value"),
    )
    def startup(
        _startup_tick: int,
        _poll_tick: int,
        _reload_clicks: int,
        current_underlying: str | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "reload-button":
            coordinator.start(force=True)
        elif trigger == "startup-trigger" or coordinator.status().phase == "idle":
            coordinator.start()

        status = coordinator.status()
        snapshot = coordinator.snapshot()
        percent = (
            100
            if status.phase == "ready"
            else min(100, round(status.current / max(status.total, 1) * 100))
        )
        options = []
        selected = None
        if snapshot is not None:
            options = [
                {"label": value, "value": value}
                for value in snapshot.underlyings
            ]
            selected = (
                current_underlying
                if current_underlying in snapshot.data
                else snapshot.underlyings[0]
                if snapshot.underlyings
                else None
            )

        message = status.message + (f" {status.error}" if status.error else "")
        fill = {
            "height": "100%",
            "width": f"{percent}%",
            "borderRadius": "999px",
            "background": (
                "#B42318"
                if status.phase == "failed"
                else "#297A43"
                if status.phase == "ready"
                else "#111111"
            ),
            "transition": "width 180ms linear",
        }
        return (
            status.stage,
            f"{percent}%",
            fill,
            message,
            options,
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

    @dash_app.callback(
        Output("date-store", "data"),
        Output("date-slider", "max"),
        Output("date-slider", "value"),
        Output("date-slider", "marks"),
        Output("date-slider", "disabled"),
        Input("underlying", "value"),
        Input("generation-store", "data"),
    )
    def configure_dates(underlying: str | None, _generation: int):
        snapshot = coordinator.snapshot()
        if snapshot is None or underlying not in snapshot.data:
            return [], 0, 0, {}, True
        dates = snapshot.data[underlying].dates
        latest = len(dates) - 1
        return list(dates), latest, latest, date_marks(dates), False

    @dash_app.callback(
        Output("surface", "figure"),
        Output("date-label", "children"),
        Output("surface-status", "children"),
        Input("underlying", "value"),
        Input("date-slider", "value"),
        Input("generation-store", "data"),
        State("date-store", "data"),
    )
    def render_surface(
        underlying: str | None,
        selected_index: int | None,
        _generation: int,
        dates: list[str] | None,
    ):
        snapshot = coordinator.snapshot()
        if snapshot is None:
            return (
                empty_figure("Historical data is loading…"),
                "—",
                "Waiting for a complete history snapshot.",
            )
        if not underlying or underlying not in snapshot.data:
            return empty_figure("Choose an Underlying"), "—", "No selection."

        date_values = list(dates or [])
        if not date_values:
            return empty_figure("No available dates"), "—", "No dates."

        try:
            index = int(selected_index)
        except (TypeError, ValueError):
            index = len(date_values) - 1
        index = max(0, min(index, len(date_values) - 1))
        date = date_values[index]
        figure, status = surface_figure(
            underlying,
            snapshot.data[underlying],
            date,
        )
        return figure, date, status

    return dash_app


# CLI settings are applied before the module-level app is constructed.
if __name__ == "__main__":
    _configure_cli_environment(parse_args())


SETTINGS = RuntimeSettings.from_env()
app = create_app(SETTINGS)
server = app.server


def run_app() -> None:
    """Run the already-constructed local app without a second reloader process."""
    app.run(
        debug=SETTINGS.debug,
        host=SETTINGS.host,
        port=SETTINGS.port,
        use_reloader=False,
    )


if __name__ == "__main__":
    run_app()
