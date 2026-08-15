"""Callbacks for governed PL adjustments, SOG/portfolio sending, and saving."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable

import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, Patch, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from core.s01_schema import PORTFOLIO_MAPPED_COLUMN, PORTFOLIO_METADATA_COLUMNS
from core.s04_pl import (
    ADJUSTMENT,
    MARKET_DATE,
    PL,
    CONCERTO_FIELD,
    HISTO_TYPE,
    HISTORY_TYPE,
    HISTORY_IDENTITY_COLUMNS,
    PL_SEND_COLUMNS,
    PLSendValidationError,
    PORTFOLIO,
    PREDICTED_TYPE,
    RISK_GREEK,
    RISK_TYPE,
    SIGNOFF_GROUP,
    apply_adjustment_overlay,
    build_pl_send_base,
    build_saved_pl_frame,
    collapse_pl_send_rows,
    load_pl_history,
    load_plsend_mapping,
    load_portfolio_governance,
)
from .s06_plview import (
    DISPLAY_COLUMNS,
    GRID_ROW_ID,
    HISTORY_HIERARCHY_COLUMNS,
    HISTORY_VALUE_COLUMNS,
    PL_AGGREGATE_TOGGLE_TYPE,
    PL_FILTER_FIELDS,
    PL_FILTER_IDS,
    build_pl_aggregate_table,
    pl_filter_map,
    pl_filter_options,
)
from .s02_constants import RISK_TYPE_ORDER
from .s03_aggregate import apply_filters, prepare_risk_data
from .s01_contracts import AdjustmentRepositoryProtocol, RefreshManagerProtocol
from .s11_saved_views import (
    SavedFilterViewControls,
    saved_view_request_id,
    saved_view_request_matches_base,
    saved_view_request_values,
)


SendFunction = Callable[[pd.DataFrame], None]
WritePLFunction = Callable[[pd.DataFrame, str, int], None]
_SAVE_LOCK = RLock()
_CHECKED = "\N{BALLOT BOX WITH CHECK}"
_UNCHECKED = "\N{BALLOT BOX}"
_SELECTION_SUMMARY_SCRIPT = r"""
function (selectedCells, rows) {
    if (!Array.isArray(selectedCells) || selectedCells.length < 2) {
        return ["", true];
    }
    var records = Array.isArray(rows) ? rows : [];
    var numbers = selectedCells.filter(function (cell) {
        return cell.column_id === "PL";
    }).map(function (cell) {
        var record = records[cell.row] || {};
        var value = record[cell.column_id];
        if (value === null || value === undefined || value === ""
            || typeof value === "boolean") {
            return null;
        }
        if (typeof value === "number") {
            return Number.isFinite(value) ? value : null;
        }
        var normalized = String(value).replace(/[£€¥,\s]/g, "");
        if (!normalized) return null;
        var parsed = Number(normalized);
        return Number.isFinite(parsed) ? parsed : null;
    }).filter(function (value) { return value !== null; });
    if (!numbers.length) {
        return [selectedCells.length + " cells selected · no PL values", false];
    }
    var sum = numbers.reduce(function (total, value) { return total + value; }, 0);
    var format = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
    return [selectedCells.length + " cells selected · "
        + numbers.length + " PL · Sum " + format.format(sum)
        + " · Average " + format.format(sum / numbers.length)
        + " · Min " + format.format(Math.min.apply(null, numbers))
        + " · Max " + format.format(Math.max.apply(null, numbers)), false];
}
"""

_CLEAR_SELECTION_SCRIPT = r"""
function (_nClicks, _scope, _effectiveStore) {
    return [[], null];
}
"""


def _is_checked(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip()
    return (
        normalized in (_CHECKED, "true", "True", "1")
        or 'data-adjustment="true"' in normalized
    )


@dataclass(frozen=True)
class PLSendConfig:
    mapping_source: str | Path
    adjustment_repository: AdjustmentRepositoryProtocol
    saved_directory: str | Path
    send_sog_pl: SendFunction
    send_portfolio_pl: SendFunction
    # Production boundary for writing the complete PL frame to site-owned s3.
    # The market date is ISO formatted and revision is the committed snapshot.
    write_pl: WritePLFunction | None = None
    # Strict layout: histo/YYYY/MM-DD/{histo,predicted}.csv at the governed
    # Risk Type/Risk Greek/Underlying/Product/Book daily leaf grain.
    history_source: str | Path | pd.DataFrame | None = None


def _governance(snapshot) -> pd.DataFrame:
    columns = [PORTFOLIO, *PORTFOLIO_METADATA_COLUMNS]
    raw = snapshot.combined_pl
    if PORTFOLIO_MAPPED_COLUMN not in raw:
        raise ValueError(f"omitted P&L is missing {PORTFOLIO_MAPPED_COLUMN}")
    mapped = raw.loc[raw[PORTFOLIO_MAPPED_COLUMN].eq(True), columns].drop_duplicates()
    conflicts = mapped.duplicated(PORTFOLIO, keep=False)
    if conflicts.any():
        raise ValueError(
            "portfolio governance is inconsistent in the committed snapshot"
        )
    return load_portfolio_governance(mapped)


def _display_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    display = frame.copy()
    if MARKET_DATE in display:
        display[MARKET_DATE] = pd.to_datetime(display[MARKET_DATE]).dt.date.astype(str)
    display[ADJUSTMENT] = display[ADJUSTMENT].map(
        lambda value: _CHECKED if bool(value) else _UNCHECKED
    )
    return display.to_dict("records")


def _history_value(frame: pd.DataFrame, history_type: str) -> float | None:
    """Aggregate one latest-day hierarchy cell without fabricating missing data."""
    values = frame.loc[frame[HISTORY_TYPE].eq(history_type), PL]
    if values.empty:
        return None
    value = values.sum(min_count=1)
    return None if pd.isna(value) else float(value)


def build_pl_history_hierarchy(history: pd.DataFrame) -> list[dict[str, object]]:
    """Return the all-date identity union with latest-day values, fully expanded."""
    if history.empty:
        return []
    latest_date = max(history[MARKET_DATE].astype(str))
    rows: list[dict[str, object]] = []

    def ordered_values(frame: pd.DataFrame, column: str) -> list[str]:
        values = frame[column].astype(str).drop_duplicates().tolist()
        if column == RISK_TYPE:
            return sorted(
                values,
                key=lambda value: (RISK_TYPE_ORDER.get(value, 99), value.casefold()),
            )
        return sorted(values, key=str.casefold)

    def visit(scope: pd.DataFrame, depth: int, path: tuple[str, ...]) -> None:
        column = HISTORY_HIERARCHY_COLUMNS[depth]
        for value in ordered_values(scope, column):
            child = scope.loc[scope[column].astype(str).eq(value)]
            child_path = (*path, value)
            latest_child = child.loc[child[MARKET_DATE].astype(str).eq(latest_date)]
            row: dict[str, object] = {
                hierarchy_column: (child_path[index] if index < len(child_path) else "")
                for index, hierarchy_column in enumerate(HISTORY_HIERARCHY_COLUMNS)
            }
            row.update(
                {
                    HISTO_TYPE: _history_value(latest_child, HISTO_TYPE),
                    PREDICTED_TYPE: _history_value(latest_child, PREDICTED_TYPE),
                    "id": json.dumps(child_path, separators=(",", ":")),
                }
            )
            rows.append(row)
            if depth + 1 < len(HISTORY_HIERARCHY_COLUMNS):
                visit(child, depth + 1, child_path)

    # The table must not erase a valid historical series merely because that
    # identity is absent on the latest global day. Its latest-day cells remain
    # blank, but either Histo/Predicted cell can still select the older series.
    visit(history, 0, ())
    return rows


def history_selection_from_cell(
    active_cell: Mapping[str, object] | None,
    rows: Sequence[Mapping[str, object]],
    previous: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Translate one numeric table cell into a stable hierarchy-series selection."""
    if not rows:
        return {}
    if active_cell is None:
        return dict(previous or {})
    column = str(active_cell.get("column_id", ""))
    row_index = active_cell.get("row")
    if column not in HISTORY_VALUE_COLUMNS or not isinstance(row_index, int):
        return dict(previous or {})
    if row_index < 0 or row_index >= len(rows):
        return dict(previous or {})
    record = rows[row_index]
    path = [
        str(record[column_name])
        for column_name in HISTORY_HIERARCHY_COLUMNS
        if str(record.get(column_name, "")).strip()
    ]
    if not path:
        return dict(previous or {})
    return {"history_type": column, "path": path}


