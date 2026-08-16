# Saved filter views

The app writes every named Risk, Stock, and P&L filter view into one validated
catalogue beneath this directory at runtime:

```text
saved_views/
  shared/
    <normalized-name>--<hash>.json
```

Each view stores only the five filter selections and the include/exclude mode.
It never stores financial DataFrames. Writes use an atomic replacement and a
short cross-worker lock. A view created on one of the three pages is therefore
available from the other two pages.

The catalogue is shared; live page state is not. Risk, Stock, and P&L retain
separate selected-view values, filter values, and include/exclude modes. A view
changes a page only when it is explicitly selected on that page. Within one
filter, multiple values are ORed; populated filters are ANDed with one another.
For example, Activity `Credit` plus Portfolio `B` and `D` means
`Credit AND (B OR D)`.

The always-present **Base / No view** option is not written to disk. Selecting
it clears that page's five filters and restores include mode. With Base active,
the editor shows **Save New**; with a named view active, it shows **Update View**
and overwrites that view's stored filter definition. The saved-view editor is a
collapsed disclosure by default so it does not occupy permanent page space.

Plotly app filesystems are runtime-local: views can be shared by workers on the
same running app instance, but a restart or redeploy may discard them. Use an
approved durable database or object store if saved views must survive deploys.
