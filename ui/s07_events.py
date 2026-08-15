"""Dash callback registration and staged Force Risk transactions.

The browser-local force-date store contains only values that a refresh manager
has successfully committed. Checkbox and date edits live in a memory-backed
draft until the user explicitly applies or cancels them.
"""

from __future__ import annotations

import logging
import json
import re
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from inspect import Parameter, signature
from threading import RLock, Thread, Timer
from time import monotonic, sleep
from typing import Any, Callable, Mapping, Sequence

import pandas as pd
from dash import ALL, MATCH, Dash, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from core.s02_pipeline import RefreshInProgressError, StaleRefreshError

from .s03_aggregate import (
    apply_credit_measure,
    apply_filters,
    default_open_rows,
    filter_ir_family,
    ordered_unique,
    parse_row_key,
    prepare_risk_data,
    recompute_filtered_promotion,
    selected_underlying_sort_metric,
)
from .s04_components import (
    build_aggregate_pl_table,
    build_alt_risk_table,
    build_credit_multi_table,
    build_detail_panel_with_state,
    build_initial_load_layout,
    build_layout,
    build_quick_market_result,
    build_quick_search_pivot,
    build_operating_date_content,
    build_risk_checker_inventory,
    build_risk_date_editor,
    build_risk_table,
    build_shared_refresh_shell,
    build_top_book_exposures,
    build_unmapped_books_table,
    default_top_book_open_rows,
    QUICK_SEARCH_DEFAULT_INDEX,
    QUICK_SEARCH_HIERARCHY_DEPTH,
    QUICK_SEARCH_INDEX_OPTIONS,
    QUICK_RISK_PIVOT_LIMIT,
    QUICK_MARKET_DEFAULT_INDEX,
)
from .s02_constants import (
    CREDIT_MEASURES,
    DETAIL_COMPONENTS,
    DETAIL_COMPONENT_LABELS,
    DIMENSION_FILTER_IDS,
    EXPANDABLE_METRICS,
    FILTER_DIMENSION_FIELDS,
    METRIC_COLUMNS,
    PLOT_METRICS,
    RISK_TYPE_ORDER,
    compose_detail_metric,
    split_detail_metric,
)
from .s01_contracts import (
    ControlSnapshotProtocol,
    RefreshManagerProtocol,
    RefreshSnapshotProtocol,
)


FORCE_STORE_ID = "perspective-risk-cube-forced-risk-v1"
VIEW_DATE_STORE_ID = "perspective-risk-cube-view-date-v1"
AUTO_REFRESH_STORE_ID = "perspective-risk-cube-auto-refresh-v1"
COMMODITY_MARKET_STORE_ID = "perspective-risk-cube-commodity-market-v1"
RISK_CHECKER_STORE_ID = "perspective-risk-cube-risk-checker-v1"
FORCE_DRAFT_STORE_ID = "force-risk-draft-store"
FORCE_RENDER_STORE_ID = "force-risk-render-store"
REFRESH_RESULT_STORE_ID = "refresh-result-store"
STARTUP_UI_ERROR_CONFIG_KEY = "CUBE_STARTUP_UI_ERROR"
STARTUP_COORDINATOR_CONFIG_KEY = "CUBE_STARTUP_COORDINATOR"
_UNSET = object()
_LOGGER = logging.getLogger(__name__)
_QUICK_SEARCH_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class StartupStatus:
    """Small lock-safe view of the one process-wide cold-start attempt."""

    phase: str
    attempt: int
    server_boot_id: str
    attempt_id: str | None
    elapsed_seconds: float
    error: str | None
    retryable: bool
    worker_alive: bool


