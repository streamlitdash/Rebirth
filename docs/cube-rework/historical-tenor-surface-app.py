from __future__ import annotations

import hashlib
import json
import os
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback, ctx, dcc, html

try:
    import pyarrow.dataset as ds
except ImportError:
    ds = None

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("CUBE_HISTORY_DATA_DIR", ROOT / "data")).expanduser().resolve()
CACHE = Path(os.getenv("CUBE_HISTORY_CACHE_DIR", DATA / ".cube_surface_cache")).expanduser().resolve()
PARQUET = CACHE / "parquet"
MANIFEST = CACHE / "manifest.json"
DATE_RE = re.compile(r"^(\d{8})\.csv$")
COLUMNS = ("Underlying", "Tenor Swap", "Tenor Option", "OFFICIAL")
CACHE_VERSION = 1


def market_date(path: Path) -> pd.Timestamp:
    match = DATE_RE.fullmatch(path.name)
    if not match:
        raise ValueError(f"Expected DDMMYYYY.csv, found {path.name}")
    try:
        return pd.Timestamp(datetime.strptime(match.group(1), "%d%m%Y")).normalize()
    except ValueError as exc:
        raise ValueError(f"Invalid date in {path.name}") from exc


def sources() -> list[Path]:
    if not DATA.is_dir():
        raise FileNotFoundError(f"Data directory does not exist: {DATA}")
    found = [p for p in DATA.iterdir() if p.is_file() and DATE_RE.fullmatch(p.name)]
    found.sort(key=market_date)
    if not found:
        raise FileNotFoundError(f"No DDMMYYYY.csv files found in {DATA}")
    return found


def fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def read_manifest() -> dict[str, Any]:
    try:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        value = {}
    if value.get("version") != CACHE_VERSION or not isinstance(value.get("files"), dict):
        return {"version": CACHE_VERSION, "files": {}, "generation": ""}
    return value


def write_manifest(value: dict[str, Any]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    temp = MANIFEST.with_name(f".{MANIFEST.name}.tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, MANIFEST)


def clean_csv(path: Path) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0)
    actual: dict[str, str] = {}
    for raw in header.columns:
        canonical = {c.casefold(): c for c in COLUMNS}.get(str(raw).strip().casefold())
        if canonical:
            actual[canonical] = str(raw)
    missing = [c for c in COLUMNS if c not in actual]
    if missing:
        raise ValueError(f"{path.name} is missing {missing}; found {list(header.columns)}")

    frame = pd.read_csv(path, usecols=[actual[c] for c in COLUMNS]).rename(
        columns={actual[c]: c for c in COLUMNS}
    )
    underlying = frame["Underlying"].astype("string").str.strip()
    if (underlying.isna() | underlying.eq("")).any():
        raise ValueError(f"{path.name}: Underlying contains blank values")
    frame["Underlying"] = underlying.astype(str)
    for column in ("Tenor Swap", "Tenor Option"):
        values = frame[column].astype("string").str.strip().fillna("N/A")
        frame[column] = values.mask(values.eq(""), "N/A").astype(str)

    raw = frame["OFFICIAL"]
    numeric = pd.to_numeric(raw, errors="coerce")
    bad = raw.notna() & raw.astype("string").str.strip().ne("") & numeric.isna()
    bad |= numeric.notna() & ~np.isfinite(numeric)
    if bad.any():
        raise ValueError(f"{path.name}: OFFICIAL contains non-numeric values")
    frame["OFFICIAL"] = numeric.astype(float)
    frame = frame.loc[frame["OFFICIAL"].notna()]

    grouped = frame.groupby(
        ["Underlying", "Tenor Swap", "Tenor Option"], as_index=False, sort=False
    ).agg(OFFICIAL=("OFFICIAL", "mean"), **{"Source Rows": ("OFFICIAL", "size")})
    grouped.insert(0, "Market Date", market_date(path))
    return grouped.sort_values(
        ["Underlying", "Tenor Option", "Tenor Swap"], kind="stable"
    ).reset_index(drop=True)


TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)")
TENOR_RE = re.compile(r"^(\d+(?:\.\d+)?)([DWMY])$", re.I)
DAYS = {"D": 1.0, "W": 7.0, "M": 30.4375, "Y": 365.25}
SPECIAL = {"ON": 0.1, "O/N": 0.1, "TN": 0.2, "T/N": 0.2, "SN": 0.3, "S/N": 0.3, "SPOT": 0.4}


