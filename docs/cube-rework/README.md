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
