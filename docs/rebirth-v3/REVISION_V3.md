# Rebirth V3 revision summary

This V3 specification supersedes the V2 revision 1.2 target architecture while preserving its
accepted product decisions:

- Default Risk view selects Activity 1, Activity 2, and Activity 3.
- Clear Cache sits next to Theme and performs a controlled full reset/date recalculation.
- Top Promotions is a collapsed full-width flat table beneath the top workspace.
- Risk Explorer remains a native pivot with a collapsible field drawer and bounded viewports.

V3 changes the previous proposal in four major ways:

1. Reduces the target from more than 400 files to a lean tree of fewer than 100 production/config
   files.
2. Replaces separate Quick Risk and Quick Market expanders with top workspace tabs beside Aggregate
   P&L.
3. Adds a consistent 3D current and historical chart contract for Quick Risk and Quick Market,
   including Risk history snapshots and Play/Pause for every time-framed 3D view.
4. Replaces coarse failure behavior with operationally open, financially quarantined degraded
   partitions.
