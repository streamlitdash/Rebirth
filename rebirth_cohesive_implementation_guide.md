# Rebirth Repository Audit and Cohesive Implementation Guide

Reviewed against `main` at commit `e715d7c5eefd562e8eea8066ea1a3128e382e5ed`.

## Verdict

Together, the two prior answers cover the original brief, but neither should be applied verbatim on its own.

The first answer is stronger on actually collapsing Portfolio rows before Dash receives them, averaging duplicate market quotes, replacing the Stock hierarchy with a simpler P&L-style aggregate page, removing dead commented code, and adding `orjson`.

The second answer is stronger on Quick Search dependencies that would break when Portfolio disappears, the hard-coded Portfolio input in the unmapped-books callback, the two P&L-history callbacks that assume exactly five filters, removing duplicate initial Risk and P&L table construction, and preserving the cold-start hero through the dashboard handoff.

Important corrections:

1. Do not ignore all incoming tenor ranks. Preserve valid connector-owned order where possible, resolve conflicts deterministically, and renumber.
2. Do not only cosmetically rewrap the Stock hierarchy. Remove the promotion threshold, temporary currency grouping, hierarchy JSON paths, and tree state if the objective is a simpler P&L-style Stock page.
3. Do not increase Gunicorn workers yet. The snapshot, caches, refresh coordinator, locks, and Stock state are process-local.

---

# Cohesive implementation guide

Line numbers are approximate for commit `e715d7c5…`. After editing, use the function name as the reliable locator.

## 1. Keep Portfolio for mapping and sending, but remove it from the analytical frame

Portfolio must remain in:

- Connector output.
- `combined_pl`.
- Portfolio configuration.
- Stock mapping.
- Portfolio P&L sending.

The P&L sender reads Portfolio governance from `snapshot.combined_pl`, independently of `dashboard_frame`. Therefore Portfolio can safely disappear from the analytical frame without breaking the sender.

### File: `core/s02_pipeline.py`

### Location: immediately before `to_dashboard_frame()`, around lines 1,700–1,725

Add:

```python
def _sum_available(values: pd.Series):
    """Sum populated values while keeping an all-missing group missing."""
    return values.sum(min_count=1)


def _collapse_dashboard_portfolios(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse Portfolio rows before publishing data to Dash."""
    additive_columns = [
        RISK,
        DRISK,
        PL,
        *[
            column
            for column in CREDIT_MEASURE_COLUMNS
            if column in frame
        ],
    ]

    group_columns = [
        SOURCE_TYPE,
        RISK_TYPE,
        RISK_GREEK,
        SPLIT,
        *PORTFOLIO_METADATA_COLUMNS,
        GROUP,
        *([REGION] if REGION in frame else []),
        REPORTED_UNDERLYING,
        UNDERLYING,
        *TENOR_COLUMNS,
        PORTFOLIO_MAPPED,
    ]

    aggregations = {
        **{
            column: _sum_available
            for column in additive_columns
        },
        **{
            column: "min"
            for column in TENOR_ORDER_COLUMNS
        },
        OPEN: "mean",
        CURRENT: "mean",
        DISPLAY_BUCKET: "first",
        PROMOTION_REASON: "first",
        PROMOTION_SCORE: "max",
        RISK_THRESHOLD: "first",
        DRISK_THRESHOLD: "first",
        PL_THRESHOLD: "first",
        MARKET_STATUS: "first",
    }

    collapsed = (
        frame.groupby(
            group_columns,
            as_index=False,
            dropna=False,
            sort=False,
            observed=True,
        )
        .agg(aggregations)
    )

    collapsed[MARKET_AVAILABLE] = (
        collapsed[OPEN].notna()
        & collapsed[CURRENT].notna()
    )

    collapsed[MARKET_DATA_STATUS] = np.select(
        [
            collapsed[MARKET_AVAILABLE],
            collapsed[OPEN].isna()
            & collapsed[CURRENT].isna(),
            collapsed[OPEN].isna(),
            collapsed[CURRENT].isna(),
        ],
        [
            "Available",
            "Missing Open and Current (Live/OFFICIAL)",
            "Missing Open",
            "Missing Current (Live/OFFICIAL)",
        ],
        default="Incomplete market data",
    )

    collapsed[MARKET_MOVE] = (
        collapsed[CURRENT] - collapsed[OPEN]
    )

    LOGGER.info(
        "Dashboard rows after removing Portfolio: %s -> %s",
        len(frame),
        len(collapsed),
    )
    return collapsed
```

