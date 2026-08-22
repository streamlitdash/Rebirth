# V3.1 implementation checklist

- [ ] Inventory every V1 page, field, hierarchy level, table, chart, sender action, and saved-view action.
- [ ] Keep Filter View and Risk View state independent.
- [ ] Seed the immutable Activities 1–3 Filter View before the first Risk query.
- [ ] Implement direct Cross, SplitVA, Credit, and custom Risk View selection.
- [ ] Use the closed V1 dimension and metric allowlists.
- [ ] Preserve Credit measures and XVA/Hedges breakdowns.
- [ ] Build bounded row and column viewport responses; do not add AG Grid.
- [ ] Put Aggregate P&L, Quick Risk, and Quick Market in one current-analytics tab set.
- [ ] Keep exact current value tables beside Quick charts.
- [ ] Add Risk and Market history to Data with correct zero-, one-, and two-axis semantics.
- [ ] Use chart-local TimelinePlayer instances.
- [ ] Preserve historical P&L hierarchy, disclosures, chart, and daily table on P&L.
- [ ] Preserve Stock hierarchy, source rows, chart, and daily table on Stock.
- [ ] Keep optional failures feature-local and visible.
- [ ] Keep all financial validation, commit, write, adjustment, and send boundaries fail-closed.
- [ ] Verify Clear Cache stops players, advances reset generation, recomputes date authority, and restores the default Filter View.
- [ ] Run parity, architecture, performance, and browser smoke tests before deleting V1 compatibility modules.