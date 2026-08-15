# Risk Explorer Performance Repair Guide

## Purpose

This guide explains how to make the Risk Explorer respond quickly after adding:

- filtered promotion;
- the Promotion and Region switches;
- Underlying sorting by Risk, dRisk, or P&L;
- the large `reduce_and_render_risk_view` callback.

It is written as an implementation handoff for an AI working on this repository. Follow the phases in order. Do not begin by rewriting the financial pipeline or splitting the reducer into a chain of server callbacks.

The desired result is:

1. every control that changes the visible table triggers exactly one table render;
2. detail-only controls do not rebuild the table;
3. promotion and sorting use the already-filtered data;
4. the browser receives a much smaller component payload;
5. financial refresh remains separate from ordinary UI interactions.

## Important source locations

The relevant path is:

```text
ui/s07_events.py::_RiskDataCache
  -> ui/s07_events.py::reduce_and_render_risk_view
  -> ui/s07_events.py::render_active_risk_table
  -> ui/s04_components.py::build_risk_table / build_alt_risk_table
  -> ui/s04_components.py::build_tree_rows
  -> ui/s03_aggregate.py::ordered_unique
```

The financial refresh is separate:

```text
ui/s07_events.py::refresh_pipeline
  -> core/s02_pipeline.py::RiskRefreshManager.refresh
```

A Promotion, Region, sort, filter, row, or view click should not call `RiskRefreshManager.refresh()`.

## Executive diagnosis

There are two different issues.

### Issue 1: definite callback-state bugs

In the modified callback shown in the implementation notes, these are Dash `Input` values:

```python
Input("promotion-toggle-store", "data")
Input("region-toggle-store", "data")
Input("underlying-sort-metric", "value")
```

However, they are missing from the callback's trigger-routing sets. They therefore reach the final `PreventUpdate` branch instead of rendering the table.

They are also missing from the shown `risk_generation_state(...)`. That state contributes to the rendered-cache key and delegated-action token. A render can therefore reuse a table created with a different Promotion, Region, or sort setting.

This explains behavior such as:

- the first click appears to do nothing;
- the change appears after clicking another control;
- a stale table returns from cache;
- stale row actions can be accepted against a stale component when both token paths omit the new values; if only one token path includes them, otherwise-valid actions can be rejected.

### Issue 2: expensive full-table rendering

On every cold render, the current implementation can:

1. filter a pandas frame;
2. recompute promotion;
3. construct a hierarchy aggregation index;
4. recursively slice the frame at every visible node;
5. repeatedly group and sort children;
6. construct thousands of Dash `html.Tr`, `html.Td`, `html.Button`, and `html.Span` objects;
7. serialize the complete tree to JSON;
8. send it over the network;
9. make React reconcile the complete DOM tree.

The Risk/dRisk/P&L groupby used for ordering is not normally a ten-second calculation by itself. More often, changing the sort metric creates a new cache key and exposes the cost of rebuilding and transmitting the entire expanded hierarchy.

The number of open rows is especially important. A few thousand visible hierarchy nodes can produce a multi-megabyte callback response.

In a synthetic audit of the current recursive renderer, approximately 2,700 filtered rows with 2,875 open hierarchy keys took about 2.9 seconds to build and serialized to roughly 7.8 MB. At approximately 5,400 rows with 5,745 open keys, component construction took about 6.3 seconds and serialized to roughly 15.6 MB, before cloud latency and browser reconciliation. These are diagnostic examples rather than production benchmarks, but they show how a ten-second interaction can arise without running the financial refresh.

## Input and State: the correct rule

Do not declare the same component property as both `Input` and `State`.

- `Input` supplies its current value **and** triggers the callback when it changes.
- `State` supplies its current value but does **not** trigger the callback.

Therefore:

```python
Input("underlying-sort-metric", "value")
```

already gives the callback the selected value. It does not also need:

```python
State("underlying-sort-metric", "value")
```

Use `Input` for controls that must update the screen immediately. Use `State` for passive context that the callback needs but that should not independently trigger rendering.

Recommended classification:

| Control | Dependency | Reason |
|---|---|---|
| Risk type / IR family / view | `Input` | Changes the displayed table |
| Dimension filters / Split | `Input` | Changes table data |
| Promotion / Region | `Input` | Changes displayed hierarchy |
| Underlying sort metric | `Input` | Changes row order |
| Row and metric action stores | `Input` | User action must render |
| Plot measure / component / tenor | `Input` | Changes detail panel |
| Current open rows | `State` | Read as context; row action is the trigger |
| Current selection | `State` | Read as context; cell or detail control is the trigger |
| Current expanded metrics | `State` | Metric action is the trigger |
| Current disclosure open flags | `State` | Summary click is the trigger |

