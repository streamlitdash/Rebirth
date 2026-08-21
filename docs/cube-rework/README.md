# Cube rework analysis

This directory contains the architecture review and interactive examples prepared from the current Rebirth codebase.

## Launch the prototypes in your browser

No download or local server is required. These links render the HTML stored on this GitHub branch with the correct browser content type:

- **[Launch the proposed full Cube application](https://raw.githack.com/streamlitdash/Rebirth/docs/cube-rework-deep-dive/docs/cube-rework/proposed-cube-full-app-prototype.html)**
- **[Launch the IR Vega 3-D date-slider prototype](https://raw.githack.com/streamlitdash/Rebirth/docs/cube-rework-deep-dive/docs/cube-rework/ir-vega-3d-date-slider-prototype.html)**
- **[Launch the architecture interaction prototype](https://raw.githack.com/streamlitdash/Rebirth/docs/cube-rework-deep-dive/docs/cube-rework/interactive-prototype.html)**

The GitHub code viewer intentionally shows HTML source and does not execute JavaScript. The launch links above read the files from this repository and serve them with an HTML content type. The computer must be online because the prototypes load Plotly and, for the full application, its compressed payload from this branch.

## Files

- [Architecture deep dive](architecture-deep-dive.md) — current-state findings, target architecture, file-by-file changes, migration sequence, and performance plan.
- [Full proposed Cube application](proposed-cube-full-app-prototype.html) — integrated fake-data prototype covering Risk, Stock, P&L, Statics, promotion, history, 500-Portfolio views, and refresh behavior.
- [IR Vega 3-D date-slider prototype](ir-vega-3d-date-slider-prototype.html) — draggable and playable double-tenor surface history.
- [Single-file historical tenor Dash app](historical-tenor-surface-app.py) — runnable app for `data/DDMMYYYY.csv` snapshots with an Underlying filter and drag-controlled 3-D date surface.
- [Interactive architecture prototype](interactive-prototype.html) — synthetic examples for initialization, promotion ownership, Market dimensionality, Stock history, P&L history, 500-Portfolio views, and Pyright.
- [Data download and Parquet guide](data-download-and-parquet-guide.md) — source schemas, daily archive layout, and PyArrow conversion/query examples.
- [Architecture overview](overview.svg) — compact target architecture diagram.
- [Market-history interaction](market-history.svg) — dimensional visualization model for zero-, one-, and two-tenor products.

## Main recommendations

1. Stop recalculating promotion after every ordinary UI filter. Use the committed promotion classification, with an explicit **Recalculate for current view** action.
2. Keep Aggregate P&L open, but make Top Book genuinely lazy and closed by default.
3. Replace full reporting-filter option payloads with bounded server-side search.
4. Preserve all Portfolio columns; optimize the 500-column view through indexed aggregation and column virtualization rather than a hard cap.
5. Introduce a common `HistoryRepository` backed by PyArrow/Parquet for Market, Stock, P&L, Risk, and dated Portfolio authority.
6. Archive raw Stock daily, then retain the existing full-outer two-date comparison and view-local promotion threshold.
7. Drive Market-history visualization from `ProductSpec.axes`: line for zero axes, curve/heatmap/surface for one axis, and date-controlled heatmaps plus exact-cell history for two axes.
8. Add Pyright in standard mode repository-wide, with strict mode first on the new history and promotion boundaries.

All prototypes use synthetic data. They do not connect to financial sources or modify application state.
