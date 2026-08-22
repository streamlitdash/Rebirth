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