def select_pl_history_series(
    history: pd.DataFrame,
    selection: Mapping[str, object] | None,
) -> pd.DataFrame:
    """Aggregate the selected hierarchy node to exactly one P&L per day."""
    if history.empty or not selection:
        return history.iloc[0:0].copy()
    history_type = str(selection.get("history_type", ""))
    path = selection.get("path")
    if history_type not in HISTORY_VALUE_COLUMNS or not isinstance(path, list):
        return history.iloc[0:0].copy()
    if not path or len(path) > len(HISTORY_IDENTITY_COLUMNS):
        return history.iloc[0:0].copy()
    scoped = history.loc[history[HISTORY_TYPE].eq(history_type)]
    for column, value in zip(HISTORY_IDENTITY_COLUMNS, path, strict=False):
        scoped = scoped.loc[scoped[column].astype(str).eq(str(value))]
    if scoped.empty:
        return scoped
    daily = (
        scoped.groupby(MARKET_DATE, as_index=False, sort=True)[PL]
        .sum(min_count=1)
        .sort_values(MARKET_DATE, kind="stable")
    )
    daily[HISTORY_TYPE] = history_type
    return daily[[MARKET_DATE, HISTORY_TYPE, PL]]


def history_range_bounds(
    series: pd.DataFrame,
    preset: str,
    *,
    start_date: object = None,
    end_date: object = None,
) -> tuple[str | None, str | None]:
    """Resolve 1W/MTD/YTD/All or an explicit inclusive daily date window."""
    if series.empty:
        return None, None
    available = pd.to_datetime(series[MARKET_DATE], errors="raise")
    minimum = available.min().normalize()
    maximum = available.max().normalize()
    normalized = str(preset or "all").strip().casefold()
    if normalized == "custom":
        start = pd.Timestamp(start_date).normalize() if start_date else minimum
        end = pd.Timestamp(end_date).normalize() if end_date else maximum
    elif normalized == "1w":
        start, end = maximum - pd.Timedelta(days=6), maximum
    elif normalized == "mtd":
        start, end = maximum.replace(day=1), maximum
    elif normalized == "ytd":
        start, end = maximum.replace(month=1, day=1), maximum
    else:
        start, end = minimum, maximum
    if start > end:
        start, end = end, start
    start = min(max(start, minimum), maximum)
    end = min(max(end, minimum), maximum)
    if start > end:
        start, end = end, start
    return start.date().isoformat(), end.date().isoformat()


