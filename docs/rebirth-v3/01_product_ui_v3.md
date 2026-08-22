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
