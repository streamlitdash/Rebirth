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
