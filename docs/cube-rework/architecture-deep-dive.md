# Cube / Rebirth architecture deep dive

This review covers the current `main` branch and the topics discussed: initialization, high-cardinality reporting dimensions, promotion ownership, PyArrow/Parquet history, Market visualization, Stock history, P&L history, wide Portfolio views, and Pyright.

## Executive recommendation

The app should move from:

> committed data → repeated reinterpretation inside callbacks → large Dash component trees

Towards:

> committed data + revision-owned indexes → bounded query/projection services → lazy rows and virtualized columns

The largest immediate win is promotion. The core pipeline already calculates a committed promotion classification during final P&L release. The Risk UI then discards that result and recalculates promotion for every unseen filter combination. Remove that duplicated UI calculation first.

The target architecture has six layers:

1. **Connectors** return strict source-owned frames.
2. **Refresh transaction** calculates Risk, Market and P&L.
3. **Revision finalization** calculates baseline promotion, validates outputs and commits one snapshot.
4. **History writer** publishes immutable Stock, Market, P&L and Risk Parquet partitions under one date commit.
5. **Query/index layer** serves current and historical slices without exposing full frames to callbacks.
6. **UI projections** render only visible rows and visible columns.

---

## 1. What is already strong

### Cold start

`ui/s09_factory.py` and `ui/s07_events.py` already follow the right principle: a manager-backed app becomes reachable before connector I/O, and the browser-triggered startup coordinator owns the first writer. Keep this.

### Transactional refresh

`core/s02_pipeline.py` stages Risk, Market, P&L, mapping, thresholds, search catalogues and release validation before one snapshot publication. If refresh fails, the previous successful snapshot remains usable. Keep this transaction boundary.

### Search catalogue

`core/s03_search.py` already demonstrates the correct high-cardinality pattern:

- immutable row-position maps per revision;
- pre-normalized search labels;
- bounded option search;
- selected-value retention outside the current search window;
- independent position and quote grains.

The five reporting-dimension filters should reuse this model.

### Lazy Stock hierarchy

`core/s07_stock.py` computes only visible descendants. Closed branches do not allocate, aggregate or serialize deeper levels. The Stock tests also enforce a bounded payload for 10,000 source rows. This is a good model for Risk and P&L hierarchy services.

### Archive contracts

`core/s11_risk_archive.py` writes to a temporary date leaf, validates schemas and uniqueness, hashes files, writes `_SUCCESS`, then atomically publishes the date. This commit protocol is more valuable than the current CSV format and should survive the Parquet migration.

### Structural typing

`ui/s01_contracts.py` already has runtime-checkable Protocols for managers, snapshots, frame reads, history loaders and repositories. Pyright should extend this design rather than invent a new one.

---

## 2. Promotion rework

### Current behavior

Promotion is calculated twice:

1. `_apply_validated_thresholds` in `core/s02_pipeline.py` calculates the committed classification during final release.
2. `_RiskDataCache.filtered` in `ui/s07_events.py` calls `recompute_filtered_promotion` for every new UI filter key.

The second operation repeats:

```text
group Risk / dRisk / P&L
→ compare with thresholds
→ calculate score and reasons
→ assign Display Bucket
→ merge back to filtered position rows
```

The cache helps only when the exact same filter combination is revisited.

### Target behavior

Create first-class policy and result objects:

```python
@dataclass(frozen=True)
class PromotionPolicy:
    name: str
    activities: tuple[str, ...]
    keys: tuple[str, ...] = (
        "Risk Type",
        "Risk Greek",
        "Reported Underlying",
    )


@dataclass(frozen=True)
class PromotionSnapshot:
    revision: int
    policy: PromotionPolicy
    calculated_at: datetime
    index: pd.DataFrame
```

Finalization becomes:

```text
mapped position rows
       ↓
attach validated thresholds
       ↓
select configured promotion activities
       ↓
group once by promotion keys
       ↓
calculate score / reason / bucket
       ↓
attach classification to the full dashboard
       ↓
commit
```

Ordinary UI interaction becomes:

```text
filter rows → render
```

No promotion groupby and no promotion merge.

### Activity basis