### Location: `to_dashboard_frame()`, around lines 1,725–1,780

Immediately after the existing `_require_columns(...)` call, add:

```python
frame = _collapse_dashboard_portfolios(frame)
```

In the final `columns = [...]` list, delete only:

```python
PORTFOLIO,
```

Keep Portfolio in `_require_columns(...)`. It must exist until mapping and metadata attachment have completed.

### Aggregation rules

| Column type | Rule |
|---|---|
| Risk, dRisk, P&L and Credit measures | Sum with `min_count=1` |
| Open and Current | Mean |
| Move | Recalculate as `Current - Open` |
| Tenor order | Minimum, followed by normalization |
| Thresholds and reporting labels | First |
| Promotion score | Maximum |
| All-missing numeric group | Keep missing |

Do not group by Portfolio, market status text, promotion score, or market-data status. Grouping by those fields would prevent otherwise identical Portfolio rows from collapsing.

---

## 2. Remove Portfolio from all analytical selectors

### File: `ui/s02_constants.py`

### Location: top of file, around lines 5–45

Delete:

```python
PORTFOLIO_UI_FIELD = ...
```

Replace the view/filter registry block with:

```python
from core.s01_schema import PORTFOLIO_FIELDS


VIEW_DIMENSION_FIELDS = tuple(
    field
    for field in PORTFOLIO_FIELDS
    if "view_dimension" in field.roles
)

FILTER_DIMENSION_FIELDS = tuple(
    field
    for field in PORTFOLIO_FIELDS
    if "filter_dimension" in field.roles
)

FILTER_DIMENSION_ORDER = tuple(
    field.key for field in FILTER_DIMENSION_FIELDS
)
```

Delete the old synthetic Portfolio field, old filter map, and the explicit five-field order. Remove `PORTFOLIO_UI_FIELD` from `__all__`.

After this change:

- View by: Product, Activity, Signoff Group, Category, Sub Category.
- Filters: Activity, Signoff Group, Category, Sub Category.
- Default View by: Activity.

---

## 3. Remove Portfolio from Quick Search

### File: `core/s03_search.py`

Remove the `PORTFOLIO_COLUMN` import and delete:

```python
PORTFOLIO = PORTFOLIO_COLUMN
```

Remove `PORTFOLIO` from `PIVOT_INDEX_COLUMNS`.

Replace:

```python
GOVERNANCE_COLUMNS = (
    PORTFOLIO,
    *PORTFOLIO_METADATA_COLUMNS,
)
```

with:

```python
GOVERNANCE_COLUMNS = tuple(
    PORTFOLIO_METADATA_COLUMNS
)
```

Replace `QUICK_RISK_FILTER_COLUMNS` with:

```python
QUICK_RISK_FILTER_COLUMNS = (
    SPLIT,
    *(
        field.external_name
        for field in PORTFOLIO_FIELDS
        if "filter_dimension" in field.roles
    ),
)
```

### Function: `_risk_pivot_catalog_frame()`

Remove Portfolio from the `required` list.

### Function: `build_search_catalog()`

Delete:

```python
fallback[PORTFOLIO] = UNSPECIFIED
```

### File: `ui/s04_components.py`

In `_QUICK_SEARCH_IDENTITY_OPTIONS`, delete:

```python
("Portfolio", "Portfolio"),
```

---

## 4. Remove callback code that assumes Portfolio still exists

### 4.1 Unmapped books

### File: `ui/s07_events.py`

Delete the entire `filter_unmapped_portfolios()` function and remove it from `__all__`.

In `render_unmapped_books()`, remove:

```python
Input(DIMENSION_FILTER_IDS["portfolio"], "value"),
Input("risk-filter-exclude-selected", "value"),
```

Change the function signature to:

```python
def render_unmapped_books(
    _summary_clicks,
    _revision,
    is_open,
):
```

Delete the call to `filter_unmapped_portfolios(...)`.

The unmapped diagnostic table may still display Portfolio because it is a mapping diagnostic, not an analytical selector.

### 4.2 P&L history callbacks

### File: `ui/s08_plevents.py`

#### Function: `render_historical_pl_hierarchy()`