def natural(value: str) -> tuple[Any, ...]:
    return tuple(float(p) if p.replace(".", "", 1).isdigit() else p for p in TOKEN_RE.split(value.casefold()))


def tenor_key(value: Any) -> tuple[Any, ...]:
    label = str(value).strip()
    normalized = label.upper().replace(" ", "")
    if normalized.casefold() in {"", "n/a", "na", "none", "null", "unspecified"}:
        return (3, float("inf"), natural(label))
    if normalized in SPECIAL:
        return (0, SPECIAL[normalized], natural(label))
    match = TENOR_RE.fullmatch(normalized)
    if match:
        return (1, float(match.group(1)) * DAYS[match.group(2).upper()], natural(label))
    return (2, float("inf"), natural(label))


def ordered(series: pd.Series) -> list[str]:
    return sorted(series.dropna().astype(str).str.strip().drop_duplicates().tolist(), key=tenor_key)


def scale(values: pd.Series) -> tuple[float, float, str, float | None]:
    finite = pd.to_numeric(values, errors="coerce")
    finite = finite[np.isfinite(finite)]
    if finite.empty:
        return -1.0, 1.0, "RdBu", 0.0
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        padding = max(abs(low) * 0.05, 1.0)
        low, high = low - padding, high + padding
    if low < 0 < high:
        bound = max(abs(low), abs(high))
        return -bound, bound, "RdBu", 0.0
    return low, high, "Viridis", None


