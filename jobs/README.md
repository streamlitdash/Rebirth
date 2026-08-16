# Official Risk, P&L, and MarketBook archive job

`archive_official_risk.ipynb` is the thin Jupyter Scheduler wrapper around the
tested Python archive API. It refreshes one coherent Risk snapshot, checks that
the naturally resolved business Market Date is `OFFICIAL`, reads Colossus P&L,
and atomically publishes one completed date partition containing Risk,
Colossus, and the full Quick Market projection. `market_date_for(System Date)`
keeps weekdays unchanged and resolves a natural Saturday or Sunday to the
preceding Friday; explicit weekend view dates are still rejected.

In JupyterLab, create a recurring job for the notebook with this schedule:

```cron
0-55/5 22 * * 1-5
```

That retries every five minutes during the 22:00 hour, Monday to Friday. A run
before the official source is available exits as `skipped`; once a date is
complete, later attempts exit as `already_archived` without calling Colossus or
overwriting files. Configure the Jupyter Scheduler timezone as Europe/London,
or translate the cron hour to the scheduler's server timezone.

Environment variables:

- `risk_cube_project_root` notebook parameter (or the
  `RISK_CUBE_PROJECT_ROOT` environment variable): only needed if Jupyter
  Scheduler stages the notebook outside the repository. Alternatively enable
  the scheduler option that runs the job from the notebook's input folder.
- `PL_HISTORICAL_PATH`: the single durable history root. Relative paths are
  resolved against the project root, not the scheduler's staging directory.
  The default is `<project>/data/histo`.
- `COLOSSUS_LOADER`: optional `module:function` override. The default is
  `feeds.s01_sources:get_colossus_pl`.

Set the same `PL_HISTORICAL_PATH` for the scheduled notebook and the Dash app.
That single setting is what makes Validate P&L and Histo P&L read the files the
job publishes and lets Quick Market load historical context for an exact quote.

The daily leaf is:

```text
<PL_HISTORICAL_PATH>/<YYYY-MM-DD>/
    risk.csv
    colossus.csv
    market.csv
    _SUCCESS
```

Predict (`P`) is projected from `risk.csv` column `PL`; Colossus (`C`) is read
from `colossus.csv`. Validate P&L and Histo P&L use these same two official
P&L files. Predict keeps the archived SignoffGroup, Product, Portfolio, and
filter metadata. Colossus remains an exact Portfolio + Underlying + Risk Type +
Risk Greek source: SignoffGroup and Product are attached only when `risk.csv`
proves one unique Portfolio authority. C-only rows with valid authority remain
mapped with P missing; absent or ambiguous authority is retained exactly once
as Unmapped and never duplicated across Products.

The P&L Explorer's Activity, Signoff Group, Portfolio, Category, and Sub
Category filters govern both Validate P&L and Histo P&L after these files are
loaded. Missing Predict or Colossus observations remain missing rather than
being zero-filled. `market.csv` is independent of P/C and uses the exact ordered
`core.s03_search.MARKET_RESULT_COLUMNS` schema:

```text
Source Type, Risk Type, Risk Greek, Underlying, Tenor Swap, Tenor Option,
Tenor Swap Order, Tenor Option Order, Market Date, Open, Current, Move,
Market Status, Market Data Status
```

It intentionally contains no Portfolio. The stored quote grain is Source Type,
Risk Type, Risk Greek, raw Underlying, and the declared tenor axes. Quick Market
selects an exact Risk Type + Risk Greek + raw Underlying, then an explicit Tenor
Swap + Tenor Option cell, and plots that cell's `Current` through the completed
dates. It does not average cells or zero-fill missing dates. The committed
in-memory quote is authoritative for today and replaces any archived point with
the same date. Open was loaded from `Market Date - BDay(1)`; Current and market
status were loaded for Market Date.

Only leaves with a valid `_SUCCESS` manifest are accepted as official. A
schema-v2 manifest records rows, exact columns, and SHA-256 digests for all
three CSVs, including `market_rows`, `market_columns`, and `market.csv` in its
digest map. Existing schema-v1 official leaves without `market.csv` remain
readable and simply provide no Quick Market observations.

One immutable file per date is deliberate. The scheduler can stage all of one
day's files, publish the leaf with one atomic rename, retry idempotently, and
leave every other day untouched. An append-only long CSV would need shared
append locking, could expose a partial tail, and would couple every date to one
ever-growing rewrite and failure boundary. The market reader fingerprints only
the files needed to establish market authority and caches the selected identity
subset for each unchanged daily leaf; it does not load historical `risk.csv` or
`colossus.csv` just to draw a Quick Market chart.

Existing checked-in leaves containing `histo.csv` plus `predicted.csv` remain
supported only as legacy/demo compatibility data; they may optionally include
an independently validated `market.csv`. New official dates use the four-file
contract above. The scheduled notebook records its `ArchiveResult` in the
Jupyter job output.

For local/Plotly inspection, `data/histo/2026-08-10` is an explicit synthetic
official fixture with `risk.csv`, `colossus.csv`, `market.csv`, and `_SUCCESS`.
Select 2026-08-10 under **Validate P&L** to inspect the P/C table. Its manifest
is marked `synthetic-validate-pl-and-market`; only that official-shaped demo is
allow-listed into Git and the Plotly bundle. Arbitrary runtime official leaves
remain ignored by Git and excluded from deployment, so the deployed demo never
silently packages live scheduler output.