The configured activities define the **calculation universe**, not the rows that receive the result. For example, promotion can be calculated using `Rates`, `Credit`, and `FX`, then attached by Risk Type + Risk Greek + Reported Underlying to the complete dashboard.

### Manual recalculation

Add:

```text
Promotion: Committed baseline
[ Recalculate for current view ]
```

The explicit action creates a session-scoped immutable promotion generation. Keep only a small token and scope in the browser; keep the promotion index server-side.

Changing filters afterwards does not silently rerun it. Instead show:

```text
Promotion: Custom view snapshot
Basis: Activity=Rates, Portfolio=BOOK-104
Calculated against revision 218
Current filters differ from the basis
```

The user can recalculate or reset to the committed baseline.

### Top Book

Top Book should consume the active promotion generation. It should remain closed and have no calculated children until opened. Do not calculate default Top Book expansion during `build_layout`.

### Stock promotion

Stock promotion is intentionally different. It is a user-selected comparison threshold based on the selected current Stock date and current filters. Keep it view-local. Do not persist a Stock promotion bucket in history.

---

## 3. Initialization

### Current warm-page work

Once a revision exists, `build_layout` currently:

- enumerates every option for all five reporting filters;
- selects and prepares the initial Risk Type;
- recomputes promotion for it;
- builds the initial Risk hierarchy;
- builds the open Aggregate P&L table;
- computes Top Book default open rows despite Top Book being closed;
- serializes the complete Dash tree.

### Recommended order

#### P0 — remove filtered promotion

This reduces both startup and every new filter combination without changing the user interface.

#### P0 — remove closed Top Book work

Initialize the Top Book state empty. Calculate its useful default expansion only on open.

#### P1 — bounded reporting-filter search

Add a `DimensionCatalog` beside `SearchCatalog`:

```python
catalog.search_dimension(
    column="portfolio",
    search_value="rates",
    limit=100,
    include=("BOOK-100",),
)
```

All values remain selectable; the browser receives only a bounded search slice.

#### P1 — visible-row-budget expansion

Use a total initial visible-row budget rather than one arbitrary branch threshold:

```text
budget = 150 rows
IR Delta estimated 18 → open
IR Gamma estimated 26 → open
IR Vega estimated 240 → closed
```

#### P1/P2 — staged warm-page hydration

Keep Aggregate P&L open in the route response, but allow the main Risk tree to hydrate in the first mounted callback:

```text
route response
├─ controls
├─ bounded filters
├─ always-open Aggregate P&L
├─ stable empty Risk grid
└─ closed empty Top Book

first Risk callback
└─ visible Risk hierarchy
```

This improves first paint. It is not a substitute for reducing total work.

---

## 4. Current-snapshot indexing

If promotion removal is not sufficient, move from full filtered-frame caches to a revision-owned positional index.

```python
@dataclass(frozen=True)
class RiskRevisionIndex:
    frame: pd.DataFrame
    metric_values: np.ndarray
    filter_postings: Mapping[str, Mapping[str, np.ndarray]]
    hierarchy_codes: Mapping[str, np.ndarray]
    dimension_values: Mapping[str, tuple[str, ...]]
```

Each filter value owns sorted `int32` row positions.

```text
Portfolio = A or B  → union postings A and B
Category = Rates    → intersect Rates postings
Exclude Legacy      → subtract Legacy postings
```

This follows the design already used by `SearchCatalog`.

Prepare helper values such as `rows=1` and `abs pl` once per revision instead of once per filter.

### Cache data, not large components

Prefer caching:

- row-position selections;
- grouped numeric matrices;
- promotion indexes;
- visible hierarchy records.

A fixed cache of 24 component trees does not meaningfully bound memory when one tree can be far larger than another. Use a byte-aware cache if component caching remains necessary.

---

## 5. The 500-Portfolio view

Do not impose a 60-column limit.

There are two separate costs:

1. calculating a 500-bucket matrix;
2. serializing and mounting 500 columns.

### Indexed aggregation

The wide SplitVA path currently performs a pandas groupby by selected dimension for each visible hierarchy node. Factorize Portfolio once and use NumPy reductions:

```python
def pivot_values(
    positions: np.ndarray,
    dimension_codes: np.ndarray,
    metric_values: np.ndarray,
    dimension_count: int,
) -> np.ndarray:
    return np.bincount(
        dimension_codes[positions],
        weights=metric_values[positions],
        minlength=dimension_count,
    )
```

