# Rebirth V2 redesign and migration specification

This folder contains the GitHub review copy of the implementation contract for transforming the current Rebirth/Cube codebase into the proposed modular V2 architecture.

Start with:

- [`rebirth-v2-redesign-and-migration-guide.md`](rebirth-v2-redesign-and-migration-guide.md) - the complete product/UI decisions, V1-to-V2 architecture mapping, migration phases and definition of done.
- [`target-tree.txt`](target-tree.txt) - the exact proposed package tree, including the new Data page, flat Top Promotions and pivot workspace.
- [`v1-to-v2-file-map.csv`](v1-to-v2-file-map.csv) - old paths, new owners, transformation rules, phases and deletion gates.

Supporting specifications:

- [`ui-design-system.md`](ui-design-system.md)
- [`fake-data-and-promotion-scenarios.md`](fake-data-and-promotion-scenarios.md)
- [`migration-runbook.md`](migration-runbook.md)
- [`acceptance-test-matrix.md`](acceptance-test-matrix.md)

The accompanying assistant delivery also includes a reviewed 53-page PDF, the full 235-file ownership catalogue, diagrams, DOCX source and one ZIP bundle. Those binary/large artifacts are delivered directly rather than committed to this branch.

The key UI decisions are: retain Cube page logic and financial semantics; use boxed cards and responsive side-by-side panels; replace Top Book with a flat ranked Top Promotions table plus Promotion Summary; use a hideable PivotSpec field sidebar for Risk Explorer; keep Quick Market current-only; and move historical Market, Stock, P&L and optional Risk analysis into a dedicated Data page.