Change to dynamic positional parsing:

```python
def render_historical_pl_hierarchy(
    summary_clicks,
    _row_clicks,
    _period_header_clicks,
    _metric_clicks,
    *args,
):
    filter_count = len(PL_FILTER_FIELDS)
    filter_values = args[:filter_count]
    exclude_filter = args[filter_count]

    (
        open_path_tokens,
        open_comparison_tokens,
        selection_state,
    ) = args[filter_count + 1:]
```

Replace its hard-coded five-filter list with:

```python
page_filters = pl_external_filter_map(
    filter_values
)
```

#### Function: `render_historical_pl_chart()`

Use:

```python
def render_historical_pl_chart(
    selection_state,
    series_choice,
    *args,
):
    filter_count = len(PL_FILTER_FIELDS)
    filter_values = args[:filter_count]
    exclude_filter = args[filter_count]

    (
        _wtd_clicks,
        _mtd_clicks,
        _ytd_clicks,
        _all_clicks,
        explicit_start,
        explicit_end,
        range_state,
    ) = args[filter_count + 1:]
```

Again replace the hard-coded five-filter list with `pl_external_filter_map(filter_values)`.

### 4.3 Update selector text

In `ui/s04_components.py`, make `RISK_FILTER_NOTE` generic and remove Portfolio examples.

In `ui/s10_stock.py`, make `STOCK_FILTER_NOTE` generic.

In `ui/s06_plview.py`, change the filter-bar docstring to:

```python
"""Build the configured P&L filter row."""
```

In `ui/s14_pl_filters.py`, change the selector docstring to:

```python
"""Normalize the configured P&L selectors to external columns."""
```

---

## 5. Migrate saved views and change the filter grid to four columns

Run once during deployment:

```python
import json
from pathlib import Path


for path in Path("data/saved_views").rglob("*.json"):
    payload = json.loads(
        path.read_text(encoding="utf-8")
    )
    payload["filters"].pop("portfolio", None)
    path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
```

No change is required in `core/s08_saved_views.py`.

### File: `assets/s01_style.css`

Replace:

```css
grid-template-columns: repeat(5, minmax(120px, 1fr));
```

with:

```css
grid-template-columns: repeat(4, minmax(120px, 1fr));
```

---

## 6. Allow partially populated Credit measures

The repository calls the middle measure `PSP01`, not PSP.

### 6.1 Permit NA during ingestion

### File: `core/s02_pipeline.py`

### Function: `get_product_risk()`

Replace:

```python
if credit_measure_columns:
    frame = _coerce_numeric(
        frame,
        credit_measure_columns,
        f"{spec.key} optional Credit measures",
    )
```

with:

```python
if credit_measure_columns:
    frame[credit_measure_columns] = frame[
        credit_measure_columns
    ].apply(pd.to_numeric)
```

Leave mandatory Risk and dRisk coercion unchanged.

### 6.2 Change completeness from “all” to “any”

### File: `ui/s03_aggregate.py`

In `credit_measure_available()`, change:

```python
connector_rows[column].notna().all()
```

to:

```python
connector_rows[column].notna().any()
```

In `credit_measure_values()`, replace the local completeness check with a local availability check using `.notna().any()`.

Do not fill missing values with zero.

---

## 7. Aggregate duplicate market quotes instead of rejecting them

Duplicate Risk/P&L rows should sum. Duplicate quotes should average.

### File: `core/s02_pipeline.py`

Add near the market-order helper:

```python
def _aggregate_market_leg(
    frame: pd.DataFrame,
    spec: ProductSpec,
    value_column: str,
) -> pd.DataFrame:
    """Average duplicate quotes at one market identity."""
    aggregations: dict[str, object] = {
        value_column: "mean",
    }

    for order_column in spec.tenor_order_columns:
        if order_column in frame:
            aggregations[order_column] = "min"

    if MARKET_STATUS in frame:
        aggregations[MARKET_STATUS] = "first"

    result = (
        frame.groupby(
            list(spec.market_keys),
            as_index=False,
            dropna=False,
            sort=False,
            observed=True,
        )
        .agg(aggregations)
    )

    for order_column in spec.tenor_order_columns:
        if order_column not in result:
            result[order_column] = pd.Series(
                pd.NA,
                index=result.index,
                dtype="Int64",
            )

    return _normalize_market_tenor_orders(
        result,
        spec,
    )
```

