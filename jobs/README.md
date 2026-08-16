# Official Risk and P&L archive job

`archive_official_risk.ipynb` is the thin Jupyter Scheduler wrapper around the
tested Python archive API. It refreshes one coherent Risk snapshot, checks that
today's market source is `OFFICIAL`, reads Colossus P&L, and atomically publishes
one completed date partition.

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
job publishes.

The daily leaf is:

```text
<PL_HISTORICAL_PATH>/<YYYY-MM-DD>/
    risk.csv
    colossus.csv
    _SUCCESS
```

Predict (`P`) is projected from `risk.csv` column `PL`; Colossus (`C`) is read
from `colossus.csv`. Validate P&L and Histo P&L use these same two official
files. Only leaves with a valid `_SUCCESS` manifest are accepted as official.

Existing checked-in leaves containing `histo.csv` plus `predicted.csv` remain
supported only as legacy/demo compatibility data. New official dates use the
three-file contract above. The scheduled notebook records its `ArchiveResult`
in the Jupyter job output.
