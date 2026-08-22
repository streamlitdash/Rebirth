# Rebirth Revision Updates

> Consolidated Markdown archive of the Rebirth/Cube redesign documents committed across the redesign branches.
>
> **Reading rule:** this is historical. Later revisions supersede conflicting earlier statements. The latest normative correction is V3.2.

- Source Markdown paths represented: **19**
- Unique Markdown contents embedded: **19**
- Source branches: `docs/cube-rework-deep-dive`, `rebirth-v2`, `docs/rebirth-v3-spec`

## Consolidated source index

1. [`docs/cube-rework-deep-dive:docs/cube-rework/README.md`](#source-001)
2. [`docs/cube-rework-deep-dive:docs/cube-rework/architecture-deep-dive.md`](#source-002)
3. [`docs/cube-rework-deep-dive:docs/cube-rework/data-download-and-parquet-guide.md`](#source-003)
4. [`docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-blueprint/README.md`](#source-004)
5. [`docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/README.md`](#source-005)
6. [`docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/acceptance-test-matrix.md`](#source-006)
7. [`docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/fake-data-and-promotion-scenarios.md`](#source-007)
8. [`docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/migration-runbook.md`](#source-008)
9. [`docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/rebirth-v2-redesign-and-migration-guide.md`](#source-009)
10. [`docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/ui-design-system.md`](#source-010)
11. [`docs/rebirth-v3-spec:docs/rebirth-v3/01_product_ui_v3.md`](#source-011)
12. [`docs/rebirth-v3-spec:docs/rebirth-v3/02_viability_and_simplification.md`](#source-012)
13. [`docs/rebirth-v3-spec:docs/rebirth-v3/04_data_pipeline_contracts.md`](#source-013)
14. [`docs/rebirth-v3-spec:docs/rebirth-v3/README.md`](#source-014)
15. [`docs/rebirth-v3-spec:docs/rebirth-v3/REVISION_V3.md`](#source-015)
16. [`docs/rebirth-v3-spec:docs/rebirth-v3/v3.1/IMPLEMENTATION_CHECKLIST.md`](#source-016)
17. [`docs/rebirth-v3-spec:docs/rebirth-v3/v3.1/README.md`](#source-017)
18. [`docs/rebirth-v3-spec:docs/rebirth-v3/v3.1/REVISION_V3_1.md`](#source-018)
19. [`docs/rebirth-v3-spec:docs/rebirth-v3/v3.2/REVISION_V3_2.md`](#source-019)

---

<a id="source-001"></a>

# Source 001 — `docs/cube-rework-deep-dive:docs/cube-rework/README.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/README.md`
- **SHA-256:** `dd999fc729bfffbb9326f1cef4660d0d57d9ab659c2a99f368c4b04703e25278`

---

# Cube rework analysis

This directory contains the architecture review, implementation specifications and interactive examples prepared from the current Rebirth codebase.

## Rebirth V2 implementation specification

The latest detailed redesign is here:

- **[Rebirth V2 redesign and migration guide](rebirth-v2-design/rebirth-v2-redesign-and-migration-guide.md)** - product/UI redesign, Top Promotions, PivotSpec Risk Explorer, Data page, V1-to-V2 mapping, migration phases and definition of done.
- [Proposed V2 target tree](rebirth-v2-design/target-tree.txt)
- [V1-to-V2 file map and deletion gates](rebirth-v2-design/v1-to-v2-file-map.csv)
- [UI design system](rebirth-v2-design/ui-design-system.md)
- [Fake-data and promotion scenarios](rebirth-v2-design/fake-data-and-promotion-scenarios.md)
- [Migration runbook](rebirth-v2-design/migration-runbook.md)
- [Acceptance test matrix](rebirth-v2-design/acceptance-test-matrix.md)

The companion assistant delivery includes the complete 53-page reviewed PDF, DOCX source, 235-file ownership catalogue, diagrams and one ZIP bundle.

## Launch the prototypes in your browser

No download or local server is required. These links render the HTML stored on this GitHub branch with the correct browser content type:

- **[Launch the proposed full Cube application](https://raw.githack.com/streamlitdash/Rebirth/docs/cube-rework-deep-dive/docs/cube-rework/proposed-cube-full-app-prototype.html)**
- **[Launch the IR Vega 3-D date-slider prototype](https://raw.githack.com/streamlitdash/Rebirth/docs/cube-rework-deep-dive/docs/cube-rework/ir-vega-3d-date-slider-prototype.html)**
- **[Launch the architecture interaction prototype](https://raw.githack.com/streamlitdash/Rebirth/docs/cube-rework-deep-dive/docs/cube-rework/interactive-prototype.html)**

The GitHub code viewer intentionally shows HTML source and does not execute JavaScript. The launch links above read the files from this repository and serve them with an HTML content type. The computer must be online because the prototypes load Plotly and, for the full application, its compressed payload from this branch.

## Files

- [Architecture deep dive](architecture-deep-dive.md) - current-state findings, target architecture, file-by-file changes, migration sequence, and performance plan.
- [Full proposed Cube application](proposed-cube-full-app-prototype.html) - integrated fake-data prototype covering Risk, Stock, P&L, Statics, promotion, history, wide Portfolio views, and refresh behavior.
- [IR Vega 3-D date-slider prototype](ir-vega-3d-date-slider-prototype.html) - draggable and playable double-tenor surface history.
- [Single-file historical tenor Dash app](historical-tenor-surface-app.py) - runnable app for dated CSV snapshots with Risk Type/Risk Greek/Underlying controls and axis-aware history views.
- [Interactive architecture prototype](interactive-prototype.html) - synthetic examples for initialization, promotion ownership, Market dimensionality, Stock history, P&L history, wide Portfolio views, and Pyright.
- [Data download and Parquet guide](data-download-and-parquet-guide.md) - source schemas, daily archive layout, and PyArrow conversion/query examples.
- [Architecture overview](overview.svg) - compact target architecture diagram.
- [Market-history interaction](market-history.svg) - dimensional visualization model for zero-, one-, and two-tenor products.

## Updated recommendations

1. Stop recalculating promotion after every ordinary UI filter. Use the committed baseline classification, with an explicit **Recalculate current view** action that creates a separate session generation.
2. Replace Top Book with a flat ranked **Top Promotions** table and a side-by-side Promotion Summary card.
3. Replace hard-coded Risk Explorer hierarchy toggles with a hideable PivotSpec field sidebar for rows, columns, values, filters, sorting and display settings.
4. Keep Aggregate P&L open and financially equivalent while bounding high-cardinality column payloads.
5. Replace full reporting-filter option payloads with bounded server-side search.
6. Preserve all Portfolio columns; expose them through a server-side column viewport rather than a hard cap or AG Grid.
7. Introduce a common `HistoryRepository` backed by PyArrow/Parquet for Market, Stock, P&L, Risk, and dated Portfolio authority.
8. Keep Quick Market current-only and add a deep link into a dedicated **Data** page for historical Market, Stock, P&L and optional Risk analysis.
9. Drive Market-history visualization from `ProductSpec.axes`: flat line for zero axes, selected-tenor line and date-tenor 3-D surface for one axis, and playable selected-date surfaces plus fixed-axis/A-B modes for two axes.
10. Archive raw Stock daily, retain the full-outer two-date comparison, and keep Stock promotion view-local.
11. Add Pyright and architecture/performance tests around the new ports, pipeline, page packages, history and promotion boundaries.

All prototypes use synthetic data. They do not connect to financial sources or modify application state.


---

<a id="source-002"></a>

# Source 002 — `docs/cube-rework-deep-dive:docs/cube-rework/architecture-deep-dive.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/architecture-deep-dive.md`
- **SHA-256:** `0cfd515101aedd703a0132dee0e3b0b404e8d1e637d871dcb2cc1d7de25362b4`

---

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


---

<a id="source-003"></a>

# Source 003 — `docs/cube-rework-deep-dive:docs/cube-rework/data-download-and-parquet-guide.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/data-download-and-parquet-guide.md`
- **SHA-256:** `01aa3fe695c441de0a6ab60f33ba7722b6352408450c425e618d39253cef3c0f`

---

# Historical data download and Parquet guide

This guide describes how to download and store the historical Market, Stock, Predict, Colossus and Portfolio data for the proposed Cube architecture.

## Main rule

Download **one complete, long-form snapshot per dataset per business date**, then write each snapshot to a date-partitioned Parquet dataset.

Do not:

- append forever to one giant CSV;
- pivot tenors into columns;
- mix Market, Stock, Predict and Colossus into one table;
- replace missing values with zero;
- write a pandas index column.

Physically, each date has its own Parquet partition. Logically, PyArrow reads each dataset directory as one table.

## Recommended directory structure

```text
history/
├── commits/
│   ├── market_date=2026-08-19.json
│   └── market_date=2026-08-20.json
├── market/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
├── stock/
│   └── stock_date=2026-08-20/
│       └── snapshot-....parquet
├── pnl/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
├── risk/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
├── colossus_raw/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
└── portfolio_authority/
    └── effective_date=2026-08-20/
        └── snapshot-....parquet
```

Adding a new day means adding a new date partition. It does not mean editing the previous day's file.

---

## 1. Market data

### Download grain

Download one row per exact raw quote cell:

```text
market_date
source_type
risk_type
risk_greek
underlying
tenor_swap
tenor_option
tenor_swap_order
tenor_option_order
open
current
move
market_status
market_data_status
```

Recommended unique key:

```text
market_date
+ source_type
+ risk_type
+ risk_greek
+ underlying
+ tenor_swap
+ tenor_option
```

Recommended types:

| Column | Arrow type |
|---|---|
| `market_date` | `date32` |
| identity and status columns | `string` |
| tenor order columns | nullable `int32` |
| `open`, `current`, `move` | nullable `float64` |

### Tenor shapes

Use one schema for every Market product.

#### FX Spot: no tenor axes

```text
source_type         fx/delta
risk_type           FX
risk_greek          Delta
underlying          EUR/USD
tenor_swap          Spot
tenor_option        N/A
tenor_swap_order    null
tenor_option_order  null
```

#### IR Delta: one tenor axis

```text
source_type         ir/delta
risk_type           IR
risk_greek          Delta
underlying          USD-SOFR
tenor_swap          5Y
tenor_option        N/A
tenor_swap_order    4
tenor_option_order  null
```

#### IR Vega: two tenor axes

```text
source_type         ir/deltavega
risk_type           IR
risk_greek          DeltaVega
underlying          USD
tenor_swap          10Y
tenor_option        6M
tenor_swap_order    5
tenor_option_order  2
```

This lets the Market History UI choose naturally:

```text
0 meaningful tenor axes → time-series line
1 tenor axis            → curves, heatmap, optional 3-D surface
2 tenor axes            → surface heatmap, A/B/difference, cell history
```

### Market rules

- Preserve connector-owned tenor orders.
- Store unavailable quotes as null, not zero.
- Treat Open and Current as authoritative.
- Validate `Move = Current - Open` when both quote legs exist.
- Reject duplicate quote identities.
- Keep the data long-form.

Correct:

```text
date | underlying | tenor_swap | tenor_option | current
```

Incorrect:

```text
date | 1Y | 2Y | 5Y | 10Y
```

### Current CSV-compatible header

```csv
Source Type,Risk Type,Risk Greek,Underlying,Tenor Swap,Tenor Option,Tenor Swap Order,Tenor Option Order,Market Date,Open,Current,Move,Market Status,Market Data Status
```

---

## 2. Stock data

### Download grain

Download raw Stock facts:

```text
stock_date
crds
cpty
portfolio
instrument
currency
quantity
market_value
```

Recommended types:

| Column | Arrow type |
|---|---|
| `stock_date` | `date32` |
| text identities | `string` |
| `quantity` | `float64` |
| `market_value` | `float64` |

The current source identity is:

```text
CRDS + CPTY + Portfolio + Instrument + Currency
```

Reject duplicates at that grain rather than silently aggregating them.

### Do not persist comparison outputs

Do not store:

```text
Prior Quantity
Current Quantity
Quantity Change
Prior Market Value
Current Market Value
Market Value Change
Stock Change
Promotion Bucket
```

Those are derived after the user selects Date A and Date B:

```text
Stock A + Stock B
        ↓
full outer join
        ↓
Added / Removed / Changed / Unchanged
        ↓
Quantity and Market Value changes
```

### Current CSV-compatible header

```csv
CRDS,CPTY,Portfolio,Instrument,Currency,Quantity,Market Value
```

Add the selected Stock date before writing Parquet.

---

## 3. Raw Colossus data

Download:

```text
market_date
portfolio
underlying
risk_type
risk_greek
pl
```

Raw Colossus should not invent Product, Activity or Signoff Group.

Recommended unique key:

```text
market_date
+ portfolio
+ underlying
+ risk_type
+ risk_greek
```

Current CSV-compatible header:

```csv
Portfolio,Underlying,Risk Type,Risk Greek,PL
```

The date is added from the requested historical date or date partition.

---

## 4. Predict / Risk data

### Preferred route

Archive the complete committed Risk Explorer snapshot for the date.

At minimum, Predict history needs:

```text
market_date
portfolio
underlying
risk_type
risk_greek
product
pl
```

The full Risk archive should also retain:

```text
activity
signoff_group
category
sub_category
risk
drisk
tenor_swap
tenor_option
tenor orders
promotion fields
```

This is preferable to permanently storing a reduced `predicted.csv`, because the complete snapshot remains the rebuild and audit authority.

### Legacy minimum

Existing historical files may use:

```csv
Risk Type,Risk Greek,Underlying,Product,Book,PL
```

with:

```text
YYYY-MM-DD/
  histo.csv
  predicted.csv
```

This is acceptable for migration, but the new permanent history should use the complete Risk snapshot plus a canonical P&L history projection.

---

## 5. Historical Portfolio authority

Archive the Portfolio mapping by effective date:

```text
effective_date
portfolio
product
activity
signoff_group
category
sub_category
```

Current CSV-compatible header:

```csv
Portfolio,Product,Activity,SignoffGroup,Category,Sub Category
```

This is required so historical data is not silently classified using today's Portfolio registry.

Default Stock behavior should remain:

> Map both comparison legs using the newer selected Stock date.

The UI should display the mapping authority date explicitly.

---

## 6. Canonical P&L history dataset

After loading raw Predict/Risk, raw Colossus and historical Portfolio authority, write one combined `pnl` dataset:

```text
market_date
pnl_type
activity
signoff_group
category
sub_category
risk_type
risk_greek
underlying
product
portfolio
mapping_status
pl
```

`pnl_type` should be exactly:

```text
Predict
Colossus
```

Recommended unique key:

```text
market_date
+ pnl_type
+ signoff_group
+ risk_type
+ risk_greek
+ underlying
+ product
+ portfolio
```

Example:

```text
2026-08-20 | Predict  | Rates | SOG-A | Core | IR | IR | Delta | USD-SOFR | XVA | BOOK-001 | Mapped | 125000
2026-08-20 | Colossus | Rates | SOG-A | Core | IR | IR | Delta | USD-SOFR | XVA | BOOK-001 | Mapped | 119500
```

This canonical table is what the P&L History page should query. Keep raw Risk and Colossus so it can be rebuilt.

---

## 7. Minimum daily downloads

For every business date, obtain:

```text
YYYY-MM-DD/
├── market.csv
├── stock.csv
├── risk.csv or predicted.csv
├── colossus.csv
└── portfolio.csv
```

Recommended preference:

```text
full risk.csv > reduced predicted.csv
```

---

## 8. Export rules

Apply these rules to every dataset:

1. Use long-form rows.
2. Do not include formatted numbers such as currency symbols or thousands separators.
3. Do not write a pandas index.
4. Use null for unavailable values.
5. Use stable text labels.
6. Preserve source-owned tenor ranks.
7. Reject duplicate source identities.
8. Write one complete immutable partition per date.
9. Validate before publishing the date.
10. Publish the date commit manifest last.

---

## 9. Example PyArrow schemas

```python
import pyarrow as pa

MARKET_SCHEMA = pa.schema(
    [
        ("market_date", pa.date32()),
        ("source_type", pa.string()),
        ("risk_type", pa.string()),
        ("risk_greek", pa.string()),
        ("underlying", pa.string()),
        ("tenor_swap", pa.string()),
        ("tenor_option", pa.string()),
        ("tenor_swap_order", pa.int32()),
        ("tenor_option_order", pa.int32()),
        ("open", pa.float64()),
        ("current", pa.float64()),
        ("move", pa.float64()),
        ("market_status", pa.string()),
        ("market_data_status", pa.string()),
    ]
)

STOCK_SCHEMA = pa.schema(
    [
        ("stock_date", pa.date32()),
        ("crds", pa.string()),
        ("cpty", pa.string()),
        ("portfolio", pa.string()),
        ("instrument", pa.string()),
        ("currency", pa.string()),
        ("quantity", pa.float64()),
        ("market_value", pa.float64()),
    ]
)

PNL_SCHEMA = pa.schema(
    [
        ("market_date", pa.date32()),
        ("pnl_type", pa.string()),
        ("activity", pa.string()),
        ("signoff_group", pa.string()),
        ("category", pa.string()),
        ("sub_category", pa.string()),
        ("risk_type", pa.string()),
        ("risk_greek", pa.string()),
        ("underlying", pa.string()),
        ("product", pa.string()),
        ("portfolio", pa.string()),
        ("mapping_status", pa.string()),
        ("pl", pa.float64()),
    ]
)
```

---

## 10. Example date-partitioned writer

```python
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds


def write_daily_dataset(
    frame: pd.DataFrame,
    *,
    root: str | Path,
    partition_column: str,
    schema: pa.Schema,
    replace_date: bool = False,
) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")

    missing = [name for name in schema.names if name not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    table = pa.Table.from_pandas(
        frame.loc[:, schema.names],
        schema=schema,
        preserve_index=False,
        safe=True,
    )

    parquet_format = ds.ParquetFileFormat()
    write_options = parquet_format.make_write_options(compression="zstd")

    ds.write_dataset(
        table,
        base_dir=str(Path(root)),
        format=parquet_format,
        file_options=write_options,
        partitioning=[partition_column],
        partitioning_flavor="hive",
        basename_template=f"snapshot-{uuid4().hex}-{{i}}.parquet",
        existing_data_behavior=(
            "delete_matching" if replace_date else "overwrite_or_ignore"
        ),
    )
```

Use:

- `overwrite_or_ignore` with unique file names for new immutable data;
- `delete_matching` only when replacing a complete authoritative date partition.

---

## 11. Example reads

### IR Delta history

```python
from datetime import date

import pyarrow as pa
import pyarrow.dataset as ds

market = ds.dataset(
    "history/market",
    format="parquet",
    partitioning=ds.partitioning(
        pa.schema([("market_date", pa.date32())]),
        flavor="hive",
    ),
)

result = market.to_table(
    columns=[
        "market_date",
        "tenor_swap",
        "tenor_swap_order",
        "current",
    ],
    filter=(
        (ds.field("market_date") >= date(2026, 8, 1))
        & (ds.field("market_date") <= date(2026, 8, 20))
        & (ds.field("risk_type") == "IR")
        & (ds.field("risk_greek") == "Delta")
        & (ds.field("underlying") == "USD-SOFR")
    ),
)

delta_history = result.to_pandas()
```

### Two Stock dates

```python
stock = ds.dataset(
    "history/stock",
    format="parquet",
    partitioning=ds.partitioning(
        pa.schema([("stock_date", pa.date32())]),
        flavor="hive",
    ),
)

result = stock.to_table(
    filter=ds.field("stock_date").isin(
        [date(2026, 8, 19), date(2026, 8, 20)]
    )
)

stock_frame = result.to_pandas()

prior = stock_frame.loc[
    stock_frame["stock_date"].eq(date(2026, 8, 19))
].drop(columns="stock_date")

current = stock_frame.loc[
    stock_frame["stock_date"].eq(date(2026, 8, 20))
].drop(columns="stock_date")
```

The two resulting frames can go directly into the existing Stock comparison logic.

### P&L history

```python
pnl = ds.dataset(
    "history/pnl",
    format="parquet",
    partitioning=ds.partitioning(
        pa.schema([("market_date", pa.date32())]),
        flavor="hive",
    ),
)

result = pnl.to_table(
    columns=[
        "market_date",
        "pnl_type",
        "activity",
        "risk_type",
        "pl",
    ],
    filter=(
        (ds.field("market_date") >= date(2026, 8, 1))
        & (ds.field("market_date") <= date(2026, 8, 20))
        & (ds.field("activity") == "Rates")
    ),
)

pnl_history = result.to_pandas()
```

---

## Recommended daily workflow

```text
1. Download MarketBook
2. Download Stock
3. Obtain full Predict/Risk snapshot
4. Download Colossus
5. Download dated Portfolio mapping
6. Validate schemas and uniqueness keys
7. Build canonical P&L history rows
8. Write staged Parquet partitions
9. Verify row counts and checksums
10. Publish the date commit manifest
```

The minimum datasets required by the app are:

```text
market
stock
pnl
portfolio_authority
```

The recommended audited store also keeps:

```text
risk
colossus_raw
```

## Final recommendation

Use:

> **one logical dataset per domain, with one complete immutable Parquet partition per date.**

Do not use one giant appended file.


---

<a id="source-004"></a>

# Source 004 — `docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-blueprint/README.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/rebirth-v2-blueprint/README.md`
- **SHA-256:** `8c866f1fe3aa569bd96ed5bae6feb7c758c51b3653fcea706e12fea7e09afd00`

---

# Rebirth V2 blueprint bundle

Placeholder index. Detailed files are being uploaded in the same commit series.

---

<a id="source-005"></a>

# Source 005 — `docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/README.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/rebirth-v2-design/README.md`
- **SHA-256:** `a2fe0544d6fc6302074922d48513f2f230a3740836b426d916220e44d0f167b1`

---

# Rebirth V2 redesign and migration specification

This folder contains the GitHub review copy of the implementation contract for transforming the current Rebirth/Cube codebase into the proposed modular V2 architecture.

Start with:

- [`rebirth-v2-redesign-and-migration-guide.md`](rebirth-v2-redesign-and-migration-guide.md) - the complete product/UI decisions, V1-to-V2 architecture mapping, migration phases and definition of done.
- [`target-tree.txt`](target-tree.txt) - the exact proposed package tree, including the new Data page, flat Top Promotions and pivot workspace.
- [`v1-to-v2-file-map.csv`](v1-to-v2-file-map.csv) - old paths, new owners, transformation rules, phases and deletion gates.

Supporting specifications:

- [`ui-design-system.md`](ui-design-system.md)
- [`fake-data-and-promotion-scenarios.md`](fake-data-and-promotion-scenarios.md)
- [`migration-runbook.md`](migration-runbook.md)
- [`acceptance-test-matrix.md`](acceptance-test-matrix.md)

The accompanying assistant delivery also includes a reviewed 53-page PDF, the full 235-file ownership catalogue, diagrams, DOCX source and one ZIP bundle. Those binary/large artifacts are delivered directly rather than committed to this branch.

The key UI decisions are: retain Cube page logic and financial semantics; use boxed cards and responsive side-by-side panels; replace Top Book with a flat ranked Top Promotions table plus Promotion Summary; use a hideable PivotSpec field sidebar for Risk Explorer; keep Quick Market current-only; and move historical Market, Stock, P&L and optional Risk analysis into a dedicated Data page.


---

<a id="source-006"></a>

# Source 006 — `docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/acceptance-test-matrix.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/rebirth-v2-design/acceptance-test-matrix.md`
- **SHA-256:** `4a6d1ecad4af5a2e790e2d4675e3289708d0a03d33b289632e5cdd6df80e84a9`

---

# Acceptance test matrix

| Area | Required proof |
|---|---|
| Startup | Importing app performs no connector reads; shell paints before first refresh; JupyterHub proxy/service prefixes work. |
| Pipeline | Exact stage order; one writer; complete validation before commit; failure retains last-good revision. |
| Promotion | Baseline generated during refresh; ordinary filters do not recalculate; explicit recalculation creates session generation; flat ranking is deterministic. |
| Top Promotions | No hierarchy chevrons; one row per promotion identity; all reasons/ratios visible; opens identity in Explorer. |
| Pivot Explorer | Sidebar can hide/restore; rows/columns/values validate; no AG Grid; full data retained; bounded payload/DOM. |
| Filters | OR within a field, AND across fields; exclude mode; selected values retained outside search window; page state isolated. |
| Market current | Open-authoritative merge; missing quotes remain null; Move equals Current minus Open when both exist. |
| Market history | 0/1/2-axis modes; Play becomes Pause; custom A/B; WTD/MTD/YTD/1Y/5Y/All; outright and Move; stable camera/scale. |
| Data deep-link | Quick Market opens Data with exact Risk Type/Greek/Underlying and does not ask for Underlying again. |
| Stock | Full-outer comparison, mapping authority, view-local threshold, lazy hierarchy, source rows only on request. |
| P&L | Aggregate/send/editor parity; adjustment atomicity; Predict/Colossus states; missing history is not zero-filled. |
| History | Partition pruning, column projection, commit manifests, checksum validation, legacy/Parquet parity. |
| Page isolation | AST test rejects imports from one page package into another and rejects concrete adapters in pages. |
| Performance | Initial layout budget, filter latency, pivot payload budget, history query budget and playback clientside behavior. |
| Visual | Card boundaries, side-by-side layout, mobile stacking, no clipping, sticky headers, neutral colors. |


---

<a id="source-007"></a>

# Source 007 — `docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/fake-data-and-promotion-scenarios.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/rebirth-v2-design/fake-data-and-promotion-scenarios.md`
- **SHA-256:** `98f5e769b2347fe2163ac0c7eac89ca7a24a3804586554c580617ac219db5e4f`

---

# Fake data and promotion scenario specification

## Purpose

Fake data must exercise behavior, not merely make the page look busy. The generator is deterministic and ProductSpec-driven. It should create enough identities and dates to reveal performance and classification errors while remaining understandable during debugging.

## Recommended scale

- 36 to 60 Portfolios, not 500 by default;
- 8 to 12 Signoff Groups;
- 6 Activities and 6 Categories;
- 120 business dates for the standard fixture;
- optional performance profile with 500 Portfolios and larger quote counts;
- all 0-, 1- and 2-axis product shapes;
- mapped and deliberately unmapped Portfolio rows;
- missing Open/current cells, duplicate-rejection fixtures and nonstandard tenor labels.

## Mandatory promotion scenarios

Create named identities for every scenario below. Tests must refer to scenario names rather than relying on random luck.

1. below all thresholds;
2. exactly equal to Risk threshold;
3. Risk-only breach;
4. dRisk-only breach;
5. P&L-only breach;
6. Risk and dRisk breach;
7. Risk and P&L breach;
8. dRisk and P&L breach;
9. all three breach;
10. negative Risk breach using absolute magnitude;
11. negative dRisk breach;
12. negative P&L breach;
13. multiple Portfolio rows aggregating into one promotion identity;
14. XVA and Hedges rows contributing to one promoted identity;
15. unmapped rows excluded from baseline promotion;
16. a filter scope that removes enough rows to de-promote an identity after explicit recalculation;
17. a filter scope that promotes an identity only after explicit recalculation;
18. a stable tie requiring deterministic secondary sorting.

## Market history scenarios

- FX Delta: zero tenor axes and a visible short-lived shock;
- IR Delta: one swap-tenor axis with slope, curvature and daily movement;
- FX Vega or Credit Delta: one axis with missing tenor observations;
- IR DeltaVega, XCCYVega and InflationVega: two axes with localized date/tenor shocks;
- connector-owned tenor ordering that differs from lexical ordering;
- an A/B difference surface with both positive and negative cells;
- stable camera/range tests across playback.

## Stock scenarios

Include Added, Removed, Changed and Unchanged identities, quantity-only changes, market-value-only changes, unmapped Portfolios, multiple currencies and promotion values just below/at/above the selected threshold.

## P&L scenarios

Include Matched, Predict-only and Colossus-only identities, positive and negative differences, missing dates, adjustment overlays and a sender validation failure case.

## Reproducibility

The generator accepts a fixed seed, but named edge cases use explicit values. Regenerating fixtures with the same version and seed must produce byte-for-byte identical canonical CSV/Parquet rows after stable sorting.


---

<a id="source-008"></a>

# Source 008 — `docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/migration-runbook.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/rebirth-v2-design/migration-runbook.md`
- **SHA-256:** `2375fc186bc630cb9f8417f5707460f145bfef0440677187b33dceeb325f4444`

---

# Migration runbook

## Phase 0 - Characterize V1

- Freeze representative Risk, Market, P&L, Stock and archive fixtures.
- Add golden outputs for dashboard_frame, market_frame, combined_pl, unmapped_frame and search results.
- Record callback IDs and page routes that must remain compatible.
- Record timing and payload baselines.
- Do not change runtime behavior in this phase.

Exit: V1 behavior is reproducible and failures are visible as tests rather than tribal knowledge.

## Phase 1 - Scaffold V2

- Add the package, runtime settings, composition container and factories.
- Copy shell-first/JupyterHub behavior without refactoring financial code.
- Create ports and architecture tests.
- Run V1 and V2 entrypoints side by side against the same fixture manager.

Exit: V2 starts, exposes health/progress and renders a placeholder page without source I/O during import.

## Phase 2 - Adapter boundary

- Wrap every current feed function in one typed port implementation.
- Split fake and production implementations.
- Preserve batch versus per-underlying call behavior.
- Move only source-independent validation into domain modules.

Exit: the old feed module is no longer imported by pages or domain; connector contract tests pass.

## Phase 3 - Pipeline decomposition

- Extract pure date, market merge, P&L, mapping and promotion functions.
- Build explicit stage classes around those exact functions.
- Execute the new coordinator in shadow mode and compare PipelineResult to V1 snapshot.
- Keep V1 manager as production authority until parity is proven.

Exit: all golden snapshots match and injected stage failures retain the previous revision.

## Phase 4 - Snapshot and query layer

- Introduce SnapshotStore, CurrentSnapshot and RevisionIndex.
- Move bounded filter search and pivots into application queries.
- Make pages request targeted DTOs rather than copy complete snapshots.

Exit: V2 page services do not access manager internals or full unrelated frames.

## Phase 5 - Page packages and card shell

- Implement shared card/split-panel design system.
- Move shell callbacks first, then Risk layout/callbacks.
- Keep existing page routes and current financial outputs.
- Add architecture tests for page isolation.

Exit: Risk page owns its callbacks and visual regression matches approved card design.

## Phase 6 - Top Promotions and Pivot Explorer

- Replace Top Book with TopPromotionRow query and flat table.
- Add Promotion Summary side card.
- Introduce PivotSpec, field sidebar and bounded row/column viewport.
- Remove hard-coded Region/Promotion/order controls only after equivalent fields work in sidebar.
- Stop filtered promotion recomputation in ordinary callbacks; retain explicit Recalculate action.

Exit: promotion scenarios and pivot acceptance matrix pass; Top Book code is unused and removable.

## Phase 7 - Data page and history

- Define HistoryRepository and Parquet schemas/manifests.
- Add legacy readers and migration jobs.
- Implement Data page and Quick Market deep-link.
- Add 0/1/2-axis outright/Move modes, period presets, A/B and playback.
- Migrate Market first, then Stock and P&L; run readers in dual mode until reconciled.

Exit: legacy and Parquet query outputs match and Data page meets playback/query budgets.

## Phase 8 - Stock, P&L and Statics modularization

- Move each page into its package without changing financial behavior.
- Preserve adjustment and saved-view repositories behind ports.
- Retain DataTable only where governed editing requires it; do not introduce AG Grid.

Exit: all page parity and isolation tests pass.

## Phase 9 - Cutover and cleanup

- Run V1 and V2 in parallel against the same production-like source snapshots.
- Compare revisions and user workflows for an agreed period.
- Switch deployment to V2.
- Delete old modules only when no imports, tests or operations depend on them.
- Keep migration readers until historical reconciliation is signed off.

Exit: V2 is sole runtime, rollback package is documented, and obsolete code is removed in small reviewable commits.


---

<a id="source-009"></a>

# Source 009 — `docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/rebirth-v2-redesign-and-migration-guide.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/rebirth-v2-design/rebirth-v2-redesign-and-migration-guide.md`
- **SHA-256:** `74a35a689fdf70b14a9f36ceaefba72f5c3592400f4bfffd349a24f14b376e62`

---

# Rebirth V2: redesign, V1-to-V2 mapping, and implementation contract

This document is the GitHub review copy of the Rebirth V2 design. The delivered documentation bundle contains the same design as a 53-page PDF plus a 235-file catalogue, migration CSVs, diagrams, fake-data scenarios and acceptance tests.

## Non-negotiable decisions

1. Preserve Cube's financial and operational behavior unless a change is explicitly documented and tested.
2. Keep Risk, Stock, P&L and Statics. Add a fifth first-class page named **Data** for historical analysis.
3. Keep Aggregate P&L. Its financial calculation is not redesigned in the visual migration; it is placed in a standard card and uses a bounded column viewport.
4. Remove the current hierarchical Top Book. Replace it with a flat **Top Promotions** table and a side-by-side Promotion Summary card.
5. Calculate baseline promotion during refresh after P&L, Portfolio mapping, Reported Underlying and thresholds. Ordinary filters do not recalculate promotion.
6. Add an explicit **Recalculate current view** command that creates a separate session-scoped promotion generation without mutating the committed baseline.
7. Replace hard-coded Risk Explorer Region, Promotion and order-by controls with a validated pivot specification and a hideable field sidebar.
8. Do not use AG Grid. Use native semantic tables with sticky headers/index cells and server-side row and column projections.
9. Keep Quick Market on Risk current-only. Add **Open in Data**, which carries Risk Type, Risk Greek and Underlying into the historical page.
10. Drive current and historical Market controls from `ProductSpec.axes`.
11. Preserve adapters, factories, the transactional pipeline, last-good snapshot behavior and production connector boundaries.

# Part I - Product and UI redesign

## 1. Visual system

The V2 interface must remain recognizably Cube. Retain the compact navigation, blue identity/index cells, yellow P&L/total cells, negative-value treatment and disclosure behavior. Adopt the calmer boxed layout from the previous interactive prototypes.

Every major table, chart or editor sits inside a card with:

- header: title, short explanation, optional status and actions;
- optional control strip affecting only that card;
- body: table, chart or editor;
- optional footer: viewport, playback, row count, revision/date and query status.

Large datasets must not float directly on the page canvas. Related but distinct blocks can use responsive side-by-side cards. Desktop may use a 65/35 or 50/50 split; tablet/mobile stacks the cards without clearing state.

Recommended neutral tokens:

```css
:root {
  --canvas: #f4f6f8;
  --surface: #ffffff;
  --surface-soft: #f7f8fa;
  --surface-muted: #edf1f4;
  --text: #111318;
  --muted: #626b75;
  --line: #d9dee5;
  --line-strong: #aeb8c3;
  --index: #c4def5;
  --total: #fffbdc;
  --negative: #b42318;
  --success: #287a43;
}
```

Use neutral historical color scales. For one identity, period and metric, playback must preserve camera, color scale and Z range.

## 2. Page navigation and ownership

Target navigation:

1. Risk
2. Stock
3. P&L
4. Data
5. Statics

Each page owns its layout, callbacks, serializable state and a narrow page service. Shared shell code owns only navigation, refresh status, operating dates and common components. One page package must never import another page package.

## 3. Risk page order

The Risk page is composed as follows:

1. shared refresh strip and operating dates;
2. Dates/Readiness disclosure;
3. saved view and bounded reporting filters;
4. Aggregate P&L, always open;
5. Top Promotions and Promotion Summary, side by side;
6. Quick Risk and Quick Market current-view disclosures;
7. Risk Explorer pivot workspace;
8. selected-cell chart and detail cards;
9. Unmapped diagnostics disclosure.

## 4. Top Promotions replaces Top Book

Top Promotions is a flat ranked table. It has no hierarchy, row chevrons or label tree. Each row represents one promotion identity:

```text
Risk Type + Risk Greek + Reported Underlying
```

Required fields:

- Rank
- Promotion Reason
- Risk Type
- Risk Greek
- Reported Underlying
- Risk
- dRisk
- P&L
- Risk Ratio
- dRisk Ratio
- P&L Ratio
- Promotion Score
- Promotion Basis

Promotion reasons remain `Big Risk`, `Big dRisk` and `Big PL`; a row may contain more than one. Default ordering is Promotion Score descending, absolute P&L descending, then Risk Type/Risk Greek/Reported Underlying for deterministic ties. Optional sorts are Promotion Score, absolute P&L, absolute Risk and absolute dRisk. Sorting never recalculates classification.

The row below Aggregate P&L uses:

- left 65%: Top Promotions flat table;
- right 35%: Promotion Summary.

Promotion Summary shows the active generation, source revision, filter basis for manual generation, reason counts, promoted identity count, **Recalculate current view**, **Use baseline**, and a warning when filters have changed since a manual generation was created.

Selecting a Top Promotions row sends its exact context to Risk Explorer and can open the detail card. It must not rebuild the full page.

## 5. Promotion lifecycle

There are two valid generation types.

### Baseline

- created once during the refresh pipeline;
- calculated after P&L, mapping, Reported Underlying and threshold attachment;
- owned by the committed revision;
- shared by all sessions;
- selected by default after each successful refresh.

### Current-view generation

- created only by an explicit user command;
- calculated from the already-filtered mapped rows;
- session-scoped;
- immutable after creation;
- records the exact filters and source revision;
- never mutates the baseline snapshot.

Ordinary changes to reporting filters, Risk Type, Greek, split, pivot rows, pivot columns, sorting or detail selection only filter/present the active generation. They do not recalculate it.

Suggested model:

```python
@dataclass(frozen=True)
class PromotionGeneration:
    generation_id: str
    kind: Literal["baseline", "current_view"]
    source_revision: int
    created_at: datetime
    basis_filters: FilterSpec | None
    policy_version: str
    rows: pd.DataFrame
```

The existing `recompute_filtered_promotion()` remains temporarily as a parity oracle, then moves into `domain/promotion/calculator.py` and is called only by the explicit command boundary.

## 6. Quick Risk and Quick Market

Quick Risk remains a current-revision bounded query. It retains identity authority, selectable hierarchy levels and Risk/dRisk/P&L/Open/Current/Move. It must not read connectors or history.

Quick Market answers one question: **what is the current selected market shape?** It retains current Open, OFFICIAL/Live and Move charts. Historical controls move out of this disclosure.

Add **Open in Data**. The route state carries:

- dataset = market;
- Risk Type;
- Risk Greek;
- Underlying;
- optional current chart mode;
- current Market Date as default end date.

The Data page validates the deep link against its history catalogue. It does not ask the user to select Underlying again when the context is valid.

## 7. Risk Explorer becomes a pivot workspace

The explorer uses a hideable left sidebar. Closing it does not clear state; the table expands into the free space.

Sidebar sections:

### Rows

Ordered hierarchy dimensions such as Risk Greek, Promotion Reason, Display Bucket, Region, Group, Reported Underlying, Underlying, Tenor Swap, Tenor Option, Split, Product, Activity, Signoff Group, Portfolio, Category and Sub Category.

### Columns

Zero or one dimension in the first release. Portfolio is allowed and is never capped. High-cardinality choices use a bounded column viewport.

### Values

Risk, dRisk, P&L, Move, Open and Current, with optional XVA/Hedges breakdown.

### Filters

Page-local governed filters plus optional Region and Promotion Reason. Region and promotion are ordinary selectable fields rather than special hard-coded hierarchy controls.

### Sort

Label or metric sorting, direction and optional absolute-magnitude sorting.

### Display

Grand total, subtotals, null policy, visible row limit and column window size.

The complete configuration is a validated immutable value:

```python
@dataclass(frozen=True)
class PivotSpec:
    row_dimensions: tuple[str, ...]
    column_dimension: str | None
    values: tuple[str, ...]
    filters: FilterSpec
    sort: SortSpec
    show_grand_total: bool = True
    show_subtotals: bool = True
    row_limit: int = 250
    column_window_size: int = 12
```

Validation rejects duplicate/unknown fields, the same field in rows and columns, no selected values and unsupported market aggregations.

The server computes the complete financial result but serializes only visible hierarchy rows, one column window, totals/range metadata and selected-cell context. A native table supports sticky cells, delegated expansion, horizontal viewport controls, keyboard selection and predictable DOM size. This preserves all 500 logical Portfolio columns without mounting 500 DOM columns.

## 8. Detail cards

A cell selection contains revision, PivotSpec fingerprint, row path, optional column value and metric. It is rejected when stale.

Display selected detail in two cards:

- chart: tenor line, heatmap/surface, bar or time series based on scope;
- table: bounded exact rows and values.

New Trades can add execution/audit detail. XGAMMA keeps source and output distinction. Generic pivot code must not contain those product-specific details.

## 9. Data page

The new Data page owns historical exploration with tabs:

- Market
- Stock
- P&L
- Risk, optional in the first release but supported by the architecture

Every tab shares period controls:

- WTD
- MTD
- YTD
- 1Y
- 5Y
- All
- Custom

Presets resolve against available committed dates. Custom supplies start/end and optional A/B comparison dates. The UI visibly reports nearest-available-date resolution.

### Market: zero axes

Example: FX Delta.

- outright time series;
- Move time series;
- optional Open versus Current overlay;
- no tenor selector.

### Market: one axis

Example: IR Delta.

- selected tenor line through time;
- selected-date curve;
- full date x tenor 3-D surface;
- outright or Move;
- Play/Pause for curve playback through selected dates;
- A/B curves and B-minus-A curve.

Playback keeps a stable Y range. A 3-D date x tenor surface keeps camera and Z/color range stable.

### Market: two axes

Applies to IR DeltaVega, XCCYVega and InflationVega.

Default selected-date surface:

```text
X = Tenor Swap
Y = Tenor Option
Z = selected metric
frame = Market Date
```

Controls:

- Outright, Daily Move, Start-date Move, Source Move or A/B Difference;
- period/date controls;
- one Play button that becomes Pause;
- draggable date scrubber and selected-date label;
- fixed Tenor Swap through time;
- fixed Tenor Option through time;
- all populated tenor cells through time;
- A surface, B surface and B-minus-A surface.

Playback is client-side after query data loads. Changing identity, period or metric stops playback and rebuilds frames. Missing cells remain null.

Historical move names must be explicit:

- Daily Move = value(date) - value(previous available date)
- Start-date Move = value(date) - value(start date)
- A/B Difference = value(B) - value(A)
- Source Move = archived Current - Open for the same date

### Stock Data

Provide selected-identity Quantity/Market Value history, A/B comparison, mapping authority, period presets, Added/Removed/Changed/Unchanged filtering and source rows only on request. The operational Stock page remains the main two-date workflow; Data is the longer historical lens.

### P&L Data

Retain canonical Predict/Colossus identity, mapping status, hierarchy expansion, series choice, period presets, missing observations and A/B difference where meaningful. The Data page is read-only; send and adjustment actions remain on P&L.

## 10. History repository

Define one `HistoryRepository` for Market, Stock, P&L, Risk and Portfolio authority. The Parquet implementation uses partition pruning and column projection. Legacy CSV readers remain during migration.

```text
data/history/
|-- commits/
|-- market/market_date=YYYY-MM-DD/*.parquet
|-- stock/stock_date=YYYY-MM-DD/*.parquet
|-- pnl/market_date=YYYY-MM-DD/*.parquet
|-- risk/market_date=YYYY-MM-DD/*.parquet
`-- portfolio_authority/effective_date=YYYY-MM-DD/*.parquet
```

A date becomes visible only after its commit manifest is published. Writers stage files, validate schemas/keys/counts/checksums and publish the manifest last.

Market history key:

```text
market_date + source_type + risk_type + risk_greek + underlying
+ tenor_swap + tenor_option
```

Historical Portfolio authority remains dated and separate so old observations are not silently classified with today's registry.

## 11. Fake data

The standard fake profile should use approximately 36-60 Portfolios and 120 business dates. A separate performance profile can use 500 Portfolio columns. Named deterministic scenarios must cover:

- below threshold;
- exact threshold;
- Risk-only, dRisk-only and P&L-only breaches;
- every combined breach;
- negative-value breaches using absolute magnitude;
- multiple rows/Portfolios aggregating into one identity;
- XVA/Hedges contribution;
- mapped/unmapped behavior;
- filter scopes that promote or de-promote only after explicit recalculation;
- deterministic ranking ties.

Market fixtures include zero-, one- and two-axis shapes, localized shocks, missing cells, nonlexical connector orders and positive/negative A/B differences. Stock fixtures include Added/Removed/Changed/Unchanged. P&L fixtures include Matched/Predict-only/Colossus-only and adjustment/send failures.

# Part II - V1-to-V2 architecture and migration

## 12. Ideas retained from V1

- ProductSpec and explicit tenor axes;
- Open-authoritative MarketBook and connector tenor order;
- vectorized P&L formulas;
- Portfolio and Reported Underlying authority;
- threshold-based promotion grain;
- one-writer transactional refresh;
- last-good snapshot during refresh/failure;
- revision-local search catalogues;
- shell-first/JupyterHub runtime behavior;
- bounded Quick searches;
- full-outer Stock comparison;
- governed adjustments, P&L send validation and Predict/Colossus history;
- atomic official archive behavior;
- native Dash pages and semantic HTML tables.

## 13. New V2 ideas

- typed application ports and enforced dependency direction;
- page-owned callback packages;
- stage-based pipeline decomposition;
- immutable PromotionGeneration;
- flat Top Promotions;
- hideable PivotSpec field sidebar;
- row/column viewport queries;
- dedicated Data page and deep links;
- common Parquet/manifest HistoryRepository;
- standard card and split-panel components;
- architecture/performance tests.

## 14. Removed ideas

- Top Book hierarchy;
- hard-coded Region/Promotion/order hierarchy toggles;
- implicit UI promotion recalculation on ordinary filters;
- full historical Market workbench inside Quick Market;
- page wrappers with central monolithic callbacks;
- giant pipeline/component/event modules;
- unbounded high-cardinality payloads;
- rendering all 500 Portfolio columns simultaneously.

## 15. Dependency direction

```text
Dash page packages
        |
        v
Application services and queries
        |
        v
Domain calculations and models
        |
        v
Typed application ports
        ^
        |
Concrete adapters and repositories
```

Only composition imports production adapters. Domain imports no Dash, Flask, filesystem, environment or network code. Pages import no concrete adapters and no other page package.

## 16. Main V1-to-V2 mapping

| V1 | V2 | Change |
|---|---|---|
| `s01_app.py`, `s02_config.py`, `ui/s09_factory.py` | `config/`, `composition/`, `pages/shell/` | Thin entrypoint and explicit factories. |
| `core/s02_pipeline.py` | `domain/`, `application/pipeline/`, `application/snapshots/` | Pure finance rules split from orchestration/state. |
| `feeds/s01_sources.py`, `adapters/*` | `application/ports/`, `adapters/production/`, `adapters/fake/` | One port per external boundary. |
| `ui/s02_constants.py`, `ui/s03_aggregate.py` | `domain/risk/`, `application/queries/` | PivotSpec and pure query logic. |
| `ui/s04_components.py`, `ui/s07_events.py` | `pages/*`, `shared/*` | Feature/page ownership. |
| Top Book | `pages/risk/top_promotions/` | Flat ranked table and summary card. |
| Quick Market history | `pages/data/market/` | Current-only Quick Market, full Data history. |
| `core/s11_risk_archive.py` | `adapters/history/`, history port and jobs | Dual legacy/Parquet repository with manifests. |
| Stock/P&L UI modules | `pages/stock/`, `pages/pnl/` | Page-owned callbacks and narrow services. |

The full old-path mapping and deletion gates are in `v1-to-v2-file-map.csv`. The exact target tree is in `target-tree.txt`. The complete 235-file purpose/inputs/outputs/rule catalogue is in the delivered documentation bundle.

## 17. Migration phases

### Phase 0 - Characterize V1

Freeze representative input fixtures and golden outputs for readiness, Risk, Market, P&L, mapping, promotion, search, Stock and history. Record source call counts and startup/prefix behavior. No runtime behavior changes.

### Phase 1 - Scaffold V2

Add package, runtime settings, composition container/factories, ports and architecture tests. Reuse the V1 manager through a compatibility adapter if needed. V2 must paint a shell without import-time connector I/O.

### Phase 2 - Adapter boundary

Wrap every feed function in a typed implementation. Split fake and production. Preserve batch versus per-underlying behavior. Move only source-independent validation into domain.

### Phase 3 - Pipeline decomposition

Extract pure functions in this order: dates/ProductSpec, Risk validation, Market merge, P&L, mapping, threshold/promotion, release validation, indexes. Build explicit stages around exact functions and shadow-compare V1/V2 snapshots.

### Phase 4 - Snapshot and query layer

Introduce SnapshotStore, CurrentSnapshot and RevisionIndex. Move bounded filter/search/pivot queries into application APIs. Pages stop reading manager internals or unrelated frames.

### Phase 5 - Card shell and page packages

Implement card/split-panel components. Move shell and then Risk callbacks feature by feature. Achieve parity before changing UX. Add page-isolation tests.

### Phase 6 - Promotions and pivot

Implement PromotionGeneration, Top Promotions and PivotSpec. Keep Top Book behind a temporary comparison flag, then delete it after acceptance. Remove hard-coded controls only after equivalent sidebar fields work.

### Phase 7 - Data and history

Implement HistoryRepository, legacy readers and Parquet schemas/manifests. Migrate Market first, then Stock and P&L. Add Data page, deep links and 0/1/2-axis playback. Run dual readers until reconciled.

### Phase 8 - Stock, P&L and Statics modularization

Move each page into its own package without changing finance behavior. Preserve editors/senders and native DataTable where governed editing requires it. Do not add AG Grid.

### Phase 9 - Cutover

Run V1/V2 against the same inputs, compare revisions/workflows, switch deployment, retain rollback and remove old files only after their deletion gates pass.

## 18. Ordered implementation instructions for another LLM

1. Create modules from `target-tree.txt`.
2. Add architecture tests before moving logic.
3. Copy RuntimeSettings behavior and tests.
4. Define ports from current connector signatures and exact schemas.
5. Wrap current fake adapters; do not regenerate data yet.
6. Build AdapterFactory and a V1-compatible container.
7. Extract ProductSpec/axes without changing keys/formulas/order.
8. Extract Market merge and P&L and compare exact frames.
9. Extract mapping/promotion and create baseline PromotionGeneration.
10. Build stage pipeline and compare final snapshots.
11. Add SnapshotStore and revision indexes with stale-token rejection.
12. Implement card CSS/components and visual snapshots.
13. Move Risk layout/callbacks while retaining current controls.
14. Add Top Promotions and Promotion Summary.
15. Add PivotSpec/sidebar using the old hierarchy as the default spec.
16. Add row/column viewport queries and prove 500-column logical access.
17. Replace hard-coded controls only after sidebar parity.
18. Move historical Quick Market to Data and add deep link.
19. Add HistoryRepository, legacy readers and Market migration.
20. Add Data Market modes and client-side playback.
21. Add Stock/P&L history tabs and migrations.
22. Move remaining page callbacks into their packages.
23. Generate named fake scenarios and run all acceptance/performance tests.
24. Shadow-run, document differences, cut over and delete obsolete modules.

## 19. Release proof

The work is done only when:

- navigation includes Risk, Stock, P&L, Data and Statics;
- all major datasets are card-contained and side-by-side cards stack correctly;
- Aggregate P&L remains financially equivalent;
- Top Book is gone and Top Promotions is flat/ranked/auditable;
- baseline promotion is revision-owned and filters do not recalculate it;
- current-view recalculation creates a separate generation;
- Risk Explorer uses PivotSpec and a hideable field sidebar;
- no AG Grid dependency exists;
- 500 logical Portfolio columns are available through a bounded viewport;
- Quick Market is current-only and deep-links to Data;
- Data supports 0/1/2 axes, periods, custom dates, outright/Move and playback;
- IR Delta curves and two-axis Vega surfaces both support Play/Pause where appropriate;
- Stock and P&L history use the common repository;
- missing values remain missing rather than zero-filled;
- production/fake adapters implement the same ports;
- pipeline order and atomic commit are tested;
- pages import no other page and no concrete adapter;
- legacy/Parquet history reconcile;
- compile, Ruff, Pyright, unit, integration, architecture, smoke, performance and visual tests pass.


---

<a id="source-010"></a>

# Source 010 — `docs/cube-rework-deep-dive:docs/cube-rework/rebirth-v2-design/ui-design-system.md`

- **Branch:** `docs/cube-rework-deep-dive`
- **Path:** `docs/cube-rework/rebirth-v2-design/ui-design-system.md`
- **SHA-256:** `0904c5616d8da441654e68a161bb232b455fc69a15363d4a0929f94b757cc282`

---

# Rebirth V2 UI design system

## Intent

The V2 interface keeps the page and workflow identity of Cube while adopting the boxed, calm and legible visual language used by the interactive prototypes. The redesign is structural, not decorative: a user must be able to see which controls govern which data, where a result begins and ends, and which sections are current versus historical.

## Card rule

Every major dataset or workflow is rendered inside a card with:

1. a header containing title, short description and optional status/actions;
2. a body containing controls and data;
3. an optional footer containing pagination, viewport, playback or query status.

Never render a large table or chart directly on the page canvas without a card boundary.

## Side-by-side rule

Use a responsive two-column card row when two blocks answer related but distinct questions. Desktop layouts may use 65/35 or 50/50 proportions. Below the tablet breakpoint, cards stack without changing query state.

The Risk-page row immediately below Aggregate P&L is:

- left: Top Promotions flat ranked table;
- right: Promotion Summary and generation controls.

## Top Promotions

This replaces Top Book. It is not a tree and has no row chevrons. Each row represents one unique promotion identity:

`Risk Type + Risk Greek + Reported Underlying`

Required columns:

- Rank
- Promotion Reason
- Risk Type
- Risk Greek
- Reported Underlying
- Risk
- dRisk
- P&L
- Risk Ratio
- dRisk Ratio
- P&L Ratio
- Promotion Score
- Promotion Basis

Default ordering is Promotion Score descending, then absolute P&L descending, then stable text identity. User-selectable sorts may include Promotion Score, absolute P&L, absolute Risk and absolute dRisk. A row action opens that exact identity in the Risk Explorer.

## Risk Explorer

The explorer uses a hideable field sidebar rather than hard-coded Region, Promotion and order-by controls. The sidebar contains:

- Rows: ordered list of row dimensions;
- Columns: zero or one high-cardinality column dimension;
- Values: Risk, dRisk, P&L, Move, Open and Current;
- Filters: page-local filters plus optional Promotion Reason and Region;
- Sort: dimension and metric sort configuration;
- Display: totals, subtotals, XVA/Hedges breakdown and null policy.

The sidebar can be closed with one button. Closing it does not clear its state. The table grows into the recovered space.

## Native table rule

Do not use AG Grid. Use semantic HTML tables with sticky headers/index columns and server-side projections. The server computes the complete financial result but returns only:

- the visible hierarchy rows;
- a bounded column window;
- selected totals and metadata.

## Color rule

Use neutral backgrounds, soft grey borders, black text and restrained pale blue/yellow semantic fills. Use red only for negative values or errors and green only for success. Historical 3-D surfaces use neutral sequential or muted diverging scales with stable limits.

## Historical playback

Play is one button. When running, its label becomes Pause. Playback changes only the selected date/frame and preserves camera, color scale, Z range, identity and period. Date labels are sparse and stay inside the chart/card boundary.


---

<a id="source-011"></a>

# Source 011 — `docs/rebirth-v3-spec:docs/rebirth-v3/01_product_ui_v3.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/01_product_ui_v3.md`
- **SHA-256:** `41c79086d814feab89b20befa4f74e6b12f4b08c460380e675e93df7a9f54551`

---

# Part I - Product and UI redesign

## 1. The Risk page becomes one consistent workspace

The Risk page keeps the original Cube sequence - saved view and reporting filters, top-level P&L context, Quick inspection, Risk Explorer and detail - but the top region becomes one boxed workspace with mini tabs:

```text
[ Aggregate P&L ] [ Quick Risk ] [ Quick Market ]
```

The default active tab is **Aggregate P&L**. Tabs are not separate routes and do not create independent copies of the page filter state. They are three views over the same committed revision.

### 1.1 Aggregate P&L tab

The Aggregate P&L tab contains:

1. the always-visible, full-width Aggregate P&L table;
2. immediately beneath it, a vertically expandable **Top Promotions** section;
3. no Top Book;
4. no side rail or right-side promotions panel.

Top Promotions is collapsed by default and is lazy: when closed, the flat ranked table is neither queried nor mounted. Opening it reads the already committed promotion generation; it never recalculates promotion. The table contains Rank, reason, Risk Type, Risk Greek, Reported Underlying, Risk, dRisk, P&L and score. Ranking can switch between overall score, absolute P&L, absolute Risk and absolute dRisk.

### 1.2 Quick Risk tab

Quick Risk is no longer only a hierarchy table. It becomes a current-snapshot visual inspector with the same design grammar as Quick Market:

- exact bounded search for `Risk Type | Risk Greek | Underlying`;
- selected metric: Risk, dRisk or P&L;
- current 3-D tenor visualization when the product has a real tenor dimension;
- exact values table next to or beneath the chart;
- status footer containing Risk Date, revision, availability and mapping scope;
- a compact **History preview** control;
- an **Open Risk history in Data** link carrying the exact identity and metric.

Page reporting filters apply to position-based Quick Risk. Quick Risk never calls the production connector directly; it queries the committed snapshot/index.

### 1.3 Quick Market tab

Quick Market retains exact identity search but presents the current MarketBook in the same visual grammar:

- exact `Risk Type | Risk Greek | Underlying` search;
- metric toggle: Open, Current/OFFICIAL, Move;
- current 3-D tenor visualization for one- and two-axis products;
- quote table with exact tenor cells and missing values preserved as unavailable;
- status footer with Market Date, Live/OFFICIAL state and revision;
- compact **History preview**;
- **Open Market history in Data** deep link.

Market is quote-level and therefore ignores Portfolio/Activity filters. It uses the selected exact market identity only.

## 2. Default view and saved views

Every fresh Risk session starts in the immutable system view:

```text
Default - Activities 1-3
Activity IN {Activity 1, Activity 2, Activity 3}
```

The filter is applied before the first Risk query. Users may save new views, update or delete their own views, and save a copy of the system default, but cannot rename, overwrite or delete the system default. **Reset view** and a successful **Clear Cache** return to it.

The baseline promotion policy may initially use the same Activities 1-3 scope, but its financial scope is separately configured. Loading a saved view never silently changes the baseline promotion generation.

## 3. The 3-D chart grammar

V3 uses product dimensionality from `ProductSpec.axes`. It does not infer dimensionality from whichever columns happen to contain labels.

### 3.1 Zero-axis products

Examples: FX Delta and other genuinely no-tenor identities.

A meaningful 3-D surface cannot be constructed from one scalar per date without inventing a fake axis. V3 therefore deliberately uses:

- current value cards or a signed 2-D bar for Quick Risk;
- Open/Current/Move cards for Quick Market;
- a normal date time series for history.

This is the only exception to the request that tenor plots be 3-D: there is no tenor plot to render. Fabricating depth would be visually impressive but financially misleading.

### 3.2 One-axis products

Examples: IR Delta, IR Gamma, IR XCCY, IR Basis, FX Vega, Credit Delta, Commodity Delta.

#### Current Quick Market

Use a true 3-D two-row ridge surface:

```text
X = available tenor
Y = quote state (Open, Current/OFFICIAL)
Z = quote value
Surface colour = Move
```

This is more legible than a flat line while still encoding a real second dimension. The current curve is not extruded merely for decoration.

#### Current Quick Risk

Use a true 3-D contribution surface:

```text
X = available tenor
Y = contribution group
Z = selected Risk metric
```

The default contribution group is Product partition (`XVA`, `Hedges`) because the two rows share the same unit. The user may switch the small `Surface by` control to Split or Activity. It is bounded to a small number of groups; it is not a 500-Portfolio surface.

#### Historical one-axis views

Two complementary history modes are available:

1. **Curve playback** - the current-style 3-D ridge updates date by date. Play becomes Pause in the same button location; a slider and date pill scrub the frames.
2. **History surface** - `X = Market/Risk Date`, `Y = Tenor`, `Z = selected metric`. Playback moves a highlighted date slice across the static 3-D history surface.

Therefore a product with only Tenor Swap or only Tenor Option always has a consistent Play/Pause history experience.

### 3.3 Two-axis products

The governed two-axis products include IR DeltaVega, IR XCCYVega and IR InflationVega.

#### Current Quick Market

```text
X = Tenor Swap
Y = Tenor Option
Z = Open, Current or Move
```

#### Current Quick Risk

```text
X = Tenor Swap
Y = Tenor Option
Z = Risk, dRisk or P&L
```

#### Historical views

- selected-date full surface playback;
- fixed Tenor Swap: `X = Date`, `Y = Tenor Option`, `Z = value`;
- fixed Tenor Option: `X = Date`, `Y = Tenor Swap`, `Z = value`;
- all populated tenor cells through time;
- Date A, Date B and B-A surfaces.

Even after fixing one tenor, the remaining tenor and Date still form a true 3-D surface, so Play/Pause remains available.

## 4. Common 3-D interaction contract

Every historical 3-D chart uses the same `TimelineControl`:

```text
[Play] / [Pause]   [date slider]   [selected date]
Period: WTD | MTD | YTD | 1Y | 5Y | All | Custom
Speed: 0.5x | 1x | 2x
```

Rules:

- the Play button changes label and icon in place;
- the camera is retained with `uirevision`;
- Z and colour ranges are stable for the selected identity/range;
- missing cells remain gaps, never zero;
- neutral colours dominate; diverging colours are muted and centred at zero only for signed values;
- axis labels use connector-owned tenor order;
- the full history bundle is queried once and frame changes run clientside;
- if only one date is available, Play is disabled with an explanation;
- leaving the page stops playback;
- switching identity or period resets to the latest available date in that range.

Recommended camera and shape defaults:

```python
camera_eye = {"x": 1.55, "y": 1.65, "z": 1.25}
aspectratio = {"x": 1.30, "y": 1.08, "z": 0.78}
```

The Z aspect ratio must not be compressed to the point that surfaces appear flat. It may be adjusted within a bounded range after examining real value distributions, but never independently rescaled on every animation frame.

## 5. History preview versus the Data page

Quick Risk and Quick Market default to **current snapshot**. A compact History preview can be expanded inside the tab, initially using MTD and the same chart. The full analytical workflow lives on the Data page.

The footer link sends a route payload rather than asking for the identity again:

```json
{
  "kind": "risk" | "market",
  "risk_type": "IR",
  "risk_greek": "DeltaVega",
  "underlying": "EUR",
  "metric": "risk" | "current" | "move",
  "source_revision": 218
}
```

The Data page locks the routed identity by default, displays it as a breadcrumb, and lets the user unlock it explicitly.

## 6. Data page scope

V3 reduces duplication. The Data page owns **Risk history** and **Market history** only. Historical P&L remains in the P&L page and historical Stock remains in the Stock page. The Data landing page contains clear links to those existing sections rather than implementing them twice.

Data tabs:

```text
[ Risk History ] [ Market History ]
```

Both tabs support WTD, MTD, YTD, 1Y, 5Y, All and Custom Date A/Date B. The period resolver selects actual available repository dates, not assumed calendar rows.

## 7. Risk Explorer

The Risk Explorer keeps the native pivot approach from the previous specification, but V3 removes unnecessary page-level special controls. Region, Promotion bucket, Product, Split, Activity and Portfolio are normal pivot fields.

A collapsible field drawer contains:

```text
Rows | Columns | Values | Filters | Sort | Totals | Display | Presets
```

`Cross` and `SplitVA` are presets of the same engine. The complete logical pivot is computed server-side, but only a bounded row and column viewport is serialized. No AG Grid is introduced.

## 8. Clear Cache and date rollover

The compact header keeps **Clear Cache** immediately to the left of dark mode. It remains a controlled application reset, not a claim to erase Safari's global cache.

It clears reconstructable session/query state, advances a shared reset generation, rejects late refresh commits, recomputes the London/business-date authority and runs a new transactional refresh. It preserves saved views, P&L adjustments, history, configuration and the theme preference.

A rollover guard compares the active snapshot date token with a fresh authoritative token. A mismatch invokes the same reset coordinator. Yesterday may be shown only as an explicitly labelled stale snapshot; it can never continue to be labelled Today.


---

<a id="source-012"></a>

# Source 012 — `docs/rebirth-v3-spec:docs/rebirth-v3/02_viability_and_simplification.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/02_viability_and_simplification.md`
- **SHA-256:** `77a050c6833722ddefbf4d42e1846ef7e553fc33075d6ac3cc7dc7b7b247c706`

---

# Part II - Current-code viability review and the V3 simplification

## 9. Executive viability conclusion

The current Rebirth code is viable and should be refactored, not discarded. Its calculations and contracts are substantially more mature than its module boundaries.

The review used the current `main` tree and focused on the executable entry point, runtime settings, all adapter families, `feeds/s01_sources.py`, the core schema/pipeline/search/P&L/storage/Stock/archive modules, all `ui/` modules, native page wrappers, assets and the numbered test suite.

The strongest existing design decisions are:

- shell-first startup and JupyterHub-aware prefixes;
- ProductSpec as tenor/formula authority;
- strict input frames and quote identities;
- one writer / immutable last-good snapshot;
- exact Quick identity positions;
- a closed pivot allowlist;
- atomic P&L adjustments and daily archives;
- lazy Stock hierarchy projection.

V3 wraps these behaviors in clearer ownership boundaries and adds the new UI/history experience.

## 10. Why V1 felt like 25 files and V2 grew to 413

V1 concentrated many responsibilities in a small number of very large files. Examples from the reviewed tree:

| File | Approximate size | Concentrated responsibilities |
|---|---:|---|
| `core/s02_pipeline.py` | 174 KB | ProductSpec, dates, readiness, validation, Market merge, P&L, promotion, refresh, snapshot |
| `ui/s04_components.py` | 223 KB | Risk page layout, Quick search, chart/table builders, detail, controls |
| `ui/s07_events.py` | 141 KB | startup, refresh, filters, hierarchy, Quick callbacks, detail callbacks |
| `assets/s02_app.js` | 95 KB | startup polling, browser controls, table interactions, miscellaneous helpers |
| `ui/s08_plevents.py` | 71 KB | aggregate P&L, editors, adjustments, sending |
| `core/s11_risk_archive.py` | 55 KB | Risk/Market/Colossus archive, manifests, validation, historical loaders |

That made the apparent file count low while the cognitive units were large.

The prior V2 proposal responded by splitting nearly every stage, protocol, table, chart and callback into a separate file. That was too granular. It increased navigation, import and coordination cost without improving business isolation.

V3 uses a middle ground:

```text
V1: approximately 25-50 broad files, several very large
V2 proposal: 413 micro-files
V3 target: 75 total files, 53 runtime/configuration files, 11 focused test files
```

The 75 includes root configuration, assets, documentation and CI. The executable package remains compact.

## 11. V3 file-creation rule

Create a new file only when at least one is true:

1. it is an external I/O boundary;
2. it is a page vertical slice owned by one route;
3. it is a pure domain with a stable public contract;
4. it is genuinely shared by two or more pages;
5. it is an independently run operational job;
6. keeping it together would exceed roughly 800-1,000 coherent lines.

Do not create a file for:

- one callback;
- one button;
- one panel;
- one pipeline stage of a few lines;
- a protocol with only one implementation and no replacement boundary;
- a dataclass used by one adjacent module.

Page packages normally contain only `layout.py` and `callbacks.py`. Query and transformation logic lives in a page-neutral service. The Data page's substantial chart grammar stays in shared chart helpers rather than a separate file for every view.

## 12. What remains from V1

### Keep with minimal change

- `s01_app.py` startup order;
- `s02_config.py` proxy/service prefix rules;
- adapter function signatures;
- ProductSpec identities, axes and formulas;
- exact source schemas and quote keys;
- Market Open/Current/Move semantics;
- P&L formulas and send mapping;
- Stock full-outer comparison;
- atomic adjustment files;
- completed archive leaves and manifests;
- exact search positions and closed pivot fields;
- last-good snapshot behavior.

### Refactor without changing output

- split `core/s02_pipeline.py` into product/domain and four pipeline files;
- split page layouts/callbacks from the large UI files;
- replace thin `pages/*.py` wrappers with page-owned vertical slices;
- move connector selection from comment/uncomment blocks into an adapter registry;
- move historical query logic behind one repository contract;
- consolidate browser helpers into a smaller namespaced clientside module.

### Extend

- Risk history snapshots and Data-page query shapes;
- Quick Risk/Market 3-D chart matrices;
- TimelineControl and clientside playback;
- source availability map and fail-soft issue display;
- native pivot row/column viewport;
- reset/date-rollover generation.

### Retire

- Top Book;
- independent Quick Risk and Quick Market expanders;
- per-page Region/Promotion/sort controls that duplicate pivot fields;
- giant global callback registration files;
- one-file-per-widget V2 plan;
- unlabelled stale data;
- AG Grid as a proposed solution for wide Portfolio dimensions.

## 13. Fail-open terminology: implement fail-soft, not unsafe fail-open

The user's intent is correct: Cube must remain available when one optional source or feature breaks. Pure fail-open, however, would risk treating unvalidated data as authoritative.

V3 uses two levels:

```text
Application shell and unaffected features: fail-soft / degraded-open
Financial frame validation, snapshot commit and P&L send: fail-closed
```

### 13.1 Availability states

Every product/feature exposes one of:

```text
AVAILABLE
DEGRADED
UNAVAILABLE
STALE
```

A committed snapshot contains an `AvailabilityMap` and structured issues. Pages render a standard status panel around the affected component.

### 13.2 Recoverable failures

| Failure | What remains usable | What is disabled/flagged |
|---|---|---|
| One Risk product adapter fails | other products, shell, history | failed product Risk/Quick Risk; aggregate excludes it with warning |
| One Market adapter fails | Risk for that product | Market, Move and Predict P&L for the failed market scope |
| Promotion calculation fails | Aggregate P&L and Risk Explorer | Top Promotions unavailable; no stale custom generation silently reused |
| Colossus fails | Predict P&L and editors | validation/Colossus series unavailable |
| Market/Risk history repository fails | current snapshot pages | Data history panel unavailable |
| saved-view repository fails | immutable system default | save/update/delete actions disabled |
| one Stock date fails | rest of app | selected comparison unavailable |

### 13.3 Critical failures

Date authority corruption, duplicate authoritative quote keys, invalid available-frame schema, or snapshot-store failure prevent a new snapshot commit. The app still starts and serves the previous explicitly dated last-good snapshot plus an error panel. This is fail-soft at the application boundary and fail-closed at the data boundary.

### 13.4 Stage contract

```python
@dataclass(frozen=True)
class StageOutcome:
    context: PipelineContext
    issues: tuple[PipelineIssue, ...]
    availability_updates: Mapping[str, AvailabilityState]

class RecoverableSourceError(Exception): ...
class CriticalPipelineError(Exception): ...
```

Stages catch and classify known connector errors per product. Unknown programming errors are critical and are never silently swallowed.


---

<a id="source-013"></a>

# Source 013 — `docs/rebirth-v3-spec:docs/rebirth-v3/04_data_pipeline_contracts.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/04_data_pipeline_contracts.md`
- **SHA-256:** `d5ee28570142b5316923e721e52af4562feefa5563d8769322098ff34ff581a8`

---

# Part IV - Data, pipeline and resilience contracts

## 17. Adapter boundary

V3 keeps adapters. The distinction is clearer:

- `adapters/contracts.py` defines replaceable I/O ports;
- `adapters/fake.py` supplies deterministic local data;
- `adapters/production.py` wires existing site-owned connector functions;
- page callbacks never import adapters;
- domain and pipeline code receive adapters through the composition root.

Risk and Market remain separate calls because their dates and failure behavior differ. Stock and Colossus have their own grains. P&L senders are write boundaries and are never inferred from read adapters.

## 18. Product and axis authority

`ProductSpec` remains authoritative for:

```text
Source Type
Risk Type
Risk Greek
Axis list and order columns
Market unit
P&L formula
Special scaling
```

Two-axis products are not identified by name matching. They are identified by:

```python
len(product_spec.axes) == 2
```

This automatically covers DeltaVega, XCCYVega and InflationVega without duplicated UI conditionals.

## 19. Snapshot and availability model

```python
@dataclass(frozen=True)
class AvailabilityEntry:
    state: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE", "STALE"]
    reason: str | None
    source_date: date | None
    updated_at: datetime | None

@dataclass(frozen=True)
class RiskSnapshot:
    revision: int
    date_token: DateToken
    risk_frame: pd.DataFrame
    market_frame: pd.DataFrame
    dashboard_frame: pd.DataFrame
    combined_pl: pd.DataFrame
    unmapped_frame: pd.DataFrame
    search_catalog: SearchCatalog
    availability: Mapping[str, AvailabilityEntry]
    issues: tuple[PipelineIssue, ...]
```

A failed optional product can be absent from the new revision if its availability state and issue are explicit. A structurally invalid frame is never marked AVAILABLE.

## 20. Pipeline flow

```text
Resolve fresh DateToken
  -> read readiness/inventory
  -> read Risk product partitions independently
  -> validate successful Risk partitions
  -> read Market partitions independently
  -> validate successful quote partitions
  -> combine Open/Current and derive Move
  -> calculate Predict P&L where inputs are available
  -> attach Portfolio and Reported Underlying mappings
  -> attach thresholds
  -> calculate baseline promotion once
  -> build exact search and pivot indexes
  -> create AvailabilityMap and issues
  -> validate the coherent available subset
  -> atomic snapshot commit
```

If a recoverable product fails, dependent rows are unavailable and aggregate totals are explicitly partial. The UI never presents a partial total without a visible degraded flag.

## 21. Risk history schema

Risk history stores committed current risk at source identity grain, not the rendered hierarchy:

```text
Snapshot Date
Risk Date
Revision
Source Type
Risk Type
Risk Greek
Underlying
Tenor Swap
Tenor Option
Tenor Swap Order
Tenor Option Order
Portfolio
Product
Activity
Signoff Group
Category
Sub Category
Split
Region
Group
Risk
dRisk
P&L
Mapping Status
```

Recommended partition:

```text
data/history/risk/snapshot_date=YYYY-MM-DD/part-*.parquet
```

Unique key:

```text
Snapshot Date
+ Source Type
+ Underlying
+ Tenor Swap
+ Tenor Option
+ Portfolio
+ Split
```

If source Risk identity requires more fields, add them to the key; never aggregate distinct source rows solely to fit this proposed key.

## 22. Market history schema

```text
Market Date
Source Type
Risk Type
Risk Greek
Underlying
Tenor Swap
Tenor Option
Tenor Swap Order
Tenor Option Order
Open
Current
Move
Market Status
Market Data Status
```

Partition by `market_date=YYYY-MM-DD`. The raw quote key is Market Date plus Source Type, Risk Type, Risk Greek, Underlying and both tenor labels. Missing values remain null.

## 23. History repository interface

```python
class HistoryRepository(Protocol):
    def available_dates(self, dataset: HistoryDataset, *, identity: HistoryIdentity | None = None) -> tuple[date, ...]: ...
    def read_range(self, request: HistoryRangeRequest) -> pd.DataFrame: ...
    def read_dates(self, request: HistoryDatesRequest) -> pd.DataFrame: ...
    def append_partition(self, request: HistoryAppendRequest) -> CommitManifest: ...
```

`HistoryRangeRequest` contains:

```text
dataset
start_date
end_date
exact identity
metric columns
additional filters
required revision/date semantics
```

The Parquet implementation must project only requested columns and push date/identity filters into PyArrow before converting the bounded result to pandas.

## 24. Chart matrix builders

Chart builders consume small matrices, not broad source frames.

### Current two-axis matrix

```python
@dataclass(frozen=True)
class SurfaceMatrix:
    x_labels: tuple[str, ...]
    y_labels: tuple[str, ...]
    z: tuple[tuple[float | None, ...], ...]
    cmin: float
    cmax: float
    zmin: float
    zmax: float
```

### Current one-axis market ridge

```python
x = tenor
rows = ("Open", "Current")
z = [[open values], [current values]]
colour = current - open
```

### Current one-axis risk contribution

```python
x = tenor
rows = selected contribution groups
z = grouped selected metric
```

### Historical playback bundle

```python
@dataclass(frozen=True)
class PlaybackBundle:
    dates: tuple[date, ...]
    frames: tuple[SurfaceMatrix, ...]
    selected_index: int
    identity: HistoryIdentity
    metric: str
    period: str
```

The browser store receives the playback bundle only after the user requests history. The date slider callback is clientside.

## 25. Promotion contract

Baseline promotion remains a pipeline output. Ordinary filter, tab, pivot and chart changes never invoke it.

The committed promotion table contains:

```text
Promotion generation
Revision
Risk Type
Risk Greek
Reported Underlying
Risk
dRisk
P&L
Risk threshold
dRisk threshold
P&L threshold
Reason flags
Score
```

Top Promotions ranks this table. A session override is built from a current filtered scope only after explicit confirmation and is separately identified. Failure of the override keeps baseline available and shows the error.

## 26. Clear Cache/reset contract

Clear Cache advances `ResetGeneration`, clears reconstructable caches and session stores, reruns DateAuthority and starts a new refresh with the new generation. A refresh commit must satisfy:

```text
attempt.reset_generation == shared_reset_generation
```

otherwise it is discarded as stale.

Durable history, saved views, adjustments and theme preference are not deleted. The UI shows phases `Resetting`, `Refreshing`, `Ready` or `Failed`, and offers Retry after a failure.

## 27. Saved view versioning

Saved views add schema versioning and preserve unknown future fields on round-trip where possible. A Risk saved view contains filters and pivot configuration, not transient disclosure or playback state.

The immutable system default is produced by code/config rather than stored in the user repository. Users save a copy under a new identity.


---

<a id="source-014"></a>

# Source 014 — `docs/rebirth-v3-spec:docs/rebirth-v3/README.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/README.md`
- **SHA-256:** `e3ace196149fae52acdcc97091f93b1152d951f431fe49cabbf1e5cef9be4a12`

---

# Rebirth V3 implementation specification bundle

This is the authoritative compact V3 handoff. It replaces the 413-file V2 target with a 75-file design grounded in the current V1 code.

## Start here

- `Rebirth_V3_Architecture_Product_and_Migration_Spec.md`
- `Rebirth_V3_Architecture_Product_and_Migration_Spec.pdf`
- `REVISION_V3.md`
- `Rebirth_V3_Interactive_Prototype.html`

## Review chapters

- `01_product_ui_v3.md`
- `02_viability_and_simplification.md`
- `03_compact_architecture_file_catalog.md`
- `04_data_pipeline_contracts.md`
- `05_v1_to_v3_mapping.md`
- `06_implementation_runbook.md`
- `07_fake_data_testing_performance.md`
- `08_acceptance_and_llm_rules.md`

## Machine-readable handoff

- `architecture_manifest.yaml`
- `ui_contracts.json`
- `file_catalog.json`
- `v1_to_v3_mapping.csv`
- `implementation_phases.yaml`
- `SHA256SUMS.txt`

## Prototype scope

The HTML prototype demonstrates:

- immutable `Default - Activities 1-3`;
- Aggregate P&L / Quick Risk / Quick Market mini tabs;
- current 3-D Quick Risk and Quick Market tenor views with exact values;
- full-width collapsed Top Promotions beneath the workspace;
- native pivot field drawer without AG Grid;
- Risk and Market history on the Data page;
- Play/Pause and date slider for every historical 3-D mode;
- Clear Cache next to dark mode;
- degraded optional-source behavior while unaffected features remain usable.

P&L and Stock history remain on their existing pages in the authoritative design, avoiding duplicated workflows.

This bundle is an implementation specification and interactive design prototype, not a production connector release. Private credentials and connector bodies are intentionally excluded.


---

<a id="source-015"></a>

# Source 015 — `docs/rebirth-v3-spec:docs/rebirth-v3/REVISION_V3.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/REVISION_V3.md`
- **SHA-256:** `1c3ee41008aaf0f7364892ccd75055fccc68fd4c0cbb23393cfe3b6b854cc0e7`

---

# Rebirth V3 revision summary

This V3 specification supersedes the V2 revision 1.2 target architecture while preserving its
accepted product decisions:

- Default Risk view selects Activity 1, Activity 2, and Activity 3.
- Clear Cache sits next to Theme and performs a controlled full reset/date recalculation.
- Top Promotions is a collapsed full-width flat table beneath the top workspace.
- Risk Explorer remains a native pivot with a collapsible field drawer and bounded viewports.

V3 changes the previous proposal in four major ways:

1. Reduces the target from more than 400 files to a lean tree of fewer than 100 production/config
   files.
2. Replaces separate Quick Risk and Quick Market expanders with top workspace tabs beside Aggregate
   P&L.
3. Adds a consistent 3D current and historical chart contract for Quick Risk and Quick Market,
   including Risk history snapshots and Play/Pause for every time-framed 3D view.
4. Replaces coarse failure behavior with operationally open, financially quarantined degraded
   partitions.


---

<a id="source-016"></a>

# Source 016 — `docs/rebirth-v3-spec:docs/rebirth-v3/v3.1/IMPLEMENTATION_CHECKLIST.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/v3.1/IMPLEMENTATION_CHECKLIST.md`
- **SHA-256:** `02d0b0425ba86af4130ec1f1ebe7e2beb2168cbb43bfc6971925ce4692ba6d30`

---

# V3.1 implementation checklist

- [ ] Inventory every V1 page, field, hierarchy level, table, chart, sender action, and saved-view action.
- [ ] Keep Filter View and Risk View state independent.
- [ ] Seed the immutable Activities 1–3 Filter View before the first Risk query.
- [ ] Implement direct Cross, SplitVA, Credit, and custom Risk View selection.
- [ ] Use the closed V1 dimension and metric allowlists.
- [ ] Preserve Credit measures and XVA/Hedges breakdowns.
- [ ] Build bounded row and column viewport responses; do not add AG Grid.
- [ ] Put Aggregate P&L, Quick Risk, and Quick Market in one current-analytics tab set.
- [ ] Keep exact current value tables beside Quick charts.
- [ ] Add Risk and Market history to Data with correct zero-, one-, and two-axis semantics.
- [ ] Use chart-local TimelinePlayer instances.
- [ ] Preserve historical P&L hierarchy, disclosures, chart, and daily table on P&L.
- [ ] Preserve Stock hierarchy, source rows, chart, and daily table on Stock.
- [ ] Keep optional failures feature-local and visible.
- [ ] Keep all financial validation, commit, write, adjustment, and send boundaries fail-closed.
- [ ] Verify Clear Cache stops players, advances reset generation, recomputes date authority, and restores the default Filter View.
- [ ] Run parity, architecture, performance, and browser smoke tests before deleting V1 compatibility modules.

---

<a id="source-017"></a>

# Source 017 — `docs/rebirth-v3-spec:docs/rebirth-v3/v3.1/README.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/v3.1/README.md`
- **SHA-256:** `7594cb1f9c02ce25a6c49f2809eb893c40b6305da2f7c94e4482ca387e7c75fd`

---

# Rebirth V3.1 — preservation-first correction

This revision restores the V1 information and interactions that the first V3 prototype compressed too aggressively.

## Authoritative decisions

- **Filter View** and **Risk View** are separate saved concepts.
- The immutable Risk Filter View defaults to `Activity 1`, `Activity 2`, and `Activity 3`.
- Built-in Risk Views are directly selectable as **Cross**, **SplitVA**, and **Credit**; user-created Risk Views appear in the same selector.
- A Risk View saves **Rows, Columns, Metrics, Local Filters, Sort, Totals, Display, and viewport defaults**.
- The field allowlist retains Product, Portfolio, Activity, Signoff Group, Category, Sub Category, Risk Type, Risk Greek, Display Bucket, Promotion Reason, Region, Group, Reported Underlying, Underlying, Tenor Swap, Tenor Option, and Split.
- The metric allowlist retains Risk, dRisk, P&L, Open, Current, Move, XVA/Hedges breakdowns, and supplied Credit measures such as SP01, PSP01, PM01, PM01P, Theta, and JTD.
- Product remains a Risk Explorer dimension. It is **not** used as a fabricated Quick Risk chart axis.
- One-tenor historical 3-D charts use `X = tenor`, `Y = date/time`, `Z = value`.
- Two-tenor selected-date playback uses `X = Tenor Swap`, `Y = Tenor Option`, `Z = value`, with date as the frame.
- Fixed-swap history uses Option Tenor × Date × Value; fixed-option history uses Swap Tenor × Date × Value.
- Play/Pause is chart-local and stops on navigation or any identity, metric, period, view, tenor, revision, visibility, or Clear Cache change.
- The **Data** page owns Risk and Market history only.
- Historical P&L remains on the P&L page with its hierarchy table, Daily Predict, MTD C/P, YTD C/P, chart, and exact daily observations.
- Historical Stock remains on the Stock page with its hierarchy, source rows, chart, and exact daily observations.
- The shell is fail-soft for optional feature failures; financial validation, commits, history writes, adjustments, and P&L sends remain fail-closed.
- No AG Grid is introduced. Logical wide pivots use bounded row and column viewports.

## Local review artifacts

The generated review bundle contains:

- `Rebirth_V3_1_Preservation_First_Architecture_Product_and_Migration_Spec.md`
- `Rebirth_V3_1_Preservation_First_Architecture_Product_and_Migration_Spec.pdf`
- `Rebirth_V3_1_Preservation_Prototype.html`
- `risk_view_contracts_v3_1.json`
- `ui_contracts_v3_1_preservation.json`
- `feature_preservation_matrix_v3_1.csv`
- browser smoke-test evidence and preview images.

The implementation remains a compact approximately 75-file target rather than returning to the 413-file V2 proposal.

---

<a id="source-018"></a>

# Source 018 — `docs/rebirth-v3-spec:docs/rebirth-v3/v3.1/REVISION_V3_1.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/v3.1/REVISION_V3_1.md`
- **SHA-256:** `9b449a075d5d109013507cb2eecd827ca82db1c3b685949e5b57a3a9c5b0b0e8`

---

# Revision V3.1

V3.1 corrects the first V3 prototype after comparing it again with the current V1 implementation.

## What was wrong

The first prototype compressed several independent V1 concepts into one simplified screen. In doing so it obscured direct Cross/SplitVA switching, removed visible page filters, used Product as a visual depth category, and underrepresented the historical P&L and Stock tables.

## Corrected design

### Filter Views

Filter Views own Activity, Signoff Group, Portfolio, Category, Sub Category, and include/exclude mode. The immutable default is `Default - Activities 1-3`.

### Risk Views

Risk Views own the pivot layout and are independently saved. Built-in choices are Cross, SplitVA, and Credit. A custom Risk View stores Rows, Columns, Metrics, Local Filters, Sort, Totals, Display, and viewport settings.

### Chart semantics

Product is retained as a table dimension but is never invented as a 3-D chart axis. One-tenor full history is Tenor × Date × Value. Two-tenor playback is Swap Tenor × Option Tenor × Value with date as the frame. Fixed-tenor modes retain a real 3-D surface by using the remaining tenor and date axes.

### Playback

Every historical 3-D panel owns a local player. It stops when navigation, tab, identity, metric, period, view, fixed tenor, revision, browser visibility, or Clear Cache changes.

### Historical ownership

Data owns Risk History and Market History. P&L history remains on P&L. Stock history remains on Stock. Their hierarchy tables, exact daily rows, charts, editors, validation, comparison rows, and saved filters remain part of the acceptance contract.

### Failure model

Optional adapter or history failure degrades only the affected feature. Invalid financial schemas, duplicate identities, writes, snapshot commits, adjustments, and sends continue to fail closed.

---

<a id="source-019"></a>

# Source 019 — `docs/rebirth-v3-spec:docs/rebirth-v3/v3.2/REVISION_V3_2.md`

- **Branch:** `docs/rebirth-v3-spec`
- **Path:** `docs/rebirth-v3/v3.2/REVISION_V3_2.md`
- **SHA-256:** `96ab7258ceb4f4cdb49f7ef3f4250962badaf86d7495d7c19322a7a0feab1718`

---

# Rebirth V3.2 — plot logic, Risk Explorer navigation, and migration correction

This revision is normative and supersedes conflicting plot, playback, and Risk Explorer navigation decisions in earlier V2, V3, and V3.1 documents.

Reviewed baseline:

```text
Repository: streamlitdash/Rebirth
Branch: main
Commit: 6559e46faf2a2fcb3acadd06700910bf3bd0aae8
```

## ProductSpec is the plot authority

`ProductSpec.axes` is the only authority for determining current and historical Risk/Market chart shape. Do not infer dimensionality from product-name strings or incidental null values.

### Zero tenor axes

Examples: FX Delta and FX Gamma.

Current Quick Risk and Quick Market have no tenor chart. They show value cards, status metadata, exact tables, and a history link.

Historical Data uses a normal date line:

```text
X = Risk Date or Market Date
Y = selected Risk or Market value
```

Do not invent a decorative third dimension. Play/Pause is not required because the complete time series is already visible.

### One tenor axis

Examples: IR Delta, IR Gamma, IR XCCY, IR Basis, IR Inflation, IR Bond, Credit Delta, Credit Vega, FX Vega, and Commodity Delta/Vega.

Current Quick views use a normal current tenor curve:

```text
X = governed Tenor Swap or Tenor Option
Y = selected current value
```

Historical Data defaults to a true 3-D surface:

```text
X = tenor
Y = Risk Date or Market Date
Z = selected value
```

The history bundle loads once. External Play/Pause moves a highlighted selected-date curve and updates only that widget's date pill, slider, and exact-value table.

Alternative modes include a specific-tenor historical line, Date A/B curves, and a difference curve.

### Two tenor axes

Examples: IR DeltaVega, IR XCCYVega, and IR InflationVega.

Current Quick views use:

```text
X = Tenor Swap
Y = Tenor Option
Z = selected value
```

Historical Data supports:

```text
Full selected-date surface playback
Tenor Swap history with one Tenor Option slice
Tenor Option history with one Tenor Swap slice
Date A, Date B, and Date B minus Date A surfaces
```

The slice modes remain 3-D:

```text
Tenor Swap history:
    X = Tenor Swap
    Y = Date
    Z = value

Tenor Option history:
    X = Tenor Option
    Y = Date
    Z = value
```

## Tenor ordering

Connector-owned order columns remain authoritative.

1. Require non-negative finite integer ranks.
2. Reject conflicting ranks for one tenor label within an exact identity.
3. Reject rank collisions within an exact identity.
4. Sort ascending so the smallest governed tenor appears first.
5. Use text parsing only for genuinely unranked labels.
6. Freeze one canonical order for an entire historical playback query.
7. Keep missing cells null rather than filling them with zero.
8. Flag fallback ordering as `ORDER_AMBIGUOUS`.
9. Preserve camera, grid shape, Z range, and color range during playback.

## Quick-to-Data handoff

Quick Risk and Quick Market pass a typed history request containing the exact source/risk identity, selected metric, revision, snapshot date, and kind (`risk` or `market`).

Quick Risk also passes its active Filter View because historical contributor aggregation must match the current reporting scope. Quick Market does not accept Portfolio/reporting filters because those fields do not exist at market-quote grain.

Data opens with a locked identity breadcrumb. Users may change period, metric, projection, or slice tenor without choosing the Underlying again.

## Exact tables remain mandatory

Charts supplement exact data and never replace it.

Quick Risk retains:

```text
Risk Date
Source Type
Risk Type
Risk Greek
Underlying
Tenor Swap
Tenor Option
Product
Portfolio
Activity
Signoff Group
Category
Sub Category
Split
Risk
dRisk
P&L
Mapping Status
```

Quick Market retains:

```text
Market Date
Source Type
Risk Type
Risk Greek
Underlying
Tenor Swap
Tenor Option
Tenor Swap Order
Tenor Option Order
Open
Current/OFFICIAL
Move
Market Status
Market Data Status
```

Data retains both a selected-date exact table and a raw historical rows table.

## Playback isolation

Each player is keyed by widget, kind, exact identity, metric, projection, period, and history generation. Play/Pause is external to Plotly.

A player may update only its own chart, selected-date table, slider, date pill, and button label. It must stop when identity, metric, projection, period, or slice changes; when the user changes mini-tab or page; when the chart unmounts; when Clear Cache advances the reset generation; when history generation changes; or when the feature becomes unavailable.

A playback callback must never update page filters, Risk Explorer state, another chart, Aggregate P&L, Top Promotions, Stock, or P&L.

## Risk Explorer top navigation

Render exactly:

```text
Cross | SplitVA | Custom
```

Cross and SplitVA remain immutable built-in modes. Custom views never create more browser tabs.

The Custom tab contains one saved-view dropdown plus:

```text
New
Clone Cross
Clone SplitVA
Edit
Save copy
Rename
Delete
```

A Custom Risk View stores validated Rows, Columns, Measures, view-local filters, sort, totals, and viewport settings. Structural fields and Measures come from closed allowlists. A document contains no financial frame payload.

## Existing mini-tabs and controls remain

Below Cross/SplitVA/Custom, preserve dynamic Risk Type tabs:

```text
Credit | IR | FX | Commo | Cash Flow
```

When Credit is active, preserve:

```text
Single | Multi
```

Cross + Credit Single applies one selected Credit measure to the ordinary Cross hierarchy.

Cross + Credit Multi selects Risk, dRisk, or P&L and displays SP01, PSP01, PM01, PM01P, Theta, and JTD as measure columns.

SplitVA + Credit uses Single only. Do not produce a reporting-dimension × credit-measure Cartesian column explosion.

When IR is active, preserve:

```text
Delta | Basis | Vega
```

These are product-family tabs, not saved Custom views.

Also preserve the Split filter, reporting dimension, underlying sort, Promotion toggle, Region toggle, SplitVA metric, expandable Cross metrics, Credit controls, Risk Detail controls, Product, Portfolio, Activity, Signoff Group, Category, and Sub Category.

## Filter Views remain separate

A page Filter View controls source-row eligibility:

```text
Activity
Signoff Group
Portfolio
Category
Sub Category
Include / Exclude
```

The immutable default remains:

```text
Default - Activities 1-3
Activity IN {Activity 1, Activity 2, Activity 3}
```

Cross, SplitVA, or a selected Custom definition then controls table presentation. Saving one kind of view must never overwrite the other.

## Historical ownership

```text
Data:
    Risk History
    Market History

Stock:
    Current comparison
    Stacked hierarchy
    Source comparison rows
    Historical Stock chart
    Historical Stock table

P&L:
    Aggregate P&L
    Editors and sending
    Predict/Colossus validation
    Historical hierarchy
    Daily Predict
    MTD Colossus and Predict
    YTD Colossus and Predict
    Historical chart
    Raw historical rows
```

## Fail-soft and fail-closed boundaries

The shell and unaffected features remain usable when an optional source fails. The affected identity is marked unavailable and dependent values are not fabricated.

Continue to fail closed for invalid authoritative schemas, duplicate quote identities, conflicting tenor ranks, non-finite financial values, incomplete snapshot publication, corrupt history partitions, stale reset-generation commits, and P&L send validation.

## Migration order

```text
Phase 0  Characterize and freeze V1 behavior
Phase 1  Extract pure ProductSpec plot policy
Phase 2  Correct current Quick Risk and Quick Market
Phase 3  Guarantee immutable Risk history
Phase 4  Build Risk and Market history on Data
Phase 5  Isolate external playback
Phase 6  Restore Cross, SplitVA, Risk Type, Credit, and IR family navigation
Phase 7  Add the Custom dropdown and repository
Phase 8  Preserve Stock and P&L historical workflows
Phase 9  Run V1/V3.2 parity checks and cut over behind a feature flag
```

Do not begin by deleting the existing component or reducer modules. Characterize V1 first, extract one policy or vertical slice at a time, and remove compatibility paths only after financial and UI parity tests pass.


---