### Function: `get_product_market_open()`

Require only `market_keys + OPEN`, keep tenor-order columns optional, and after numeric conversion call:

```python
frame = _aggregate_market_leg(
    frame,
    spec,
    OPEN,
)
```

Delete strict tenor validation and duplicate-key rejection.

### Function: `get_product_market_status()`

Require only `market_keys + CURRENT + MARKET_STATUS`, keep tenor-order columns optional, then call:

```python
frame = _aggregate_market_leg(
    frame,
    spec,
    CURRENT,
)
```

Delete strict tenor validation and duplicate-key rejection.

---

## 8. Gracefully normalize misaligned tenor orders

Recommended policy:

> Preserve valid connector order where possible. For conflicts, choose the lowest supplied rank per tenor, break ties by the tenor label’s natural sort order, place missing ranks last, and renumber to `0, 1, 2, ...`.

### 8.1 Move the tenor label sorter into core

Move the existing `tenor_sort_key()` from `ui/s03_aggregate.py` into `core/s01_schema.py`.

Add `import re` and export `tenor_sort_key` through `__all__`.

### 8.2 Replace strict core validation with normalization

### File: `core/s02_pipeline.py`

Replace `_validate_market_tenor_orders()` with a `_normalize_market_tenor_orders()` implementation that:

- Converts supplied order values with `errors="coerce"`.
- Groups by `Underlying`.
- Takes the minimum supplied rank per tenor label.
- Sorts by supplied rank, then `tenor_sort_key()`.
- Places missing ranks last.
- Renumbers the final sequence from zero.

### 8.3 Accept Open/Current disagreement

### Function: `_merge_validated_market_legs()`

Delete the initial loop that raises when Open and Current disagree on tenor order.

Keep the existing `combine_first()` logic so Open order remains preferred and Current fills missing Open ranks.

Then normalize the merged market using `_normalize_market_tenor_orders()`.

### 8.4 Apply the same policy to static/UI data

### File: `ui/s03_aggregate.py`

In `_resolved_tenor_orders()`, remove conflict and collision exceptions. Use the same lowest-rank-per-label plus natural-sort tie-break policy.

---

## 9. Always display an aggregate at Reported Underlying

### File: `ui/s03_aggregate.py`

### Function: `should_show_sum()`

Replace:

```python
if column in {"move", "open", "current"}:
    return _is_semantic_underlying(context)
```

with:

```python
if column in {"move", "open", "current"}:
    return (
        "reported underlying" in context
        or _is_semantic_underlying(context)
    )
```

Do not rewrite `hierarchical_market_value()` or `_MarketQuoteIndex`.

Resulting rule:

- Duplicate quotes at the same exact market key: mean in the pipeline.
- Multiple option tenors: equal-weight mean.
- Multiple swap tenors: equal-weight mean.
- Multiple raw underlyings within one Reported Underlying: equal-weight mean.
- Move: aggregate Current minus aggregate Open.
- Incomplete Open/Current pair: excluded from the displayed quote aggregate.

---

## 10. Remove duplicate initial Risk and P&L rendering

### 10.1 Risk page

### File: `ui/s04_components.py`

### Function: `build_layout()`

Keep lightweight setup such as `initial_risk_type` and `top_book_open_rows`.

Delete construction of:

```python
initial_risk_frame
initial_risk_table
initial_aggregate_table
```

Replace initial `aggregate-pl-grid` content with a loading placeholder.

Replace initial `risk-grid` content with a loading placeholder.

Keep the IDs unchanged so the existing reducers populate them after mount.

### 10.2 P&L page

### File: `ui/s09_factory.py`

### Function: `pnl_page_body()`

Stop calling `prepared_committed_dashboard()` during page construction. Pass `initial_aggregate_frame=None` and let the aggregate callback populate the table after mount.

Keep `prepared_committed_dashboard()` itself because callbacks still benefit from the revision cache.

---

## 11. Fix the cold-start hero lifecycle

The full fix is:

1. Build the shared shell in loading mode on a cold start.
2. Preserve loading mode until the mounted page adopts the committed revision.
3. Leave success visible long enough to paint.

### 11.1 Build a cold shell correctly

### File: `ui/s09_factory.py`

