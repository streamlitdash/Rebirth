# Saved filter views

The app writes validated, page-scoped JSON beneath this directory at runtime:

```text
saved_views/
  risk/
  stock/
  pnl/
```

Each view stores only the five filter selections and the include/exclude mode.
It never stores financial DataFrames. Writes use an atomic replacement and a
short cross-worker lock.

Plotly app filesystems are runtime-local: views can be shared by workers on the
same running app instance, but a restart or redeploy may discard them. Use an
approved durable database or object store if saved views must survive deploys.