class Store:
    def __init__(self) -> None:
        self.lock = Lock()
        self.generation = ""
        self.bundles: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

    def ensure_cache(self, force: bool = False) -> dict[str, Any]:
        if ds is None:
            raise RuntimeError("PyArrow is not installed. Run: python -m pip install pyarrow")
        with self.lock:
            old = read_manifest().get("files", {})
            current: dict[str, Any] = {}
            PARQUET.mkdir(parents=True, exist_ok=True)
            converted = reused = 0
            pending: list[tuple[Path, Path]] = []
            try:
                for source in sources():
                    fp = fingerprint(source)
                    target = PARQUET / f"{source.stem}.parquet"
                    previous = old.get(source.name)
                    same = (
                        not force and isinstance(previous, dict)
                        and previous.get("size") == fp["size"]
                        and previous.get("mtime_ns") == fp["mtime_ns"]
                        and target.is_file()
                    )
                    if same:
                        entry = dict(previous)
                        reused += 1
                    else:
                        frame = clean_csv(source)
                        temp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
                        frame.to_parquet(temp, engine="pyarrow", compression="zstd", index=False)
                        pending.append((temp, target))
                        entry = {
                            **fp,
                            "parquet": target.name,
                            "date": market_date(source).date().isoformat(),
                            "rows": len(frame),
                            "underlyings": sorted(frame["Underlying"].drop_duplicates().tolist(), key=str.casefold),
                        }
                        converted += 1
                    current[source.name] = entry

                for temp, target in pending:
                    os.replace(temp, target)
                for name in set(old) - set(current):
                    old_target = old.get(name, {}).get("parquet") if isinstance(old.get(name), dict) else None
                    if old_target:
                        (PARQUET / old_target).unlink(missing_ok=True)

                generation = hashlib.sha256(
                    json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()[:16]
                write_manifest({"version": CACHE_VERSION, "generation": generation, "files": current})
            except Exception:
                for temp, _target in pending:
                    temp.unlink(missing_ok=True)
                raise

            if generation != self.generation:
                self.bundles.clear()
            self.generation = generation
            underlyings = sorted(
                {str(u) for entry in current.values() for u in entry.get("underlyings", [])},
                key=str.casefold,
            )
            return {
                "generation": generation,
                "underlyings": underlyings,
                "files": len(current),
                "rows": sum(int(e.get("rows", 0)) for e in current.values()),
                "converted": converted,
                "reused": reused,
            }

    def bundle(self, underlying: str, generation: str) -> dict[str, Any]:
        key = (generation, underlying)
        with self.lock:
            if key in self.bundles:
                self.bundles.move_to_end(key)
                return self.bundles[key]

        files = sorted(PARQUET.glob("*.parquet"))
        if not files:
            raise RuntimeError("Parquet cache is empty")
        table = ds.dataset([str(p) for p in files], format="parquet").to_table(
            columns=["Market Date", "Underlying", "Tenor Swap", "Tenor Option", "OFFICIAL", "Source Rows"],
            filter=ds.field("Underlying") == underlying,
        )
        history = table.to_pandas()
        if history.empty:
            raise ValueError(f"No cached history for {underlying!r}")
        history["Market Date"] = pd.to_datetime(history["Market Date"]).dt.normalize()
        dates = sorted(history["Market Date"].drop_duplicates().tolist())
        swaps, options = ordered(history["Tenor Swap"]), ordered(history["Tenor Option"])
        surfaces, counts = [], []
        for date in dates:
            day = history.loc[history["Market Date"].eq(date)]
            values = day.pivot_table(index="Tenor Option", columns="Tenor Swap", values="OFFICIAL", aggfunc="mean", sort=False).reindex(index=options, columns=swaps)
            source_counts = day.pivot_table(index="Tenor Option", columns="Tenor Swap", values="Source Rows", aggfunc="sum", sort=False).reindex(index=options, columns=swaps)
            surfaces.append([[float(v) if np.isfinite(v) else None for v in row] for row in values.to_numpy(float)])
            counts.append([[int(v) if np.isfinite(v) else None for v in row] for row in source_counts.to_numpy(float)])
        cmin, cmax, colorscale, cmid = scale(history["OFFICIAL"])
        result = {
            "underlying": underlying,
            "dates": [pd.Timestamp(d).date().isoformat() for d in dates],
            "swap_tenors": swaps,
            "option_tenors": options,
            "surfaces": surfaces,
            "source_counts": counts,
            "cmin": cmin,
            "cmax": cmax,
            "colorscale": colorscale,
            "cmid": cmid,
            "rows": len(history),
            "duplicate_cells": int((history["Source Rows"] > 1).sum()),
        }
        with self.lock:
            self.bundles[key] = result
            self.bundles.move_to_end(key)
            while len(self.bundles) > 8:
                self.bundles.popitem(last=False)
        return result


STORE = Store()


def empty_figure(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        annotations=[{"text": message, "showarrow": False, "x": 0.5, "y": 0.5, "xref": "paper", "yref": "paper"}],
        scene={"xaxis": {"visible": False}, "yaxis": {"visible": False}, "zaxis": {"visible": False}},
    )
    return fig


def marks(dates: list[str]) -> dict[int, str]:
    if not dates:
        return {}
    indexes = sorted(set(np.linspace(0, len(dates) - 1, min(8, len(dates)), dtype=int).tolist()))
    return {i: pd.Timestamp(dates[i]).strftime("%d %b\n%Y") for i in indexes}


app = Dash(__name__)
app.title = "Historical tenor surface"
server = app.server
app.layout = html.Main([
    dcc.Interval(id="catalog-trigger", interval=350, n_intervals=0, max_intervals=1),
    dcc.Store(id="generation-store"),
    dcc.Store(id="bundle-store"),
    html.Section([
        html.Header([
            html.H1("Historical double-tenor market surface", style={"margin": 0, "fontSize": "22px"}),
            html.P("Choose an Underlying, then hold and drag the date control. Slider updates happen locally in the browser.", style={"margin": "6px 0 0", "color": "#626875"}),
        ], style={"padding": "18px 22px 14px", "borderBottom": "1px solid #D9DEE5"}),
        html.Div([
            html.Div([html.Label("Underlying", style={"display": "block", "fontWeight": 800, "marginBottom": "6px"}), dcc.Dropdown(id="underlying", options=[], disabled=True, clearable=False, searchable=True)], style={"flex": "1 1 420px"}),
            html.Button("Refresh cache", id="refresh-cache", n_clicks=0, style={"minHeight": "39px", "padding": "8px 14px"}),
        ], style={"display": "flex", "alignItems": "end", "gap": "12px", "padding": "14px 22px 4px", "maxWidth": "760px"}),
        html.Div("Starting Dash; checking the cache after the page is visible.", id="cache-status", style={"padding": "6px 22px 0", "color": "#626875", "whiteSpace": "pre-wrap"}),
        dcc.Loading(dcc.Graph(id="surface", figure=empty_figure("Preparing the cache…"), config={"displaylogo": False, "responsive": True, "scrollZoom": True}, style={"height": "680px"}), type="dot"),
        html.Div([
            html.Div([html.Strong("Historical date"), html.Strong("—", id="date-label", style={"padding": "6px 10px", "border": "1px solid #D9DEE5", "borderRadius": "999px", "background": "white"})], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "8px"}),
            dcc.Slider(id="date-slider", min=0, max=0, value=0, step=1, marks={}, disabled=True, updatemode="drag"),
            html.Div(id="surface-status", style={"marginTop": "16px", "color": "#626875"}),
        ], style={"padding": "14px 24px 22px", "borderTop": "1px solid #D9DEE5", "background": "#F7F8FA"}),
    ], style={"maxWidth": "1380px", "margin": "22px auto", "background": "white", "border": "1px solid #D9DEE5", "borderRadius": "14px", "overflow": "hidden", "boxShadow": "0 8px 28px rgba(16,24,40,.08)"}),
    html.P(["Source: ", html.Code(str(DATA)), " · Cache: ", html.Code(str(CACHE))], style={"maxWidth": "1380px", "margin": "-8px auto 28px", "color": "#626875", "fontSize": "11px"}),
], style={"minHeight": "100vh", "padding": "1px 16px", "background": "#F4F6F8", "fontFamily": "Segoe UI,Arial,sans-serif"})


