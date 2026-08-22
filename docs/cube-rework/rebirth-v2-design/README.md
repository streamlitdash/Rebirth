# Rebirth V2 redesign and migration specification

This folder contains the detailed implementation contract for transforming the current Rebirth/Cube codebase into the proposed modular V2 architecture.

Start with:

- [`rebirth-v2-redesign-and-migration-guide.md`](rebirth-v2-redesign-and-migration-guide.md) - the complete redesign, V1-to-V2 mapping, file-by-file ownership catalogue, migration phases, performance budgets and definition of done.
- [`rebirth-v2-redesign-and-migration-guide.pdf`](rebirth-v2-redesign-and-migration-guide.pdf) - the same guide as a reviewed 53-page PDF.
- [`rebirth-v2-implementation-documentation-bundle.zip`](rebirth-v2-implementation-documentation-bundle.zip) - the complete Markdown/PDF/DOCX, maps, catalogues, diagrams and test specifications.

Supporting files:

- `target-tree.txt`
- `v1-to-v2-file-map.csv`
- `new-file-catalog.csv`
- `ui-design-system.md`
- `fake-data-and-promotion-scenarios.md`
- `migration-runbook.md`
- `acceptance-test-matrix.md`
- `diagrams/`

The key UI decisions are: retain Cube page logic and financial semantics; use boxed cards and responsive side-by-side panels; replace Top Book with a flat ranked Top Promotions table plus Promotion Summary; use a hideable PivotSpec field sidebar for Risk Explorer; keep Quick Market current-only; and move historical Market, Stock, P&L and optional Risk analysis into a dedicated Data page.