### Function: `serve_layout()`

Add:

```python
shared_snapshot = current_shared_snapshot()

cold_start = (
    refresh_manager is not None
    and shared_snapshot is None
)
```

Change the shared shell call to:

```python
build_shared_refresh_shell(
    shared_snapshot,
    refresh_enabled=refresh_manager is not None,
    stage_delays=stage_delays,
    initial_loading=cold_start,
    keep_polling=cold_start,
    style={"display": "none"},
),
```

Keep `style={"display": "none"}` because navigation already reveals the shell on Risk and P&L.

### 11.2 Preserve the hero during handoff

### File: `ui/s07_events.py`

### Function: `hydrate_shared_refresh_shell()`

Inside the successful-startup branch add:

```python
pending_handoff = (
    shell_revision
    < int(refresh_manager.health.revision)
)
```

Pass `initial_loading=pending_handoff` and `keep_polling=pending_handoff` when rebuilding the shared shell.

### 11.3 Extend the completed display

### File: `assets/s02_app.js`

### Function: `finishRefreshProgress()`

Replace:

```javascript
}, hasNewError ? 5000 : 300);
```

with:

```javascript
}, hasNewError ? 5000 : 1500);
```

---

## 12. Replace the Stock hierarchy with a P&L-style aggregate page

Target layout:

1. Date controls.
2. Saved views and four reporting filters.
3. Aggregate Stock.
4. P&L-style View by selector.
5. Prior, Current and Change columns.
6. On-demand detailed comparison table.

### 12.1 Aggregate duplicate raw Stock rows

### File: `core/s07_stock.py`

Add immediately before `compare_stock_snapshots()`:

```python
def _aggregate_stock_snapshot(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Sum duplicate rows at the current Stock identity."""
    return (
        frame.groupby(
            list(STOCK_IDENTITY_COLUMNS),
            as_index=False,
            dropna=False,
            sort=False,
            observed=True,
        )[list(STOCK_NUMERIC_COLUMNS)]
        .sum(min_count=1)
    )
```

Inside `compare_stock_snapshots()`, aggregate the validated current and prior snapshots before merging. Delete `_reject_duplicate_stock_identity()`.

### 12.2 Remove Portfolio after Stock mapping

Keep raw `STOCK_TEXT_COLUMNS` unchanged because Portfolio is still needed for mapping.

Replace `STOCK_FILTER_COLUMN_BY_KEY` with a registry-derived mapping from fields with the `filter_dimension` role.

Replace `MAPPED_STOCK_COMPARISON_COLUMNS` so Portfolio is omitted from the public mapped comparison.

Add `_collapse_mapped_stock_portfolios()` that groups by the non-Portfolio Stock identity plus governed metadata, sums prior/current Quantity and Market Value, recalculates changes, and recalculates `Stock Change`.

Return `_collapse_mapped_stock_portfolios(mapped)` from `map_stock_comparison_portfolios()`.

### 12.3 Copy the P&L aggregate section

Copy `_pl_aggregate_section()` from `ui/s06_plview.py` into `ui/s10_stock.py` and rename it `_stock_aggregate_section()`.

Use these replacements:

| P&L value | Stock value |
|---|---|
| `Aggregate P&L` | `Aggregate Stock` |
| `pnl-aggregate-pl-dimension` | `stock-aggregate-dimension` |
| `pnl-aggregate-pl-grid` | `stock-aggregate-grid` |
| `Loading aggregate P&L` | `Loading aggregate Stock` |
| `build_pl_aggregate_table` | `build_stock_aggregate_table` |

### 12.4 Add the Stock aggregate builder

### File: `ui/s10_stock.py`

Create `STOCK_VIEW_COLUMN_BY_KEY` from `VIEW_DIMENSION_FIELDS`.

Add `build_stock_aggregate_table(mapped_stock, dimension)` that groups by the selected reporting dimension and sums:

- Prior Quantity.
- Current Quantity.
- Prior Market Value.
- Current Market Value.

Then calculate:

- Quantity Change.
- Market Value Change.

Reuse the existing Stock DataTable styling and use `id="stock-aggregate-table"`.

### 12.5 Replace the hierarchy in the page

In `build_stock_page_from_data()`, delete the complete “Stacked Stock” panel and replace it with:

```python
_stock_aggregate_section(filtered),
```