@callback(
    Output("underlying", "options"), Output("underlying", "value"), Output("underlying", "disabled"),
    Output("cache-status", "children"), Output("generation-store", "data"),
    Input("catalog-trigger", "n_intervals"), Input("refresh-cache", "n_clicks"), State("underlying", "value"),
)
def build_catalog(_interval: int, _clicks: int, current: str | None):
    try:
        catalog = STORE.ensure_cache(force=ctx.triggered_id == "refresh-cache")
    except Exception as exc:
        return [], None, True, f"Cache could not be prepared:\n{exc}", None
    values = catalog["underlyings"]
    selected = current if current in values else (values[0] if values else None)
    status = f"{catalog['files']:,} CSV files · {catalog['rows']:,} cached cells · {catalog['converted']:,} converted · {catalog['reused']:,} reused"
    return [{"label": v, "value": v} for v in values], selected, not bool(values), status, catalog["generation"]


@callback(
    Output("bundle-store", "data"), Output("date-slider", "max"), Output("date-slider", "value"),
    Output("date-slider", "marks"), Output("date-slider", "disabled"),
    Input("underlying", "value"), Input("generation-store", "data"),
)
def load_underlying(underlying: str | None, generation: str | None):
    if not underlying or not generation:
        return None, 0, 0, {}, True
    try:
        bundle = STORE.bundle(underlying, generation)
    except Exception as exc:
        return {"error": str(exc)}, 0, 0, {}, True
    latest = len(bundle["dates"]) - 1
    return bundle, latest, latest, marks(bundle["dates"]), False