For Aggregate P&L, group once by:

```text
Risk Type × Risk Greek × selected dimension
```

and pivot once rather than running another groupby for each expanded row.

### Virtualized renderer

Use Dash AG Grid only for high-cardinality matrix views:

- pinned left hierarchy/index column;
- all 500 logical Portfolio columns;
- column virtualization;
- current visible hierarchy rows only;
- column search/jump;
- optional saved column state.

Keep the existing server-side hierarchy reducer; send flattened visible rows. Enterprise Tree Data is not required.

Column virtualization reduces DOM work but not necessarily JSON payload. If benchmarks still show excessive payload, add a horizontal server column window. This keeps all 500 values reachable without sending them all on every interaction.

---

## 6. HistoryRepository and PyArrow/Parquet

### One service, separate grains

```python
HistoryDataset = Literal[
    "market",
    "stock",
    "pnl",
    "risk",
    "portfolio_authority",
]


class HistoryRepository(Protocol):
    def available_dates(
        self,
        dataset: HistoryDataset,
    ) -> tuple[date, ...]: ...

    def read_dates(
        self,
        dataset: HistoryDataset,
        dates: Sequence[date],
        *,
        columns: Sequence[str] | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> pd.DataFrame: ...

    def read_range(...): ...
```

Do not combine Market, Stock, P&L and Risk into one nullable super-table.

### Physical layout

```text
history/
  commits/
    market_date=2026-08-20.json

  market/market_date=2026-08-20/part-*.parquet
  stock/stock_date=2026-08-20/part-*.parquet
  pnl/market_date=2026-08-20/part-*.parquet
  risk/market_date=2026-08-20/part-*.parquet
  portfolio_authority/effective_date=2026-08-20/part-*.parquet
```

Partition by date only at first. Do not partition by Portfolio, Underlying, Category, CPTY or another high-cardinality identity.

### Write behavior

A scheduled run writes unique immutable files. It does not append bytes to an existing Parquet file.

```python
ds.write_dataset(
    table,
    base_dir=history_root / "market",
    format="parquet",
    partitioning=["market_date"],
    partitioning_flavor="hive",
    basename_template=f"revision-{revision}-{{i}}.parquet",
    existing_data_behavior="overwrite_or_ignore",
)
```

### Commit protocol

Arrow Dataset is not transactional. Preserve the current archive discipline:

1. write all date partitions under a unique staging root;
2. validate Arrow schemas and financial uniqueness keys;
3. record files, row counts, sizes and hashes;
4. publish files;
5. publish the commit manifest last;
6. readers discover dates only from valid manifests.

On local storage, use atomic rename. On object storage, use immutable file names and let the manifest be the visibility pointer.

### Query behavior

PyArrow should reduce data before pandas sees it:

```text
partition pruning
+ row predicate pushdown
+ column projection
       ↓
small Arrow Table
       ↓
.to_pandas()
       ↓
existing aggregation / Plotly code
```

Do not rewrite the P&L engine in Arrow initially.

---

## 7. Market History by dimensionality

Use `ProductSpec.axes` from `core/s02_pipeline.py` rather than guessing shape from Risk Greek labels.

### Zero tenor axes — FX Spot

Natural view:

- Date × Current line;
- optional Open overlay;
- Current−Open move;
- Date A / Date B summary.

### One tenor axis — IR Delta

Natural views:

1. Date A and Date B curves;
2. date × tenor heatmap;
3. B−A curve;
4. optional date × tenor 3-D surface;
5. click a tenor for its exact history.

The heatmap is the better default historical overview. Three-dimensional rendering is secondary exploration.

### Two tenor axes — IR Vega

IR Vega has four dimensions:

```text
Date
Tenor Option
Tenor Swap
Value
```

Keep the two spatial axes in a heatmap and make time interactive:

```text
Date A
Date B
[ A ] [ B ] [ B−A ] [ % Change ] [ 3-D at selected date ]
```

Click an option×swap cell to display its exact quote history through time.

Missing cells remain blank, never zero. Percentage change should mask near-zero Date A values.

---

## 8. Stock rework

### Keep the current business logic

The current Stock comparison correctly:

