# Rebirth V3 implementation specification bundle

This is the authoritative compact V3 handoff. It replaces the 413-file V2 target with a 75-file design grounded in the current V1 code.

## Start here

- `Rebirth_V3_Architecture_Product_and_Migration_Spec.md`
- `Rebirth_V3_Architecture_Product_and_Migration_Spec.pdf`
- `REVISION_V3.md`
- `Rebirth_V3_Interactive_Prototype.html`

## Review chapters

- `01_product_ui_v3.md`
- `02_viability_and_simplification.md`
- `03_compact_architecture_file_catalog.md`
- `04_data_pipeline_contracts.md`
- `05_v1_to_v3_mapping.md`
- `06_implementation_runbook.md`
- `07_fake_data_testing_performance.md`
- `08_acceptance_and_llm_rules.md`

## Machine-readable handoff

- `architecture_manifest.yaml`
- `ui_contracts.json`
- `file_catalog.json`
- `v1_to_v3_mapping.csv`
- `implementation_phases.yaml`
- `SHA256SUMS.txt`

## Prototype scope

The HTML prototype demonstrates:

- immutable `Default - Activities 1-3`;
- Aggregate P&L / Quick Risk / Quick Market mini tabs;
- current 3-D Quick Risk and Quick Market tenor views with exact values;
- full-width collapsed Top Promotions beneath the workspace;
- native pivot field drawer without AG Grid;
- Risk and Market history on the Data page;
- Play/Pause and date slider for every historical 3-D mode;
- Clear Cache next to dark mode;
- degraded optional-source behavior while unaffected features remain usable.

P&L and Stock history remain on their existing pages in the authoritative design, avoiding duplicated workflows.

This bundle is an implementation specification and interactive design prototype, not a production connector release. Private credentials and connector bodies are intentionally excluded.