class StartupCoordinator:
    """Run revision 1 outside the Dash request thread.

    The coordinator belongs to one application process, not to a browser
    session. Every browser follows the same writer, so repeated interval
    callbacks and simultaneous first visitors cannot launch duplicate source
    calls. Python cannot safely kill an arbitrary connector call; the
    watchdog therefore reports a stalled call while retaining ownership until
    that worker returns. This is deliberate: starting another writer would be
    more dangerous than waiting for the connector's own I/O timeout.
    """

    def __init__(
        self,
        manager: RefreshManagerProtocol,
        *,
        timeout_seconds: float = 2400.0,
        logger: Any = None,
    ) -> None:
        self._manager = manager
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._logger = logger
        self._lock = RLock()
        self._server_boot_id = uuid.uuid4().hex
        self._phase = "idle"
        self._attempt = 0
        self._attempt_id: str | None = None
        self._started_at = 0.0
        self._error: str | None = None
        self._worker: Thread | None = None
        self._start_timer: Timer | None = None

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def _revision(self) -> int:
        try:
            return int(self._manager.health.revision)
        except Exception:
            return 0

    @staticmethod
    def _error_text(error: BaseException) -> str:
        detail = " ".join(str(error).splitlines()).strip()
        if not detail:
            detail = type(error).__name__
        return f"Initial data load failed: {type(error).__name__}: {detail}"[:1_500]

    def start(self, *, retry: bool = False) -> bool:
        """Start one writer, returning whether a new worker was launched."""
        with self._lock:
            if self._revision() > 0:
                self._phase = "succeeded"
                self._error = None
                return False
            worker_alive = self._worker is not None and self._worker.is_alive()
            if worker_alive or self._phase in {"running", "stalled", "succeeded"}:
                return False
            if self._phase == "failed" and not retry:
                return False

            if self._start_timer is not None:
                self._start_timer.cancel()
                self._start_timer = None
            self._attempt += 1
            attempt = self._attempt
            self._attempt_id = f"{self._server_boot_id}:{attempt}"
            self._phase = "running"
            self._error = None
            self._started_at = monotonic()
            worker = Thread(
                target=self._run,
                args=(attempt,),
                name=f"cube-startup-{attempt}",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            if self._logger is not None:
                self._logger.info(
                    "Cube initial load started; boot=%s attempt=%s.",
                    self._server_boot_id,
                    self._attempt_id,
                )
            return True

    def schedule_start(self, *, delay_seconds: float = 0.25) -> bool:
        """Schedule at most one delayed first-paint start for this process."""
        delay = max(0.0, float(delay_seconds))
        with self._lock:
            if self._revision() > 0:
                self._phase = "succeeded"
                self._error = None
                return False
            worker_alive = self._worker is not None and self._worker.is_alive()
            timer_alive = self._start_timer is not None and self._start_timer.is_alive()
            if worker_alive or timer_alive or self._phase != "idle":
                return False

            timer: Timer

            def launch() -> None:
                try:
                    self.start()
                finally:
                    with self._lock:
                        if self._start_timer is timer:
                            self._start_timer = None

            timer = Timer(delay, launch)
            timer.daemon = True
            self._start_timer = timer
            timer.start()
            return True

    def status(self) -> StartupStatus:
        """Return current state and apply the non-destructive watchdog."""
        with self._lock:
            if self._revision() > 0:
                self._phase = "succeeded"
                self._error = None
            worker_alive = self._worker is not None and self._worker.is_alive()
            elapsed = (
                max(0.0, monotonic() - self._started_at) if self._started_at else 0.0
            )
            if (
                self._phase == "running"
                and worker_alive
                and elapsed >= self._timeout_seconds
            ):
                function_name = None
                try:
                    function_name = self._manager.progress.function_name
                except Exception:
                    pass
                active = f" Active call: {function_name}." if function_name else ""
                self._phase = "stalled"
                self._error = (
                    f"Initial load has exceeded the {self._timeout_seconds:g}-second "
                    f"watchdog.{active} The original connector call still owns the "
                    "writer; configure an I/O timeout in that connector."
                )
            return StartupStatus(
                phase=self._phase,
                attempt=self._attempt,
                server_boot_id=self._server_boot_id,
                attempt_id=self._attempt_id,
                elapsed_seconds=elapsed,
                error=self._error,
                retryable=self._phase == "failed",
                worker_alive=worker_alive,
            )

    def _succeed(self, attempt: int) -> None:
        with self._lock:
            if attempt != self._attempt:
                return
            self._phase = "succeeded"
            self._error = None
            attempt_id = self._attempt_id
        if self._logger is not None:
            self._logger.info(
                "Cube initial load succeeded; boot=%s attempt=%s.",
                self._server_boot_id,
                attempt_id,
            )

    def _fail(self, attempt: int, error: BaseException) -> None:
        message = self._error_text(error)
        with self._lock:
            if attempt != self._attempt:
                return
            self._phase = "failed"
            self._error = message
        if self._logger is not None:
            self._logger.error(
                "Cube initial data load failed on attempt %s",
                attempt,
                exc_info=(type(error), error, error.__traceback__),
            )

    def _follow_existing_writer(self, attempt: int) -> None:
        while True:
            if self._revision() > 0:
                self._succeed(attempt)
                return
            try:
                progress = self._manager.progress
                running = bool(progress.running)
                progress_error = progress.error
            except Exception as error:
                self._fail(attempt, error)
                return
            if not running:
                self._fail(
                    attempt,
                    RuntimeError(
                        str(progress_error)
                        if progress_error
                        else "The existing refresh ended without publishing revision 1."
                    ),
                )
                return
            sleep(0.2)

    def _run(self, attempt: int) -> None:
        try:
            if self._revision() > 0:
                self._succeed(attempt)
                return
            self._manager.refresh(
                forced_dates={},
                view_date=None,
                commodity_market_enabled=False,
                risk_checker_enabled=True,
                reason="initial load",
                expected_revision=0,
            )
            if self._revision() <= 0:
                raise RuntimeError(
                    "Initial refresh returned without publishing revision 1."
                )
            self._succeed(attempt)
        except RefreshInProgressError:
            self._follow_existing_writer(attempt)
        except StaleRefreshError:
            if self._revision() > 0:
                self._succeed(attempt)
            else:
                self._fail(
                    attempt,
                    RuntimeError(
                        "The initial refresh became stale before revision 1 was published."
                    ),
                )
        except Exception as error:
            if self._revision() > 0:
                self._succeed(attempt)
            else:
                self._fail(attempt, error)


def _combine_udl_browser_search(value: str) -> str:
    """Return whitespace aliases that Dash's local dropdown search can index."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    parts = tuple(
        part for part in _QUICK_SEARCH_NON_ALPHANUMERIC.split(normalized) if part
    )
    aliases = list(parts)
    for size in range(2, min(4, len(parts)) + 1):
        aliases.extend(
            "".join(parts[start : start + size])
            for start in range(len(parts) - size + 1)
        )
    if parts:
        aliases.append("".join(parts))
    return " ".join(dict.fromkeys(aliases))


def _quick_search_result_parts(
    result: object,
) -> tuple[pd.DataFrame, int | None, int | None]:
    """Normalise the small result contract without copying manager internals."""
    if isinstance(result, pd.DataFrame):
        return result, len(result), None
    if isinstance(result, Mapping):
        frame = result.get("frame", result.get("rows", result.get("results")))
        total = result.get("total")
        revision = result.get("revision")
    else:
        frame = getattr(result, "frame", None)
        total = getattr(result, "total", None)
        revision = getattr(result, "revision", None)
    if isinstance(frame, Sequence) and not isinstance(
        frame, (str, bytes, pd.DataFrame)
    ):
        frame = pd.DataFrame(frame)
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("quick search returned no DataFrame")
    return (
        frame,
        int(total) if total is not None else len(frame),
        int(revision) if revision is not None else None,
    )


def _combine_udl_dropdown_options(raw_options: object) -> list[dict[str, str]]:
    """Return stable Dash options for the snapshot's exact identity keys."""
    if raw_options is None:
        return []
    if isinstance(raw_options, pd.Series):
        candidates = raw_options.tolist()
    elif isinstance(raw_options, Sequence) and not isinstance(
        raw_options, (str, bytes)
    ):
        candidates = raw_options
    else:
        try:
            candidates = list(raw_options)  # type: ignore[arg-type]
        except TypeError:
            return []

    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            value = str(candidate.get("value", "")).strip()
            label = str(candidate.get("label", value)).strip()
            search = str(candidate.get("search", "")).strip()
        else:
            value = str(candidate).strip()
            label = value
            search = ""
        if not value or value in seen:
            continue
        seen.add(value)
        options.append(
            {
                "label": label or value,
                "value": value,
                "search": search or _combine_udl_browser_search(label or value),
            }
        )
    return options


def _normalise_quick_search_index(
    index_columns: object,
) -> tuple[tuple[str, ...], bool]:
    """Return a valid ordered index and whether the UI value needs restoring."""
    allowed = {value for _label, value in QUICK_SEARCH_INDEX_OPTIONS}
    if not isinstance(index_columns, Sequence) or isinstance(
        index_columns, (str, bytes)
    ):
        return QUICK_SEARCH_DEFAULT_INDEX, True
    selected = tuple(str(value) for value in index_columns)
    if (
        not selected
        or len(selected) != len(set(selected))
        or any(value not in allowed for value in selected)
    ):
        return QUICK_SEARCH_DEFAULT_INDEX, True
    return selected, False


_QUICK_SEARCH_TENOR_INDEXES = frozenset(("Tenor Swap", "Tenor Option"))
_QUICK_SEARCH_ABSENT_TENORS = frozenset(("", "n/a", "na", "spot", "unspecified"))


def _prune_quick_search_indexes(
    hierarchy: pd.DataFrame,
    index_columns: Sequence[str],
) -> tuple[str, ...]:
    """Remove tenor levels that carry no coordinate for this exact identity.

    The decision comes only from the returned data, not from Risk Type or Risk
    Greek names. This lets one-axis products drop at `Tenor Swap`, and surfaces
    retain both axes while preserving any selected reporting dimensions.
    """

    selected = tuple(str(column) for column in index_columns)
    if hierarchy.empty or QUICK_SEARCH_HIERARCHY_DEPTH not in hierarchy:
        return selected
    depths = pd.to_numeric(
        hierarchy[QUICK_SEARCH_HIERARCHY_DEPTH],
        errors="coerce",
    )
    leaves = hierarchy.loc[depths.eq(len(selected))]
    if leaves.empty:
        return selected

    effective: list[str] = []
    for column in selected:
        if column not in _QUICK_SEARCH_TENOR_INDEXES:
            effective.append(column)
            continue
        if column not in leaves:
            continue
        labels = leaves[column].astype("string").str.strip().str.casefold()
        meaningful = labels.notna() & ~labels.isin(_QUICK_SEARCH_ABSENT_TENORS)
        if meaningful.any():
            effective.append(column)

    if effective:
        return tuple(effective)
    # An exact Combine Udl identity always has an Underlying even when the user
    # selected only an unavailable tenor level. Keep the resulting hierarchy
    # useful and return the corrected picker value to the browser.
    return ("Underlying",)


def _render_quick_search_pivot(
    manager: RefreshManagerProtocol,
    *,
    combine_udl: object,
    identity_mode: object = "reported",
    index_columns: object,
    is_open: object,
    risk_filters: Mapping[str, Sequence[str] | None] | None = None,
    exclude_selected: bool = False,
):
    selected_mode = str(identity_mode or "reported").strip().casefold()
    selected_indexes, restore_index = _normalise_quick_search_index(index_columns)
    index_update = list(selected_indexes) if restore_index else no_update
    if not bool(is_open):
        return None, index_update
    selected_identity = str(combine_udl or "").strip()
    if not selected_identity:
        return (
            html.Div(
                "Select a Search Risk value to build the pivot.",
                className="quick-search-hint",
                role="status",
            ),
            index_update,
        )
    try:
        pivot = manager.pivot_combined_hierarchy
        pivot_kwargs: dict[str, object] = {
            "index_columns": selected_indexes,
            "leaf_limit": QUICK_RISK_PIVOT_LIMIT,
            "identity_mode": selected_mode,
            "risk_filters": risk_filters,
            "exclude_selected": exclude_selected,
        }
        # Filter arguments were added to the manager
        # contract after the exact-pivot helper first shipped.  Keeping this
        # small compatibility boundary lets cold fixtures and direct helper
        # callers use the older read-only protocol without catching a
        # TypeError raised *inside* the manager implementation.
        try:
            pivot_parameters = signature(pivot).parameters
        except (TypeError, ValueError):
            pivot_parameters = {}
        if pivot_parameters and not any(
            parameter.kind is Parameter.VAR_KEYWORD
            for parameter in pivot_parameters.values()
        ):
            pivot_kwargs = {
                name: value
                for name, value in pivot_kwargs.items()
                if name in pivot_parameters
            }

        result = pivot(
            selected_identity,
            **pivot_kwargs,
        )
        frame, total, revision = _quick_search_result_parts(result)
        effective_indexes = _prune_quick_search_indexes(frame, selected_indexes)
        if effective_indexes != selected_indexes:
            pivot_kwargs["index_columns"] = effective_indexes
            result = pivot(
                selected_identity,
                **pivot_kwargs,
            )
            frame, total, revision = _quick_search_result_parts(result)
            index_update = list(effective_indexes)
        return (
            build_quick_search_pivot(
                frame,
                combine_udl=selected_identity,
                index_columns=effective_indexes,
                total=total,
                revision=revision,
            ),
            index_update,
        )
    except (AttributeError, LookupError, TypeError, ValueError, RuntimeError) as error:
        _LOGGER.exception("Quick Risk Search render failed")
        detail = " ".join(str(error).splitlines()).strip() or type(error).__name__
        return (
            html.Div(
                f"Quick Risk Search failed: {type(error).__name__}: {detail[:400]}",
                className="quick-search-error",
                role="alert",
            ),
            index_update,
        )


def risk_action_view_token(
    risk_context: Mapping[str, Any] | None,
    table_view: str | None,
    dimension: str | None,
    credit_view: str | None,
    *,
    generation_state: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the generation token embedded in an interactive risk table.

    Delegated browser events can arrive while a table replacement is in flight.
    Binding every event to the rendered risk generation prevents an action from
    the old table being applied to the new risk type or view.
    """
    if not isinstance(risk_context, Mapping):
        return None
    risk_type = risk_context.get("risk_type")
    if not isinstance(risk_type, str) or not risk_type:
        return None
    normalized_view = table_view if table_view in {"main", "alt"} else "main"
    payload: dict[str, Any] = {
        "data_revision": risk_context.get("data_revision"),
        "dimension": str(dimension or ""),
        "ir_family": risk_context.get("ir_family"),
        "risk_type": risk_type,
        "table_view": normalized_view,
    }
    if normalized_view == "main" and risk_type == "Credit":
        payload["credit_view"] = (
            credit_view if credit_view in {"single", "multi"} else "single"
        )
    if generation_state is not None:
        payload["generation"] = dict(generation_state)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _top_book_action_view_token(
    data_revision: Any,
    *,
    splits: Sequence[str] | None = None,
    dimension_filters: Mapping[str, Sequence[str] | None] | None = None,
    exclude_selected: bool = False,
) -> str:
    """Bind Top Book actions to the exact filtered hierarchy generation."""
    return json.dumps(
        {
            "data_revision": data_revision,
            "filters": {
                key: sorted(values or [])
                for key, values in sorted((dimension_filters or {}).items())
            },
            "exclude_selected": bool(exclude_selected),
            "splits": sorted(splits or []),
            "view": "top-book",
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def risk_exclude_selected(value: Sequence[str] | None) -> bool:
    """Normalize the Risk-local exclusion checklist value."""
    return "exclude" in (value or [])


def filter_unmapped_portfolios(
    frame: pd.DataFrame,
    selected_portfolios: Sequence[str] | None,
    *,
    exclude_selected: bool = False,
) -> pd.DataFrame:
    """Apply the Portfolio subset of shared Risk filters to unmapped books.

    Unmapped rows deliberately have no governed Activity, Signoff Group,
    Category, or Sub Category. Portfolio is their only meaningful shared
    dimension, so mapped-only selections do not erase this diagnostic table.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("unmapped frame must be a pandas DataFrame")
    selected = list(selected_portfolios or [])
    if frame.empty or not selected:
        return frame.copy()
    if "Portfolio" not in frame:
        raise ValueError("unmapped frame is missing required column 'Portfolio'")
    matches = frame["Portfolio"].isin(selected)
    mask = ~matches if exclude_selected else matches
    return frame.loc[mask].copy()


def _has_valid_action_envelope(
    action: Mapping[str, Any] | None,
    *,
    kind: str,
) -> bool:
    """Validate the common delegated-action envelope."""
    if not isinstance(action, Mapping) or action.get("kind") != kind:
        return False
    sequence = action.get("sequence")
    return not isinstance(sequence, bool) and isinstance(sequence, int) and sequence > 0


def _is_current_risk_action(
    action: Mapping[str, Any] | None,
    *,
    kind: str,
    expected_view_token: str | None,
) -> bool:
    """Validate a delegated action and reject events from a replaced table."""
    return (
        _has_valid_action_envelope(action, kind=kind)
        and bool(expected_view_token)
        and action.get("view_token") == expected_view_token
    )


def _valid_delegated_row_key(value: Any, *, allow_total: bool) -> bool:
    """Accept canonical hierarchy keys, with an empty key only for TOTAL cells."""
    if not isinstance(value, str):
        return False
    if not value:
        return allow_total
    parsed = parse_row_key(value)
    return bool(parsed) and all(str(item).strip() for item in parsed.values())


def normalize_forced_dates(values: Mapping[str, Any] | None) -> dict[str, str]:
    """Return a stable source-to-ISO-date mapping or reject malformed state."""
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ValueError("forced dates must be a mapping")

    normalized: dict[str, str] = {}
    for raw_source, raw_value in values.items():
        source = str(raw_source).strip()
        if not source:
            raise ValueError("forced-date source names must not be blank")
        if (
            raw_value is None
            or isinstance(raw_value, bool)
            or str(raw_value).strip() == ""
        ):
            raise ValueError(f"forced date for {source} is missing")
        try:
            timestamp = pd.Timestamp(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"forced date for {source} is invalid") from exc
        if pd.isna(timestamp):
            raise ValueError(f"forced date for {source} is invalid")
        normalized[source] = timestamp.date().isoformat()
    return dict(sorted(normalized.items()))


def normalize_view_date(value: Any) -> str | None:
    """Normalize a global Today-view override; `None` means system Today."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("view date is invalid")
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("view date is invalid") from exc
    if pd.isna(timestamp):
        raise ValueError("view date is invalid")
    return timestamp.date().isoformat()


def auto_refresh_enabled(value: Any) -> bool:
    """Use a safe On default for missing or malformed browser-local state."""
    return value if isinstance(value, bool) else True


def commodity_market_enabled(value: Any) -> bool:
    """Commodity quote connectors are opt-in and therefore default Off."""
    return value if isinstance(value, bool) else False


def risk_checker_enabled(value: Any) -> bool:
    """Risk readiness/inventory validation is enabled unless explicitly disabled."""
    return value if isinstance(value, bool) else True


def collect_forced_dates(
    check_values: Sequence[Sequence[str] | None] | None,
    dates: Sequence[Any] | None,
    check_ids: Sequence[Mapping[str, Any]] | None,
    date_ids: Sequence[Mapping[str, Any]] | None,
) -> dict[str, str]:
    """Collect pattern-matching controls by source instead of array position."""
    checked_sources = {
        str(component_id.get("source", "")).strip()
        for component_id, values in zip(check_ids or (), check_values or ())
        if "force" in (values or ()) and str(component_id.get("source", "")).strip()
    }
    dates_by_source = {
        str(component_id.get("source", "")).strip(): value
        for component_id, value in zip(date_ids or (), dates or ())
        if str(component_id.get("source", "")).strip()
    }
    missing_dates = sorted(
        source
        for source in checked_sources
        if source not in dates_by_source or dates_by_source[source] in (None, "")
    )
    if missing_dates:
        raise ValueError(
            "forced dates are required for checked sources: " + ", ".join(missing_dates)
        )
    selected = {source: dates_by_source[source] for source in checked_sources}
    return normalize_forced_dates(selected)


def snapshot_forced_dates(
    snapshot: RefreshSnapshotProtocol | ControlSnapshotProtocol,
) -> dict[str, str]:
    """Serialize the manager's authoritative applied overrides."""
    return normalize_forced_dates(snapshot.forced_dates)


def snapshot_forced_view_date(
    snapshot: RefreshSnapshotProtocol | ControlSnapshotProtocol,
) -> str | None:
    """Serialize the manager's authoritative global Today-view override."""
    return normalize_view_date(snapshot.forced_view_date)


def make_force_draft(
    applied: Mapping[str, Any] | None,
    overrides: Mapping[str, Any] | None = None,
    *,
    revision: int = 0,
    applied_view_date: Any = None,
    view_date: Any = _UNSET,
) -> dict[str, Any]:
    """Create serializable draft state with an optimistic-concurrency base."""
    base = normalize_forced_dates(applied)
    proposal = base if overrides is None else normalize_forced_dates(overrides)
    base_view = normalize_view_date(applied_view_date)
    proposal_view = base_view if view_date is _UNSET else normalize_view_date(view_date)
    return {
        "base_revision": int(revision),
        "base_overrides": base,
        "overrides": proposal,
        "base_view_date": base_view,
        "view_date": proposal_view,
        "conflict": False,
    }


def draft_forced_dates(
    draft: Mapping[str, Any] | None,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Read the proposed overrides from the versioned draft envelope."""
    fallback_dates = normalize_forced_dates(fallback)
    if not draft or "overrides" not in draft:
        return fallback_dates
    return normalize_forced_dates(draft.get("overrides"))


def draft_base_dates(
    draft: Mapping[str, Any] | None,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Return the applied state against which a proposal was edited."""
    fallback_dates = normalize_forced_dates(fallback)
    if not draft or "base_overrides" not in draft:
        return fallback_dates
    return normalize_forced_dates(draft.get("base_overrides"))


def draft_view_date(
    draft: Mapping[str, Any] | None,
    *,
    fallback: Any = None,
) -> str | None:
    fallback_date = normalize_view_date(fallback)
    if not draft or "view_date" not in draft:
        return fallback_date
    return normalize_view_date(draft.get("view_date"))


def draft_base_view_date(
    draft: Mapping[str, Any] | None,
    *,
    fallback: Any = None,
) -> str | None:
    fallback_date = normalize_view_date(fallback)
    if not draft or "base_view_date" not in draft:
        return fallback_date
    return normalize_view_date(draft.get("base_view_date"))


def force_dates_dirty(
    draft: Mapping[str, Any] | None,
    applied: Mapping[str, Any] | None,
    *,
    applied_view_date: Any = None,
) -> bool:
    """Whether a draft differs from the currently committed manager state."""
    normalized_applied = normalize_forced_dates(applied)
    normalized_view = normalize_view_date(applied_view_date)
    return (
        draft_forced_dates(draft, fallback=normalized_applied) != normalized_applied
        or draft_view_date(draft, fallback=normalized_view) != normalized_view
    )


def cancel_force_dates(applied: Mapping[str, Any] | None) -> dict[str, str]:
    """Discard a draft without touching the refresh manager."""
    return normalize_forced_dates(applied)


def rebase_force_draft(
    draft: Mapping[str, Any] | None,
    applied: Mapping[str, Any] | None,
    *,
    revision: int,
    applied_view_date: Any = None,
) -> dict[str, Any]:
    """Rebase clean drafts and retain dirty drafts unless they were committed."""
    current = normalize_forced_dates(applied)
    current_view = normalize_view_date(applied_view_date)
    base = draft_base_dates(draft, fallback=current)
    proposal = draft_forced_dates(draft, fallback=current)
    base_view = draft_base_view_date(draft, fallback=current_view)
    proposal_view = draft_view_date(draft, fallback=current_view)

    if (proposal == base and proposal_view == base_view) or (
        proposal == current and proposal_view == current_view
    ):
        return make_force_draft(
            current,
            current,
            revision=revision,
            applied_view_date=current_view,
            view_date=current_view,
        )
    if base != current or base_view != current_view:
        return {
            "base_revision": int(draft.get("base_revision", revision))
            if draft
            else int(revision),
            "base_overrides": base,
            "overrides": proposal,
            "base_view_date": base_view,
            "view_date": proposal_view,
            "conflict": True,
        }
    return {
        "base_revision": int(revision),
        "base_overrides": base,
        "overrides": proposal,
        "base_view_date": base_view,
        "view_date": proposal_view,
        "conflict": False,
    }


@dataclass(frozen=True)
class ForceApplyResult:
    """Outcome used to decide whether browser-local state may be persisted."""

    snapshot: RefreshSnapshotProtocol
    requested: dict[str, str]
    requested_view_date: str | None
    committed: bool


def apply_force_dates(
    manager: RefreshManagerProtocol,
    requested: Mapping[str, Any] | None,
    *,
    reason: str = "apply forced risk dates",
    view_date: Any = _UNSET,
    commodity_market: bool | None = None,
    risk_checker: bool | None = None,
    expected_revision: int | None = None,
) -> ForceApplyResult:
    """Run exactly one transactional manager refresh for an Apply action."""
    normalized = normalize_forced_dates(requested)
    view_requested = view_date is not _UNSET
    normalized_view = normalize_view_date(view_date) if view_requested else None
    refresh_kwargs: dict[str, Any] = {"forced_dates": normalized, "reason": reason}
    if expected_revision is not None:
        refresh_kwargs["expected_revision"] = expected_revision
    if view_requested:
        refresh_kwargs["view_date"] = normalized_view
    if commodity_market is not None:
        refresh_kwargs["commodity_market_enabled"] = commodity_market
    if risk_checker is not None:
        refresh_kwargs["risk_checker_enabled"] = risk_checker
    snapshot = manager.refresh(**refresh_kwargs)
    view_committed = (
        not view_requested or snapshot_forced_view_date(snapshot) == normalized_view
    )
    committed = (
        not snapshot.errors
        and snapshot_forced_dates(snapshot) == normalized
        and view_committed
        and (
            commodity_market is None
            or bool(snapshot.commodity_market_enabled) == commodity_market
        )
        and (
            risk_checker is None or bool(snapshot.risk_checker_enabled) == risk_checker
        )
    )
    return ForceApplyResult(
        snapshot=snapshot,
        requested=normalized,
        requested_view_date=normalized_view,
        committed=committed,
    )


def persisted_force_dates(result: ForceApplyResult) -> dict[str, str] | None:
    """Return a local-store value only for a confirmed committed snapshot."""
    return dict(result.requested) if result.committed else None


class _RiskDataCache:
    """Thread-safe prepared and filtered frames shared by callback closures."""

    def __init__(self, risk_data: pd.DataFrame, revision: int) -> None:
        self._lock = RLock()
        self._revision = int(revision)
        self._frame = risk_data
        self._filtered: dict[tuple[Any, ...], pd.DataFrame] = {}
        self._rendered: OrderedDict[str, Any] = OrderedDict()

    def current(self, manager: RefreshManagerProtocol | None) -> pd.DataFrame:
        manager_revision = manager.health.revision if manager is not None else None
        with self._lock:
            if manager_revision is None or self._revision == manager_revision:
                return self._frame
        # Read only the dashboard frame. Date, PL, market, checker, and
        # unmapped frames stay in the manager and are not deep-copied here.
        dashboard = manager.read_frame("dashboard_frame")
        return self.replace_frame(dashboard.frame, dashboard.revision)

    def replace(self, snapshot: RefreshSnapshotProtocol) -> pd.DataFrame:
        return self.replace_frame(snapshot.dashboard_frame, snapshot.revision)

    def replace_frame(self, frame: pd.DataFrame, revision: int) -> pd.DataFrame:
        """Publish one already-defensive dashboard-frame read to the UI cache."""
        prepared = prepare_risk_data(frame)
        with self._lock:
            if int(revision) <= self._revision:
                return self._frame
            self._frame = prepared
            self._revision = int(revision)
            self._filtered.clear()
            self._rendered.clear()
            return prepared

    def filtered(
        self,
        manager: RefreshManagerProtocol | None,
        active_risk_type: str | None,
        ir_family: str | None,
        splits: Sequence[str] | None,
        dimension_filters: Mapping[str, Sequence[str] | None],
        *,
        exclude_selected: bool = False,
    ) -> pd.DataFrame:
        """Return filtered data with promotion computed AFTER filtering.

        This ensures that only rows matching the current UI filters participate
        in threshold aggregation, so an underlying is promoted only when its
        aggregated risk/drisk/pl exceeds the threshold within the selected
        filter context.
        """
        while True:
            frame = self.current(manager)
            with self._lock:
                revision = self._revision
            key = (
                revision,
                active_risk_type,
                ir_family if active_risk_type == "IR" else None,
                tuple(splits or ()),
                tuple(
                    (column, tuple(dimension_filters.get(column) or ()))
                    for column in sorted(dimension_filters)
                ),
                bool(exclude_selected),
            )
            with self._lock:
                cached = self._filtered.get(key)
                if cached is not None:
                    return cached
            filtered = apply_filters(
                filter_ir_family(frame, active_risk_type, ir_family),
                [active_risk_type] if active_risk_type else [],
                list(splits or ()),
                dimension_filters,
                exclude_selected=exclude_selected,
            )
            # Recompute promotion on filtered data so that only selected rows
            # participate in threshold comparison.
            if not filtered.empty:
                filtered = recompute_filtered_promotion(filtered)
            with self._lock:
                if self._revision != revision:
                    continue
                cached = self._filtered.setdefault(key, filtered)
                if len(self._filtered) > 32:
                    self._filtered.pop(next(iter(self._filtered)))
                return cached

    def rendered(self, key: str, build: Callable[[], Any]) -> Any:
        """Return one exact immutable table tree from a bounded thread-safe LRU."""
        with self._lock:
            cached = self._rendered.get(key, _UNSET)
            if cached is not _UNSET:
                self._rendered.move_to_end(key)
                return cached
        component = build()
        with self._lock:
            existing = self._rendered.get(key, _UNSET)
            if existing is not _UNSET:
                self._rendered.move_to_end(key)
                return existing
            self._rendered[key] = component
            while len(self._rendered) > 24:
                self._rendered.popitem(last=False)
            return component


def _next_counter(value: Any) -> int:
    try:
        return int(value or 0) + 1
    except (TypeError, ValueError):
        return 1


def _refresh_status(
    snapshot: RefreshSnapshotProtocol,
    *,
    action_committed: bool = True,
) -> tuple[str, str, str]:
    status_frame = snapshot.risk_status
    t_minus_one = int(
        ((status_frame["Age"] == 0) & ~status_frame["Force Risk"].astype(bool)).sum()
    )
    forced = int(status_frame["Force Risk"].astype(bool).sum())
    action_labels = {
        "portfolio mapping": "Portfolios refreshed",
        "reload all risk": "Risk refreshed",
        "manual P&L": "P&L refreshed",
        "automatic 15-minute refresh": "AutoPL refreshed",
        "dashboard settings updated": "Settings applied",
        "apply forced risk dates": "Date settings applied",
    }
    reason = str(getattr(snapshot, "refresh_reason", "") or "")
    action_succeeded = (
        action_committed and not snapshot.errors and reason in action_labels
    )
    success_label = action_labels[reason] if action_succeeded else "Last success"
    status_at = snapshot.refreshed_at
    if action_succeeded:
        last_attempt_at = getattr(snapshot, "last_attempt_at", None)
        if last_attempt_at is not None:
            status_at = max(status_at, last_attempt_at)
    status_time = status_at.strftime("%H:%M:%S UTC")
    status = (
        f"{success_label} {status_time} · T-1 risk {t_minus_one} · Forced risk {forced}"
    )
    if snapshot.errors:
        return (
            status,
            "⚠ Refresh warning\n" + "\n".join(snapshot.errors),
            "error-log has-errors",
        )
    return status, "", "error-log"


def register_callbacks(
    app: Dash,
    refresh_manager: RefreshManagerProtocol | None,
    initial_snapshot: RefreshSnapshotProtocol | None,
    risk_data: pd.DataFrame,
    *,
    route_prefix: str = "/",
    startup_coordinator: StartupCoordinator | None = None,
) -> None:
    """Register the complete interactive behavior for the risk dashboard."""
    cache = _RiskDataCache(
        risk_data, initial_snapshot.revision if initial_snapshot is not None else 0
    )
    dimension_filter_ids = [
        DIMENSION_FILTER_IDS[field.key] for field in FILTER_DIMENSION_FIELDS
    ]
    dimension_filter_inputs = [
        Input(component_id, "value") for component_id in dimension_filter_ids
    ]

    def reporting_filter_map(
        values: Sequence[Sequence[str] | None],
    ) -> dict[str, list[str]]:
        """Bind callback values to the one authoritative portfolio schema."""
        return {
            field.key: list(selected or [])
            for field, selected in zip(FILTER_DIMENSION_FIELDS, values, strict=True)
        }

    def quick_risk_filter_map(
        splits: Sequence[str] | None,
        dimension_values: Sequence[Sequence[str] | None],
    ) -> dict[str, list[str]]:
        """Map the shared UI filters to SearchCatalog column names."""
        return {
            "Split": list(splits or []),
            **{
                field.external_name: list(selected or [])
                for field, selected in zip(
                    FILTER_DIMENSION_FIELDS,
                    dimension_values,
                    strict=True,
                )
            },
        }

    def materialize_initial_dashboard(
        snapshot: RefreshSnapshotProtocol,
    ) -> html.Div:
        """Build the full page or retain a safe, retryable startup shell."""
        try:
            prepared = cache.replace(snapshot)
            layout = build_layout(
                prepared,
                snapshot,
                refresh_enabled=True,
                stage_delays=refresh_manager.stage_delays,
                include_shared_refresh_shell=False,
            )
        except Exception as error:
            incident_id = uuid.uuid4().hex[:10]
            app.logger.error(
                "Cube startup UI preparation failed; incident=%s type=%s",
                incident_id,
                type(error).__name__,
                exc_info=True,
            )
            safe_error = (
                f"Dashboard preparation failed (incident {incident_id}). "
                "No dashboard was published; retry after checking the server log."
            )
            app.server.config[STARTUP_UI_ERROR_CONFIG_KEY] = safe_error
            return build_initial_load_layout(
                stage_delays=refresh_manager.stage_delays,
                error=safe_error,
                include_shared_refresh_shell=False,
            )
        app.server.config[STARTUP_UI_ERROR_CONFIG_KEY] = None
        return layout

    if refresh_manager is not None:
        coordinator = startup_coordinator or StartupCoordinator(
            refresh_manager,
            logger=app.logger,
        )

        def start_or_follow_initial_snapshot(
            triggered: Any,
            load_intervals: Any,
            retry_clicks: Any,
            pnl_intervals: Any = 0,
        ) -> StartupStatus:
            """Apply one idempotent startup signal and return its current state."""
            if triggered in {"initial-load-trigger", "pnl-initial-load-trigger"}:
                intervals = (
                    pnl_intervals
                    if triggered == "pnl-initial-load-trigger"
                    else load_intervals
                )
                if int(intervals or 0) <= 0:
                    raise PreventUpdate
                # n_intervals=1 is delivered only after the cold Risk page has
                # or the cold P&L page has painted. Static/Stock never own one.
                coordinator.start()
            elif triggered == "initial-load-retry":
                if int(retry_clicks or 0) <= 0:
                    raise PreventUpdate
                coordinator.start(retry=True)
            return coordinator.status()

        @app.callback(
            Output("cube-page-container", "children"),
            Input("initial-load-trigger", "n_intervals", allow_optional=True),
            Input("initial-load-retry", "n_clicks", allow_optional=True),
            State("initial-load-message", "children", allow_optional=True),
            prevent_initial_call=True,
        )
        def load_initial_snapshot_after_first_paint(
            load_intervals,
            retry_clicks,
            displayed_error="",
        ):
            """Hydrate only the cold Risk page; it may safely unmount mid-call."""
            startup = start_or_follow_initial_snapshot(
                ctx.triggered_id,
                load_intervals,
                retry_clicks,
            )
            if startup.phase == "succeeded" and refresh_manager.health.revision > 0:
                return materialize_initial_dashboard(refresh_manager.snapshot)
            if startup.phase == "failed":
                return build_initial_load_layout(
                    stage_delays=refresh_manager.stage_delays,
                    error=startup.error
                    or "Initial data load failed. Check the server log and retry.",
                    retry_enabled=startup.retryable,
                    include_shared_refresh_shell=False,
                )
            if startup.phase == "stalled":
                # Retain the page poll so a late connector return can still
                # publish. Never offer a second writer while this one is alive.
                if str(displayed_error or "") != str(startup.error or ""):
                    return build_initial_load_layout(
                        stage_delays=refresh_manager.stage_delays,
                        error=startup.error,
                        retry_enabled=False,
                        keep_polling=True,
                        include_shared_refresh_shell=False,
                    )
            if ctx.triggered_id == "initial-load-retry":
                return build_initial_load_layout(
                    stage_delays=refresh_manager.stage_delays,
                    include_shared_refresh_shell=False,
                )
            raise PreventUpdate

        @app.callback(
            Output("shared-refresh-shell", "children"),
            Input("initial-load-trigger", "n_intervals", allow_optional=True),
            Input("initial-load-retry", "n_clicks", allow_optional=True),
            Input("pnl-initial-load-trigger", "n_intervals", allow_optional=True),
            Input("shared-refresh-bootstrap-interval", "n_intervals"),
            State("refresh-status", "className", allow_optional=True),
            State("error-log", "children", allow_optional=True),
            State("refresh-commit-revision", "children", allow_optional=True),
            prevent_initial_call=True,
        )
        def hydrate_shared_refresh_shell(
            load_intervals,
            retry_clicks,
            pnl_intervals,
            _shared_intervals,
            status_class="",
            displayed_error="",
            displayed_revision=0,
        ):
            """Follow revision 1 independently of the mounted Dash page."""
            if (
                ctx.triggered_id == "shared-refresh-bootstrap-interval"
                and int(_shared_intervals or 0) <= 0
            ):
                raise PreventUpdate
            startup = start_or_follow_initial_snapshot(
                ctx.triggered_id,
                load_intervals,
                retry_clicks,
                pnl_intervals,
            )
            common_options = {
                "refresh_enabled": True,
                "stage_delays": refresh_manager.stage_delays,
            }
            if startup.phase == "succeeded" and refresh_manager.health.revision > 0:
                try:
                    shell_revision = int(displayed_revision or 0)
                except (TypeError, ValueError):
                    shell_revision = 0
                if (
                    shell_revision >= refresh_manager.health.revision
                    and "is-refreshing" not in str(status_class or "").split()
                ):
                    raise PreventUpdate
                return build_shared_refresh_shell(
                    refresh_manager.snapshot,
                    # The committed marker may advance on any page, but the
                    # live revision Store is released only after a consuming
                    # financial page (warm Risk or P&L) mounts.
                    data_revision=shell_revision,
                    **common_options,
                ).children
            if startup.phase == "failed":
                error_text = startup.error or (
                    "Initial data load failed. Check the server log and retry."
                )
                if (
                    str(displayed_error or "") == str(error_text)
                    and "is-error" in str(status_class or "").split()
                ):
                    raise PreventUpdate
                return build_shared_refresh_shell(
                    None,
                    initial_error=error_text,
                    **common_options,
                ).children
            if startup.phase == "stalled":
                if str(displayed_error or "") != str(startup.error or ""):
                    return build_shared_refresh_shell(
                        None,
                        initial_error=startup.error,
                        keep_polling=True,
                        **common_options,
                    ).children
                raise PreventUpdate
            if "is-refreshing" not in str(status_class or "").split():
                return build_shared_refresh_shell(
                    None,
                    initial_loading=True,
                    **common_options,
                ).children
            raise PreventUpdate

    @app.callback(
        Output("dimension-filter-store", "data"),
        Output("dimension-filter-values-store", "data"),
        *dimension_filter_inputs,
    )
    def sync_dimension_filters(*filter_values):
        """Sync filter dropdown values to both filter stores."""
        return (
            dict(
                zip(
                    (field.key for field in FILTER_DIMENSION_FIELDS),
                    filter_values,
                    strict=True,
                )
            ),
            list(filter_values),
        )

    @app.callback(
        Output("aggregate-open-risk-types", "data"),
        Output("aggregate-pl-grid", "children"),
        Input("aggregate-pl-dimension", "value"),
        Input("data-revision-store", "data"),
        Input({"type": "aggregate-row-toggle", "risk_type": ALL}, "n_clicks"),
        Input("split-filter", "value"),
        *dimension_filter_inputs,
        Input("risk-filter-exclude-selected", "value"),
        State("aggregate-open-risk-types", "data"),
    )
    def reduce_and_render_aggregate_pl(
        dimension,
        _data_revision,
        row_clicks,
        selected_splits,
        *values,
    ):
        """Apply shared filters, reduce a chevron, and render Aggregate P&L."""
        dimension_count = len(dimension_filter_ids)
        dimension_values = values[:dimension_count]
        exclude_value = (
            values[dimension_count] if len(values) > dimension_count else None
        )
        open_risk_types = (
            values[dimension_count + 1] if len(values) > dimension_count + 1 else None
        )

        updated_open_risk_types = no_update
        effective_open_risk_types = list(open_risk_types or [])
        triggered = ctx.triggered_id

        if isinstance(triggered, dict):
            if not row_clicks or max(row_clicks) == 0:
                raise PreventUpdate

            risk_type = triggered.get("risk_type")
            opened = set(effective_open_risk_types)

            if risk_type in opened:
                opened.remove(risk_type)
            else:
                opened.add(risk_type)

            effective_open_risk_types = sorted(
                opened,
                key=lambda value: (RISK_TYPE_ORDER.get(value, 99), value),
            )
            updated_open_risk_types = effective_open_risk_types

        aggregate_frame = cache.filtered(
            refresh_manager,
            None,  # Do not limit Aggregate P&L to the active Risk Explorer tab.
            None,  # Do not apply the active IR-family tab.
            selected_splits,
            reporting_filter_map(dimension_values),
            exclude_selected=risk_exclude_selected(exclude_value),
        )

        return (
            updated_open_risk_types,
            build_aggregate_pl_table(
                aggregate_frame,
                dimension,
                effective_open_risk_types,
            ),
        )

    @app.callback(
        Output("top-book-open-rows-store", "data"),
        Output("top-book-details", "open"),
        Output("top-book-grid", "children"),
        Input("top-book-summary", "n_clicks"),
        Input("data-revision-store", "data"),
        Input("top-book-row-action-store", "data"),
        Input("split-filter", "value"),
        *dimension_filter_inputs,
        Input("risk-filter-exclude-selected", "value"),
        State("top-book-open-rows-store", "data"),
    )
    def reduce_and_render_top_book(
        summary_clicks,
        data_revision,
        row_action,
        selected_splits,
        *values,
    ):
        """Keep Top Book lazy while expanding rows in one request."""
        dimension_count = len(dimension_filter_ids)
        dimension_values = values[:dimension_count]
        exclude_value = (
            values[dimension_count] if len(values) > dimension_count else None
        )
        exclude_selected = risk_exclude_selected(exclude_value)
        open_rows = (
            values[dimension_count + 1] if len(values) > dimension_count + 1 else None
        )
        reporting_filters = reporting_filter_map(dimension_values)
        filtered = cache.filtered(
            refresh_manager,
            None,
            None,
            selected_splits,
            reporting_filters,
            exclude_selected=exclude_selected,
        )
        view_token = _top_book_action_view_token(
            data_revision,
            splits=selected_splits,
            dimension_filters=reporting_filters,
            exclude_selected=exclude_selected,
        )
        is_open = bool(int(summary_clicks or 0) % 2)
        updated_open_rows = no_update
        effective_open_rows = list(open_rows or [])
        if ctx.triggered_id == "data-revision-store":
            effective_open_rows = default_top_book_open_rows(filtered)
            updated_open_rows = effective_open_rows
            if not is_open:
                return updated_open_rows, no_update, None
        if not is_open:
            if ctx.triggered_id == "top-book-summary":
                return no_update, False, None
            raise PreventUpdate
        if ctx.triggered_id == "top-book-row-action-store":
            if not _is_current_risk_action(
                row_action,
                kind="row",
                expected_view_token=view_token,
            ):
                raise PreventUpdate
            key = row_action.get("key")
            opened = row_action.get("open_rows")
            if (
                row_action.get("source") != "top-book-row-toggle"
                or not _valid_delegated_row_key(key, allow_total=False)
                or not isinstance(opened, list)
                or any(
                    not _valid_delegated_row_key(item, allow_total=False)
                    for item in opened
                )
            ):
                raise PreventUpdate
            effective_open_rows = sorted(set(opened))
            updated_open_rows = effective_open_rows
        return (
            updated_open_rows,
            True,
            build_top_book_exposures(
                filtered,
                effective_open_rows,
                view_token=view_token,
            ),
        )

    @app.callback(
        Output("risk-type-tabs", "children"),
        Output("risk-type-tabs", "value"),
        Input("data-revision-store", "data"),
        State("risk-type-tabs", "value"),
    )
    def update_risk_type_tabs(_revision, selected_risk_type):
        available = ordered_unique(cache.current(refresh_manager), "risk type")
        if not available:
            return [], None
        selected = (
            selected_risk_type if selected_risk_type in available else available[0]
        )
        return [dcc.Tab(label=value, value=value) for value in available], selected

    @app.callback(
        *[
            output
            for component_id in dimension_filter_ids
            for output in (
                Output(component_id, "options"),
                Output(component_id, "value"),
            )
        ],
        Input("data-revision-store", "data"),
        *[State(component_id, "value") for component_id in dimension_filter_ids],
    )
    def update_dimension_filters(_revision, *selected_values):
        frame = cache.current(refresh_manager)

        def options_and_valid(column, selected):
            available = ordered_unique(frame, column)
            valid = [value for value in (selected or []) if value in available]
            return [{"label": value, "value": value} for value in available], valid

        result = []
        for field, selected in zip(
            FILTER_DIMENSION_FIELDS,
            selected_values,
            strict=True,
        ):
            options, valid = options_and_valid(field.key, selected)
            result.extend((options, valid))
        return tuple(result)

    app.clientside_callback(
        """
        function (activeRiskType) {
            return activeRiskType === "IR" ? {} : {display: "none"};
        }
        """,
        Output("ir-family-tabs", "style"),
        Input("risk-type-tabs", "value"),
    )

    def risk_generation_state(
        *,
        splits,
        dimension_values,
        exclude_selected,
        credit_measure,
        credit_multi_metric,
        alt_metric,
        expanded_metrics,
        promotion_enabled,
        region_enabled,
        underlying_sort_metric,
    ) -> dict[str, Any]:
        """Return every value-affecting input embedded in delegated actions."""
        filters = reporting_filter_map(dimension_values)
        return {
            "alt_metric": alt_metric,
            "credit_measure": credit_measure,
            "credit_multi_metric": credit_multi_metric,
            "expanded_metrics": sorted(expanded_metrics or []),
            "filters": {key: sorted(values) for key, values in sorted(filters.items())},
            "exclude_selected": bool(exclude_selected),
            "splits": sorted(splits or []),
            "promotion_enabled": bool(promotion_enabled),
            "region_enabled": bool(region_enabled),
            "underlying_sort_metric": selected_underlying_sort_metric(
                underlying_sort_metric
            ),
        }

    def render_active_risk_table(
        *,
        active_risk_type,
        ir_family,
        data_revision,
        table_view,
        dimension,
        underlying_sort_metric,
        splits,
        expanded_metrics,
        credit_view,
        credit_measure,
        credit_multi_metric,
        alt_metric,
        open_rows,
        dimension_values,
        exclude_selected=False,
        promotion_enabled=True,
        region_enabled=True,
    ):
        """Build only the visible Risk Explorer table for the current state."""
        normalized_ir_family = ir_family if active_risk_type == "IR" else None
        selected_sort_metric = selected_underlying_sort_metric(underlying_sort_metric)
        risk_context = {
            "risk_type": active_risk_type,
            "ir_family": normalized_ir_family,
            "data_revision": data_revision,
        }
        reporting_filters = reporting_filter_map(dimension_values)
        generation_state = risk_generation_state(
            splits=splits,
            dimension_values=dimension_values,
            credit_measure=credit_measure,
            credit_multi_metric=credit_multi_metric,
            alt_metric=alt_metric,
            expanded_metrics=expanded_metrics,
            exclude_selected=exclude_selected,
            promotion_enabled=promotion_enabled,
            region_enabled=region_enabled,
            underlying_sort_metric=selected_sort_metric,
        )

        def filtered_frame() -> pd.DataFrame:
            return cache.filtered(
                refresh_manager,
                active_risk_type,
                normalized_ir_family,
                splits,
                reporting_filters,
                exclude_selected=exclude_selected,
            )

        if table_view == "alt":
            view_token = risk_action_view_token(
                risk_context,
                table_view,
                dimension,
                None,
                generation_state=generation_state,
            )
            render_key = json.dumps(
                [view_token, sorted(open_rows or [])],
                separators=(",", ":"),
            )

            def build_alt_component():
                filtered = filtered_frame()
                if active_risk_type == "Credit":
                    filtered = apply_credit_measure(filtered, credit_measure)
                return build_alt_risk_table(
                    filtered,
                    alt_metric,
                    open_rows,
                    dimension,
                    index_label=active_risk_type,
                    view_token=view_token,
                    promotion_enabled=promotion_enabled,
                    region_enabled=region_enabled,
                    underlying_sort_metric=selected_sort_metric,
                )

            return no_update, cache.rendered(render_key, build_alt_component)

        view_token = risk_action_view_token(
            risk_context,
            table_view,
            dimension,
            credit_view,
            generation_state=generation_state,
        )
        render_key = json.dumps(
            [view_token, sorted(open_rows or []), promotion_enabled, region_enabled],
            separators=(",", ":"),
        )

        def build_main_component():
            filtered = filtered_frame()
            if active_risk_type == "Credit" and credit_view == "multi":
                return build_credit_multi_table(
                    filtered,
                    credit_multi_metric,
                    open_rows,
                    dimension,
                    view_token,
                    promotion_enabled=promotion_enabled,
                    region_enabled=region_enabled,
                    underlying_sort_metric=selected_sort_metric,
                )
            if active_risk_type == "Credit":
                filtered = apply_credit_measure(filtered, credit_measure)
            return build_risk_table(
                filtered,
                expanded_metrics,
                open_rows,
                dimension=dimension,
                toggle_type="main-row-toggle",
                cell_type="main-risk-cell",
                index_label=active_risk_type,
                view_token=view_token,
                promotion_enabled=promotion_enabled,
                region_enabled=region_enabled,
                underlying_sort_metric=selected_sort_metric,
            )

        return cache.rendered(render_key, build_main_component), no_update

    def render_active_detail(
        *,
        active_risk_type,
        ir_family,
        splits,
        table_view,
        credit_view,
        credit_measure,
        selection,
        plot_measure,
        plot_component,
        tenor_view,
        dimension_values,
        exclude_selected=False,
    ):
        """Build the current detail, short-circuiting the empty state."""
        if not selection:
            return build_detail_panel_with_state(
                pd.DataFrame(),
                None,
                compose_detail_metric(plot_measure, plot_component),
                tenor_view,
            )
        selected_context = parse_row_key(selection.get("key"))
        detail_risk_type = selected_context.get("risk type", active_risk_type)
        filtered = cache.filtered(
            refresh_manager,
            detail_risk_type,
            ir_family,
            splits,
            reporting_filter_map(dimension_values),
            exclude_selected=exclude_selected,
        )
        selected_credit_measure = selection.get("credit_measure")
        if detail_risk_type == "Credit" and selected_credit_measure:
            filtered = apply_credit_measure(filtered, selected_credit_measure)
        elif detail_risk_type == "Credit" and (
            table_view == "alt" or credit_view == "single"
        ):
            filtered = apply_credit_measure(filtered, credit_measure)
        return build_detail_panel_with_state(
            filtered,
            selection,
            compose_detail_metric(plot_measure, plot_component),
            tenor_view,
        )

    @app.callback(
        Output("split-filter", "options"),
        Output("split-filter", "value"),
        Output("open-rows-store", "data"),
        Output("selected-cell-store", "data"),
        Output("plot-measure", "value"),
        Output("detail-component-request-store", "data"),
        Output("risk-view-context-store", "data"),
        Output("plot-component", "options"),
        Output("plot-component", "value"),
        Output("detail-tenor-view", "options"),
        Output("detail-tenor-view", "value"),
        Output("expanded-metrics", "value"),
        Output("risk-grid", "children"),
        Output("alt-risk-grid", "children"),
        Output("detail-panel", "children"),
        Input("risk-type-tabs", "value"),
        Input("ir-family-tabs", "value"),
        Input("data-revision-store", "data"),
        Input("table-dimension", "value"),
        Input("table-view-tabs", "value"),
        Input("credit-view-tabs", "value"),
        Input("risk-row-action-store", "data"),
        Input("risk-cell-action-store", "data"),
        Input("top-book-cell-action-store", "data"),
        Input("risk-metric-action-store", "data"),
        Input("split-filter", "value"),
        Input("credit-measure", "value"),
        Input("credit-multi-metric", "value"),
        Input("alt-metric", "value"),
        Input("plot-measure", "value"),
        Input("plot-component", "value"),
        Input("detail-tenor-view", "value"),
        Input("dimension-filter-values-store", "data"),
        Input("risk-filter-exclude-selected", "value"),
        Input("promotion-toggle-store", "data"),
        Input("region-toggle-store", "data"),
        Input("underlying-sort-metric", "value"),
        State("open-rows-store", "data"),
        State("expanded-metrics", "value"),
        State("risk-view-context-store", "data"),
        State("selected-cell-store", "data"),
    )
    def reduce_and_render_risk_view(
        active_risk_type,
        ir_family,
        data_revision,
        dimension,
        table_view,
        credit_view,
        row_action,
        cell_action,
        top_book_action,
        metric_action,
        selected_splits,
        credit_measure,
        credit_multi_metric,
        alt_metric,
        plot_measure,
        plot_component,
        tenor_view,
        dimension_values,
        exclude_value,
        promotion_enabled,
        region_enabled,
        underlying_sort_metric,
        current_open_rows,
        current_expanded_metrics,
        previous_context,
        current_selection,
    ):
        """Atomically reduce an interaction and return its visible table.

        Dash Cloud latency used to be paid twice: once to update a Store and
        again to render the table from that Store.  This callback is the sole
        owner of Risk Explorer state *and* its two table containers, so tab,
        row, metric and filter interactions each require one server request.
        """
        dimension_values = dimension_values or []
        exclude_selected = risk_exclude_selected(exclude_value)
        normalized_ir_family = ir_family if active_risk_type == "IR" else None
        context = {
            "risk_type": active_risk_type,
            "ir_family": normalized_ir_family,
            "data_revision": data_revision,
        }
        triggered = set(ctx.triggered_prop_ids)
        context_inputs = {
            "risk-type-tabs.value",
            "ir-family-tabs.value",
            "data-revision-store.data",
        }
        view_inputs = {
            "table-dimension.value",
            "table-view-tabs.value",
            "credit-view-tabs.value",
        }
        table_inputs = {
            "split-filter.value",
            "credit-measure.value",
            "credit-multi-metric.value",
            "alt-metric.value",
            "dimension-filter-values-store.data",
            "risk-filter-exclude-selected.value",
            "promotion-toggle-store.data",
            "region-toggle-store.data",
            "underlying-sort-metric.value",
        }
        detail_inputs = {
            "plot-measure.value",
            "plot-component.value",
            "detail-tenor-view.value",
        }

        updates = [no_update] * 12
        effective_splits = list(selected_splits or [])
        effective_open_rows = list(current_open_rows or [])
        effective_expanded_metrics = list(current_expanded_metrics or [])
        effective_selection = current_selection
        effective_plot_measure = (
            plot_measure if plot_measure in DETAIL_COMPONENTS else "risk"
        )
        allowed_components = DETAIL_COMPONENTS[effective_plot_measure]
        default_component = "move" if effective_plot_measure == "move" else "total"
        effective_plot_component = (
            plot_component
            if plot_component in allowed_components
            else default_component
        )
        effective_tenor_view = (
            tenor_view
            if tenor_view in {"auto", "tenor", "swap", "option", "surface"}
            else "auto"
        )
        should_render_table = False
        should_render_detail = False

        if triggered & context_inputs or not isinstance(previous_context, Mapping):
            frame = cache.current(refresh_manager)
            frame = frame.loc[frame["risk type"].eq(active_risk_type)]
            frame = filter_ir_family(frame, active_risk_type, normalized_ir_family)
            available = ordered_unique(frame, "split")
            effective_splits = [
                split for split in effective_splits if split in available
            ]
            context_changed = (
                not isinstance(previous_context, Mapping)
                or previous_context.get("risk_type") != active_risk_type
                or previous_context.get("ir_family") != normalized_ir_family
            )
            updates[0] = [{"label": split, "value": split} for split in available]
            updates[1] = effective_splits
            if context_changed:
                effective_open_rows = default_open_rows(frame, active_risk_type)
                effective_selection = None
                effective_plot_measure = "risk"
                effective_plot_component = "total"
                effective_tenor_view = "auto"
                updates[2] = effective_open_rows
                updates[3] = None
                updates[4] = "risk"
                updates[5] = {"measure": "risk", "component": "total"}
                updates[7] = [
                    {
                        "label": DETAIL_COMPONENT_LABELS[value],
                        "value": value,
                    }
                    for value in DETAIL_COMPONENTS["risk"]
                ]
                updates[8] = "total"
            updates[6] = context
            should_render_table = True
            should_render_detail = True

        elif triggered & view_inputs:
            effective_selection = None
            effective_plot_measure = "risk"
            effective_plot_component = "total"
            effective_tenor_view = "auto"
            updates[3] = None
            updates[4] = "risk"
            updates[5] = {"measure": "risk", "component": "total"}
            updates[7] = [
                {"label": DETAIL_COMPONENT_LABELS[value], "value": value}
                for value in DETAIL_COMPONENTS["risk"]
            ]
            updates[8] = "total"
            should_render_table = True
            should_render_detail = True

        else:
            expected_token = risk_action_view_token(
                context,
                table_view,
                dimension,
                credit_view,
                generation_state=risk_generation_state(
                    splits=effective_splits,
                    dimension_values=dimension_values,
                    exclude_selected=exclude_selected,
                    credit_measure=credit_measure,
                    credit_multi_metric=credit_multi_metric,
                    alt_metric=alt_metric,
                    expanded_metrics=effective_expanded_metrics,
                    promotion_enabled=bool(promotion_enabled),
                    region_enabled=bool(region_enabled),
                    underlying_sort_metric=selected_underlying_sort_metric(
                        underlying_sort_metric
                    ),
                ),
            )
            if ctx.triggered_id == "risk-row-action-store":
                if not _is_current_risk_action(
                    row_action,
                    kind="row",
                    expected_view_token=expected_token,
                ):
                    raise PreventUpdate
                expected_source = (
                    "alt-row-toggle" if table_view == "alt" else "main-row-toggle"
                )
                key = row_action.get("key")
                opened = row_action.get("open_rows")
                if (
                    row_action.get("source") != expected_source
                    or not _valid_delegated_row_key(key, allow_total=False)
                    or not isinstance(opened, list)
                    or any(
                        not _valid_delegated_row_key(item, allow_total=False)
                        for item in opened
                    )
                ):
                    raise PreventUpdate
                effective_open_rows = sorted(set(opened))
                updates[2] = effective_open_rows
                should_render_table = True

            elif ctx.triggered_id == "risk-metric-action-store":
                if not _is_current_risk_action(
                    metric_action,
                    kind="metric",
                    expected_view_token=expected_token,
                ):
                    raise PreventUpdate
                metric = metric_action.get("metric")
                if table_view != "main" or metric not in EXPANDABLE_METRICS:
                    raise PreventUpdate
                expanded = set(effective_expanded_metrics)
                if metric in expanded:
                    expanded.remove(metric)
                else:
                    expanded.add(metric)
                effective_expanded_metrics = [
                    name for name in EXPANDABLE_METRICS if name in expanded
                ]
                updates[11] = effective_expanded_metrics
                should_render_table = True

            elif ctx.triggered_id in {
                "risk-cell-action-store",
                "top-book-cell-action-store",
            }:
                action = (
                    top_book_action
                    if ctx.triggered_id == "top-book-cell-action-store"
                    else cell_action
                )
                source = action.get("source") if isinstance(action, Mapping) else None
                is_top_book = source == "top-book-risk-cell"
                if is_top_book:
                    if not _is_current_risk_action(
                        action,
                        kind="cell",
                        expected_view_token=_top_book_action_view_token(
                            data_revision,
                            splits=effective_splits,
                            dimension_filters=reporting_filter_map(dimension_values),
                            exclude_selected=exclude_selected,
                        ),
                    ):
                        raise PreventUpdate
                    allowed_metrics = METRIC_COLUMNS
                else:
                    if not _is_current_risk_action(
                        action,
                        kind="cell",
                        expected_view_token=expected_token,
                    ):
                        raise PreventUpdate
                    if table_view == "alt":
                        expected_source = "alt-risk-cell"
                    elif active_risk_type == "Credit" and credit_view == "multi":
                        expected_source = "credit-risk-cell"
                    else:
                        expected_source = "main-risk-cell"
                    if source != expected_source:
                        raise PreventUpdate
                    allowed_metrics = (
                        METRIC_COLUMNS
                        if source in {"alt-risk-cell", "credit-risk-cell"}
                        else PLOT_METRICS
                    )

                metric = action.get("metric")
                key = action.get("key")
                if metric not in allowed_metrics or not _valid_delegated_row_key(
                    key,
                    allow_total=True,
                ):
                    if not (is_top_book and key == "{}" and metric in allowed_metrics):
                        raise PreventUpdate
                selection = {"key": key, "metric": metric, "source": source}
                if source == "credit-risk-cell":
                    selected_credit_measure = action.get("measure")
                    if selected_credit_measure not in CREDIT_MEASURES:
                        raise PreventUpdate
                    selection["credit_measure"] = selected_credit_measure
                elif action.get("measure") is not None:
                    raise PreventUpdate
                measure, component = split_detail_metric(metric)
                effective_selection = selection
                effective_plot_measure = measure
                effective_plot_component = component
                effective_tenor_view = "auto"
                updates[3] = selection
                updates[4] = measure
                updates[5] = {"measure": measure, "component": component}
                updates[7] = [
                    {
                        "label": DETAIL_COMPONENT_LABELS[value],
                        "value": value,
                    }
                    for value in DETAIL_COMPONENTS[measure]
                ]
                updates[8] = component
                should_render_detail = True

            elif "plot-measure.value" in triggered:
                effective_plot_measure = (
                    plot_measure if plot_measure in DETAIL_COMPONENTS else "risk"
                )
                effective_plot_component = (
                    "move" if effective_plot_measure == "move" else "total"
                )
                updates[7] = [
                    {
                        "label": DETAIL_COMPONENT_LABELS[value],
                        "value": value,
                    }
                    for value in DETAIL_COMPONENTS[effective_plot_measure]
                ]
                updates[8] = effective_plot_component
                should_render_detail = True

            elif "plot-component.value" in triggered:
                should_render_detail = True

            elif triggered & table_inputs:
                should_render_table = True
                should_render_detail = bool(effective_selection)
            elif triggered & detail_inputs:
                should_render_detail = True
            else:
                raise PreventUpdate

        main_grid = no_update
        alt_grid = no_update
        if should_render_table:
            main_grid, alt_grid = render_active_risk_table(
                active_risk_type=active_risk_type,
                ir_family=normalized_ir_family,
                data_revision=data_revision,
                table_view=table_view,
                dimension=dimension,
                underlying_sort_metric=underlying_sort_metric,
                splits=effective_splits,
                expanded_metrics=effective_expanded_metrics,
                credit_view=credit_view,
                credit_measure=credit_measure,
                credit_multi_metric=credit_multi_metric,
                alt_metric=alt_metric,
                open_rows=effective_open_rows,
                dimension_values=dimension_values,
                exclude_selected=exclude_selected,
                promotion_enabled=bool(promotion_enabled),
                region_enabled=bool(region_enabled),
            )
        detail_panel = no_update
        if should_render_detail:
            (
                detail_panel,
                detail_tenor_options,
                effective_tenor_view,
            ) = render_active_detail(
                active_risk_type=active_risk_type,
                ir_family=normalized_ir_family,
                splits=effective_splits,
                table_view=table_view,
                credit_view=credit_view,
                credit_measure=credit_measure,
                selection=effective_selection,
                plot_measure=effective_plot_measure,
                plot_component=effective_plot_component,
                tenor_view=effective_tenor_view,
                dimension_values=dimension_values,
                exclude_selected=exclude_selected,
            )
            updates[9] = detail_tenor_options
            updates[10] = effective_tenor_view
        return (
            *updates,
            main_grid,
            alt_grid,
            detail_panel,
        )

    @app.callback(
        Output("unmapped-books-details", "open"),
        Output("unmapped-books-grid", "children"),
        Input("unmapped-books-summary", "n_clicks"),
        Input("data-revision-store", "data"),
        Input(DIMENSION_FILTER_IDS["portfolio"], "value"),
        Input("risk-filter-exclude-selected", "value"),
        State("unmapped-books-details", "open"),
        prevent_initial_call=True,
    )
    def render_unmapped_books(
        _summary_clicks,
        _revision,
        selected_portfolios,
        exclude_value,
        is_open,
    ):
        """Load the complete unmapped-book inventory only while it is open."""
        open_update = (
            not bool(is_open)
            if ctx.triggered_id == "unmapped-books-summary"
            else bool(is_open)
        )
        if not open_update:
            return False, None
        frame = (
            refresh_manager.read_frame("unmapped_frame").frame
            if refresh_manager is not None
            else pd.DataFrame()
        )
        frame = filter_unmapped_portfolios(
            frame,
            selected_portfolios,
            exclude_selected=risk_exclude_selected(exclude_value),
        )
        return True, build_unmapped_books_table(frame)

    if refresh_manager is not None:

        @app.callback(
            Output("quick-search-combine-udl", "options"),
            Output("quick-search-combine-udl", "value"),
            Input("data-revision-store", "data"),
            Input("quick-search-identity-mode", "value"),
            Input("quick-search-combine-udl", "search_value"),
            State("quick-search-combine-udl", "value"),
            prevent_initial_call=False,
        )
        def load_combine_udl_options(
            _revision, identity_mode, search_value, current_value
        ):
            selected_mode = str(identity_mode or "reported").strip().casefold()

            try:
                options = _combine_udl_dropdown_options(
                    refresh_manager.search_combine_udl_options(
                        search_value,
                        identity_mode=selected_mode,
                        limit=100,
                        include=(str(current_value) if current_value else None),
                    )
                )
            except (
                AttributeError,
                LookupError,
                TypeError,
                ValueError,
                RuntimeError,
            ):
                return no_update, no_update

            # A mode change starts a fresh exact search rather than silently using
            # a selection created under the other identity authority.
            if ctx.triggered_id == "quick-search-identity-mode":
                return options, None

            values = {option["value"] for option in options}
            selected = str(current_value or "").strip()
            if selected in values:
                return options, no_update
            return options, (options[0]["value"] if options else None)

        @app.callback(
            Output("quick-search-results", "children"),
            Output("quick-search-dimensions", "value"),
            Input("quick-search-combine-udl", "value"),
            Input("quick-search-identity-mode", "value"),
            Input("quick-search-dimensions", "value"),
            Input("quick-search-summary", "n_clicks"),
            Input("data-revision-store", "data"),
            Input("split-filter", "value"),
            Input("risk-filter-exclude-selected", "value"),
            *dimension_filter_inputs,
            prevent_initial_call=True,
        )
        def render_current_pivot(
            combine_udl,
            identity_mode,
            index_columns,
            summary_clicks,
            _revision,
            selected_splits,
            exclude_value,
            *dimension_values,
        ):
            rendered, index_update = _render_quick_search_pivot(
                refresh_manager,
                combine_udl=combine_udl,
                identity_mode=identity_mode,
                index_columns=index_columns,
                is_open=bool(int(summary_clicks or 0) % 2),
                risk_filters=quick_risk_filter_map(
                    selected_splits,
                    dimension_values,
                ),
                exclude_selected=risk_exclude_selected(exclude_value),
            )
            return rendered, index_update

        @app.callback(
            Output("quick-market-combine-udl", "options"),
            Output("quick-market-combine-udl", "value"),
            Input("data-revision-store", "data"),
            Input("quick-market-summary", "n_clicks"),
            Input("quick-market-combine-udl", "search_value"),
            State("quick-market-combine-udl", "value"),
            prevent_initial_call=False,
        )
        def load_market_udl_options(
            _revision, summary_clicks, search_value, current_value
        ):
            if not int(summary_clicks or 0) % 2:
                return [], no_update
            try:
                options = _combine_udl_dropdown_options(
                    refresh_manager.search_market_udl_options(
                        search_value,
                        limit=100,
                        include=(str(current_value) if current_value else None),
                    )
                )
            except (AttributeError, LookupError, TypeError, ValueError, RuntimeError):
                return no_update, no_update

            values = {option["value"] for option in options}
            selected = str(current_value or "").strip()
            if selected in values:
                return options, no_update
            return options, (options[0]["value"] if options else None)

        @app.callback(
            Output("quick-market-surface-metric-control", "hidden"),
            Input("quick-market-view", "value"),
        )
        def show_market_surface_metric(requested_view):
            return str(requested_view or "auto") != "surface"

        @app.callback(
            Output("quick-market-results", "children"),
            Output("quick-market-view", "value"),
            Output("quick-market-view", "options"),
            Output("quick-market-surface-metric", "options"),
            Input("quick-market-combine-udl", "value"),
            Input("quick-market-view", "value"),
            Input("quick-market-surface-metric", "value"),
            Input("quick-market-summary", "n_clicks"),
            Input("data-revision-store", "data"),
            prevent_initial_call=True,
        )
        def render_market_search(
            combine_udl,
            requested_view,
            surface_metric,
            summary_clicks,
            _revision,
        ):
            if not int(summary_clicks or 0) % 2:
                return None, no_update, no_update, no_update
            selected = str(combine_udl or "").strip()
            if not selected:
                return (
                    html.Div(
                        "Select a Market identity to build its full tenor view.",
                        className="quick-search-hint",
                    ),
                    no_update,
                    no_update,
                    no_update,
                )
            try:
                result = refresh_manager.pivot_market_exact(
                    selected,
                    index_columns=QUICK_MARKET_DEFAULT_INDEX,
                )
                if result.frame.empty:
                    selected_status = "Current"
                else:
                    statuses = result.frame["Market Status"].dropna().unique()
                    if len(statuses) != 1:
                        raise ValueError(
                            "exact MarketBook result has an ambiguous Market Status"
                        )
                    selected_status = str(statuses[0])
                rendered, resolved, options, surface_options = (
                    build_quick_market_result(
                        result.frame,
                        combine_udl=selected,
                        requested_view=str(requested_view or "auto"),
                        surface_metric=str(surface_metric or "current"),
                        market_status=selected_status,
                        revision=int(result.revision),
                    )
                )
                return rendered, resolved, options, surface_options
            except (
                AttributeError,
                KeyError,
                LookupError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as error:
                app.logger.exception("Quick Market Search render failed")
                detail = (
                    " ".join(str(error).splitlines()).strip() or type(error).__name__
                )
                return (
                    html.Div(
                        f"Quick Market Search failed: {type(error).__name__}: {detail[:400]}",
                        className="quick-search-error",
                        role="alert",
                    ),
                    no_update,
                    no_update,
                    no_update,
                )

        @app.callback(
            Output(AUTO_REFRESH_STORE_ID, "data"),
            Input("auto-refresh-toggle", "n_clicks"),
            State(AUTO_REFRESH_STORE_ID, "data"),
            prevent_initial_call=True,
        )
        def toggle_auto_refresh(n_clicks, stored_value):
            if not n_clicks:
                raise PreventUpdate
            return not auto_refresh_enabled(stored_value)

        @app.callback(
            Output("auto-refresh-interval", "disabled"),
            Output("auto-refresh-toggle", "children"),
            Output("auto-refresh-toggle", "title"),
            Output("auto-refresh-toggle", "aria-label"),
            Output("auto-refresh-toggle", "aria-pressed"),
            Output("auto-refresh-toggle", "className"),
            Input(AUTO_REFRESH_STORE_ID, "data"),
        )
        def sync_auto_refresh(stored_value):
            enabled = auto_refresh_enabled(stored_value)
            state = "On" if enabled else "Off"
            action = "Off" if enabled else "On"
            title = (
                f"Automatic 15-minute P&L refresh is {state}. "
                f"Activate to turn it {action}."
            )
            return (
                not enabled,
                f"AutoPL: {state}",
                title,
                f"AutoPL is {state}",
                str(enabled).lower(),
                f"data-source-toggle auto-refresh-toggle {'is-on' if enabled else 'is-off'}",
            )

        @app.callback(
            Output("commo-market-toggle", "children"),
            Output("commo-market-toggle", "title"),
            Output("commo-market-toggle", "aria-pressed"),
            Output("commo-market-toggle", "className"),
            Input(COMMODITY_MARKET_STORE_ID, "data"),
        )
        def sync_commodity_market(stored_value):
            enabled = commodity_market_enabled(stored_value)
            state = "On" if enabled else "Off"
            return (
                f"Commo: {state}",
                f"Commodity market data is {state}.",
                str(enabled).lower(),
                f"data-source-toggle {'is-on' if enabled else 'is-off'}",
            )

        # Promotion toggle callbacks
        @app.callback(
            Output("promotion-toggle-store", "data"),
            Input("promotion-toggle", "n_clicks"),
            State("promotion-toggle-store", "data"),
            prevent_initial_call=True,
        )
        def toggle_promotion(n_clicks, stored_value):
            if not n_clicks:
                raise PreventUpdate
            return not bool(stored_value)

        @app.callback(
            Output("promotion-toggle", "disabled"),
            Output("promotion-toggle", "children"),
            Output("promotion-toggle", "title"),
            Output("promotion-toggle", "aria-pressed"),
            Output("promotion-toggle", "className"),
            Input("promotion-toggle-store", "data"),
        )
        def sync_promotion(stored_value):
            enabled = bool(stored_value)
            state = "On" if enabled else "Off"
            action = "Off" if enabled else "On"
            return (
                False,
                f"Promotion: {state}",
                f"Underlying promotion is {state}. Click to turn it {action} (show group immediately).",
                str(enabled).lower(),
                f"data-source-toggle is-on promotion-toggle {'is-on' if enabled else 'is-off'}",
            )

        # Region toggle callbacks
        @app.callback(
            Output("region-toggle-store", "data"),
            Input("region-toggle", "n_clicks"),
            State("region-toggle-store", "data"),
            prevent_initial_call=True,
        )
        def toggle_region(n_clicks, stored_value):
            if not n_clicks:
                raise PreventUpdate
            return not bool(stored_value)

        @app.callback(
            Output("region-toggle", "disabled"),
            Output("region-toggle", "children"),
            Output("region-toggle", "title"),
            Output("region-toggle", "aria-pressed"),
            Output("region-toggle", "className"),
            Input("region-toggle-store", "data"),
        )
        def sync_region(stored_value):
            enabled = bool(stored_value)
            state = "On" if enabled else "Off"
            action = "Off" if enabled else "On"
            return (
                False,
                f"Region: {state}",
                f"Region is {state}. Click to {action}.",
                str(enabled).lower(),
                f"data-source-toggle is-on region-toggle {'is-on' if enabled else 'is-off'}",
            )

        @app.callback(
            Output("risk-checker-toggle", "children"),
            Output("risk-checker-toggle", "title"),
            Output("risk-checker-toggle", "aria-pressed"),
            Output("risk-checker-toggle", "className"),
            Input(RISK_CHECKER_STORE_ID, "data"),
        )
        def sync_risk_checker(stored_value):
            enabled = risk_checker_enabled(stored_value)
            state = "On" if enabled else "Off"
            return (
                f"RiskChecker: {state}",
                f"Risk checker is {state}",
                str(enabled).lower(),
                f"data-source-toggle {'is-on' if enabled else 'is-off'}",
            )

        @app.callback(
            # Keep the long financial request outside the live-data callback
            # graph. Browser progress publishes the committed revision only
            # after the manager's atomic transaction finishes, so readers can
            # continue interacting with the previous immutable snapshot.
            Output("refresh-commit-revision", "children"),
            Output(REFRESH_RESULT_STORE_ID, "data"),
            Output("refresh-status", "children"),
            Output("error-log", "children"),
            Output("error-log", "className"),
            Output(FORCE_STORE_ID, "data"),
            Output(VIEW_DATE_STORE_ID, "data"),
            Input("auto-refresh-interval", "n_intervals"),
            Input("refresh-portfolios-button", "n_clicks"),
            Input("refresh-pl-button", "n_clicks"),
            Input("reload-risk-button", "n_clicks"),
            Input("force-risk-apply-button", "n_clicks", allow_optional=True),
            Input("commo-market-toggle", "n_clicks"),
            Input("risk-checker-toggle", "n_clicks"),
            State(FORCE_DRAFT_STORE_ID, "data"),
            State(AUTO_REFRESH_STORE_ID, "data"),
            State(REFRESH_RESULT_STORE_ID, "data"),
            running=[
                (Output("refresh-portfolios-button", "disabled"), True, False),
                (Output("refresh-pl-button", "disabled"), True, False),
                (Output("reload-risk-button", "disabled"), True, False),
                (Output("auto-refresh-toggle", "disabled"), True, False),
                (Output("commo-market-toggle", "disabled"), True, False),
                (Output("risk-checker-toggle", "disabled"), True, False),
                (Output("refresh-busy-store", "data"), True, False),
                (
                    Output("refresh-status", "className"),
                    "refresh-status is-refreshing",
                    "refresh-status",
                ),
            ],
            prevent_initial_call=True,
        )
        def refresh_pipeline(
            _auto_intervals,
            _portfolio_clicks,
            _pl_clicks,
            _risk_clicks,
            _apply_clicks,
            _commodity_clicks,
            _checker_clicks,
            draft_state,
            auto_refresh_state,
            refresh_result_counter,
        ):
            triggered_ids = {
                value
                for value in ctx.triggered_prop_ids.values()
                if isinstance(value, str)
            }
            triggered = ctx.triggered_id
            if isinstance(triggered, str):
                triggered_ids.add(triggered)

            current_snapshot = refresh_manager.control_snapshot
            current_applied = snapshot_forced_dates(current_snapshot)
            current_view_date = snapshot_forced_view_date(current_snapshot)
            current_revision = current_snapshot.revision
            committed_commodity = bool(current_snapshot.commodity_market_enabled)
            committed_checker = bool(current_snapshot.risk_checker_enabled)
            commodity_enabled = (
                not committed_commodity
                if "commo-market-toggle" in triggered_ids
                else committed_commodity
            )
            checker_enabled = (
                not committed_checker
                if "risk-checker-toggle" in triggered_ids
                else committed_checker
            )
            applying = "force-risk-apply-button" in triggered_ids
            apply_result: ForceApplyResult | None = None

            try:
                if applying:
                    requested = draft_forced_dates(
                        draft_state, fallback=current_applied
                    )
                    base = draft_base_dates(draft_state, fallback=current_applied)
                    requested_view = draft_view_date(
                        draft_state, fallback=current_view_date
                    )
                    base_view = draft_base_view_date(
                        draft_state, fallback=current_view_date
                    )
                    if (base != current_applied or base_view != current_view_date) and (
                        requested != current_applied
                        or requested_view != current_view_date
                    ):
                        return (
                            no_update,
                            no_update,
                            no_update,
                            "⚠ Applied force dates changed while you were editing. Cancel to reload them before applying.",
                            "error-log has-errors",
                            no_update,
                            no_update,
                        )
                    if (
                        requested == current_applied
                        and requested_view == current_view_date
                    ):
                        raise PreventUpdate
                    apply_result = apply_force_dates(
                        refresh_manager,
                        requested,
                        view_date=requested_view,
                        commodity_market=commodity_enabled,
                        risk_checker=checker_enabled,
                        expected_revision=int(
                            draft_state.get("base_revision", current_revision)
                            if isinstance(draft_state, Mapping)
                            else current_revision
                        ),
                    )
                    snapshot = apply_result.snapshot
                elif "refresh-portfolios-button" in triggered_ids:
                    snapshot = refresh_manager.refresh_portfolios(
                        reason="portfolio mapping",
                        expected_revision=current_revision,
                    )
                elif "reload-risk-button" in triggered_ids:
                    snapshot = refresh_manager.refresh(
                        force_risk=True,
                        forced_dates=current_applied,
                        view_date=current_view_date,
                        commodity_market_enabled=commodity_enabled,
                        risk_checker_enabled=checker_enabled,
                        reason="reload all risk",
                        expected_revision=current_revision,
                    )
                elif "refresh-pl-button" in triggered_ids:
                    snapshot = refresh_manager.refresh(
                        force_pl=True,
                        forced_dates=current_applied,
                        view_date=current_view_date,
                        commodity_market_enabled=commodity_enabled,
                        risk_checker_enabled=checker_enabled,
                        reason="manual P&L",
                        expected_revision=current_revision,
                    )
                elif "auto-refresh-interval" in triggered_ids:
                    if int(_auto_intervals or 0) <= 0 or not auto_refresh_enabled(
                        auto_refresh_state
                    ):
                        raise PreventUpdate
                    snapshot = refresh_manager.refresh(
                        force_pl=True,
                        forced_dates=current_applied,
                        view_date=current_view_date,
                        commodity_market_enabled=commodity_enabled,
                        risk_checker_enabled=checker_enabled,
                        reason="automatic 15-minute refresh",
                        expected_revision=current_revision,
                    )
                elif (
                    "commo-market-toggle" in triggered_ids
                    or "risk-checker-toggle" in triggered_ids
                ):
                    apply_result = apply_force_dates(
                        refresh_manager,
                        current_applied,
                        reason="dashboard settings updated",
                        view_date=current_view_date,
                        commodity_market=commodity_enabled,
                        risk_checker=checker_enabled,
                        expected_revision=current_revision,
                    )
                    snapshot = apply_result.snapshot
                else:
                    raise PreventUpdate
            except PreventUpdate:
                raise
            except RefreshInProgressError:
                return (
                    no_update,
                    no_update,
                    "A refresh is already running; following its live progress.",
                    "",
                    "error-log",
                    no_update,
                    no_update,
                )
            except StaleRefreshError:
                return (
                    no_update,
                    no_update,
                    "The data changed before this action could start.",
                    "⚠ The committed revision changed. Reload the staged controls and try again.",
                    "error-log has-errors",
                    no_update,
                    no_update,
                )
            except (TypeError, ValueError):
                return (
                    no_update,
                    no_update,
                    no_update,
                    "⚠ Saved or staged force dates are invalid and were not applied.",
                    "error-log has-errors",
                    no_update,
                    no_update,
                )
            except Exception as error:
                incident_id = uuid.uuid4().hex[:10]
                app.logger.exception(
                    "Unexpected refresh callback failure; incident=%s type=%s",
                    incident_id,
                    type(error).__name__,
                )
                return (
                    no_update,
                    no_update,
                    "The refresh action failed; the last successful data remains visible.",
                    f"⚠ Unexpected refresh failure (incident {incident_id}). Check the server log.",
                    "error-log has-errors",
                    no_update,
                    no_update,
                )

            cache.replace(snapshot)
            status_text, error_text, error_class = _refresh_status(
                snapshot,
                action_committed=(apply_result is None or bool(apply_result.committed)),
            )
            if (
                apply_result is not None
                and not apply_result.committed
                and not snapshot.errors
            ):
                error_text = (
                    "⚠ Date settings were not committed; the last successful "
                    "settings remain applied."
                    if applying
                    else "⚠ Dashboard settings were not committed; the last "
                    "successful settings remain applied."
                )
                error_class = "error-log has-errors"

            persisted = (
                persisted_force_dates(apply_result)
                if applying and apply_result is not None
                else None
            )
            return (
                snapshot.revision,
                _next_counter(refresh_result_counter),
                status_text,
                error_text,
                error_class,
                persisted if persisted is not None else no_update,
                (
                    apply_result.requested_view_date
                    if applying and apply_result is not None and apply_result.committed
                    else no_update
                ),
            )

        @app.callback(
            Output("operating-date-banner", "children"),
            Input("data-revision-store", "data"),
        )
        def sync_operating_dates(_revision):
            """Keep the prominent dates aligned with the committed snapshot."""
            if not _revision or refresh_manager.health.revision <= 0:
                return no_update
            return build_operating_date_content(refresh_manager.control_snapshot)

        @app.callback(
            Output(COMMODITY_MARKET_STORE_ID, "data"),
            Output(RISK_CHECKER_STORE_ID, "data"),
            Input("data-revision-store", "data"),
            Input(REFRESH_RESULT_STORE_ID, "data"),
            prevent_initial_call=True,
        )
        def sync_committed_dashboard_settings(_revision, _refresh_result):
            """Rebase settings after data or same-revision metadata commits."""
            if refresh_manager.health.revision <= 0:
                raise PreventUpdate
            committed = refresh_manager.control_snapshot
            return (
                bool(committed.commodity_market_enabled),
                bool(committed.risk_checker_enabled),
            )

        @app.callback(
            Output(FORCE_DRAFT_STORE_ID, "data"),
            Output(FORCE_RENDER_STORE_ID, "data"),
            Input(FORCE_STORE_ID, "modified_timestamp"),
            Input(VIEW_DATE_STORE_ID, "modified_timestamp"),
            Input("force-risk-cancel-button", "n_clicks", allow_optional=True),
            Input(REFRESH_RESULT_STORE_ID, "data"),
            Input("risk-date-editor", "id", allow_optional=True),
            Input({"type": "force-risk-checkbox", "source": ALL}, "value"),
            Input({"type": "forced-risk-date", "source": ALL}, "date"),
            # These controls are rendered inside `risk-date-editor` by a
            # callback. Mark them optional so Dash can build the initial
            # callback graph before that editor has mounted.
            Input("force-view-date-checkbox", "value", allow_optional=True),
            Input("forced-view-date", "date", allow_optional=True),
            Input("force-all-risk-checkbox", "value", allow_optional=True),
            Input("forced-all-risk-date", "date", allow_optional=True),
            State(FORCE_STORE_ID, "data"),
            State(VIEW_DATE_STORE_ID, "data"),
            State({"type": "force-risk-checkbox", "source": ALL}, "id"),
            State({"type": "forced-risk-date", "source": ALL}, "id"),
            State(FORCE_DRAFT_STORE_ID, "data"),
            State(FORCE_RENDER_STORE_ID, "data"),
            prevent_initial_call=False,
        )
        def manage_force_risk_draft(
            saved_modified,
            saved_view_modified,
            _cancel_clicks,
            _refresh_result,
            risk_date_editor_id,
            check_values,
            dates,
            force_view_values,
            forced_view_date,
            force_all_risk_values,
            forced_all_risk_date,
            saved_dates,
            saved_view_date,
            check_ids,
            date_ids,
            current_draft,
            render_counter,
        ):
            if risk_date_editor_id != "risk-date-editor":
                raise PreventUpdate
            triggered_ids = list(ctx.triggered_prop_ids.values())
            manager_snapshot = refresh_manager.control_snapshot
            applied = snapshot_forced_dates(manager_snapshot)
            applied_view = snapshot_forced_view_date(manager_snapshot)
            revision = manager_snapshot.revision

            if "force-risk-cancel-button" in triggered_ids:
                cancelled = cancel_force_dates(applied)
                return make_force_draft(
                    cancelled,
                    cancelled,
                    revision=revision,
                    applied_view_date=applied_view,
                    view_date=applied_view,
                ), _next_counter(render_counter)

            if REFRESH_RESULT_STORE_ID in triggered_ids:
                rebased = rebase_force_draft(
                    current_draft,
                    applied,
                    revision=revision,
                    applied_view_date=applied_view,
                )
                return rebased, _next_counter(render_counter)

            if (
                ctx.triggered_id is None
                or FORCE_STORE_ID in triggered_ids
                or VIEW_DATE_STORE_ID in triggered_ids
                or "risk-date-editor" in triggered_ids
            ):
                try:
                    proposal = (
                        normalize_forced_dates(saved_dates)
                        if saved_modified not in (None, -1) or saved_dates
                        else applied
                    )
                    proposal_view = (
                        normalize_view_date(saved_view_date)
                        if saved_view_modified not in (None, -1)
                        or saved_view_date not in (None, "")
                        else applied_view
                    )
                except (TypeError, ValueError):
                    proposal = applied
                    proposal_view = applied_view
                return make_force_draft(
                    applied,
                    proposal,
                    revision=revision,
                    applied_view_date=applied_view,
                    view_date=proposal_view,
                ), _next_counter(render_counter)

            if (
                any(isinstance(value, dict) for value in triggered_ids)
                or "force-view-date-checkbox" in triggered_ids
                or "forced-view-date" in triggered_ids
                or "force-all-risk-checkbox" in triggered_ids
                or "forced-all-risk-date" in triggered_ids
            ):
                proposal = collect_forced_dates(
                    check_values, dates, check_ids, date_ids
                )
                if "force-all-risk-checkbox" in triggered_ids and "force" not in (
                    force_all_risk_values or []
                ):
                    proposal = {}
                elif "force" in (force_all_risk_values or []):
                    selected_all_date = normalize_view_date(forced_all_risk_date)
                    if selected_all_date is None:
                        raise ValueError("forced all-risk date is missing")
                    proposal = {
                        source_type: selected_all_date
                        for source_type in manager_snapshot.risk_status[
                            "Source Type"
                        ].astype(str)
                    }
                proposal_view = (
                    normalize_view_date(forced_view_date)
                    if "force" in (force_view_values or [])
                    else None
                )
                previous = draft_forced_dates(current_draft, fallback=applied)
                previous_view = draft_view_date(current_draft, fallback=applied_view)
                if proposal == previous and proposal_view == previous_view:
                    raise PreventUpdate
                base = draft_base_dates(current_draft, fallback=applied)
                base_view = draft_base_view_date(current_draft, fallback=applied_view)
                return (
                    {
                        "base_revision": int(
                            current_draft.get("base_revision", revision)
                            if isinstance(current_draft, Mapping)
                            else revision
                        ),
                        "base_overrides": base,
                        "overrides": proposal,
                        "base_view_date": base_view,
                        "view_date": proposal_view,
                        "conflict": (
                            (base != applied or base_view != applied_view)
                            and (proposal != applied or proposal_view != applied_view)
                        ),
                    },
                    (
                        _next_counter(render_counter)
                        if "force-view-date-checkbox" in triggered_ids
                        or "forced-view-date" in triggered_ids
                        else no_update
                    ),
                )
            raise PreventUpdate

        @app.callback(
            Output("risk-date-editor", "children"),
            Input(FORCE_RENDER_STORE_ID, "data"),
            State(FORCE_DRAFT_STORE_ID, "data"),
            State("risk-date-editor", "id", allow_optional=True),
        )
        def render_risk_dates(_render_revision, draft_state, risk_date_editor_id):
            if risk_date_editor_id != "risk-date-editor":
                raise PreventUpdate
            snapshot = refresh_manager.control_snapshot
            applied = snapshot_forced_dates(snapshot)
            applied_view = snapshot_forced_view_date(snapshot)
            draft = draft_forced_dates(draft_state, fallback=applied)
            view_draft = draft_view_date(draft_state, fallback=applied_view)
            return build_risk_date_editor(
                snapshot,
                applied,
                draft,
                applied_view,
                view_draft,
            )

        @app.callback(
            Output("risk-checker-inventory", "children"),
            Input(
                "risk-checker-inventory-summary",
                "n_clicks",
                allow_optional=True,
            ),
            Input(REFRESH_RESULT_STORE_ID, "data"),
            prevent_initial_call=True,
        )
        def render_risk_checker_inventory(summary_clicks, _refresh_result):
            """Serialise the checker inventory only after its chevron opens."""
            if not int(summary_clicks or 0) % 2:
                raise PreventUpdate
            checker = refresh_manager.read_frame("risk_checker")
            return build_risk_checker_inventory(
                checker.frame,
                checker.checker_date,
                enabled=checker.risk_checker_enabled,
            )

        @app.callback(
            Output({"type": "forced-risk-date", "source": MATCH}, "disabled"),
            Input({"type": "force-risk-checkbox", "source": MATCH}, "value"),
        )
        def toggle_forced_date_picker(check_value):
            return "force" not in (check_value or [])

        @app.callback(
            Output("forced-view-date", "disabled"),
            Input("force-view-date-checkbox", "value", allow_optional=True),
        )
        def toggle_forced_view_date_picker(check_value):
            return "force" not in (check_value or [])

        @app.callback(
            Output("forced-all-risk-date", "disabled"),
            Input("force-all-risk-checkbox", "value", allow_optional=True),
        )
        def toggle_forced_all_risk_date_picker(check_value):
            return "force" not in (check_value or [])

        @app.callback(
            Output("force-risk-apply-button", "disabled"),
            Output("force-risk-cancel-button", "disabled"),
            Output("force-risk-edit-status", "children"),
            Output("force-risk-edit-status", "className"),
            Input(FORCE_DRAFT_STORE_ID, "data"),
            Input(REFRESH_RESULT_STORE_ID, "data"),
            Input("refresh-busy-store", "data"),
            Input("force-risk-apply-button", "id", allow_optional=True),
        )
        def update_force_risk_actions(
            draft_state,
            _refresh_result,
            refresh_busy,
            force_apply_button_id,
        ):
            if force_apply_button_id != "force-risk-apply-button":
                raise PreventUpdate
            if bool(refresh_busy):
                return (
                    True,
                    True,
                    "Refresh in progress. Apply and Cancel are temporarily unavailable.",
                    "force-risk-edit-status",
                )
            manager_snapshot = refresh_manager.control_snapshot
            applied = snapshot_forced_dates(manager_snapshot)
            applied_view = snapshot_forced_view_date(manager_snapshot)
            try:
                proposal = draft_forced_dates(draft_state, fallback=applied)
                base = draft_base_dates(draft_state, fallback=applied)
                proposal_view = draft_view_date(draft_state, fallback=applied_view)
                base_view = draft_base_view_date(draft_state, fallback=applied_view)
            except (TypeError, ValueError):
                return (
                    True,
                    False,
                    "The staged date settings are invalid. Cancel to restore applied dates.",
                    "force-risk-edit-status is-error",
                )

            dirty = proposal != applied or proposal_view != applied_view
            conflict = dirty and (base != applied or base_view != applied_view)
            if conflict:
                return (
                    True,
                    False,
                    (
                        "Applied date settings changed while you were editing. "
                        "Cancel to reload them before applying."
                    ),
                    "force-risk-edit-status is-error",
                )
            if dirty:
                changed = sum(
                    1
                    for source in set(applied) | set(proposal)
                    if applied.get(source) != proposal.get(source)
                )
                changed += int(proposal_view != applied_view)
                noun = "change" if changed == 1 else "changes"
                return (
                    False,
                    False,
                    f"{changed} staged date {noun}. Apply to refresh, or Cancel to discard.",
                    "force-risk-edit-status is-dirty",
                )
            return (
                True,
                True,
                "All date settings are applied.",
                "force-risk-edit-status",
            )

    app.clientside_callback(
        """
        function (view) {
            if (view === "alt") {
                return [{display: "none"}, {}, {}];
            }
            return [{}, {display: "none"}, {display: "none"}];
        }
        """,
        Output("main-risk-panel", "style"),
        Output("alt-risk-panel", "style"),
        Output("alt-metric-control", "style"),
        Input("table-view-tabs", "value"),
    )

    app.clientside_callback(
        """
        function (activeRiskType, tableView, creditView) {
            const hidden = {display: "none"};
            if (activeRiskType !== "Credit") {
                return [hidden, hidden, hidden, hidden];
            }
            if (tableView === "alt") {
                return [{}, hidden, {}, hidden];
            }
            if (creditView === "multi") {
                return [{}, {}, hidden, {}];
            }
            return [{}, {}, {}, hidden];
        }
        """,
        Output("credit-view-controls", "style"),
        Output("credit-view-tabs", "style"),
        Output("credit-single-control", "style"),
        Output("credit-multi-control", "style"),
        Input("risk-type-tabs", "value"),
        Input("table-view-tabs", "value"),
        Input("credit-view-tabs", "value"),
    )

    @app.callback(
        Output("static-data-table-container", "children"),
        Input("static-data-file-selector", "value"),
    )
    def render_static_data_table(selected_file):
        """Render a filterable DataTable for the selected static CSV file."""
        from .s05_staticdata import build_static_data_table

        if not selected_file:
            return html.Div("No file selected.", className="static-data-empty")
        return build_static_data_table(selected_file)


__all__ = [
    "ForceApplyResult",
    "STARTUP_COORDINATOR_CONFIG_KEY",
    "STARTUP_UI_ERROR_CONFIG_KEY",
    "StartupCoordinator",
    "StartupStatus",
    "apply_force_dates",
    "cancel_force_dates",
    "collect_forced_dates",
    "draft_base_dates",
    "draft_forced_dates",
    "force_dates_dirty",
    "filter_unmapped_portfolios",
    "make_force_draft",
    "normalize_forced_dates",
    "persisted_force_dates",
    "rebase_force_draft",
    "register_callbacks",
    "risk_action_view_token",
    "risk_exclude_selected",
    "snapshot_forced_dates",
]