def _historical_pl_figure(
    frame: pd.DataFrame,
    selection: Mapping[str, object] | None = None,
) -> go.Figure:
    """Plot one selected hierarchy/type series at the governed daily grain."""
    figure = go.Figure()
    path = [str(value) for value in (selection or {}).get("path", [])]
    history_type = str((selection or {}).get("history_type", ""))
    label = " → ".join(path)
    if frame.empty:
        figure.add_annotation(
            text="Select a populated Histo or Predicted cell to plot its daily P&L.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
    else:
        ordered = frame.sort_values(MARKET_DATE, kind="stable")
        figure.add_trace(
            go.Scatter(
                x=ordered[MARKET_DATE],
                y=ordered[PL],
                mode="lines+markers",
                name=history_type,
                line={
                    "color": "#1f77b4" if history_type == HISTO_TYPE else "#ff7f0e",
                    "dash": "solid" if history_type == HISTO_TYPE else "dash",
                },
                marker={
                    "symbol": "circle" if history_type == HISTO_TYPE else "diamond"
                },
                customdata=[[history_type, label] for _index in ordered.index],
                hovertemplate=(
                    "Market Date %{x}<br>P&L Type %{customdata[0]}<br>"
                    "Scope %{customdata[1]}<br>P&L %{y:,.2f}<extra></extra>"
                ),
            )
        )
    title = f"{history_type} P&L · {label}" if label else "Historical P&L"
    figure.update_layout(
        title=title,
        xaxis_title="Market Date",
        yaxis_title="P&L",
        hovermode="x unified",
        margin={"l": 70, "r": 30, "t": 60, "b": 70},
        template="plotly_white",
    )
    figure.update_xaxes(type="date", automargin=True)
    figure.update_yaxes(automargin=True, tickformat=",.2f", zeroline=True)
    return figure


def _domain_frame(records: list[dict[str, object]] | None) -> pd.DataFrame:
    frame = pd.DataFrame(records or [])
    if frame.empty:
        return pd.DataFrame(columns=list(PL_SEND_COLUMNS))
    for column in PL_SEND_COLUMNS:
        if column not in frame:
            frame[column] = None
    frame[ADJUSTMENT] = frame[ADJUSTMENT].map(_is_checked)
    return frame[list(PL_SEND_COLUMNS)]


def _risk_type_options(mapping: pd.DataFrame) -> list[str]:
    """Return the stable Risk Type domain exposed by the mapping."""
    return sorted(mapping[RISK_TYPE].astype(str).unique().tolist())


def _risk_greek_options(mapping: pd.DataFrame, risk_type: object) -> list[str]:
    """Return only Greeks governed for the selected Risk Type."""
    scoped = mapping.loc[mapping[RISK_TYPE].astype(str).eq(str(risk_type))]
    return sorted(scoped[RISK_GREEK].astype(str).unique().tolist())


def _datatable_options(values: list[str]) -> list[dict[str, str]]:
    """Convert one governed string domain to Dash DataTable options."""
    return [{"label": value, "value": value} for value in values]


def _editor_dropdowns(
    mapping: pd.DataFrame,
    allowed_portfolios: list[str],
    *,
    portfolio_editable: bool,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Build global and Risk-Type-dependent native DataTable dropdowns."""
    risk_types = _risk_type_options(mapping)
    all_greeks = sorted(mapping[RISK_GREEK].astype(str).unique().tolist())
    dropdown: dict[str, object] = {
        RISK_TYPE: {"options": _datatable_options(risk_types)},
        RISK_GREEK: {"options": _datatable_options(all_greeks)},
    }
    if portfolio_editable:
        dropdown[PORTFOLIO] = {
            "options": _datatable_options(allowed_portfolios),
        }

    conditional: list[dict[str, object]] = []
    for risk_type in risk_types:
        escaped = risk_type.replace("\\", "\\\\").replace("'", "\\'")
        conditional.append(
            {
                "if": {
                    "column_id": RISK_GREEK,
                    "filter_query": f'{{{RISK_TYPE}}} = "{escaped}"',
                },
                "options": _datatable_options(_risk_greek_options(mapping, risk_type)),
            }
        )
    return dropdown, conditional


def _allowed_portfolios(
    governance: pd.DataFrame,
    *,
    scope_column: str,
    selected_scope: object,
) -> list[str]:
    """Return the governed Portfolio domain for the active editor scope."""
    if selected_scope in (None, ""):
        return []
    if scope_column == PORTFOLIO:
        selected = str(selected_scope)
        values = set(governance[PORTFOLIO].astype(str))
        return [selected] if selected in values else []
    scoped = governance.loc[
        governance[scope_column].astype(str).eq(str(selected_scope)), PORTFOLIO
    ]
    return sorted(scoped.astype(str).unique().tolist())


def _govern_row(
    row: dict[str, object],
    mapping: pd.DataFrame,
    governance: pd.DataFrame,
    *,
    allowed_portfolios: list[str],
    changed: bool,
) -> dict[str, object]:
    result = dict(row)
    portfolio = str(result.get(PORTFOLIO, ""))
    if portfolio not in allowed_portfolios:
        portfolio = allowed_portfolios[0]
    result[PORTFOLIO] = portfolio
    result[SIGNOFF_GROUP] = governance.set_index(PORTFOLIO).at[portfolio, SIGNOFF_GROUP]

    risk_type = str(result.get(RISK_TYPE, ""))
    if risk_type not in set(mapping[RISK_TYPE]):
        risk_type = str(mapping.iloc[0][RISK_TYPE])
    scoped = mapping.loc[mapping[RISK_TYPE].eq(risk_type)]
    risk_greek = str(result.get(RISK_GREEK, ""))
    pair = scoped.loc[scoped[RISK_GREEK].eq(risk_greek)]
    if pair.empty:
        pair = scoped.iloc[[0]]
        risk_greek = str(pair.iloc[0][RISK_GREEK])
    result[RISK_TYPE] = risk_type
    result[RISK_GREEK] = risk_greek
    result[CONCERTO_FIELD] = str(pair.iloc[0][CONCERTO_FIELD])
    result[ADJUSTMENT] = changed or _is_checked(result.get(ADJUSTMENT, False))
    if MARKET_DATE in result and result[MARKET_DATE] not in (None, ""):
        result[MARKET_DATE] = pd.Timestamp(result[MARKET_DATE]).date().isoformat()
    return result


def _editor_row(
    row: dict[str, object],
    mapping: pd.DataFrame,
    governance: pd.DataFrame,
    *,
    allowed_portfolios: list[str],
    changed: bool,
    row_id: str,
) -> dict[str, object]:
    """Govern one native DataTable row and preserve its stable row ID."""
    governed = _govern_row(
        row,
        mapping,
        governance,
        allowed_portfolios=allowed_portfolios,
        changed=changed,
    )
    governed[GRID_ROW_ID] = row_id
    governed[ADJUSTMENT] = (
        _CHECKED if _is_checked(governed.get(ADJUSTMENT, False)) else _UNCHECKED
    )
    return governed


def _editor_records(
    frame: pd.DataFrame,
    mapping: pd.DataFrame,
    governance: pd.DataFrame,
    *,
    allowed_portfolios: list[str],
    scope_key: str,
) -> list[dict[str, object]]:
    """Serialize scoped PL rows with deterministic native DataTable IDs."""
    if frame.empty or not allowed_portfolios:
        return []
    records: list[dict[str, object]] = []
    for index, row in enumerate(frame.to_dict("records")):
        market_date = pd.Timestamp(row[MARKET_DATE]).date().isoformat()
        row_id = ":".join(
            [
                "base",
                scope_key,
                str(index),
                market_date,
                str(row.get(PORTFOLIO, "")),
                str(row.get(CONCERTO_FIELD, "")),
            ]
        )
        records.append(
            _editor_row(
                row,
                mapping,
                governance,
                allowed_portfolios=allowed_portfolios,
                changed=False,
                row_id=row_id,
            )
        )
    return records


def _new_editor_row(
    *,
    market_date: object,
    mapping: pd.DataFrame,
    governance: pd.DataFrame,
    allowed_portfolios: list[str],
) -> dict[str, object]:
    """Create one visible, governed adjustment row for insertion at row zero."""
    first_mapping = mapping.iloc[0]
    return _editor_row(
        {
            MARKET_DATE: pd.Timestamp(market_date).date().isoformat(),
            RISK_TYPE: first_mapping[RISK_TYPE],
            RISK_GREEK: first_mapping[RISK_GREEK],
            PORTFOLIO: allowed_portfolios[0],
            PL: 0.0,
            ADJUSTMENT: True,
        },
        mapping,
        governance,
        allowed_portfolios=allowed_portfolios,
        changed=True,
        row_id=f"new:{uuid.uuid4().hex}",
    )


def _draft_key(scope_column: str, selected_scope: object) -> str:
    """Return the stable client-draft key for one editor scope."""
    return str(selected_scope)


def _matching_draft_rows(
    drafts: dict[str, object] | None,
    store: dict[str, object],
    *,
    scope_key: str,
    scope_column: str,
    selected_scope: object,
) -> list[dict[str, object]] | None:
    """Return only a populated draft created for this exact editor scope."""
    entry = (drafts or {}).get(scope_key)
    if not isinstance(entry, dict):
        return None
    guards = ("revision", "market_date", "include_adjustments", "editor_epoch")
    if any(entry.get(key) != store.get(key) for key in guards):
        return None
    if entry.get("scope_column") != scope_column:
        return None
    if str(entry.get("scope_value", "")) != str(selected_scope):
        return None
    rows = entry.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    if not all(isinstance(row, dict) for row in rows):
        return None
    if any(str(row.get(scope_column, "")) != str(selected_scope) for row in rows):
        return None
    return [dict(row) for row in rows]


def _baseline_editor_records(
    store: dict[str, object],
    mapping: pd.DataFrame,
    governance: pd.DataFrame,
    *,
    scope_column: str,
    selected_scope: object,
) -> list[dict[str, object]]:
    """Build the committed baseline for one SOG or Portfolio editor scope."""
    frame = pd.DataFrame(store.get("rows", []))
    if frame.empty or scope_column not in frame:
        return []
    scoped = frame.loc[frame[scope_column].astype(str).eq(str(selected_scope))].copy()
    allowed = _allowed_portfolios(
        governance,
        scope_column=scope_column,
        selected_scope=selected_scope,
    )
    return _editor_records(
        scoped,
        mapping,
        governance,
        allowed_portfolios=allowed,
        scope_key=_draft_key(scope_column, selected_scope),
    )


def _drafts_with_scope(
    drafts: dict[str, object] | None,
    store: dict[str, object],
    rows: list[dict[str, object]],
    *,
    scope_column: str,
    selected_scope: object,
) -> dict[str, object]:
    """Persist a draft only after an explicit user edit or Add Row action."""
    updated = dict(drafts or {})
    updated[_draft_key(scope_column, selected_scope)] = {
        "revision": store.get("revision"),
        "market_date": store.get("market_date"),
        "include_adjustments": bool(store.get("include_adjustments")),
        "editor_epoch": store.get("editor_epoch", 0),
        "scope_column": scope_column,
        "scope_value": str(selected_scope),
        "rows": [dict(row) for row in rows],
    }
    return updated


def _editable_signature(row: dict[str, object]) -> tuple[object, ...]:
    """Return the user-editable values used to detect unsaved changes."""
    pl_value = row.get(PL)
    try:
        normalized_pl: object = float(str(pl_value).replace(",", ""))
    except (TypeError, ValueError):
        normalized_pl = str(pl_value)
    return (
        str(row.get(RISK_TYPE, "")),
        str(row.get(RISK_GREEK, "")),
        str(row.get(PORTFOLIO, "")),
        normalized_pl,
    )


def _govern_current_editor_records(
    records: list[dict[str, object]] | None,
    store: dict[str, object],
    mapping: pd.DataFrame,
    governance: pd.DataFrame,
    *,
    scope_column: str,
    selected_scope: object,
) -> list[dict[str, object]]:
    """Re-govern current grid state independently of the UI edit callback."""
    allowed = _allowed_portfolios(
        governance,
        scope_column=scope_column,
        selected_scope=selected_scope,
    )
    if not allowed:
        return []
    baseline_frame = pd.DataFrame(store.get("rows", []))
    if not baseline_frame.empty and scope_column in baseline_frame:
        baseline_frame = baseline_frame.loc[
            baseline_frame[scope_column].astype(str).eq(str(selected_scope))
        ].copy()
    baseline = _editor_records(
        baseline_frame,
        mapping,
        governance,
        allowed_portfolios=allowed,
        scope_key=_draft_key(scope_column, selected_scope),
    )
    baseline_by_id = {str(row[GRID_ROW_ID]): row for row in baseline}

    governed: list[dict[str, object]] = []
    for row in records or []:
        current = dict(row)
        row_id = str(current.get(GRID_ROW_ID, "")) or f"save:{uuid.uuid4().hex}"
        original = baseline_by_id.get(row_id)
        changed = original is None or _editable_signature(
            current
        ) != _editable_signature(original)
        governed.append(
            _editor_row(
                current,
                mapping,
                governance,
                allowed_portfolios=allowed,
                changed=changed,
                row_id=row_id,
            )
        )
    return governed


def _merge_and_persist_adjustments(
    config: PLSendConfig,
    rows: pd.DataFrame,
    *,
    market_date: object,
    revision: int,
    replace_portfolios: set[str] | None = None,
) -> None:
    config.adjustment_repository.save(
        market_date,
        rows,
        base_revision=revision,
        replace_portfolios=replace_portfolios,
    )


def _effective_rows(
    snapshot,
    config: PLSendConfig,
    *,
    include_adjustments: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build one section's independently overlaid, governed PL rows."""
    mapping = load_plsend_mapping(config.mapping_source)
    governance = _governance(snapshot)
    base = build_pl_send_base(snapshot.combined_pl, mapping, governance)
    adjustments = (
        config.adjustment_repository.load(snapshot.market_date)
        if include_adjustments
        else None
    )
    effective = apply_adjustment_overlay(
        base,
        None
        if adjustments is None
        else adjustments.reindex(columns=list(PL_SEND_COLUMNS)),
        mapping,
        governance,
        include_adjustments=include_adjustments,
    )
    return effective, mapping, governance


def _effective_store(
    snapshot,
    effective: pd.DataFrame,
    *,
    include_adjustments: bool,
    editor_epoch: int = 0,
) -> dict[str, object]:
    """Serialize effective rows with the snapshot guard used by editors."""
    return {
        "revision": int(snapshot.revision),
        "market_date": pd.Timestamp(snapshot.market_date).date().isoformat(),
        "include_adjustments": bool(include_adjustments),
        "editor_epoch": int(editor_epoch),
        "rows": effective.to_dict("records"),
    }


def _write_pl_result(
    config: PLSendConfig,
    saved: pd.DataFrame,
    *,
    filename: str,
    market_date: str,
    revision: int,
) -> str:
    """Write through the injected boundary or an explicitly labelled fallback."""
    if config.write_pl is not None:
        config.write_pl(saved.copy(deep=True), market_date, revision)
        return f"Wrote through the configured write_pl connector as {filename}"

    directory = Path(config.saved_directory).expanduser().resolve()
    destination = directory / filename
    temporary = directory / f".{filename}.{uuid.uuid4().hex}.tmp"
    with _SAVE_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            saved.to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return f"No write_pl connector configured; saved local fallback to {destination}"


def register_pl_aggregate_callbacks(
    app: Dash,
    refresh_manager: RefreshManagerProtocol,
    *,
    prepared_frame_loader: Callable[[], pd.DataFrame | None] | None = None,
    saved_view_controls: SavedFilterViewControls | None = None,
) -> None:
    """Register P&L-local filters and the always-visible Aggregate P&L."""
    cache_lock = RLock()
    cached_revision = -1
    cached_frame: pd.DataFrame | None = None

    def current_aggregate_frame() -> pd.DataFrame | None:
        """Read only the mapped dashboard frame and prepare it once per revision."""
        nonlocal cached_frame, cached_revision
        if prepared_frame_loader is not None:
            return prepared_frame_loader()
        try:
            manager_revision = int(refresh_manager.health.revision)
        except Exception:
            manager_revision = -1
        if manager_revision <= 0:
            return None
        with cache_lock:
            if cached_frame is not None and cached_revision == manager_revision:
                return cached_frame

        try:
            dashboard = refresh_manager.read_frame("dashboard_frame")
        except RuntimeError:
            return None
        if dashboard.frame.empty:
            prepared = dashboard.frame.copy(deep=True)
        else:
            prepared = prepare_risk_data(dashboard.frame)
        with cache_lock:
            if int(dashboard.revision) >= cached_revision:
                cached_revision = int(dashboard.revision)
                cached_frame = prepared
            return cached_frame

    filter_outputs = [
        output
        for field in PL_FILTER_FIELDS
        for output in (
            Output(PL_FILTER_IDS[field.key], "options"),
            Output(PL_FILTER_IDS[field.key], "value"),
        )
    ]
    apply_inputs = (
        [Input(saved_view_controls.apply_request_id, "data")]
        if saved_view_controls is not None
        else []
    )
    apply_states = (
        [State(saved_view_controls.applied_request_id, "data")]
        if saved_view_controls is not None
        else []
    )

    @app.callback(
        *filter_outputs,
        Output("pnl-filter-exclude-selected", "value"),
        Input("data-revision-store", "data"),
        *apply_inputs,
        *[State(PL_FILTER_IDS[field.key], "value") for field in PL_FILTER_FIELDS],
        State("pnl-filter-exclude-selected", "value"),
        *apply_states,
    )
    def update_pl_filter_controls(_data_revision, *values):
        """Own all P&L filter values, including validated saved-view requests."""
        offset = 0
        request = None
        if saved_view_controls is not None:
            request = values[0]
            offset = 1
        selected_values = [
            list(selected or [])
            for selected in values[offset : offset + len(PL_FILTER_FIELDS)]
        ]
        exclude_value = list(values[offset + len(PL_FILTER_FIELDS)] or [])
        applied_saved_view_request = (
            values[offset + len(PL_FILTER_FIELDS) + 1]
            if saved_view_controls is not None
            else None
        )
        try:
            trigger = ctx.triggered_id
        except Exception:
            trigger = None
        request_id = saved_view_request_id(request)
        saved_view_pending = bool(
            request_id and request_id != applied_saved_view_request
        )
        request_matches_base = False
        if saved_view_pending and saved_view_controls is not None:
            try:
                request_matches_base = saved_view_request_matches_base(
                    request,
                    saved_view_controls,
                    selected_values,
                    exclude_value,
                )
            except ValueError:
                request_matches_base = False
        apply_pending = (
            saved_view_pending
            and saved_view_controls is not None
            and (
                trigger == saved_view_controls.apply_request_id or request_matches_base
            )
        )
        if apply_pending:
            try:
                applied = saved_view_request_values(request, saved_view_controls)
            except ValueError:
                applied = None
            if applied is not None:
                applied_values, exclude_value = applied
                selected_values = [list(selected) for selected in applied_values]

        frame = current_aggregate_frame()
        if frame is None:
            options = {field.key: [] for field in PL_FILTER_FIELDS}
            valid_values = selected_values
        else:
            options = pl_filter_options(frame)
            valid_values = []
            for field, selected in zip(
                PL_FILTER_FIELDS,
                selected_values,
                strict=True,
            ):
                available = {option["value"] for option in options[field.key]}
                valid_values.append([value for value in selected if value in available])

        result: list[object] = []
        for field, selected in zip(
            PL_FILTER_FIELDS,
            valid_values,
            strict=True,
        ):
            result.extend((options[field.key], selected))
        result.append(exclude_value)
        return tuple(result)

    @app.callback(
        Output("pnl-aggregate-open-risk-types", "data"),
        Output("pnl-aggregate-pl-grid", "children"),
        Input("pnl-aggregate-pl-dimension", "value"),
        Input("data-revision-store", "data"),
        Input(
            {"type": PL_AGGREGATE_TOGGLE_TYPE, "risk_type": ALL},
            "n_clicks",
        ),
        *[Input(PL_FILTER_IDS[field.key], "value") for field in PL_FILTER_FIELDS],
        Input("pnl-filter-exclude-selected", "value"),
        State("pnl-aggregate-open-risk-types", "data"),
    )
    def reduce_and_render_pl_aggregate(
        dimension,
        _data_revision,
        row_clicks,
        *filter_values_mode_and_open,
    ):
        """Filter at position grain and reduce one P&L-local chevron."""
        selected_values = filter_values_mode_and_open[: len(PL_FILTER_FIELDS)]
        exclude_value = filter_values_mode_and_open[len(PL_FILTER_FIELDS)]
        effective_open = list(filter_values_mode_and_open[-1] or [])
        updated_open = no_update
        if row_clicks and max(int(value or 0) for value in row_clicks) > 0:
            triggered = ctx.triggered_id
            if isinstance(triggered, dict):
                risk_type = str(triggered.get("risk_type", "")).strip()
                if risk_type:
                    opened = set(effective_open)
                    if risk_type in opened:
                        opened.remove(risk_type)
                    else:
                        opened.add(risk_type)
                    effective_open = sorted(
                        opened,
                        key=lambda value: (RISK_TYPE_ORDER.get(value, 99), value),
                    )
                    updated_open = effective_open

        frame = current_aggregate_frame()
        if frame is None:
            return (
                updated_open,
                html.Div(
                    "P&L data is still loading. Aggregate P&L will update after the first committed refresh.",
                    className="empty-state",
                    role="status",
                ),
            )
        filtered = apply_filters(
            frame,
            None,
            None,
            pl_filter_map(selected_values),
            exclude_selected="exclude" in (exclude_value or []),
        )
        valid_types = (
            set(filtered["risk type"].astype(str)) if not filtered.empty else set()
        )
        valid_open = [value for value in effective_open if value in valid_types]
        if valid_open != effective_open:
            effective_open = valid_open
            updated_open = effective_open
        return (
            updated_open,
            build_pl_aggregate_table(filtered, dimension, effective_open),
        )


def register_pl_send_callbacks(
    app: Dash,
    refresh_manager: RefreshManagerProtocol,
    config: PLSendConfig,
) -> None:
    """Register independently lazy P&L sections and governed send actions."""

    history_cache_lock = RLock()
    history_cache: pd.DataFrame | None = None

    def current_pl_history(*, reload: bool = False) -> pd.DataFrame:
        """Load history once per disclosure, then reuse it for cell/range clicks."""
        nonlocal history_cache
        if not reload:
            with history_cache_lock:
                if history_cache is not None:
                    return history_cache
        try:
            loaded = load_pl_history(config.history_source)
        except (PLSendValidationError, TypeError):
            if reload:
                with history_cache_lock:
                    history_cache = None
            raise
        with history_cache_lock:
            history_cache = loaded
            return history_cache

    def current_pl_snapshot():
        """Return None only while this worker has no committed revision yet."""
        try:
            return refresh_manager.pl_snapshot
        except RuntimeError:
            if int(refresh_manager.health.revision) <= 0:
                return None
            raise

    @app.callback(
        Output("pl-send-preview-grid", "data"),
        Output("pl-send-preview-status", "children"),
        Input("pl-preview-summary", "n_clicks"),
        Input("data-revision-store", "data"),
        Input("pl-include-adjustments", "value"),
        Input("pl-adjustment-revision-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_pl_send(
        summary_clicks,
        _revision,
        include_values,
        _adjustment_revision,
    ):
        if not int(summary_clicks or 0) % 2:
            return [], "Open P&L Preview to load its current rows."
        snapshot = current_pl_snapshot()
        if snapshot is None:
            return [], "P&L data is still loading. This preview will update shortly."
        include_adjustments = "include" in (include_values or [])
        effective, _mapping, _governance_frame = _effective_rows(
            snapshot,
            config,
            include_adjustments=include_adjustments,
        )
        return (
            _display_records(effective),
            f"{len(effective):,} unique Portfolio + ConcertoField rows.",
        )

    def register_effective_store(
        *,
        store_id: str,
        toggle_id: str,
        section_revision_id: str,
        summary_id: str,
        filter_id: str,
        scope_column: str,
    ) -> None:
        @app.callback(
            Output(store_id, "data"),
            Output(filter_id, "options"),
            Output(filter_id, "value"),
            Input(summary_id, "n_clicks"),
            Input("data-revision-store", "data"),
            Input(toggle_id, "value"),
            Input(section_revision_id, "data"),
            State(filter_id, "value"),
            prevent_initial_call=True,
        )
        def refresh_effective_store(
            summary_clicks,
            _revision,
            include_values,
            section_revision,
            selected_scope,
        ):
            if not int(summary_clicks or 0) % 2:
                return {}, no_update, no_update
            snapshot = current_pl_snapshot()
            if snapshot is None:
                return {}, [], None
            effective, _mapping, _governance_frame = _effective_rows(
                snapshot,
                config,
                include_adjustments="include" in (include_values or []),
            )
            values = sorted(effective[scope_column].astype(str).unique().tolist())
            selected = (
                selected_scope
                if selected_scope in values
                else (values[0] if values else None)
            )
            return (
                _effective_store(
                    snapshot,
                    effective,
                    include_adjustments="include" in (include_values or []),
                    editor_epoch=int(section_revision or 0),
                ),
                [{"label": value, "value": value} for value in values],
                selected,
            )

    register_effective_store(
        store_id="pl-send-sog-effective-store",
        toggle_id="pl-sog-include-adjustments",
        section_revision_id="pl-sog-adjustment-revision-store",
        summary_id="pl-sog-summary",
        filter_id="pl-send-sog-filter",
        scope_column=SIGNOFF_GROUP,
    )
    register_effective_store(
        store_id="pl-send-portfolio-effective-store",
        toggle_id="pl-portfolio-include-adjustments",
        section_revision_id="pl-portfolio-adjustment-revision-store",
        summary_id="pl-portfolio-summary",
        filter_id="pl-send-portfolio-filter",
        scope_column=PORTFOLIO,
    )

    @app.callback(
        Output("pl-history-grid", "data"),
        Output("pl-history-status", "children"),
        Output("pl-history-date-range", "min_date_allowed"),
        Output("pl-history-date-range", "max_date_allowed"),
        Input("pl-history-summary", "n_clicks"),
        prevent_initial_call=True,
    )
    def render_historical_pl_hierarchy(summary_clicks):
        """Load once per disclosure and render every latest-day hierarchy node."""
        if not int(summary_clicks or 0) % 2:
            return (
                [],
                "Open Histo P&L to load its validated hierarchy.",
                None,
                None,
            )
        if config.history_source is None:
            return [], "No historical P&L source is configured.", None, None
        try:
            history = current_pl_history(reload=True)
        except (PLSendValidationError, TypeError) as exc:
            return (
                [],
                f"Historical P&L could not be loaded: {exc}",
                None,
                None,
            )
        rows = build_pl_history_hierarchy(history)
        dates = sorted(history[MARKET_DATE].astype(str).unique())
        latest = dates[-1]
        return (
            rows,
            f"{len(rows):,} fully expanded hierarchy rows · latest daily values {latest}.",
            dates[0],
            dates[-1],
        )

    @app.callback(
        Output("pl-history-chart", "figure"),
        Output("pl-history-range-store", "data"),
        Output("pl-history-selection-store", "data"),
        Output("pl-history-plot-status", "children"),
        Output("pl-history-range-1w", "className"),
        Output("pl-history-range-mtd", "className"),
        Output("pl-history-range-ytd", "className"),
        Output("pl-history-range-all", "className"),
        Output("pl-history-date-range", "start_date"),
        Output("pl-history-date-range", "end_date"),
        Input("pl-history-grid", "data"),
        Input("pl-history-grid", "active_cell"),
        Input("pl-history-range-1w", "n_clicks"),
        Input("pl-history-range-mtd", "n_clicks"),
        Input("pl-history-range-ytd", "n_clicks"),
        Input("pl-history-range-all", "n_clicks"),
        Input("pl-history-date-range", "start_date"),
        Input("pl-history-date-range", "end_date"),
        State("pl-history-range-store", "data"),
        State("pl-history-selection-store", "data"),
        prevent_initial_call=True,
    )
    def render_historical_pl_chart(
        rows,
        active_cell,
        _one_week_clicks,
        _mtd_clicks,
        _ytd_clicks,
        _all_clicks,
        explicit_start,
        explicit_end,
        range_state,
        selection_state,
    ):
        """Plot the selected numeric hierarchy cell over one daily date window."""
        empty_figure = _historical_pl_figure(pd.DataFrame(), {})
        if not rows or config.history_source is None:
            classes = ["pl-history-range-button"] * 4
            classes[-1] += " is-active"
            return (
                empty_figure,
                {"preset": "all"},
                {},
                "Select a numeric hierarchy cell to plot its daily series.",
                *classes,
                None,
                None,
            )
        try:
            history = current_pl_history()
        except (PLSendValidationError, TypeError) as exc:
            classes = ["pl-history-range-button"] * 4
            return (
                empty_figure,
                dict(range_state or {}),
                dict(selection_state or {}),
                f"Historical P&L could not be loaded: {exc}",
                *classes,
                explicit_start,
                explicit_end,
            )

        try:
            trigger = ctx.triggered_id
        except Exception:
            trigger = None
        selection = history_selection_from_cell(active_cell, rows, selection_state)
        if not selection:
            selection = history_selection_from_cell(
                {"row": 0, "column_id": HISTO_TYPE},
                rows,
            )
        series = select_pl_history_series(history, selection)

        preset_by_button = {
            "pl-history-range-1w": "1w",
            "pl-history-range-mtd": "mtd",
            "pl-history-range-ytd": "ytd",
            "pl-history-range-all": "all",
        }
        prior_range = dict(range_state or {})
        preset = str(prior_range.get("preset", "all"))
        start_date = prior_range.get("start_date")
        end_date = prior_range.get("end_date")
        if trigger in preset_by_button:
            preset = preset_by_button[str(trigger)]
            start_date = None
            end_date = None
        elif trigger == "pl-history-date-range" and (
            explicit_start != prior_range.get("start_date")
            or explicit_end != prior_range.get("end_date")
        ):
            preset = "custom"
            start_date = explicit_start
            end_date = explicit_end
        elif not prior_range:
            preset = "all"

        resolved_start, resolved_end = history_range_bounds(
            series,
            preset,
            start_date=start_date,
            end_date=end_date,
        )
        if resolved_start is not None and resolved_end is not None:
            visible = series.loc[
                series[MARKET_DATE]
                .astype(str)
                .between(
                    resolved_start,
                    resolved_end,
                    inclusive="both",
                )
            ]
        else:
            visible = series
        resolved_range = {
            "preset": preset,
            "start_date": resolved_start,
            "end_date": resolved_end,
        }
        label = " → ".join(str(value) for value in selection.get("path", []))
        status = (
            f"{selection.get('history_type', '')} · {label} · "
            f"{resolved_start or '—'} to {resolved_end or '—'} · "
            f"{len(visible):,} daily observations."
        )
        active_preset = preset if preset in {"1w", "mtd", "ytd", "all"} else None
        classes = [
            "pl-history-range-button" + (" is-active" if active_preset == value else "")
            for value in ("1w", "mtd", "ytd", "all")
        ]
        return (
            _historical_pl_figure(visible, selection),
            resolved_range,
            selection,
            status,
            *classes,
            resolved_start,
            resolved_end,
        )

    def register_editor(
        *,
        store_id: str,
        table_id: str,
        filter_id: str,
        add_id: str,
        draft_store_id: str,
        active_scope_store_id: str,
        scope_column: str,
        portfolio_editable: bool,
        save_id: str,
        send_id: str,
    ) -> None:
        @app.callback(
            Output(table_id, "data"),
            Output(table_id, "dropdown"),
            Output(table_id, "dropdown_conditional"),
            Output(draft_store_id, "data"),
            Output(active_scope_store_id, "data"),
            Output(f"{table_id}-data-status", "children"),
            Input(store_id, "data"),
            Input(filter_id, "value"),
            Input(add_id, "n_clicks"),
            Input(table_id, "data_timestamp"),
            State(table_id, "data"),
            State(table_id, "data_previous"),
            State(draft_store_id, "data"),
            State(active_scope_store_id, "data"),
            running=[
                (Output(add_id, "disabled"), True, False),
                (Output(save_id, "disabled"), True, False),
                (Output(send_id, "disabled"), True, False),
            ],
        )
        def control_editor(
            store,
            selected_scope,
            _add_clicks,
            _data_timestamp,
            current_rows,
            _previous_rows,
            drafts,
            active_scope,
        ):
            if not store or not selected_scope:
                return (
                    [],
                    {},
                    [],
                    no_update,
                    selected_scope,
                    f"Choose a {scope_column} to load rows.",
                )

            trigger = ctx.triggered_id
            try:
                snapshot = refresh_manager.pl_snapshot
                mapping = load_plsend_mapping(config.mapping_source)
                governance = _governance(snapshot)
                allowed = _allowed_portfolios(
                    governance,
                    scope_column=scope_column,
                    selected_scope=selected_scope,
                )
                dropdown, dropdown_conditional = _editor_dropdowns(
                    mapping,
                    allowed,
                    portfolio_editable=portfolio_editable,
                )
                scope_key = _draft_key(scope_column, selected_scope)

                def baseline_or_draft() -> list[dict[str, object]]:
                    draft_rows = _matching_draft_rows(
                        drafts,
                        store,
                        scope_key=scope_key,
                        scope_column=scope_column,
                        selected_scope=selected_scope,
                    )
                    if draft_rows is not None:
                        return _govern_current_editor_records(
                            draft_rows,
                            store,
                            mapping,
                            governance,
                            scope_column=scope_column,
                            selected_scope=selected_scope,
                        )
                    return _baseline_editor_records(
                        store,
                        mapping,
                        governance,
                        scope_column=scope_column,
                        selected_scope=selected_scope,
                    )

                if not allowed:
                    return (
                        [],
                        dropdown,
                        dropdown_conditional,
                        no_update,
                        str(selected_scope),
                        f"No governed Portfolio belongs to {selected_scope}.",
                    )

                if trigger == add_id:
                    use_current_rows = bool(current_rows) and str(
                        active_scope or ""
                    ) == str(selected_scope)
                    rows = (
                        [dict(row) for row in current_rows]
                        if use_current_rows
                        else baseline_or_draft()
                    )
                    added = _new_editor_row(
                        market_date=store["market_date"],
                        mapping=mapping,
                        governance=governance,
                        allowed_portfolios=allowed,
                    )
                    final_rows = [added, *rows]
                    updated_drafts = _drafts_with_scope(
                        drafts,
                        store,
                        final_rows,
                        scope_column=scope_column,
                        selected_scope=selected_scope,
                    )
                    if use_current_rows:
                        patch = Patch()
                        patch.prepend(added)
                        data_out = patch
                    else:
                        data_out = final_rows
                    return (
                        data_out,
                        no_update,
                        no_update,
                        updated_drafts,
                        no_update,
                        f"Draft updated · {len(final_rows):,} rows for {selected_scope}.",
                    )

                if trigger == table_id:
                    rows = [dict(row) for row in (current_rows or [])]
                    for row in rows:
                        if row.get(scope_column) in (None, ""):
                            row[scope_column] = selected_scope
                    wrong_scope = str(active_scope or "") != str(selected_scope)
                    wrong_scope = wrong_scope or any(
                        str(row.get(scope_column, "")) != str(selected_scope)
                        for row in rows
                    )
                    if wrong_scope:
                        recovered = baseline_or_draft()
                        return (
                            recovered,
                            dropdown,
                            dropdown_conditional,
                            no_update,
                            str(selected_scope),
                            f"Recovered {len(recovered):,} rows for {selected_scope} after a late edit.",
                        )

                    governed = _govern_current_editor_records(
                        rows,
                        store,
                        mapping,
                        governance,
                        scope_column=scope_column,
                        selected_scope=selected_scope,
                    )
                    patch = Patch()
                    has_patch = False
                    governed_columns = (*DISPLAY_COLUMNS, GRID_ROW_ID, MARKET_DATE)
                    for row_index, (current, final) in enumerate(zip(rows, governed)):
                        for column in governed_columns:
                            if current.get(column) != final.get(column):
                                patch[row_index][column] = final.get(column)
                                has_patch = True
                    updated_drafts = _drafts_with_scope(
                        drafts,
                        store,
                        governed,
                        scope_column=scope_column,
                        selected_scope=selected_scope,
                    )
                    return (
                        patch if has_patch else no_update,
                        no_update,
                        no_update,
                        updated_drafts,
                        no_update,
                        f"Draft updated · {len(governed):,} rows for {selected_scope}.",
                    )

                loaded = baseline_or_draft()
                message = (
                    f"Ready · {len(loaded):,} rows for {selected_scope}."
                    if loaded
                    else f"No PL rows are available for {selected_scope}."
                )
                return (
                    loaded,
                    dropdown,
                    dropdown_conditional,
                    no_update,
                    str(selected_scope),
                    message,
                )
            except Exception as exc:
                data_out = no_update if trigger in (table_id, add_id) else []
                return (
                    data_out,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    f"Could not prepare {scope_column} rows: {exc}",
                )

        app.clientside_callback(
            _SELECTION_SUMMARY_SCRIPT,
            Output(f"{table_id}-selection-summary-text", "children"),
            Output(f"{table_id}-selection-summary", "hidden"),
            Input(table_id, "selected_cells"),
            Input(table_id, "data"),
        )
        app.clientside_callback(
            _CLEAR_SELECTION_SCRIPT,
            Output(table_id, "selected_cells"),
            Output(table_id, "active_cell"),
            Input(f"{table_id}-selection-clear", "n_clicks"),
            Input(filter_id, "value"),
            Input(store_id, "data"),
            prevent_initial_call=True,
        )

    register_editor(
        store_id="pl-send-sog-effective-store",
        table_id="pl-send-sog-grid",
        filter_id="pl-send-sog-filter",
        add_id="add-sog-pl-row",
        draft_store_id="pl-send-sog-drafts-store",
        active_scope_store_id="pl-send-sog-active-scope-store",
        scope_column=SIGNOFF_GROUP,
        portfolio_editable=True,
        save_id="save-sog-adjustments-button",
        send_id="send-sog-pl-button",
    )
    register_editor(
        store_id="pl-send-portfolio-effective-store",
        table_id="pl-send-portfolio-grid",
        filter_id="pl-send-portfolio-filter",
        add_id="add-portfolio-pl-row",
        draft_store_id="pl-send-portfolio-drafts-store",
        active_scope_store_id="pl-send-portfolio-active-scope-store",
        scope_column=PORTFOLIO,
        portfolio_editable=False,
        save_id="save-portfolio-adjustments-button",
        send_id="send-portfolio-pl-button",
    )

    @app.callback(
        Output("pl-save-sog-adjustments-status", "children"),
        Output("pl-save-portfolio-adjustments-status", "children"),
        Output("pl-adjustment-revision-store", "data"),
        Output("pl-sog-adjustment-revision-store", "data"),
        Output("pl-portfolio-adjustment-revision-store", "data"),
        Input("save-sog-adjustments-button", "n_clicks"),
        Input("save-portfolio-adjustments-button", "n_clicks"),
        State("pl-send-sog-grid", "data"),
        State("pl-send-portfolio-grid", "data"),
        State("pl-send-sog-effective-store", "data"),
        State("pl-send-portfolio-effective-store", "data"),
        State("pl-sog-adjustment-revision-store", "data"),
        State("pl-adjustment-revision-store", "data"),
        State("pl-portfolio-adjustment-revision-store", "data"),
        State("pl-send-sog-filter", "value"),
        State("pl-send-portfolio-filter", "value"),
        prevent_initial_call=True,
    )
    def save_adjustments(
        _sog_clicks,
        _portfolio_clicks,
        sog_records,
        portfolio_records,
        sog_store,
        portfolio_store,
        adjustment_revision,
        sog_adjustment_revision,
        portfolio_adjustment_revision,
        selected_sog,
        selected_portfolio,
    ):
        trigger = ctx.triggered_id
        is_sog = trigger == "save-sog-adjustments-button"
        records = sog_records if is_sog else portfolio_records
        store = sog_store if is_sog else portfolio_store
        unchanged = no_update
        try:
            snapshot = refresh_manager.pl_snapshot
            if not store:
                raise ValueError("the PL editor has not loaded")
            expected_date = pd.Timestamp(snapshot.market_date).date().isoformat()
            if int(store.get("revision", -1)) != int(snapshot.revision):
                raise ValueError("the risk snapshot changed; reload the PL editor")
            if str(store.get("market_date", "")) != expected_date:
                raise ValueError("the market date changed; reload the PL editor")
            mapping = load_plsend_mapping(config.mapping_source)
            governance = _governance(snapshot)
            scope_column = SIGNOFF_GROUP if is_sog else PORTFOLIO
            selected_scope = selected_sog if is_sog else selected_portfolio
            if not selected_scope:
                raise ValueError("select a Scope before saving")
            raw_rows = _domain_frame(records)
            outside_scope = raw_rows[scope_column].astype(str).ne(str(selected_scope))
            if outside_scope.any():
                raise ValueError(
                    f"the editor contains rows outside the selected {scope_column}"
                )
            governed_records = _govern_current_editor_records(
                records,
                store,
                mapping,
                governance,
                scope_column=scope_column,
                selected_scope=selected_scope,
            )
            rows = _domain_frame(governed_records)
            adjustments = rows.loc[rows[ADJUSTMENT].eq(True)].copy()
            include_existing = bool(store.get("include_adjustments"))
            if adjustments.empty and not include_existing:
                message = "No adjustments to save."
                return (
                    message if is_sog else unchanged,
                    unchanged if is_sog else message,
                    no_update,
                    no_update,
                    no_update,
                )
            collapsed = (
                collapse_pl_send_rows(
                    adjustments,
                    mapping,
                    governance,
                    require_adjustment=True,
                )
                if not adjustments.empty
                else adjustments.reindex(columns=list(PL_SEND_COLUMNS))
            )
            if include_existing:
                if is_sog:
                    replace_portfolios = set(
                        governance.loc[
                            governance[SIGNOFF_GROUP]
                            .astype(str)
                            .eq(str(selected_scope)),
                            PORTFOLIO,
                        ]
                        .astype(str)
                        .tolist()
                    )
                else:
                    replace_portfolios = {str(selected_scope)}
            else:
                replace_portfolios = set(collapsed[PORTFOLIO].astype(str).tolist())
            _merge_and_persist_adjustments(
                config,
                collapsed,
                market_date=expected_date,
                revision=int(snapshot.revision),
                replace_portfolios=replace_portfolios,
            )
            message = (
                f"Saved {len(collapsed):,} adjustments for {expected_date}."
                if not collapsed.empty
                else f"Cleared saved adjustments for {selected_scope}."
            )
            next_revision = int(adjustment_revision or 0) + 1
            next_section_revision = (
                int(
                    sog_adjustment_revision if is_sog else portfolio_adjustment_revision
                )
                or 0
            ) + 1
            return (
                message if is_sog else unchanged,
                unchanged if is_sog else message,
                next_revision,
                (next_section_revision if is_sog else no_update),
                (next_section_revision if not is_sog else no_update),
            )
        except Exception as exc:
            message = f"Not saved: {exc}"
            return (
                message if is_sog else unchanged,
                unchanged if is_sog else message,
                no_update,
                no_update,
                no_update,
            )

    def send_rows(
        records,
        sender: SendFunction,
        store: dict[str, object],
        *,
        scope_column: str,
        selected_scope: object,
    ) -> str:
        snapshot = refresh_manager.pl_snapshot
        if not store or not selected_scope:
            raise ValueError("select a loaded scope before sending")
        expected_date = pd.Timestamp(snapshot.market_date).date().isoformat()
        if int(store.get("revision", -1)) != int(snapshot.revision):
            raise ValueError("the risk snapshot changed; reload the PL editor")
        if str(store.get("market_date", "")) != expected_date:
            raise ValueError("the market date changed; reload the PL editor")
        mapping = load_plsend_mapping(config.mapping_source)
        governance = _governance(snapshot)
        raw_rows = _domain_frame(records)
        outside_scope = raw_rows[scope_column].astype(str).ne(str(selected_scope))
        if outside_scope.any():
            raise ValueError(
                f"the editor contains rows outside the selected {scope_column}"
            )
        governed_records = _govern_current_editor_records(
            records,
            store,
            mapping,
            governance,
            scope_column=scope_column,
            selected_scope=selected_scope,
        )
        rows = _domain_frame(governed_records)
        if rows.empty:
            raise ValueError("there are no rows to send")
        rows = collapse_pl_send_rows(rows, mapping, governance)
        sender(rows[list(DISPLAY_COLUMNS)].copy())
        return f"success · sent {len(rows):,} governed rows"

    @app.callback(
        Output("pl-send-sog-status", "children"),
        Input("send-sog-pl-button", "n_clicks"),
        State("pl-send-sog-grid", "data"),
        State("pl-send-sog-effective-store", "data"),
        State("pl-send-sog-filter", "value"),
        prevent_initial_call=True,
    )
    def send_sog(n_clicks, records, store, selected_scope):
        if not n_clicks:
            raise PreventUpdate

        try:
            return send_rows(
                records,
                config.send_sog_pl,
                store,
                scope_column=SIGNOFF_GROUP,
                selected_scope=selected_scope,
            )
        except Exception as exc:
            return f"Not sent: {exc}"

    @app.callback(
        Output("pl-send-portfolio-status", "children"),
        Input("send-portfolio-pl-button", "n_clicks"),
        State("pl-send-portfolio-grid", "data"),
        State("pl-send-portfolio-effective-store", "data"),
        State("pl-send-portfolio-filter", "value"),
        prevent_initial_call=True,
    )
    def send_portfolio(n_clicks, records, store, selected_scope):
        if not n_clicks:
            raise PreventUpdate

        try:
            return send_rows(
                records,
                config.send_portfolio_pl,
                store,
                scope_column=PORTFOLIO,
                selected_scope=selected_scope,
            )
        except Exception as exc:
            return f"Not sent: {exc}"

    @app.callback(
        Output("save-pl-status", "children"),
        Output("save-pl-download", "data"),
        Input("save-pl-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def save_pl(n_clicks):
        if not n_clicks:
            raise PreventUpdate
        try:
            snapshot = refresh_manager.pl_snapshot
            mapping = load_plsend_mapping(config.mapping_source)
            governance = _governance(snapshot)
            adjustments = config.adjustment_repository.load(snapshot.market_date)
            saved = build_saved_pl_frame(
                snapshot.combined_pl,
                mapping,
                governance,
                adjustments.reindex(columns=list(PL_SEND_COLUMNS)),
                include_adjustments=True,
            )
            filename = (
                f"pl_{pd.Timestamp(snapshot.market_date).date().isoformat()}_"
                f"revision_{snapshot.revision}.csv"
            )
            destination_status = _write_pl_result(
                config,
                saved,
                filename=filename,
                market_date=pd.Timestamp(snapshot.market_date).date().isoformat(),
                revision=int(snapshot.revision),
            )
            adjustment_count = int(saved[ADJUSTMENT].fillna(False).astype(bool).sum())
            status = (
                f"{destination_status}; {len(saved):,} rows"
                f" ({adjustment_count:,} adjustments) and downloaded the CSV."
            )
            return status, dcc.send_data_frame(
                saved.to_csv,
                filename,
                index=False,
                lineterminator="\n",
            )
        except Exception as exc:
            return f"Not saved: {exc}", no_update


__all__ = [
    "PLSendConfig",
    "WritePLFunction",
    "build_pl_history_hierarchy",
    "history_range_bounds",
    "history_selection_from_cell",
    "register_pl_aggregate_callbacks",
    "register_pl_send_callbacks",
    "select_pl_history_series",
]