Reference: [Dash basic callbacks](https://dash.plotly.com/basic-callbacks).

## Phase 1: repair callback correctness

Do this before performance refactoring. Otherwise profiling results will include stale-cache and missing-trigger behavior.

### Step 1: classify the triggers once

In `ui/s07_events.py`, inside `reduce_and_render_risk_view`, replace the broad `table_inputs` / `detail_inputs` definitions with explicit categories.

Preserve the selector used by the live branch. The pasted modified callback uses Split; the checked-out baseline uses Greek. Do not rename `greeks` to `splits`, or `splits` to `greeks`, as part of this performance repair. Use the same authority consistently in the callback signature, filtered-cache key, generation state, table renderer, and detail renderer.

| Modified-branch name | Baseline equivalent |
|---|---|
| `selected_splits` | `selected_greeks` |
| `effective_splits` | `effective_greeks` |
| `split-filter.value` | `greek-filter.value` |
| `splits=...` | `greeks=...` |

```python
sort_only_inputs = {
    "credit-multi-metric.value",
    "alt-metric.value",
    "underlying-sort-metric.value",
}

hierarchy_mode_inputs = {
    "promotion-toggle-store.data",
    "region-toggle-store.data",
}

table_and_detail_inputs = {
    "split-filter.value",
    "credit-measure.value",
    "dimension-filter-values-store.data",
}

detail_only_inputs = {
    "plot-component.value",
    "detail-tenor-view.value",
}
```

If the dimension filters are individual component Inputs instead of a store, use:

```python
table_and_detail_inputs = {
    "split-filter.value",
    "credit-measure.value",
    *{f"{component_id}.value" for component_id in dimension_filter_ids},
}
```

The important rules are:

- sort, Promotion, and Region must be routed to table rendering;
- tenor and plot controls must not rebuild the table;
- financial filters can update the detail only when a detail selection exists.
- sort preserves open rows and selection because it changes order only;
- Promotion and Region can change hierarchy paths and row keys, so they must reset or prune invalid open rows and clear stale selection.

### Step 2: normalize the three new values once

Immediately after the existing effective-value normalization, add:

```python
effective_promotion_enabled = bool(promotion_enabled)
effective_region_enabled = bool(region_enabled)
effective_underlying_sort_metric = selected_underlying_sort_metric(
    underlying_sort_metric
)
```

Use the normalized values everywhere below. Do not normalize separately for the cache key, table builder, and token because those copies can drift.

Import and reuse `selected_underlying_sort_metric` from `ui/s03_aggregate.py`. Its constants own the allowed metrics and default. Do not recreate `{"risk", "drisk", "pl"}` or a fallback in the callback because the callback and sorter can otherwise acquire different defaults.

### Step 3: include them in `risk_generation_state`

Change the helper signature in `ui/s07_events.py`:

```python
def risk_generation_state(
    *,
    splits,
    dimension_values,
    credit_measure,
    credit_multi_metric,
    alt_metric,
    expanded_metrics,
    promotion_enabled,
    region_enabled,
    underlying_sort_metric,
) -> dict[str, Any]:
```

Add the normalized fields to its return value:

```python
return {
    "alt_metric": alt_metric,
    "credit_measure": credit_measure,
    "credit_multi_metric": credit_multi_metric,
    "expanded_metrics": sorted(expanded_metrics or []),
    "filters": {
        key: sorted(values)
        for key, values in sorted(filters.items())
    },
    "splits": sorted(splits or []),
    "promotion_enabled": bool(promotion_enabled),
    "region_enabled": bool(region_enabled),
    "underlying_sort_metric": underlying_sort_metric,
}
```

Then update **every** call to `risk_generation_state(...)`:

1. the expected token used to validate row/cell actions;
2. the state used by `render_active_risk_table`;
3. any rendered component cache key;
4. any delegated-action view token used by main, alternate, or Credit tables.

Example:

```python
generation_state=risk_generation_state(
    splits=effective_splits,
    dimension_values=dimension_values,
    credit_measure=credit_measure,
    credit_multi_metric=credit_multi_metric,
    alt_metric=alt_metric,
    expanded_metrics=effective_expanded_metrics,
    promotion_enabled=effective_promotion_enabled,
    region_enabled=effective_region_enabled,
    underlying_sort_metric=effective_underlying_sort_metric,
)
```

The displayed component and the expected action token must be generated from exactly the same state.

### Step 4: route the trigger without rebuilding unrelated content

Near the end of the reducer's action branch, use:

```python
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
    should_render_detail = bool(effective_selection)

elif triggered & sort_only_inputs:
    should_render_table = True

elif triggered & hierarchy_mode_inputs:
    # Promotion/Region can change row-key paths. The simple safe policy is
    # to collapse the hierarchy and clear a detail selection made under the
    # previous hierarchy mode.
    effective_open_rows = []
    effective_selection = None
    updates[2] = []
    updates[3] = None
    should_render_table = True
    should_render_detail = True

elif triggered & table_and_detail_inputs:
    should_render_table = True
    should_render_detail = bool(effective_selection)

elif triggered & detail_only_inputs:
    should_render_detail = bool(effective_selection)

else:
    raise PreventUpdate
```

Do not set `should_render_table = True` in the `plot-measure` branch. Plot measure, plot component, and detail tenor view do not change Risk Explorer rows.

The example deliberately uses a simple reset policy for Promotion and Region. A more advanced implementation may keep separate open-row and selection state for each hierarchy mode, or prune keys against the new hierarchy. It must not retain a selected row key that no longer exists in the visible table.

### Step 5: verify the row-action branch

The row-action branch must adopt the open-row payload before rendering. It should contain this logic:

```python
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
```

If the code only assigns `updates[2] = effective_open_rows` without first replacing `effective_open_rows` from `opened`, it performs an expensive render while preserving the old row state.

### Step 6: pass the normalized values to the table renderer

The call should be structurally similar to:

```python
main_grid, alt_grid = render_active_risk_table(
    active_risk_type=active_risk_type,
    ir_family=normalized_ir_family,
    data_revision=data_revision,
    table_view=table_view,
    dimension=dimension,
    underlying_sort_metric=effective_underlying_sort_metric,
    splits=effective_splits,
    expanded_metrics=effective_expanded_metrics,
    credit_view=credit_view,
    credit_measure=credit_measure,
    credit_multi_metric=credit_multi_metric,
    alt_metric=alt_metric,
    open_rows=effective_open_rows,
    dimension_values=dimension_values,
    promotion_enabled=effective_promotion_enabled,
    region_enabled=effective_region_enabled,
)
```

The signature of `render_active_risk_table` and its call to `risk_generation_state` must accept the same values.

### Step 7: keep filtered promotion in one place

Promotion must be calculated from the filtered frame, once, inside the filtered-data stage:

```text
prepared dashboard frame
  -> selected Risk Type / IR family
  -> Split and reporting-dimension filters
  -> recompute promotion on surviving rows
  -> cache filtered/promoted frame
  -> build hierarchy
```

Do not recompute promotion independently in:

- `render_active_risk_table`;
- `build_risk_table`;
- `build_tree_rows`;
- the sorting helper.

Those layers should consume the already-promoted frame.

The Promotion switch should normally choose whether to display the promotion hierarchy. It should not rerun raw-data preparation or the financial pipeline.

## Phase 2: give toggle clicks immediate visual feedback

The current server pattern can require multiple sequential requests:

```text
button click
  -> server callback changes Store
  -> Store triggers table callback
  -> separate server callback updates label/class
```

Keep pandas/table rendering server-side, but move the tiny button-to-Store transition and label/class synchronization to clientside callbacks.

Before registering these clientside callbacks, delete the existing server callbacks that own the same Outputs:

- the server callback that writes `promotion-toggle-store.data`;
- the server callback that writes Promotion button `disabled`, `children`, `title`, `aria-pressed`, and `className`;
- the equivalent two Region callbacks.

Dash does not allow two normal callbacks to own the same Output. Do not leave the old server callbacks registered alongside these replacements.

Example for Promotion:

```python
app.clientside_callback(
    """
    function(nClicks, currentValue) {
        if (!nClicks) {
            return window.dash_clientside.no_update;
        }
        return !Boolean(currentValue);
    }
    """,
    Output("promotion-toggle-store", "data"),
    Input("promotion-toggle", "n_clicks"),
    State("promotion-toggle-store", "data"),
    prevent_initial_call=True,
)
```

Then synchronize visible button properties in the browser:

```python
app.clientside_callback(
    """
    function(value) {
        const enabled = Boolean(value);
        const state = enabled ? "On" : "Off";
        const action = enabled ? "Off" : "On";
        return [
            false,
            `Promotion: ${state}`,
            `Underlying promotion is ${state}. Click to turn it ${action}.`,
            String(enabled),
            `data-source-toggle promotion-toggle ${enabled ? "is-on" : "is-off"}`,
        ];
    }
    """,
    Output("promotion-toggle", "disabled"),
    Output("promotion-toggle", "children"),
    Output("promotion-toggle", "title"),
    Output("promotion-toggle", "aria-pressed"),
    Output("promotion-toggle", "className"),
    Input("promotion-toggle-store", "data"),
)
```

Use the same design for Region.

This gives immediate browser feedback while the Store still triggers one server table render. It removes two trivial server callbacks from the critical path. It does not move financial or pandas calculations into JavaScript.

Reference: [Dash clientside callbacks](https://dash.plotly.com/clientside-callbacks).

## Phase 3: measure the actual bottleneck

Do not guess whether the delay is filtering, tree construction, network transfer, or browser rendering. Measure each part.

### Step 1: record server timings

Add temporary timings around the major stages:

```python
from time import perf_counter

started = perf_counter()
filtered = filtered_frame()
ctx.record_timing(
    "risk_filter",
    perf_counter() - started,
    "Risk filtering and promotion",
)

started = perf_counter()
component = build_risk_table(...)
ctx.record_timing(
    "risk_table",
    perf_counter() - started,
    "Hierarchy, ordering, and Dash components",
)
```

Also log:

```python
app.logger.info(
    "risk render trigger=%s rows=%d open_rows=%d sort=%s promotion=%s region=%s",
    sorted(ctx.triggered_prop_ids),
    len(filtered),
    len(effective_open_rows),
    effective_underlying_sort_metric,
    effective_promotion_enabled,
    effective_region_enabled,
)
```

If possible, also count the final visible `<tr>` rows.

### Step 2: inspect one browser request

Open browser developer tools and inspect the `_dash-update-component` request.

- High server wait / TTFB means Python or worker queueing is slow.
- Fast server response with a huge response body means serialization/network is the problem.
- Response completes quickly but the page freezes means React/DOM reconciliation is the problem.
- Request stays queued before starting means another callback or refresh occupies the worker.

Use Dash Dev Tools' Callback Graph to compare compute, network, and payload size.

Reference: [Dash Dev Tools](https://dash.plotly.com/devtools).

### Step 3: test cold and warm paths separately

For each of these interactions, record cold and repeat timings:

1. switch Risk to P&L sorting;
2. switch back to Risk;
3. turn Promotion on and off;
4. turn Region on and off;
5. open one row;
6. close one row;
7. change plot measure;
8. change detail tenor view;
9. change table view;
10. run while no refresh is active, then while a refresh is active.

If a repeated state is fast but a new state is slow, the cold component build/cache key is the main issue. If every repeat is slow, response serialization and DOM work remain significant even on cache hits.

## Phase 4: optimize `build_tree_rows`

This is the most important server-side optimization after callback correctness.

### Problem 1: rebuilding `open_set` recursively

The current function performs:

```python
open_set = set(open_rows or [])
```

inside every recursive call.

Build it once at the root and pass it through recursion.

Suggested signature:

```python
def build_tree_rows(
    frame: pd.DataFrame,
    columns: list[str],
    open_rows: list[str] | None,
    expanded_metrics: list[str] | None,
    level: int = 0,
    depth: int = 0,
    context: dict[str, str] | None = None,
    groups: list[str] | None = None,
    cell_builder=None,
    toggle_type: str = "row-toggle",
    cell_type: str = "risk-cell",
    aggregation_index=None,
    delegated_actions: bool = False,
    underlying_sort_metric: str | None = None,
    _open_set: frozenset[str] | None = None,
) -> list[html.Tr]:
    if _open_set is None:
        _open_set = frozenset(open_rows or [])
```

Use `_open_set` for membership and pass it to recursive calls. This is a safe minor cleanup, not the main performance claim. Repeated DataFrame slicing and repeated hierarchy-level discovery are normally much larger costs.

### Problem 2: scanning the same parent frame once per child

The current pattern is effectively:

```python
for value in ordered_unique(frame, group_column):
    scoped = tree_scope(frame, group_column, value)
```

If there are 100 children, this can scan the same parent frame 100 times.

Build one child-position index for the current parent. Using row positions avoids materializing a separate child DataFrame for every sibling at the same time:

```python
group_positions = {
    str(value): positions
    for value, positions in frame.groupby(
        group_column,
        sort=False,
        dropna=False,
        observed=True,
    ).indices.items()
}

ordered_values = ordered_unique(
    frame,
    group_column,
    underlying_sort_metric=underlying_sort_metric,
)

for value in ordered_values:
    if group_column == "display bucket" and value == "Other":
        # Preserve tree_scope's existing business rule: Underlyings that are
        # promoted elsewhere must not reappear beneath Other.
        scoped = tree_scope(frame, group_column, value)
    else:
        positions = group_positions.get(str(value))
        scoped = frame.iloc[positions] if positions is not None else None

    if scoped is None or scoped.empty:
        continue
    ...
```

If null labels are permitted, normalize the group key with the same helper used during data preparation so the dictionary and ordering list use identical labels.

Retain both special `Other` rules:

1. it sorts last;
2. its scope excludes Reported Underlyings promoted in another bucket.

Benchmark this indexed approach on production-sized data. A dictionary of child DataFrames can create a memory spike, which is why the example stores row-position arrays instead.

### Problem 3: repeatedly discovering visible hierarchy levels

`visible_tree_level()` can repeatedly scan the same scoped columns and call `unique()` while recursion walks the tree. Include this in profiling. In the compact hierarchy index, precompute which group levels are meaningful for each parent path so child rendering does not rediscover them on every render.

### Problem 4: recomputing sort ranks at the wrong scope

Do not create one global order for Risk, dRisk, and P&L. Sibling order depends on the current parent scope, and eagerly calculating all three metrics can make a cold render slower.

Memoize ranking lazily with a key equivalent to:

```text
filtered hierarchy key
+ parent row key
+ child group column
+ selected metric
```

Alternatively, preaggregate every hierarchy prefix once with Risk, dRisk, and P&L totals, then derive the selected metric's child order cheaply. The selected metric must not rerun filters, promotion, quote aggregation, or the financial pipeline.

The financial ranking is:

```python
abs(groupby(identity)[metric].sum())
```

not:

```python
groupby(identity)[metric].apply(lambda values: values.abs().sum())
```

The first ranks the magnitude of the net risk, which is the requested behavior.

Use a deterministic label tie-break so two equal magnitudes do not move unpredictably between renders.

## Phase 5: cache compact data, not complete Dash component trees

The current `_RiskDataCache.rendered(...)` stores full Python component trees. That can save Python construction time, but a cache hit can still require:

- serializing the whole component tree;
- sending the whole JSON response;
- React reconciling the returned tree.

It can also retain large objects and increase garbage-collection pressure.

Introduce a compact view-model cache instead.

### Recommended cache layers

```text
Layer 1: prepared dashboard frame
  key: data revision

Layer 2: filtered + promoted frame
  key: revision, Risk Type, IR family, Split, reporting filters

Layer 3: hierarchy/node aggregates
  key: Layer 2 key, table view, dimension, promotion mode, region mode,
       Credit view, Credit measure, Credit multi metric, alternate metric

Layer 4: visible flattened rows
  key: Layer 3 key, sort metric, open row keys, expanded metrics

Layer 5: Dash components
  construct only for the visible flattened rows
```

Promotion belongs in Layer 2 only when promotion columns genuinely change with the filtered population. Underlying sort belongs in Layer 4 because it changes order, not source rows. Do not add a display-only value to the filtered-frame key.

The exact layer can vary, but no value affecting visible rows may disappear from the final cache identity. The delegated-action token must be built from the same normalized visible-view identity.

Normalize order-insensitive selections before making cache keys:

```python
normalized_splits = tuple(sorted(set(splits or [])))
normalized_filters = tuple(
    (column, tuple(sorted(set(values or []))))
    for column, values in sorted(filters.items())
)
```

This prevents equivalent filter selections in different click order from creating unnecessary cache misses.

For one process, a bounded in-memory cache is acceptable. If deployment later uses multiple worker processes, use a shared external cache for view models; do not assume one worker can read another worker's in-memory snapshot.

Reference: [Dash performance and memoization](https://dash.plotly.com/performance).

## Phase 6: reduce browser payload and DOM size

If profiling shows thousands of visible rows or multi-megabyte callback responses, server optimizations alone are not enough.

Choose one of these:

1. render only currently open branches and keep the default-open set small;
2. paginate children under very large nodes;
3. add a `Show more` row after the first N children;
4. flatten the hierarchy into a virtualized community grid, or use Dash AG Grid after checking which hierarchy features require an Enterprise license;
5. keep detailed low-level rows in the detail panel instead of the main hierarchy.

Virtualization is the durable option when thousands of rows must remain available at once. A browser cannot make tens of thousands of nested `html.Tr`/`html.Td` elements instantaneous merely because pandas became faster.

Dash AG Grid Tree Data may require an Enterprise feature/license depending on the chosen implementation. A flat virtualized community grid, pagination, or `Show more` remains the no-license fallback.

Dash `Patch` is useful when changing a small property or subtree. A full sort changes most row positions, so `Patch` is not the primary solution for the Risk/dRisk/P&L selector.

Reference: [Dash partial property updates](https://dash.plotly.com/partial-properties).

## Should the monolithic callback be split?

Yes, but split only independent ownership. Do not create this serial chain:

```text
control -> server reducer -> Store -> server renderer
```

That adds another network round trip and blocks downstream rendering until the Store callback completes.

Recommended eventual ownership:

### Table callback

Owns:

- Risk type and IR family;
- table view and dimension;
- Split/reporting filters;
- Promotion and Region;
- sort metric;
- row expansion;
- metric expansion;
- `risk-grid` and `alt-risk-grid`.

### Detail callback

Owns:

- cell selection;
- plot measure;
- plot component;
- tenor view;
- `detail-panel` and detail control options.

### Unmapped drawer callback

Loads and renders unmapped data only when opened.

### Raw drawer callback

Loads and renders raw data only when opened.

Each callback should listen directly to the controls that affect its output. The callbacks should not form a server-side cascade.

Define a selection-reset contract when splitting them. Both table and detail callbacks must directly receive Risk type, table view, Promotion mode, and Region mode as Inputs. When one of those values invalidates hierarchy paths, the detail callback clears the old selection and returns the empty/default detail state in its own request. Do not rely on the table callback writing a Store that later triggers the detail callback.

This refactor is Phase 6 or later. First fix trigger routing and measure the current callback, because a single callback can be fast when it renders only the outputs that changed.

## Secondary improvements

These can help after the main rendering work, but none is a ten-second fix on its own.

### Install `orjson`

Dash uses `orjson` automatically when available. It can reduce serialization time for large callback payloads.

Add it to `requirements.txt`, rebuild, and compare measurements. Do this only after measuring the component payload because it will not fix recursive tree construction or browser DOM size.

### Enable response compression

Compression can reduce network transfer for large JSON responses. Confirm that the hosting layer does not already compress callback responses before adding another compression layer.

### Keep background callbacks for refresh work

Long connector/market/P&L refreshes can use background execution so a web worker remains responsive. Do not move click-to-sort into a background callback: it adds queue overhead and does not make the result instant.

Reference: [Dash background callbacks](https://dash.plotly.com/background-callbacks).

### Do not expect extra threads to accelerate one render

The repository currently uses one Gunicorn worker with multiple threads because the financial snapshot is process-local. More threads can serve concurrent work, but they do not make one CPU-heavy pandas/component render faster. Several simultaneous expensive renders can contend under the Python runtime.

Do not increase worker count until the process-local snapshot and cache design has been deliberately externalized or replicated safely.

## Tests to add

Add focused tests before changing the recursive algorithm.

### Callback routing tests

Verify:

1. changing sort triggers table rendering once;
2. changing Promotion triggers table rendering once;
3. changing Region triggers table rendering once;
4. changing plot measure does not call `render_active_risk_table`;
5. changing detail tenor view does not call `render_active_risk_table`;
6. changing a financial filter renders the table and refreshes an active detail;
7. changing a filter with no selected cell does not build an empty detail unnecessarily.

### Cache identity tests

Verify that view tokens differ for:

- Risk versus P&L sort;
- Promotion on versus off;
- Region on versus off.

Verify that equivalent Split/filter selections in different order produce the same normalized cache key.

### Ordering tests

Build a frame in which Risk, dRisk, and P&L each rank a different Underlying first. Include negative values to prove the implementation uses `abs(net sum)`.

Verify:

- deterministic alphabetical tie-break;
- `Other` remains last;
- ordering works for `display bucket`, `reported underlying`, and raw `underlying`;
- promotion is recomputed after filtering;
- sorting does not change totals.

### Tree tests

Verify:

- the passed open-row list is adopted by the reducer;
- opening one row adds only its visible descendants;
- closing it removes those descendants;
- the optimized grouped implementation returns the same row keys, labels, totals, and order as the old implementation.

### Performance guard

Use a deterministic synthetic frame and add a generous regression threshold around the pure view-model/tree stage. Avoid a fragile end-to-end cloud timing assertion.

Also report:

- filtered row count;
- visible hierarchy row count;
- serialized component size.

## Validation commands

Run the commands from the repository root:

```powershell
& '.\.venv\Scripts\python.exe' -m py_compile ui/s07_events.py ui/s04_components.py ui/s03_aggregate.py
& '.\.venv\Scripts\python.exe' -m pytest tests/s06_ui.py -q
& '.\.venv\Scripts\python.exe' -m ruff check ui/s07_events.py ui/s04_components.py ui/s03_aggregate.py tests/s06_ui.py
& '.\.venv\Scripts\python.exe' -m ruff format --check ui/s07_events.py ui/s04_components.py ui/s03_aggregate.py tests/s06_ui.py
git diff --check
```

Then perform a browser smoke test with a production-sized frame and inspect the `_dash-update-component` response size.

## Acceptance criteria

The work is complete when:

1. Promotion, Region, and sort respond on the first click.
2. The same component property is not duplicated as both Input and State.
3. Sort/Promotion/Region are included in the render token and cache identity.
4. Plot and tenor-detail changes do not rebuild the Risk table.
5. Financial refresh is not invoked by ordinary Risk Explorer interactions.
6. Row open/close uses the action payload and preserves valid state.
7. Promotion/Region changes cannot leave a selected or open row key from an invalid hierarchy path.
8. Filtered promotion is computed once from the filtered frame.
9. Risk/dRisk/P&L sorting changes order without changing totals.
10. Warm and cold timings, visible row count, and response size are recorded.
11. A normal view interaction is comfortably below one second on representative data, or the remaining delay is conclusively shown to be browser DOM size and addressed with virtualization/pagination.

## Common mistakes to avoid

- Do not make one property both Input and State.
- Do not put `detail-tenor-view.value` in the table-render trigger set.
- Do not rebuild the table when only plot measure/component changes.
- Do not omit Promotion, Region, or sort from `risk_generation_state`.
- Do not preserve a selection/open-row key across a hierarchy-mode change unless it has been validated against the new hierarchy.
- Do not register clientside toggle callbacks while the old server callbacks still own the same Outputs.
- Do not cache only by revision and assume the table settings are irrelevant.
- Do not recompute promotion inside recursive tree rendering.
- Do not replace `tree_scope(..., "Other")` with ordinary equality grouping; its exclusion rule prevents promoted identities from reappearing beneath `Other`.
- Do not rerun the financial refresh for a display interaction.
- Do not split the reducer into a serial Store-to-render server chain.
- Do not store a large DataFrame in `dcc.Store`; it is serialized through the browser.
- Do not assume caching a Dash component removes network and React costs.
- Do not use `sum(abs(rows))` when the business ranking is `abs(sum(rows))`.
- Do not let `Other` enter the normal promoted-bucket rank.
- Do not treat `allow_optional=True`, `allow_duplicate=True`, or more threads as performance fixes.
- Do not optimize around the corrupted pasted excerpt: remove duplicated callback Inputs and duplicated refresh code first if those lines exist in the actual file.

## Recommended delivery order

Use these small pull requests or commits:

1. **Callback correctness**: trigger sets, generation state, normalized values, row action, detail-only routing.
2. **Immediate feedback**: clientside Promotion/Region toggles and label synchronization.
3. **Instrumentation**: timing, row counts, cache hits, response-size measurement.
4. **Tree optimization**: shared open set, indexed grouping per parent, cached meaningful levels, and parent-scoped lazy sort ranks.
5. **View-model cache**: cache compact hierarchy data rather than full component trees.
6. **Payload control**: default-open limits, pagination, Show more, or virtualization.
7. **Secondary deployment tuning**: `orjson`, verified compression, refresh background execution where appropriate.

This order fixes the first-click bugs immediately, produces evidence about the real bottleneck, and avoids a risky rewrite before it is needed.
