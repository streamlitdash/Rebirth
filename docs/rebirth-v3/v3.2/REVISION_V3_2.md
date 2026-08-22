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
