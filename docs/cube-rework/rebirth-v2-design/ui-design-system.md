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