Keep the on-demand detail section below it.

In `build_stock_page_placeholder()`, use `_stock_aggregate_section(None)`.

In `build_stock_page_shell()`:

- Delete `stock-hierarchy-open-paths`.
- Delete the Promotion Threshold input.
- Remove `promotion_threshold` from `stock-dimension-filter-store`.
- Keep the date controls, saved views, four reporting filters, source-row state, and loading boundary.

### 12.6 Replace the hierarchy callback

### File: `ui/s09_factory.py`

Replace the current Stock hierarchy callback with one that outputs:

- Row count.
- Mapped count.
- Unmapped count.
- Stock filter state.
- `stock-aggregate-grid`.

Inputs should be:

- Four Stock reporting filters.
- Exclude mode.
- `stock-aggregate-dimension`.
- Loaded date token.

Keep the lazy detail-row callback.

Change the Stock cache cap from 8 to 2 full page comparisons.

### 12.7 Delete obsolete hierarchy machinery

After tests pass, remove:

- Promotion threshold constants and functions.
- Temporary currency group.
- Promotion Bucket.
- Hierarchy path token functions.
- Open-state functions.
- Hierarchy row builders.
- `STOCK_HIERARCHY_TOGGLE_TYPE`.
- `import json` if no longer used.
- Toggle glyph imports.
- Hierarchy-specific exports.

Remove corresponding imports from `ui/s09_factory.py`.

`pages/stock.py` needs no change.

---

## 13. Improve Gunicorn without splitting application state

### File: `s04_server.py`

Replace the file with:

```python
"""Gunicorn settings for process-local dashboard state."""

import os


workers = 1
worker_class = "gthread"

threads = int(
    os.getenv(
        "GUNICORN_THREADS",
        "4",
    )
)

timeout = int(
    os.getenv(
        "GUNICORN_TIMEOUT_SECONDS",
        "300",
    )
)

graceful_timeout = int(
    os.getenv(
        "GUNICORN_GRACEFUL_TIMEOUT_SECONDS",
        "30",
    )
)

keepalive = int(
    os.getenv(
        "GUNICORN_KEEPALIVE_SECONDS",
        "5",
    )
)
```

Keep the default at four threads initially. After the row collapse and duplicate-render removal, benchmark eight threads.

Do not change to two workers until these are moved to shared storage:

- Committed snapshot.
- Committed revision.
- Refresh progress.
- Startup coordination.
- Prepared dashboard cache.
- Stock cache.
- Refresh and Stock locks.

Increasing threads can improve overlapping connector or HTTP work. It will not make one CPU-heavy pandas callback eight times faster.

---

## 14. Add faster Dash serialization

### File: `requirements.txt`

Add:

```text
orjson==3.11.9
```

Do not change the existing Dash, pandas, NumPy or Plotly pins in the same patch.

Treat this as a secondary improvement. Reducing row count and avoiding duplicate table construction will have a larger effect.

---

## 15. Remove genuinely over-engineered code

### File: `adapters/s04_credit.py`

Delete everything from:

```python
# === REAL CREDIT CONNECTOR (COMMENTED OUT)
```

down to immediately before:

```python
# === ACTIVE VALIDATED CONTRACT
```

Keep only the active adapter.

### File: `core/s02_pipeline.py`

Search for:

```text
RECOVERED
COMMENTED OUT
SWITCH TO
```

Delete comment-only alternatives while retaining active definitions.

Also remove the large commented recovered age-rule block under `risk_date_for()`.

### Do not remove

Keep:

- `HierarchyAggregationIndex`.
- `_MarketQuoteIndex`.
- Prepared dashboard cache by revision.
- StartupCoordinator.
- Atomic snapshot publication.
- Last-good-snapshot behaviour.
- Stock stale-request protection.
- Lazy loading of Stock detail rows.

Do not spend time deleting every validation exception. Most run once at connector or refresh boundaries and are not the source of page lag.

---

## 16. Update the tests

### `tests/s19_risk_filters.py`

Assert Portfolio is absent from both `FILTER_DIMENSION_FIELDS` and `VIEW_DIMENSION_FIELDS`.

Replace Portfolio filtering examples with Category or Activity.

Delete tests and imports for `filter_unmapped_portfolios`.

Add:

```python
assert "Portfolio" not in snapshot.dashboard_frame
```

