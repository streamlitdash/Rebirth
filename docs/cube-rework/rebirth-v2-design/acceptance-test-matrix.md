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
