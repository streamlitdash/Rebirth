# Rebirth reconstruction record

This repository reconstructs the Cube application from the source fragments
supplied for the Rebirth recovery.  It is intentionally runnable without any
private market, risk, checker, portfolio, messaging, or credential services.

## Source authority

When two sources disagree, reconstruction follows this order:

1. a complete, internally consistent supplied Rebirth implementation;
2. overlapping supplied fragments that agree on the same contract;
3. the clean Final Test implementation for missing delimiters, indentation,
   public contracts, and behavior covered by tests;
4. the smallest explicit correction needed to make the combined contract
   executable and testable.

Final Test is a recovery reference, not permission to silently discard a
coherent supplied feature.  Every material choice is recorded below.

## Runtime data boundary

The default application uses deterministic CSV fixtures under `data/`.
Production-only imports, credentials, endpoints, and private connector calls
are not executed by this repository.  Replaceable connector contracts remain
in the normal adapter/feed boundary so an authorized deployment can inject
real implementations later without changing the financial or UI layers.

The fixture mode covers readiness, checker inventory, Risk, Open, Current,
portfolio configuration, thresholds, reported-underlying mapping, and the
local P&L-send fallback.  P&L itself is calculated by the core pipeline from
the fixture Risk/Open/Current inputs rather than stored as an unexplained
output file.

## Reconstructed source map

| Supplied material | Rebirth target | Reconstruction rule |
|---|---|---|
| `s01_app.py` | `s01_app.py` | Preserve composition behavior; keep fixture manager as the default. |
| `s01_schema_part_1.py` | `core/s01_schema.py` | Preserve coherent schema changes and validate downstream names. |
| `s02_pipeline_*` and `45454` | `core/s02_pipeline.py` | Resolve overlapping versions by contract/tests; never raw-concatenate fragments. |
| `s03_search_*` | `core/s03_search.py` | Restore lost indentation and exact-search contracts. |
| `s04_pl_part1.py` | `core/s04_pl.py` | Preserve public normalization/storage contracts. |
| `s05_storage_part1.py` | `core/s05_storage.py` | Keep adjustment persistence compatible with the P&L layer. |
| `s06_mapping_part_1.py` | reporting module | Preserve reported-underlying validation and attachment. |
| adapter/feed fragments | `adapters/`, `feeds/` | Retain public adapter shapes; default to fixture-backed implementations. |
| component fragments | `ui/s04_components.py` | Restore wrappers, indentation, and one complete component tree. |
| event fragments | `ui/s07_events.py` | Restore one owner per callback Output and complete callback return contracts. |
| PL fragments | `ui/s06_plview.py`, `ui/s08_plevents.py` | Preserve lazy P&L send/explorer behavior with a local fallback. |
| factory fragment | `ui/s09_factory.py` | Preserve one app, one router, and cold-start mounting. |
| JS/CSS fragments | `assets/` | Remove Markdown fences/duplicate chunks and repair only proven token/ID corruption. |

## Material decisions

- The byte-for-byte duplicate pipeline fragment `s02_pipeline_part_3777` is
  represented once. Fragment placeholders, orphaned tails, duplicate
  definitions, Markdown fences, stripped indentation, and proven invalid token
  substitutions were treated as transfer damage rather than application logic.
- Misspellings or undefined names were corrected only when the intended public
  symbol was proved by callers, tests, or the clean reference implementation.
  Examples include the public `risk_action_view_token`, `StaleRefreshError`,
  `selected_status`, and the Force draft's actual `base_revision`.
- Supplied schema changes were retained consistently: external Product is
  `XVA`/`Hedges`; the optional reporting field is `Sub Category`; P&L governance
  uses `ConcertoField`; and Credit exposes SP01, PSP01, PM01, PM01P, Theta, and
  JTD as complete Risk/dRisk pairs.
- Split is the Risk/Gamma/New Trades/XGAMMA filter axis. `Risk Greek`
  remains a classification and IR-family concept; there is no generic Greek
  dropdown. Underlying sort, Promotion, and Region remain separate controls.
- Credit may carry optional Region. The fixture feed derives a deterministic
  demo Region from Group only so the UI path is executable; it is explicitly
  not production geography. Non-Credit products do not fabricate Region.
- XGAMMA and New Trades keep strict public loader and atomic-cache contracts.
  Their checked-in sources now contain visibly fake Credit examples so the
  dual source/developed XGAMMA path and traded-level/Open-reference New Trades
  path can be exercised end to end; replacing those adapters does not change
  the shared validation boundary.
- The alternative supplied financial-formula fragments were not selected where
  they contradicted one another: they contained product-key misspellings,
  incompatible gamma scale prose/code, undocumented factors, and inconsistent
  Open/Current/Move treatment. The tested metadata-driven formula path from the
  clean reference was retained instead of guessing a financially material rule.
- Risk, dRisk, and P&L thresholds are retained on committed internal dashboard
  rows and must be finite, non-null, and positive. This makes Promotion
  recomputation after Split/reporting filters deterministic.
- The supplied two-axis detail design is preserved as a heatmap plus a surface
  matrix (option-tenor rows and swap-tenor columns), rather than changing it
  back to an older long-form table merely to satisfy a stale assertion.
- Callback reconstruction keeps exactly one owner for every critical Output.
  `allow_duplicate` and global `initial_duplicate` were not used. Cold startup,
  table/detail rendering, date drafts, and P&L workflows retain distinct owners.
- The Static Data page is path-safe and exposes only an approved list of files.
- Real connector code is not imported at module load. The default application
  remains fully usable with fixtures and no private environment. The old
  `s08_plsend.csv`, inherited Plotly application IDs, and two stale pre-Rebirth
  PDF exports were removed after all live references were migrated. The current
  Plotly metadata belongs to the newly created Rebirth application.
- No broad style or architecture refactor was performed. Changes beyond source
  reconstruction were limited to contract alignment, fixture isolation,
  correctness, deterministic testing, and removal of proven duplicates or
  obsolete deployment artifacts.

## Validation record

- Deterministic fixture generation/check: passed for all seven generated source
  CSVs; the governed Concerto and reported-underlying mappings are checked in.
- Backend focused suite: 80 passed.
- Overlay suite during reconstruction: 5 passed.
- Complete suite: **123 passed**, with only Dash's known native DataTable
  deprecation warnings.
- Ruff check and Ruff format check: passed.
- Python compilation for root/core/feed/adapter/UI modules: passed.
- Browser JavaScript syntax checks: passed.
- Fixture refresh smoke: committed revision 1; dashboard shape `(1140, 45)`;
  Product values XVA/Hedges; Split values Risk/Gamma; Credit Region populated;
  all three threshold columns positive and non-null.
- Warm layout: 158 unique component IDs and no duplicates.
- Callback graph: 49 registered callbacks, no multiplexed callback-map keys,
  and exactly one owner for revision, hero/status, table/detail, date-draft,
  Split, Promotion, Region, and Quick Search outputs.
- Credential/proprietary-boundary scan: no private connector import, credential
  marker, embedded secret, inherited remote, or Plotly application ID is wired
  into the runtime.
- Final private-repository and browser-startup verification are recorded in the
  handoff commit/publish result rather than hard-coded here.