- validates an exact raw schema;
- rejects duplicate identities;
- performs a one-to-one full outer comparison;
- exposes Added, Removed, Changed and Unchanged;
- computes Quantity and Market Value changes;
- applies one explicit Portfolio mapping authority;
- lazily renders hierarchy descendants.

The data source should change, not the core comparison rules.

### Archive raw Stock daily

```text
GetStock(T)
    ↓
validate
    ↓
write stock_date=T Parquet partition
    ↓
commit date
```

Persist only facts:

```text
Stock Date
CRDS
CPTY
Portfolio
Instrument
Currency
Quantity
Market Value
```

Do not persist deltas, Added/Removed, mapping results or promotion.

### Page query

```text
history.read_dates("stock", [prior, current])
       ↓
compare_stock_snapshots()
       ↓
mapping authority
       ↓
filters
       ↓
view-local promotion threshold
       ↓
lazy hierarchy
```

Date selectors should expose only committed Stock dates.

### Mapping authority

Archive the Portfolio registry by effective date. Keep the current default of mapping both legs using the newer selected date, but display it explicitly. Later, offer:

- newer selected date;
- each date as historically classified;
- current registry.

### Drill-through

Click a Stock, CRDS or hierarchy node to show Market Value and Quantity through time.

---

## 9. P&L rework

### Preserve current semantics

Keep:

- Aggregate P&L always open;
- Activity as the default view;
- independent P&L filters;
- lazy SOG and Portfolio editors;
- Colossus/Predict separation;
- Daily, MTD, YTD and custom ranges;
- absent observations remaining absent rather than becoming zero.

### Current scaling issue

The current historical reader walks date leaves, validates/projects them, concatenates the complete history into pandas and caches the full frame in each worker.

### Target

At archive time:

```text
official Risk snapshot
+ Colossus
+ Portfolio authority
       ↓
project canonical PL_HISTORY_COLUMNS once
       ↓
write pnl/market_date=T partition
```

Keep raw Risk and Colossus as rebuild authority.

At query time, load only the selected range, types, filters and columns.

### Domain distinction

Market and Stock are states, so `B−A` is a natural movement. Daily P&L is a flow. Date A and Date B primarily bound a history range and cumulative period; subtracting two daily P&L values should not be labelled as a position movement.

---

## 10. Pyright

Pyright does not improve runtime speed. It makes the restructuring safer.

### Rollout

Add `pyright` to development dependencies and create `pyproject.toml`:

```toml
[tool.pyright]
include = [
  "core",
  "adapters",
  "feeds",
  "ui",
  "pages",
  "s01_app.py",
  "s02_config.py",
]
typeCheckingMode = "standard"
reportImportCycles = "warning"
reportUnnecessaryTypeIgnoreComment = "warning"

strict = [
  "core/s12_history.py",
  "core/s13_promotion.py",
  "adapters/s09_history_parquet.py",
]
```

Use standard mode repository-wide and strict mode first on new boundaries.

### Typed callback payloads

Create dataclasses or TypedDicts for:

- `HistoryQuery`;
- `HistoryCommit`;
- `PromotionGeneration`;
- `RiskViewContext`;
- `StockCacheToken`;
- saved-view requests;
- date-selection Stores.

Keep callbacks thin:

```text
Dash values → parser → typed pure reducer → typed result → Dash outputs
```

### What Pyright helps catch

- missing Optional guards;
- Protocol implementations missing methods;
- stale adapter signatures;
- wrong tuple shapes in multi-output helpers;
- malformed Store dictionary keys;
- scalar strings used as filter sequences;
- impossible Literal frame names;
- import cycles.

It cannot validate DataFrame columns or financial uniqueness; retain runtime validation and tests.

---

## 11. Proposed modules

```text
core/s12_history.py
  dataset names, schemas, query objects, HistoryRepository Protocol

core/s13_promotion.py
  PromotionPolicy, PromotionIndex, classification math

adapters/s09_history_parquet.py
  PyArrow Dataset reader/writer and commit manifests

jobs/archive_daily_cube.py
  Risk/Market/P&L/Stock/Portfolio atomic capture

ui/s15_history_market.py
  dimensional Market-history figures

ui/s16_history_stock.py
  Stock history query/view helpers

ui/s17_dimension_catalog.py
  bounded reporting-filter catalogue

ui/s18_wide_grid.py
  AG Grid representation for wide pivots
```

