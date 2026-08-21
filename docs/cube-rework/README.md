# Cube rework analysis

This directory contains the architecture review and interactive examples prepared from the current Rebirth codebase.

## Files

- [Architecture deep dive](architecture-deep-dive.md) — current-state findings, target architecture, file-by-file changes, migration sequence, and performance plan.
- [Interactive prototype](interactive-prototype.html) — synthetic examples for initialization, promotion ownership, Market dimensionality, Stock history, P&L history, 500-Portfolio views, and Pyright.
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

## Opening the prototype

GitHub does not execute an HTML file in its normal code viewer. Download `interactive-prototype.html` and open it in a browser, or serve this directory locally:

```bash
python -m http.server 8000 --directory docs/cube-rework
```

Then open `http://localhost:8000/interactive-prototype.html`.

The prototype uses synthetic data and loads Plotly from its public CDN. It does not connect to financial sources or modify application state.
