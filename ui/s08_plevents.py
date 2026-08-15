"""Callbacks for governed PL adjustments, SOG/portfolio sending, and saving."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable

import pandas as pd
from dash import Dash, Input, Output, Patch, State, ctx, dcc, no_update
from dash.exceptions import PreventUpdate
from core.s01_schema import PORTFOLIO_MAPPED_COLUMN, PORTFOLIO_METADATA_COLUMNS
from core.s04_pl import (
    ADJUSTMENT,
    MARKET_DATE,
    PL,
    CONCERTO_FIELD,
    PL_SEND_COLUMNS,
    PORTFOLIO,
    RISK_GREEK,
    RISK_TYPE,
    SIGNOFF_GROUP,
    apply_adjustment_overlay,
    build_pl_send_base,
    build_saved_pl_frame,
    collapse_pl_send_rows,
    load_plsend_mapping,
    load_portfolio_governance,
)
from .s06_plview import (
    DISPLAY_COLUMNS,
    GRID_ROW_ID,
)
from .s01_contracts import AdjustmentRepositoryProtocol, RefreshManagerProtocol


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


def register_pl_send_callbacks(
    app: Dash,
    refresh_manager: RefreshManagerProtocol,
    config: PLSendConfig,
) -> None:
    """Register the four PL-send sections against the committed manager snapshot."""

    @app.callback(
        Output("pl-send-preview-grid", "data"),
        Output("pl-send-preview-status", "children"),
        Input("pl-workflow-summary", "n_clicks"),
        Input("pl-preview-summary", "n_clicks"),
        Input("data-revision-store", "data"),
        Input("pl-include-adjustments", "value"),
        Input("pl-adjustment-revision-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_pl_send(
        workflow_clicks,
        summary_clicks,
        _revision,
        include_values,
        _adjustment_revision,
    ):
        if not int(workflow_clicks or 0) % 2 or not int(summary_clicks or 0) % 2:
            return [], "Open Preview PL to load its current rows."
        snapshot = refresh_manager.pl_snapshot
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
            Input("pl-workflow-summary", "n_clicks"),
            Input(summary_id, "n_clicks"),
            Input("data-revision-store", "data"),
            Input(toggle_id, "value"),
            Input(section_revision_id, "data"),
            State(filter_id, "value"),
            prevent_initial_call=True,
        )
        def refresh_effective_store(
            workflow_clicks,
            summary_clicks,
            _revision,
            include_values,
            section_revision,
            selected_scope,
        ):
            if not int(workflow_clicks or 0) % 2 or not int(summary_clicks or 0) % 2:
                return {}, no_update, no_update
            snapshot = refresh_manager.pl_snapshot
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


__all__ = ["PLSendConfig", "WritePLFunction", "register_pl_send_callbacks"]
