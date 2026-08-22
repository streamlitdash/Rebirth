# Migration runbook

## Phase 0 - Characterize V1

- Freeze representative Risk, Market, P&L, Stock and archive fixtures.
- Add golden outputs for dashboard_frame, market_frame, combined_pl, unmapped_frame and search results.
- Record callback IDs and page routes that must remain compatible.
- Record timing and payload baselines.
- Do not change runtime behavior in this phase.

Exit: V1 behavior is reproducible and failures are visible as tests rather than tribal knowledge.

## Phase 1 - Scaffold V2

- Add the package, runtime settings, composition container and factories.
- Copy shell-first/JupyterHub behavior without refactoring financial code.
- Create ports and architecture tests.
- Run V1 and V2 entrypoints side by side against the same fixture manager.

Exit: V2 starts, exposes health/progress and renders a placeholder page without source I/O during import.

## Phase 2 - Adapter boundary

- Wrap every current feed function in one typed port implementation.
- Split fake and production implementations.
- Preserve batch versus per-underlying call behavior.
- Move only source-independent validation into domain modules.

Exit: the old feed module is no longer imported by pages or domain; connector contract tests pass.

## Phase 3 - Pipeline decomposition

- Extract pure date, market merge, P&L, mapping and promotion functions.
- Build explicit stage classes around those exact functions.
- Execute the new coordinator in shadow mode and compare PipelineResult to V1 snapshot.
- Keep V1 manager as production authority until parity is proven.

Exit: all golden snapshots match and injected stage failures retain the previous revision.

## Phase 4 - Snapshot and query layer

- Introduce SnapshotStore, CurrentSnapshot and RevisionIndex.
- Move bounded filter search and pivots into application queries.
- Make pages request targeted DTOs rather than copy complete snapshots.

Exit: V2 page services do not access manager internals or full unrelated frames.

## Phase 5 - Page packages and card shell

- Implement shared card/split-panel design system.
- Move shell callbacks first, then Risk layout/callbacks.
- Keep existing page routes and current financial outputs.
- Add architecture tests for page isolation.

Exit: Risk page owns its callbacks and visual regression matches approved card design.

## Phase 6 - Top Promotions and Pivot Explorer

- Replace Top Book with TopPromotionRow query and flat table.
- Add Promotion Summary side card.
- Introduce PivotSpec, field sidebar and bounded row/column viewport.
- Remove hard-coded Region/Promotion/order controls only after equivalent fields work in sidebar.
- Stop filtered promotion recomputation in ordinary callbacks; retain explicit Recalculate action.

Exit: promotion scenarios and pivot acceptance matrix pass; Top Book code is unused and removable.

## Phase 7 - Data page and history

- Define HistoryRepository and Parquet schemas/manifests.
- Add legacy readers and migration jobs.
- Implement Data page and Quick Market deep-link.
- Add 0/1/2-axis outright/Move modes, period presets, A/B and playback.
- Migrate Market first, then Stock and P&L; run readers in dual mode until reconciled.

Exit: legacy and Parquet query outputs match and Data page meets playback/query budgets.

## Phase 8 - Stock, P&L and Statics modularization

- Move each page into its package without changing financial behavior.
- Preserve adjustment and saved-view repositories behind ports.
- Retain DataTable only where governed editing requires it; do not introduce AG Grid.

Exit: all page parity and isolation tests pass.

## Phase 9 - Cutover and cleanup

- Run V1 and V2 in parallel against the same production-like source snapshots.
- Compare revisions and user workflows for an agreed period.
- Switch deployment to V2.
- Delete old modules only when no imports, tests or operations depend on them.
- Keep migration readers until historical reconciliation is signed off.

Exit: V2 is sole runtime, rollback package is documented, and obsolete code is removed in small reviewable commits.