app.clientside_callback(
    """
    function(index, bundle, underlying) {
        const empty = message => ({data: [], layout: {template: 'plotly_white', paper_bgcolor: 'rgba(0,0,0,0)', annotations: [{text: message, showarrow: false, x: .5, y: .5, xref: 'paper', yref: 'paper'}]}});
        if (!bundle) return [empty('Choose an Underlying'), '—', 'No surface loaded.'];
        if (bundle.error) return [empty(bundle.error), '—', bundle.error];
        let i = Number.isFinite(Number(index)) ? Number(index) : bundle.dates.length - 1;
        i = Math.max(0, Math.min(i, bundle.dates.length - 1));
        const x = bundle.swap_tenors.map((_v, n) => n), y = bundle.option_tenors.map((_v, n) => n);
        const counts = bundle.source_counts[i] || [];
        const text = bundle.option_tenors.map((o, oi) => bundle.swap_tenors.map((s, si) => `<b>${s} swap</b><br>${o} option<br>Source rows: ${counts[oi] && counts[oi][si] != null ? counts[oi][si] : 0}`));
        const surface = bundle.swap_tenors.length >= 2 && bundle.option_tenors.length >= 2;
        const trace = surface ? {type: 'surface', x, y, z: bundle.surfaces[i], text, hovertemplate: '%{text}<br>OFFICIAL: %{z:,.6g}<extra></extra>', colorscale: bundle.colorscale, reversescale: bundle.colorscale === 'RdBu', cmin: bundle.cmin, cmax: bundle.cmax, cmid: bundle.cmid, connectgaps: false, colorbar: {title: 'OFFICIAL'}, contours: {z: {show: true, usecolormap: true, project: {z: true}}}} : {type: 'heatmap', x, y, z: bundle.surfaces[i], text, hovertemplate: '%{text}<br>OFFICIAL: %{z:,.6g}<extra></extra>', colorscale: bundle.colorscale, reversescale: bundle.colorscale === 'RdBu', zmin: bundle.cmin, zmax: bundle.cmax, zmid: bundle.cmid, connectgaps: false};
        const layout = {template: 'plotly_white', title: {text: `${underlying} · OFFICIAL surface · ${bundle.dates[i]}`, x: .02}, height: 680, margin: {l: 20, r: 30, t: 65, b: 20}, paper_bgcolor: 'rgba(0,0,0,0)', uirevision: `camera::${underlying}`};
        if (surface) layout.scene = {camera: {eye: {x: 1.55, y: 1.55, z: 1.1}}, aspectmode: 'manual', aspectratio: {x: 1.3, y: 1.1, z: .8}, xaxis: {title: 'Tenor Swap', tickmode: 'array', tickvals: x, ticktext: bundle.swap_tenors}, yaxis: {title: 'Tenor Option', tickmode: 'array', tickvals: y, ticktext: bundle.option_tenors}, zaxis: {title: 'OFFICIAL', range: [bundle.cmin, bundle.cmax]}};
        else {layout.xaxis = {title: 'Tenor Swap', tickmode: 'array', tickvals: x, ticktext: bundle.swap_tenors, side: 'top'}; layout.yaxis = {title: 'Tenor Option', tickmode: 'array', tickvals: y, ticktext: bundle.option_tenors};}
        const duplicate = bundle.duplicate_cells ? ` · ${bundle.duplicate_cells} duplicate cells averaged` : '';
        return [{data: [trace], layout}, bundle.dates[i], `${bundle.rows.toLocaleString()} cached cells across ${bundle.dates.length} dates${duplicate} · slider rendered clientside`];
    }
    """,
    Output("surface", "figure"), Output("date-label", "children"), Output("surface-status", "children"),
    Input("date-slider", "value"), Input("bundle-store", "data"), State("underlying", "value"),
)


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8050")),
        debug=os.getenv("DASH_DEBUG", "0").strip().casefold() in {"1", "true", "yes", "on"},
        use_reloader=False,
    )
