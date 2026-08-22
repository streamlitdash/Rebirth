# Fake data and promotion scenario specification

## Purpose

Fake data must exercise behavior, not merely make the page look busy. The generator is deterministic and ProductSpec-driven. It should create enough identities and dates to reveal performance and classification errors while remaining understandable during debugging.

## Recommended scale

- 36 to 60 Portfolios, not 500 by default;
- 8 to 12 Signoff Groups;
- 6 Activities and 6 Categories;
- 120 business dates for the standard fixture;
- optional performance profile with 500 Portfolios and larger quote counts;
- all 0-, 1- and 2-axis product shapes;
- mapped and deliberately unmapped Portfolio rows;
- missing Open/current cells, duplicate-rejection fixtures and nonstandard tenor labels.

## Mandatory promotion scenarios

Create named identities for every scenario below. Tests must refer to scenario names rather than relying on random luck.

1. below all thresholds;
2. exactly equal to Risk threshold;
3. Risk-only breach;
4. dRisk-only breach;
5. P&L-only breach;
6. Risk and dRisk breach;
7. Risk and P&L breach;
8. dRisk and P&L breach;
9. all three breach;
10. negative Risk breach using absolute magnitude;
11. negative dRisk breach;
12. negative P&L breach;
13. multiple Portfolio rows aggregating into one promotion identity;
14. XVA and Hedges rows contributing to one promoted identity;
15. unmapped rows excluded from baseline promotion;
16. a filter scope that removes enough rows to de-promote an identity after explicit recalculation;
17. a filter scope that promotes an identity only after explicit recalculation;
18. a stable tie requiring deterministic secondary sorting.

## Market history scenarios

- FX Delta: zero tenor axes and a visible short-lived shock;
- IR Delta: one swap-tenor axis with slope, curvature and daily movement;
- FX Vega or Credit Delta: one axis with missing tenor observations;
- IR DeltaVega, XCCYVega and InflationVega: two axes with localized date/tenor shocks;
- connector-owned tenor ordering that differs from lexical ordering;
- an A/B difference surface with both positive and negative cells;
- stable camera/range tests across playback.

## Stock scenarios

Include Added, Removed, Changed and Unchanged identities, quantity-only changes, market-value-only changes, unmapped Portfolios, multiple currencies and promotion values just below/at/above the selected threshold.

## P&L scenarios

Include Matched, Predict-only and Colossus-only identities, positive and negative differences, missing dates, adjustment overlays and a sender validation failure case.

## Reproducibility

The generator accepts a fixed seed, but named edge cases use explicit values. Regenerating fixtures with the same version and seed must produce byte-for-byte identical canonical CSV/Parquet rows after stable sorting.
