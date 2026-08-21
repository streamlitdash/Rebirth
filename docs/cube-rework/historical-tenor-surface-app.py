"""Single-file Dash viewer for dated double-tenor market CSV files.

Expected folder structure::

    historical-tenor-surface-app.py
    data/
        01082026.csv
        02082026.csv
        ...

Every CSV filename must be ``DDMMYYYY.csv`` and contain these columns:

    Underlying, Tenor Swap, Tenor Option, OFFICIAL

Install and run::

    python -m pip install dash pandas numpy plotly
    python historical-tenor-surface-app.py

The data directory can be overridden with ``CUBE_HISTORY_DATA_DIR``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback, dcc, html


APP_ROOT: Final[Path] = Path(__file__).resolve().parent
DATA_DIR: Final[Path] = Path(
    os.environ.get("CUBE_HISTORY_DATA_DIR", APP_ROOT / "data")
).expanduser().resolve()

DATE_FILE_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<date>\d{8})\.csv$")
REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "Underlying",
    "Tenor Swap",
    "Tenor Option",
    "OFFICIAL",
)

_STANDARD_TENOR_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<number>\d+(?:\.\d+)?)(?P<unit>[DWMY])$",
    re.IGNORECASE,
)
_UNIT_DAYS: Final[dict[str, float]] = {
    "D": 1.0,
    "W": 7.0,
    "M": 30.4375,
    "Y": 365.25,
}
_SPECIAL_TENORS: Final[dict[str, float]] = {
    "ON": 0.10,
    "O/N": 0.10,
    "TN": 0.20,
    "T/N": 0.20,
    "SN": 0.30,
    "S/N": 0.30,
    "SPOT": 0.40,
}


def parse_market_date(path: Path) -> pd.Timestamp:
    """Parse a strict DDMMYYYY.csv filename."""
    match = DATE_FILE_RE.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Not a DDMMYYYY.csv file: {path.name}")
    try:
        return pd.Timestamp(
            datetime.strptime(match.group("date"), "%d%m%Y")
        ).normalize()
    except ValueError as exc:
        raise ValueError(f"Invalid date in filename: {path.name}") from exc


def normalize_columns(frame: pd.DataFrame, filename: str) -> pd.DataFrame:
    """Accept harmless casing/whitespace differences in the four headers."""
    canonical = {column.casefold(): column for column in REQUIRED_COLUMNS}
    rename: dict[object, str] = {}
    for column in frame.columns:
        normalized = str(column).strip().casefold()
        if normalized in canonical:
            rename[column] = canonical[normalized]

    result = frame.rename(columns=rename)
    missing = [column for column in REQUIRED_COLUMNS if column not in result]
    if missing:
        raise ValueError(
            f"{filename} is missing {missing}; found {list(frame.columns)}"
        )
    return result.loc[:, list(REQUIRED_COLUMNS)].copy()


def load_one_file(path: Path) -> pd.DataFrame:
    """Read and validate one historical snapshot."""
    frame = normalize_columns(pd.read_csv(path), path.name)

    underlying = frame["Underlying"].astype("string").str.strip()
    invalid_underlying = underlying.isna() | underlying.eq("")
    if invalid_underlying.any():
        rows = frame.index[invalid_underlying].tolist()[:5]
        raise ValueError(f"{path.name}: blank Underlying values at rows {rows}")
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
        raise ValueError(f"{path.name}: invalid OFFICIAL values at rows {rows}")

    frame["OFFICIAL"] = official.astype(float)
    frame = frame.loc[frame["OFFICIAL"].notna()].copy()
    frame.insert(0, "Market Date", parse_market_date(path))
    return frame


def load_history(root: Path) -> pd.DataFrame:
    """Load all dated CSV snapshots into one logical history table."""
    if not root.is_dir():
        raise FileNotFoundError(
            f"Data directory does not exist: {root}. Create data/ beside this file "
            "or set CUBE_HISTORY_DATA_DIR."
        )

    files = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_file() and DATE_FILE_RE.fullmatch(path.name)
        ),
        key=parse_market_date,
    )
    if not files:
        raise FileNotFoundError(f"No DDMMYYYY.csv files found in {root}")

    history = pd.concat(
        [load_one_file(path) for path in files],
        ignore_index=True,
        sort=False,
    )

    # Duplicate source rows for the same quote cell are averaged explicitly.
    return (
        history.groupby(
            ["Market Date", "Underlying", "Tenor Swap", "Tenor Option"],
            as_index=False,
            dropna=False,
            sort=False,
        )
        .agg(OFFICIAL=("OFFICIAL", "mean"), Source_Rows=("OFFICIAL", "size"))
        .sort_values(
            ["Market Date", "Underlying", "Tenor Option", "Tenor Swap"],
            kind="stable",
        )
        .reset_index(drop=True)
    )


def tenor_sort_key(value: object) -> tuple[object, ...]:
    """Sort ordinary market tenors chronologically."""
    label = str(value).strip()
    normalized = label.upper().replace(" ", "")

    if normalized in _SPECIAL_TENORS:
        return (0, _SPECIAL_TENORS[normalized], label.casefold())

    match = _STANDARD_TENOR_RE.fullmatch(normalized)
    if match is not None:
        days = float(match.group("number")) * _UNIT_DAYS[match.group("unit").upper()]
        return (1, days, label.casefold())

    if normalized in {"", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}:
        return (3, float("inf"), label.casefold())

    return (2, float("inf"), label.casefold())


def ordered_tenors(values: pd.Series) -> list[str]:
    """Return unique labels in financial tenor order."""
    labels = values.dropna().astype(str).str.strip().drop_duplicates().tolist()
    return sorted(labels, key=tenor_sort_key)


def slider_marks(dates: list[pd.Timestamp], maximum: int = 8) -> dict[int, str]:
    """Keep the date slider readable with a small number of labels."""
    if not dates:
        return {}
    indexes = sorted(
        set(np.linspace(0, len(dates) - 1, min(maximum, len(dates)), dtype=int))
    )
    return {index: dates[index].strftime("%d %b\n%Y") for index in indexes}


def value_range(values: pd.Series) -> tuple[float, float, str, float | None]:
    """Use one fixed colour and Z range across dates for an Underlying."""
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    if numeric.empty:
        return -1.0, 1.0, "RdBu_r", 0.0

    low = float(numeric.min())
    high = float(numeric.max())
    if low == high:
        padding = max(abs(low) * 0.05, 1.0)
        low -= padding
        high += padding

    if low < 0 < high:
        bound = max(abs(low), abs(high))
        return -bound, bound, "RdBu_r", 0.0
    return low, high, "Viridis", None


def empty_figure(message: str) -> go.Figure:
    """Return a clean chart empty state."""
    figure = go.Figure()
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
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


def build_surface(
    history: pd.DataFrame,
    underlying: str,
    market_date: pd.Timestamp,
) -> tuple[go.Figure, str]:
    """Build one selected-date option-tenor by swap-tenor surface."""
    underlying_history = history.loc[history["Underlying"].eq(underlying)]
    day = underlying_history.loc[
        underlying_history["Market Date"].eq(market_date)
    ]
    if day.empty:
        return empty_figure("No rows for the selected date"), "No rows available."

    swap_tenors = ordered_tenors(underlying_history["Tenor Swap"])
    option_tenors = ordered_tenors(underlying_history["Tenor Option"])

    matrix = (
        day.pivot_table(
            index="Tenor Option",
            columns="Tenor Swap",
            values="OFFICIAL",
            aggfunc="mean",
            sort=False,
        )
        .reindex(index=option_tenors, columns=swap_tenors)
    )
    z = matrix.to_numpy(dtype=float)

    cmin, cmax, colorscale, cmid = value_range(
        underlying_history["OFFICIAL"]
    )
    surface_options: dict[str, object] = {
        "x": np.arange(len(swap_tenors)),
        "y": np.arange(len(option_tenors)),
        "z": z,
        "colorscale": colorscale,
        "cmin": cmin,
        "cmax": cmax,
        "connectgaps": False,
        "colorbar": {"title": "OFFICIAL", "thickness": 18, "len": 0.72},
        "contours": {
            "z": {
                "show": True,
                "usecolormap": True,
                "project_z": True,
                "highlightcolor": "#111111",
            }
        },
        "hovertemplate": (
            "<b>Swap %{customdata[0]}</b><br>"
            "Option %{customdata[1]}<br>"
            "OFFICIAL: %{z:,.6g}<extra></extra>"
        ),
        "customdata": np.array(
            [
                [[swap, option] for swap in swap_tenors]
                for option in option_tenors
            ],
            dtype=object,
        ),
    }
    if cmid is not None:
        surface_options["cmid"] = cmid

    figure = go.Figure(data=[go.Surface(**surface_options)])
    figure.update_layout(
        template="plotly_white",
        title={
            "text": f"{underlying} · OFFICIAL · {market_date.date().isoformat()}",
            "x": 0.02,
            "xanchor": "left",
        },
        height=680,
        margin={"l": 20, "r": 30, "t": 65, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        uirevision=f"camera::{underlying}",
        scene={
            "camera": {"eye": {"x": 1.55, "y": 1.55, "z": 1.10}},
            "aspectmode": "manual",
            "aspectratio": {"x": 1.3, "y": 1.1, "z": 0.8},
            "xaxis": {
                "title": "Tenor Swap",
                "tickmode": "array",
                "tickvals": list(range(len(swap_tenors))),
                "ticktext": swap_tenors,
            },
            "yaxis": {
                "title": "Tenor Option",
                "tickmode": "array",
                "tickvals": list(range(len(option_tenors))),
                "ticktext": option_tenors,
            },
            "zaxis": {"title": "OFFICIAL", "range": [cmin, cmax]},
        },
    )

    populated = int(np.isfinite(z).sum())
    return (
        figure,
        f"{len(day):,} aggregated quote rows · {populated:,}/{z.size:,} surface cells populated",
    )


HISTORY: Final[pd.DataFrame] = load_history(DATA_DIR)
UNDERLYINGS: Final[list[str]] = sorted(
    HISTORY["Underlying"].drop_duplicates().astype(str).tolist(),
    key=str.casefold,
)
DATES_BY_UNDERLYING: Final[dict[str, list[pd.Timestamp]]] = {
    underlying: sorted(
        HISTORY.loc[HISTORY["Underlying"].eq(underlying), "Market Date"]
        .drop_duplicates()
        .tolist()
    )
    for underlying in UNDERLYINGS
}


app = Dash(__name__)
app.title = "Historical tenor surface"
server = app.server

app.layout = html.Main(
    html.Section(
        [
            html.Header(
                [
                    html.H1(
                        "Historical double-tenor market surface",
                        style={"margin": 0, "fontSize": "22px"},
                    ),
                    html.P(
                        "Choose an Underlying, then hold and drag the date slider.",
                        style={"margin": "6px 0 0", "color": "#626875"},
                    ),
                ],
                style={
                    "padding": "18px 22px 14px",
                    "borderBottom": "1px solid #D9DEE5",
                },
            ),
            html.Div(
                [
                    html.Label(
                        "Underlying",
                        htmlFor="underlying-dropdown",
                        style={
                            "display": "block",
                            "marginBottom": "6px",
                            "fontWeight": 800,
                        },
                    ),
                    dcc.Dropdown(
                        id="underlying-dropdown",
                        options=[
                            {"label": value, "value": value}
                            for value in UNDERLYINGS
                        ],
                        value=UNDERLYINGS[0],
                        clearable=False,
                        searchable=True,
                    ),
                ],
                style={"padding": "14px 22px 4px", "maxWidth": "560px"},
            ),
            dcc.Loading(
                dcc.Graph(
                    id="surface-graph",
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
                                id="selected-date-label",
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
                        updatemode="drag",
                        tooltip={"placement": "bottom"},
                    ),
                    dcc.Store(id="available-date-store"),
                    html.Div(
                        id="surface-status",
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
            "boxShadow": "0 8px 28px rgba(16,24,40,0.08)",
            "overflow": "hidden",
        },
    ),
    style={
        "minHeight": "100vh",
        "padding": "1px 16px",
        "background": "#F4F6F8",
        "fontFamily": '"Segoe UI Variable Text", "Segoe UI", Arial, sans-serif',
    },
)


@callback(
    Output("available-date-store", "data"),
    Output("date-slider", "max"),
    Output("date-slider", "value"),
    Output("date-slider", "marks"),
    Input("underlying-dropdown", "value"),
)
def update_dates(underlying: str | None) -> tuple[list[str], int, int, dict[int, str]]:
    if not underlying or underlying not in DATES_BY_UNDERLYING:
        return [], 0, 0, {}
    dates = DATES_BY_UNDERLYING[underlying]
    serialized = [pd.Timestamp(value).date().isoformat() for value in dates]
    latest = len(dates) - 1
    return serialized, latest, latest, slider_marks(dates)


@callback(
    Output("surface-graph", "figure"),
    Output("selected-date-label", "children"),
    Output("surface-status", "children"),
    Input("underlying-dropdown", "value"),
    Input("date-slider", "value"),
    State("available-date-store", "data"),
)
def update_surface(
    underlying: str | None,
    selected_index: int | None,
    available_dates: list[str] | None,
) -> tuple[go.Figure, str, str]:
    if not underlying:
        return empty_figure("Choose an Underlying"), "—", "No Underlying selected."

    dates = list(available_dates or [])
    if not dates:
        return empty_figure("No dates available"), "—", "No dates available."

    try:
        index = int(selected_index if selected_index is not None else len(dates) - 1)
    except (TypeError, ValueError):
        index = len(dates) - 1
    index = max(0, min(index, len(dates) - 1))

    market_date = pd.Timestamp(dates[index]).normalize()
    figure, status = build_surface(HISTORY, underlying, market_date)
    return figure, market_date.date().isoformat(), status


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8050")),
        debug=os.environ.get("DASH_DEBUG", "0").strip().casefold()
        in {"1", "true", "yes", "on"},
        use_reloader=False,
    )
