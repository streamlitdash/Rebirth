# Rebirth V3.1 — preservation-first correction

This revision restores the V1 information and interactions that the first V3 prototype compressed too aggressively.

## Authoritative decisions

- **Filter View** and **Risk View** are separate saved concepts.
- The immutable Risk Filter View defaults to `Activity 1`, `Activity 2`, and `Activity 3`.
- Built-in Risk Views are directly selectable as **Cross**, **SplitVA**, and **Credit**; user-created Risk Views appear in the same selector.
- A Risk View saves **Rows, Columns, Metrics, Local Filters, Sort, Totals, Display, and viewport defaults**.
- The field allowlist retains Product, Portfolio, Activity, Signoff Group, Category, Sub Category, Risk Type, Risk Greek, Display Bucket, Promotion Reason, Region, Group, Reported Underlying, Underlying, Tenor Swap, Tenor Option, and Split.
- The metric allowlist retains Risk, dRisk, P&L, Open, Current, Move, XVA/Hedges breakdowns, and supplied Credit measures such as SP01, PSP01, PM01, PM01P, Theta, and JTD.
- Product remains a Risk Explorer dimension. It is **not** used as a fabricated Quick Risk chart axis.
- One-tenor historical 3-D charts use `X = tenor`, `Y = date/time`, `Z = value`.
- Two-tenor selected-date playback uses `X = Tenor Swap`, `Y = Tenor Option`, `Z = value`, with date as the frame.
- Fixed-swap history uses Option Tenor × Date × Value; fixed-option history uses Swap Tenor × Date × Value.
- Play/Pause is chart-local and stops on navigation or any identity, metric, period, view, tenor, revision, visibility, or Clear Cache change.
- The **Data** page owns Risk and Market history only.
- Historical P&L remains on the P&L page with its hierarchy table, Daily Predict, MTD C/P, YTD C/P, chart, and exact daily observations.
- Historical Stock remains on the Stock page with its hierarchy, source rows, chart, and exact daily observations.
- The shell is fail-soft for optional feature failures; financial validation, commits, history writes, adjustments, and P&L sends remain fail-closed.
- No AG Grid is introduced. Logical wide pivots use bounded row and column viewports.

## Local review artifacts

The generated review bundle contains:

- `Rebirth_V3_1_Preservation_First_Architecture_Product_and_Migration_Spec.md`
- `Rebirth_V3_1_Preservation_First_Architecture_Product_and_Migration_Spec.pdf`
- `Rebirth_V3_1_Preservation_Prototype.html`
- `risk_view_contracts_v3_1.json`
- `ui_contracts_v3_1_preservation.json`
- `feature_preservation_matrix_v3_1.csv`
- browser smoke-test evidence and preview images.

The implementation remains a compact approximately 75-file target rather than returning to the 413-file V2 proposal.