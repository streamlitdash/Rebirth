"""Historical double-tenor surface viewer using Rebirth's launch lifecycle.

Data layout
-----------
data/
    01082026.csv
    02082026.csv

Each DDMMYYYY.csv must contain:
    Underlying, Tenor Swap, Tenor Option, OFFICIAL

Install:
    python -m pip install dash pandas numpy plotly

Run:
    python historical-tenor-surface-app.py --data-dir data --port 8050

The important startup order mirrors Rebirth's s01_app.py:
    argparse -> environment -> RuntimeSettings -> create_app -> app/server -> run_app

Dash is constructed before any CSV is read. The browser paints a shell, then
starts one daemon loader. The most recent complete snapshot remains visible if
a reload fails. Date-slider changes are rendered client-side after one
Underlying bundle has been loaded.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Final, Mapping

import numpy as np
import pandas as pd
from dash import Dash, Input, Output, State, ctx, dcc, html
from flask import jsonify, request


# ---------------------------------------------------------------------------
# Runtime setup: intentionally aligned with Rebirth s01_app.py/s02_config.py.
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
            # jupyter-server-proxy strips its public prefix before forwarding.
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
    description="Historical double-tenor market surface viewer"
)
_parser.add_argument("--port", type=int, default=None)
_parser.add_argument("--host", type=str, default=None)
_parser.add_argument("--debug", action="store_true")
_parser.add_argument(
    "--data-dir",
    type=str,
    default=None,
    help="Folder containing DDMMYYYY.csv files (default: project-root/data).",
)
_parser.add_argument(
    "--jupyterhub-mode",
    choices=("proxy", "service"),
    default=None,
    help="Override DASH_JUPYTERHUB_MODE.",
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


# Apply CLI values before RuntimeSettings and Dash are constructed.
if __name__ == "__main__":
    _configure_cli_environment(parse_args())


# ---------------------------------------------------------------------------
# Historical-data validation and surface preparation.
# ---------------------------------------------------------------------------

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
NATURAL_TOKEN_RE: Final = re.compile(r"(\d+(?:\.\d+)?)")
TENOR_RE: Final = re.compile(r"^(\d+(?:\.\d+)?)([DWMY])$", re.IGNORECASE)
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
ABSENT_TENORS: Final = frozenset({"", "n/a", "na", "none", "null", "unspecified"})


def parse_market_date(path: Path) -> pd.Timestamp:
    match = DATE_FILE_RE.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Expected DDMMYYYY.csv, found {path.name}")
    try:
        parsed = datetime.strptime(match.group("date"), "%d%m%Y")
    except ValueError as exc:
        raise ValueError(f"Invalid calendar date in {path.name}") from exc
    return pd.Timestamp(parsed).normalize()


def natural_tokens(value: str) -> tuple[object, ...]:
    return tuple(
        float(part) if part.replace(".", "", 1).isdigit() else part
        for part in NATURAL_TOKEN_RE.split(value.casefold())
    )


def tenor_sort_key(value: object) -> tuple[object, ...]:
    label = str(value).strip()
    normalized = label.upper().replace(" ", "")
    if normalized.casefold() in ABSENT_TENORS:
        return (3, float("inf"), natural_tokens(label))
    if normalized in SPECIAL_TENORS:
        return (0, SPECIAL_TENORS[normalized], natural_tokens(label))
    match = TENOR_RE.fullmatch(normalized)
    if match is not None:
        days = float(match.group(1)) * UNIT_DAYS[match.group(2).upper()]
        return (1, days, natural_tokens(label))
    return (2, float("inf"), natural_tokens(label))


def ordered_tenors(values: pd.Series) -> tuple[str, ...]:
    labels = values.dropna().astype(str).str.strip().drop_duplicates().tolist()
    return tuple(sorted(labels, key=tenor_sort_key))


def read_history_file(path: Path) -> pd.DataFrame:
    """Read one date and retain only the required columns."""
    header = pd.read_csv(path, nrows=0)
    actual: dict[str, str] = {}
    for raw_column in header.columns:
        canonical = CANONICAL_BY_CASEFOLD.get(str(raw_column).strip().casefold())
        if canonical:
            actual[canonical] = str(raw_column)
    missing = [column for column in REQUIRED_COLUMNS if column not in actual]
    if missing:
        raise ValueError(
            f"{path.name} is missing {missing}; found {list(header.columns)}"
        )

    frame = pd.read_csv(
        path,
        usecols=[actual[column] for column in REQUIRED_COLUMNS],
        low_memory=False,
    ).rename(columns={actual[column]: column for column in REQUIRED_COLUMNS})

    underlying = frame["Underlying"].astype("string").str.strip()
    if (underlying.isna() | underlying.eq("")).any():
        raise ValueError(f"{path.name}: Underlying contains blank values")
    frame["Underlying"] = underlying.astype(str)

    for column in ("Tenor Swap", "Tenor Option"):
        values = frame[column].astype("string").str.strip().fillna("N/A")
        frame[column] = values.mask(values.eq(""), "N/A").astype(str)

    raw_official = frame["OFFICIAL"]
    official = pd.to_numeric(raw_official, errors="coerce")
    nonblank = raw_official.notna() & raw_official.astype("string").str.strip().ne("")
    invalid = nonblank & official.isna()
    invalid |= official.notna() & ~np.isfinite(official)
    if invalid.any():
        rows = frame.index[invalid].tolist()[:5]
        raise ValueError(f"{path.name}: OFFICIAL is invalid at rows {rows}")

    frame["OFFICIAL"] = official.astype(float)
    frame = frame.loc[frame["OFFICIAL"].notna()].copy()
    frame.insert(0, "Market Date", parse_market_date(path).date().isoformat())

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


def colour_scale(values: pd.Series) -> tuple[float, float, str, float | None]:
    finite = pd.to_numeric(values, errors="coerce")
    finite = finite[np.isfinite(finite)]
    if finite.empty:
        return -1.0, 1.0, "RdBu", 0.0
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        padding = max(abs(low) * 0.05, 1.0)
        low -= padding
        high += padding
    if low < 0.0 < high:
        bound = max(abs(low), abs(high))
        return -bound, bound, "RdBu", 0.0
    return low, high, "Viridis", None


@dataclass(frozen=True)
class UnderlyingBundle:
    dates: tuple[str, ...]
    swap_tenors: tuple[str, ...]
    option_tenors: tuple[str, ...]
    surfaces: tuple[tuple[tuple[float | None, ...], ...], ...]
    source_counts: tuple[tuple[tuple[int | None, ...], ...], ...]
    cmin: float
    cmax: float
    colorscale: str
    cmid: float | None
    rows: int
    duplicate_cells: int

    def browser_payload(self) -> dict[str, Any]:
        return {
            "dates": list(self.dates),
            "swap_tenors": list(self.swap_tenors),
            "option_tenors": list(self.option_tenors),
            "surfaces": self.surfaces,
            "source_counts": self.source_counts,
            "cmin": self.cmin,
            "cmax": self.cmax,
            "colorscale": self.colorscale,
            "cmid": self.cmid,
            "rows": self.rows,
            "duplicate_cells": self.duplicate_cells,
        }


@dataclass(frozen=True)
class HistorySnapshot:
    generation: int
    loaded_at: datetime
    file_count: int
    quote_rows: int
    bundles: dict[str, UnderlyingBundle]

    @property
    def underlyings(self) -> tuple[str, ...]:
        return tuple(self.bundles)


def build_bundle(frame: pd.DataFrame) -> UnderlyingBundle:
    dates = tuple(sorted(frame["Market Date"].astype(str).drop_duplicates()))
    swaps = ordered_tenors(frame["Tenor Swap"])
    options = ordered_tenors(frame["Tenor Option"])
    cmin, cmax, colorscale, cmid = colour_scale(frame["OFFICIAL"])
    surfaces = []
    counts = []

    for selected_date in dates:
        day = frame.loc[frame["Market Date"].eq(selected_date)]
        values = (
            day.pivot_table(
                index="Tenor Option",
                columns="Tenor Swap",
                values="OFFICIAL",
                aggfunc="mean",
                sort=False,
            )
            .reindex(index=options, columns=swaps)
            .to_numpy(dtype=float)
        )
        source_rows = (
            day.pivot_table(
                index="Tenor Option",
                columns="Tenor Swap",
                values="Source_Rows",
                aggfunc="sum",
                sort=False,
            )
            .reindex(index=options, columns=swaps)
            .to_numpy(dtype=float)
        )
        surfaces.append(
            tuple(
                tuple(float(value) if np.isfinite(value) else None for value in row)
                for row in values
            )
        )
        counts.append(
            tuple(
                tuple(int(value) if np.isfinite(value) else None for value in row)
                for row in source_rows
            )
        )

    return UnderlyingBundle(
        dates=dates,
        swap_tenors=swaps,
        option_tenors=options,
        surfaces=tuple(surfaces),
        source_counts=tuple(counts),
        cmin=cmin,
        cmax=cmax,
        colorscale=colorscale,
        cmid=cmid,
        rows=len(frame),
        duplicate_cells=int((frame["Source_Rows"] > 1).sum()),
    )


# ---------------------------------------------------------------------------
# One-writer background coordinator, modelled on Rebirth startup behaviour.
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
        self._message = "Waiting for browser-triggered startup."
        self._current = 0
        self._total = 0
        self._generation = 0
        self._error: str | None = None
        self._snapshot: HistorySnapshot | None = None

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
            name="historical-surface-loader",
            daemon=True,
        ).start()
        return True

    def _set_progress(
        self,
        *,
        stage: str,
        message: str,
        current: int,
        total: int,
    ) -> None:
        with self._lock:
            self._stage = stage
            self._message = message
            self._current = int(current)
            self._total = int(total)

    def _load(self, generation: int) -> None:
        try:
            if not self.data_dir.is_dir():
                raise FileNotFoundError(f"Data directory does not exist: {self.data_dir}")
            files = [
                path
                for path in self.data_dir.iterdir()
                if path.is_file() and DATE_FILE_RE.fullmatch(path.name)
            ]
            files.sort(key=parse_market_date)
            if not files:
                raise FileNotFoundError(
                    f"No DDMMYYYY.csv files were found in {self.data_dir}"
                )

            frames = []
            for index, path in enumerate(files, start=1):
                frames.append(read_history_file(path))
                self._set_progress(
                    stage="Reading CSV history",
                    message=f"Read {path.name}",
                    current=index,
                    total=len(files),
                )

            self._set_progress(
                stage="Combining dates",
                message="Combining validated quote cells.",
                current=len(files),
                total=len(files),
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

            underlyings = sorted(
                history["Underlying"].astype(str).drop_duplicates().tolist(),
                key=str.casefold,
            )
            bundles: dict[str, UnderlyingBundle] = {}
            for index, underlying in enumerate(underlyings, start=1):
                self._set_progress(
                    stage="Building date surfaces",
                    message=f"Indexing {underlying}",
                    current=index,
                    total=len(underlyings),
                )
                bundles[underlying] = build_bundle(
                    history.loc[history["Underlying"].eq(underlying)]
                )

            snapshot = HistorySnapshot(
                generation=generation,
                loaded_at=datetime.now(timezone.utc),
                file_count=len(files),
                quote_rows=len(history),
                bundles=bundles,
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
# Dash layout and callbacks.
# ---------------------------------------------------------------------------


def empty_figure(message: str) -> dict[str, Any]:
    return {
        "data": [],
        "layout": {
            "template": "plotly_white",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "annotations": [
                {
                    "text": message,
                    "showarrow": False,
                    "x": 0.5,
                    "y": 0.5,
                    "xref": "paper",
                    "yref": "paper",
                }
            ],
        },
    }


def date_marks(dates: tuple[str, ...]) -> dict[int, str]:
    if not dates:
        return {}
    indexes = sorted(
        set(np.linspace(0, len(dates) - 1, min(8, len(dates)), dtype=int).tolist())
    )
    return {
        index: pd.Timestamp(dates[index]).strftime("%d %b")
        for index in indexes
    }


def build_layout(data_dir: Path) -> html.Main:
    """Return a shell immediately; no source I/O happens here."""
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
            dcc.Store(id="bundle-store"),
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
                                            "The server paints first, then loads history in "
                                            "one background worker. Dragging the date slider "
                                            "renders locally in the browser."
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
                                clearable=False,
                                searchable=True,
                                disabled=True,
                                placeholder="History is loading…",
                            ),
                        ],
                        style={"maxWidth": "560px", "padding": "14px 22px 4px"},
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


def create_app(settings: RuntimeSettings | None = None) -> Dash:
    """Construct Dash and callbacks without reading historical source files."""
    settings = settings or RuntimeSettings.from_env()
    project_root = discover_project_root()
    data_dir = resolve_data_path(
        os.getenv("CUBE_HISTORY_DATA_DIR"),
        Path("data"),
        root=project_root,
    )
    coordinator = HistoryCoordinator(data_dir)

    app = Dash(__name__, **settings.dash_kwargs)
    app.title = "Historical tenor surface"
    app.layout = lambda: build_layout(data_dir)

    route_prefix = app.config.routes_pathname_prefix or "/"
    health_path = f"{route_prefix.rstrip('/')}/healthz" or "/healthz"
    progress_path = f"{route_prefix.rstrip('/')}/progressz" or "/progressz"

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
        options: list[dict[str, str]] = []
        selected = None
        if snapshot is not None:
            options = [
                {"label": value, "value": value}
                for value in snapshot.underlyings
            ]
            selected = (
                current_underlying
                if current_underlying in snapshot.bundles
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

    @app.callback(
        Output("bundle-store", "data"),
        Output("date-slider", "max"),
        Output("date-slider", "value"),
        Output("date-slider", "marks"),
        Output("date-slider", "disabled"),
        Input("underlying", "value"),
        Input("generation-store", "data"),
    )
    def configure_underlying(underlying: str | None, _generation: int):
        snapshot = coordinator.snapshot()
        if snapshot is None or underlying not in snapshot.bundles:
            return None, 0, 0, {}, True
        bundle = snapshot.bundles[underlying]
        latest = len(bundle.dates) - 1
        return (
            bundle.browser_payload(),
            latest,
            latest,
            date_marks(bundle.dates),
            False,
        )

    # After the Underlying bundle arrives, slider changes do not call Python.
    app.clientside_callback(
        """
        function(index, bundle, underlying) {
            const empty = message => ({
                data: [],
                layout: {
                    template: "plotly_white",
                    paper_bgcolor: "rgba(0,0,0,0)",
                    plot_bgcolor: "rgba(0,0,0,0)",
                    annotations: [{
                        text: message,
                        showarrow: false,
                        x: 0.5,
                        y: 0.5,
                        xref: "paper",
                        yref: "paper"
                    }]
                }
            });

            if (!bundle || !underlying) {
                return [empty("Choose an Underlying"), "—", "No surface loaded."];
            }

            let selected = Number.isFinite(Number(index))
                ? Number(index)
                : bundle.dates.length - 1;
            selected = Math.max(0, Math.min(selected, bundle.dates.length - 1));

            const x = bundle.swap_tenors.map((_value, position) => position);
            const y = bundle.option_tenors.map((_value, position) => position);
            const counts = bundle.source_counts[selected] || [];
            const text = bundle.option_tenors.map((optionTenor, optionIndex) =>
                bundle.swap_tenors.map((swapTenor, swapIndex) => {
                    const row = counts[optionIndex] || [];
                    const sourceRows = row[swapIndex] == null ? 0 : row[swapIndex];
                    return (
                        `<b>${swapTenor} swap</b><br>` +
                        `${optionTenor} option<br>` +
                        `Source rows: ${sourceRows}`
                    );
                })
            );

            const surface = bundle.swap_tenors.length >= 2 && bundle.option_tenors.length >= 2;
            let trace;
            if (surface) {
                trace = {
                    type: "surface",
                    x: x,
                    y: y,
                    z: bundle.surfaces[selected],
                    text: text,
                    hovertemplate: "%{text}<br>OFFICIAL: %{z:,.6g}<extra></extra>",
                    colorscale: bundle.colorscale,
                    reversescale: bundle.colorscale === "RdBu",
                    cmin: bundle.cmin,
                    cmax: bundle.cmax,
                    cmid: bundle.cmid,
                    connectgaps: false,
                    colorbar: {title: "OFFICIAL"},
                    contours: {z: {show: true, usecolormap: true, project: {z: true}}}
                };
            } else {
                trace = {
                    type: "heatmap",
                    x: x,
                    y: y,
                    z: bundle.surfaces[selected],
                    text: text,
                    hovertemplate: "%{text}<br>OFFICIAL: %{z:,.6g}<extra></extra>",
                    colorscale: bundle.colorscale,
                    reversescale: bundle.colorscale === "RdBu",
                    zmin: bundle.cmin,
                    zmax: bundle.cmax,
                    zmid: bundle.cmid,
                    connectgaps: false
                };
            }

            const layout = {
                template: "plotly_white",
                title: {
                    text: `${underlying} · OFFICIAL surface · ${bundle.dates[selected]}`,
                    x: 0.02
                },
                height: 680,
                margin: {l: 20, r: 30, t: 65, b: 20},
                paper_bgcolor: "rgba(0,0,0,0)",
                plot_bgcolor: "rgba(0,0,0,0)",
                uirevision: `camera::${underlying}`
            };

            if (surface) {
                layout.scene = {
                    camera: {eye: {x: 1.55, y: 1.55, z: 1.1}},
                    aspectmode: "manual",
                    aspectratio: {x: 1.3, y: 1.1, z: 0.8},
                    xaxis: {
                        title: "Tenor Swap",
                        tickmode: "array",
                        tickvals: x,
                        ticktext: bundle.swap_tenors
                    },
                    yaxis: {
                        title: "Tenor Option",
                        tickmode: "array",
                        tickvals: y,
                        ticktext: bundle.option_tenors
                    },
                    zaxis: {
                        title: "OFFICIAL",
                        range: [bundle.cmin, bundle.cmax]
                    }
                };
            } else {
                layout.xaxis = {
                    title: "Tenor Swap",
                    side: "top",
                    tickmode: "array",
                    tickvals: x,
                    ticktext: bundle.swap_tenors
                };
                layout.yaxis = {
                    title: "Tenor Option",
                    tickmode: "array",
                    tickvals: y,
                    ticktext: bundle.option_tenors
                };
            }

            const duplicateText = bundle.duplicate_cells
                ? ` · ${bundle.duplicate_cells} duplicate cells averaged`
                : "";
            return [
                {data: [trace], layout: layout},
                bundle.dates[selected],
                `${bundle.rows.toLocaleString()} quote cells across ` +
                    `${bundle.dates.length} dates${duplicateText} · slider rendered client-side`
            ];
        }
        """,
        Output("surface", "figure"),
        Output("date-label", "children"),
        Output("surface-status", "children"),
        Input("date-slider", "value"),
        Input("bundle-store", "data"),
        State("underlying", "value"),
    )

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