### `tests/s23_saved_views.py`

Expected keys become:

```python
(
    "activity",
    "signoffgroup",
    "category",
    "subcategory",
)
```

Remove Portfolio from fixture filters, expected JSON, saved-view request values, and manual edit positions.

Change the CSS assertion from five columns to four.

### `tests/s04_market.py`

Replace failure tests for duplicate market keys, rank collisions, and Open/Current rank disagreement.

Add tests asserting:

- Duplicate Open quotes are averaged.
- Duplicate Current quotes are averaged.
- Valid non-lexical connector order is preserved.
- Conflicting ranks are normalized.
- Missing ranks are placed last.
- Open and Current may disagree without aborting.
- Final ranks are unique and consecutive.
- Move equals aggregate Current minus aggregate Open.

### `tests/s06_ui.py`

Add tests for:

- One missing SP01 value with other values still displayed.
- One missing PSP01 value with other values still displayed.
- One missing PM01 value with other values still displayed.
- An all-missing Credit measure remaining unavailable.
- Several raw underlyings beneath one Reported Underlying displaying averaged Open, Current and Move.
- Portfolio absent from Quick Search choices.

### `tests/s12_startup.py`

Cold start should assert the progress hero is visible and the bootstrap interval is active.

Warm start should assert the hero begins hidden.

Change the JavaScript assertion from the 300 ms success delay to 1500 ms.

### `tests/s17_stock.py`

Delete hierarchy-path, promotion-threshold and temporary-group tests.

Add tests asserting:

- Duplicate raw Stock identities are summed.
- Raw Portfolio is still used for mapping.
- Public mapped Stock does not contain Portfolio.
- Aggregate-by-Activity totals equal detailed collapsed totals.
- Prior, Current and Change values are correct.
- Added/Removed/Changed/Unchanged are recalculated after collapsing.
- The Stock page contains `stock-aggregate-dimension` and `stock-aggregate-grid`.
- No hierarchy path store or promotion threshold is present.

### Integration test for dashboard collapse

Use two fully mapped rows differing only by Portfolio and assert:

```python
assert "Portfolio" not in dashboard.columns
assert len(dashboard) == 1
assert dashboard["Risk"].iloc[0] == 30.0
assert dashboard["dRisk"].iloc[0] == 3.0
assert dashboard["PL"].iloc[0] == 10.0
assert dashboard["Open"].iloc[0] == 3.0
assert dashboard["Current"].iloc[0] == 4.0
```

Do not unconditionally assert that all `combined_pl` totals equal dashboard totals if unmapped Portfolios are present.

---

## 17. Run checks in this order

```bash
python -m compileall \
    adapters \
    core \
    feeds \
    pages \
    ui
```

Focused suite:

```bash
python -m pytest \
    tests/s04_market.py \
    tests/s05_pl.py \
    tests/s06_ui.py \
    tests/s07_integration.py \
    tests/s12_startup.py \
    tests/s17_stock.py \
    tests/s19_risk_filters.py \
    tests/s23_saved_views.py \
    -q
```

Then:

```bash
python -m pytest -q
```

Finally inspect:

```text
Dashboard rows after removing Portfolio: before -> after
```

Also compare:

- Time to switch Risk → P&L.
- Time to switch P&L → Risk.
- Dash callback response sizes.
- Initial Risk hierarchy render time.
- Stock cache memory.
- Four versus eight Gunicorn threads under concurrent requests.

---

# Expected result

After applying the full sequence:

- Portfolio remains available for mapping and Portfolio P&L sending.
- Portfolio disappears from `dashboard_frame`.
- Portfolio disappears from Risk, Aggregate P&L and Stock selectors.
- Portfolio disappears from Quick Search.
- Portfolio-level rows collapse before Dash serialization.
- Partial SP01, PSP01 and PM01 columns remain visible.
- Duplicate market quotes are averaged.
- Misaligned tenor ranks are normalized without discarding intentional connector order.
- Reported Underlying displays Open, Current and Move aggregates.
- Risk and P&L no longer construct their initial tables twice.
- The cold-start hero remains visible through the revision handoff.
- Stock becomes a simple P&L-style aggregate page.
- Gunicorn retains one authoritative in-process snapshot while allowing thread tuning.
- Dead commented connector code and unnecessary Stock hierarchy state are removed.