Do not combine a broad file renaming exercise with the first behavioral changes.

---

## 12. File-by-file change map

| Current file | Recommended change |
|---|---|
| `core/s02_pipeline.py` | Separate threshold attachment from promotion policy; publish promotion metadata/index |
| `ui/s03_aggregate.py` | Stop ordinary filtered promotion recomputation; retain a manual helper temporarily |
| `ui/s07_events.py` | Cache filtered row selections without promotion; add promotion-generation state |
| `ui/s04_components.py` | Remove eager Top Book defaults; add promotion status/actions; optionally hydrate Risk after mount |
| `ui/s09_factory.py` | Inject HistoryRepository and DimensionCatalog |
| `core/s11_risk_archive.py` | Generalize manifest logic into Parquet history writer; retain legacy readers during migration |
| `adapters/s05_stock.py` | Keep source boundary; scheduled archive becomes the main historical caller |
| `core/s07_stock.py` | Keep exact comparison and lazy hierarchy; add historical query helpers |
| `ui/s10_stock.py` | Read committed dates and archived snapshots |
| `core/s04_pl.py` | Keep canonical P&L history contract |
| `ui/s08_plevents.py` | Replace full-history cache with range/filter repository queries |
| `ui/s12_plhistory.py` | Mostly retain; consume query-sized frames |
| `core/s03_search.py` | Reuse its indexing pattern for DimensionCatalog |
| `requirements.txt` | Add `pyarrow` and `dash-ag-grid` |
| `requirements-dev.txt` | Add `pyright` |
| `pyproject.toml` | Add Pyright and consolidate Ruff settings |
| `s03_publish.py` | Configure external/persistent history storage explicitly |
| `tools/s03_archive_official_risk.py` | Forward to or replace with unified daily archive job |

---

## 13. Tests and performance budgets

### Semantic tests

- committed promotion remains fixed across normal filters;
- explicit custom recalculation changes classification;
- refresh clears or rebases custom promotion;
- Top Book uses the active promotion generation;
- Stock comparison remains one-to-one full outer;
- mapping basis is explicit;
- Market tenor ranks remain connector-owned;
- missing history dates are not manufactured as zero;
- incomplete Parquet partitions remain hidden.

### Performance measurements

Measure:

- warm Risk route construction;
- first Risk callback;
- serialized layout and callback bytes;
- visible hierarchy row count;
- browser scripting/layout time;
- filter callback p50/p95;
- 500-column Portfolio scrolling;
- Parquet fragments and bytes scanned;
- Stock two-date query;
- P&L range query;
- Market 0D/1D/2D query and figure construction.

### Synthetic regression sizes

- 250,000 Risk positions;
- 500 Portfolios;
- 5,000 Category/Sub Category values;
- 100 historical dates;
- 10,000 market quote cells per date;
- 0D, 1D and 2D Market products.

---

## 14. Migration safety

### Promotion

1. Preserve current committed promotion.
2. Remove only UI recomputation.
3. Update the regression that currently requires filtered reclassification.
4. Add explicit recalculation.
5. Add configured Activity basis.

This produces a measurable improvement before the history project begins.

### History

1. Introduce the repository interface with the existing legacy reader behind it.
2. Add Parquet dual-write.
3. Backfill committed leaves.
4. Compare row counts, keys and aggregate hashes.
5. Switch reads behind configuration.
6. Retain legacy fallback for one release window.
7. Remove legacy writes only after parity.

---

## Recommended sequence

1. Remove filtered promotion and closed Top Book initialization work.
2. Add promotion policy metadata and explicit custom recalculation.
3. Add bounded reporting-dimension search.
4. Add revision-owned positional filtering and indexed wide pivots.
5. Introduce HistoryRepository and Parquet commit writer.
6. Archive Stock and migrate the Stock page.
7. Add dimensional Market History.
8. Migrate P&L history to predicate-pushed range queries.
9. Replace wide matrix HTML with AG Grid.
10. Add Pyright standard mode and strict new modules.
11. Profile again before considering deeper pandas-to-Arrow current-snapshot changes.
