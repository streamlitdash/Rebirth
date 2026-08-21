# Historical data download and Parquet guide

This guide describes how to download and store the historical Market, Stock, Predict, Colossus and Portfolio data for the proposed Cube architecture.

## Main rule

Download **one complete, long-form snapshot per dataset per business date**, then write each snapshot to a date-partitioned Parquet dataset.

Do not:

- append forever to one giant CSV;
- pivot tenors into columns;
- mix Market, Stock, Predict and Colossus into one table;
- replace missing values with zero;
- write a pandas index column.

Physically, each date has its own Parquet partition. Logically, PyArrow reads each dataset directory as one table.

## Recommended directory structure

```text
history/
├── commits/
│   ├── market_date=2026-08-19.json
│   └── market_date=2026-08-20.json
├── market/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
├── stock/
│   └── stock_date=2026-08-20/
│       └── snapshot-....parquet
├── pnl/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
├── risk/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
├── colossus_raw/
│   └── market_date=2026-08-20/
│       └── snapshot-....parquet
└── portfolio_authority/
    └── effective_date=2026-08-20/
        └── snapshot-....parquet
```

Adding a new day means adding a new date partition. It does not mean editing the previous day's file.

---

## 1. Market data

### Download grain

Download one row per exact raw quote cell:

```text
market_date
source_type
risk_type
risk_greek
underlying
tenor_swap
tenor_option
tenor_swap_order
tenor_option_order
open
current
move
market_status
market_data_status
```

Recommended unique key:

```text
market_date
+ source_type
+ risk_type
+ risk_greek
+ underlying
+ tenor_swap
+ tenor_option
```

Recommended types:

| Column | Arrow type |
|---|---|
| `market_date` | `date32` |
| identity and status columns | `string` |
| tenor order columns | nullable `int32` |
| `open`, `current`, `move` | nullable `float64` |

### Tenor shapes

Use one schema for every Market product.

#### FX Spot: no tenor axes

```text
source_type         fx/delta
risk_type           FX
risk_greek          Delta
underlying          EUR/USD
tenor_swap          Spot
tenor_option        N/A
tenor_swap_order    null
tenor_option_order  null
```

#### IR Delta: one tenor axis

```text
source_type         ir/delta
risk_type           IR
risk_greek          Delta
underlying          USD-SOFR
tenor_swap          5Y
tenor_option        N/A
tenor_swap_order    4
tenor_option_order  null
```

#### IR Vega: two tenor axes

```text
source_type         ir/deltavega
risk_type           IR
risk_greek          DeltaVega
underlying          USD
tenor_swap          10Y
tenor_option        6M
tenor_swap_order    5
tenor_option_order  2
```

This lets the Market History UI choose naturally:

```text
0 meaningful tenor axes → time-series line
1 tenor axis            → curves, heatmap, optional 3-D surface
2 tenor axes            → surface heatmap, A/B/difference, cell history
```

### Market rules

- Preserve connector-owned tenor orders.
- Store unavailable quotes as null, not zero.
- Treat Open and Current as authoritative.
- Validate `Move = Current - Open` when both quote legs exist.
- Reject duplicate quote identities.
- Keep the data long-form.

Correct:

```text
date | underlying | tenor_swap | tenor_option | current
```

Incorrect:

```text
date | 1Y | 2Y | 5Y | 10Y
```

### Current CSV-compatible header

```csv
Source Type,Risk Type,Risk Greek,Underlying,Tenor Swap,Tenor Option,Tenor Swap Order,Tenor Option Order,Market Date,Open,Current,Move,Market Status,Market Data Status
```

---

## 2. Stock data

### Download grain

Download raw Stock facts:

```text
stock_date
crds
cpty
portfolio
instrument
currency
quantity
market_value
```

Recommended types:

| Column | Arrow type |
|---|---|
| `stock_date` | `date32` |
| text identities | `string` |
| `quantity` | `float64` |
| `market_value` | `float64` |

The current source identity is:

```text
CRDS + CPTY + Portfolio + Instrument + Currency
```

Reject duplicates at that grain rather than silently aggregating them.

### Do not persist comparison outputs

Do not store:

```text
Prior Quantity
Current Quantity
Quantity Change
Prior Market Value
Current Market Value
Market Value Change
Stock Change
Promotion Bucket
```

Those are derived after the user selects Date A and Date B:

```text
Stock A + Stock B
        ↓
full outer join
        ↓
Added / Removed / Changed / Unchanged
        ↓
Quantity and Market Value changes
```

### Current CSV-compatible header

```csv
CRDS,CPTY,Portfolio,Instrument,Currency,Quantity,Market Value
```

Add the selected Stock date before writing Parquet.

---

## 3. Raw Colossus data

Download:

```text
market_date
portfolio
underlying
risk_type
risk_greek
pl
```

Raw Colossus should not invent Product, Activity or Signoff Group.

Recommended unique key:

```text
market_date
+ portfolio
+ underlying
+ risk_type
+ risk_greek
```

Current CSV-compatible header:

```csv
Portfolio,Underlying,Risk Type,Risk Greek,PL
```

The date is added from the requested historical date or date partition.

---

## 4. Predict / Risk data

### Preferred route

Archive the complete committed Risk Explorer snapshot for the date.

At minimum, Predict history needs:

```text
market_date
portfolio
underlying
risk_type
risk_greek
product
pl
```

The full Risk archive should also retain:

```text
activity
signoff_group
category
sub_category
risk
drisk
tenor_swap
tenor_option
tenor orders
promotion fields
```

This is preferable to permanently storing a reduced `predicted.csv`, because the complete snapshot remains the rebuild and audit authority.

### Legacy minimum

Existing historical files may use:

```csv
Risk Type,Risk Greek,Underlying,Product,Book,PL
```

with:

```text
YYYY-MM-DD/
  histo.csv
  predicted.csv
```

This is acceptable for migration, but the new permanent history should use the complete Risk snapshot plus a canonical P&L history projection.

---

## 5. Historical Portfolio authority

Archive the Portfolio mapping by effective date:

```text
effective_date
portfolio
product
activity
signoff_group
category
sub_category
```

Current CSV-compatible header:

```csv
Portfolio,Product,Activity,SignoffGroup,Category,Sub Category
```

This is required so historical data is not silently classified using today's Portfolio registry.

Default Stock behavior should remain:

> Map both comparison legs using the newer selected Stock date.

The UI should display the mapping authority date explicitly.

---

## 6. Canonical P&L history dataset

After loading raw Predict/Risk, raw Colossus and historical Portfolio authority, write one combined `pnl` dataset:

```text
market_date
pnl_type
activity
signoff_group
category
sub_category
risk_type
risk_greek
underlying
product
portfolio
mapping_status
pl
```

`pnl_type` should be exactly:

```text
Predict
Colossus
```

Recommended unique key:

```text
market_date
+ pnl_type
+ signoff_group
+ risk_type
+ risk_greek
+ underlying
+ product
+ portfolio
```

Example:

```text
2026-08-20 | Predict  | Rates | SOG-A | Core | IR | IR | Delta | USD-SOFR | XVA | BOOK-001 | Mapped | 125000
2026-08-20 | Colossus | Rates | SOG-A | Core | IR | IR | Delta | USD-SOFR | XVA | BOOK-001 | Mapped | 119500
```

This canonical table is what the P&L History page should query. Keep raw Risk and Colossus so it can be rebuilt.

---

## 7. Minimum daily downloads

For every business date, obtain:

```text
YYYY-MM-DD/
├── market.csv
├── stock.csv
├── risk.csv or predicted.csv
├── colossus.csv
└── portfolio.csv
```

Recommended preference:

```text
full risk.csv > reduced predicted.csv
```

---

## 8. Export rules

Apply these rules to every dataset:

1. Use long-form rows.
2. Do not include formatted numbers such as currency symbols or thousands separators.
3. Do not write a pandas index.
4. Use null for unavailable values.
5. Use stable text labels.
6. Preserve source-owned tenor ranks.
7. Reject duplicate source identities.
8. Write one complete immutable partition per date.
9. Validate before publishing the date.
10. Publish the date commit manifest last.

---

## 9. Example PyArrow schemas

```python
import pyarrow as pa

MARKET_SCHEMA = pa.schema(
    [
        ("market_date", pa.date32()),
        ("source_type", pa.string()),
        ("risk_type", pa.string()),
        ("risk_greek", pa.string()),
        ("underlying", pa.string()),
        ("tenor_swap", pa.string()),
        ("tenor_option", pa.string()),
        ("tenor_swap_order", pa.int32()),
        ("tenor_option_order", pa.int32()),
        ("open", pa.float64()),
        ("current", pa.float64()),
        ("move", pa.float64()),
        ("market_status", pa.string()),
        ("market_data_status", pa.string()),
    ]
)

STOCK_SCHEMA = pa.schema(
    [
        ("stock_date", pa.date32()),
        ("crds", pa.string()),
        ("cpty", pa.string()),
        ("portfolio", pa.string()),
        ("instrument", pa.string()),
        ("currency", pa.string()),
        ("quantity", pa.float64()),
        ("market_value", pa.float64()),
    ]
)

PNL_SCHEMA = pa.schema(
    [
        ("market_date", pa.date32()),
        ("pnl_type", pa.string()),
        ("activity", pa.string()),
        ("signoff_group", pa.string()),
        ("category", pa.string()),
        ("sub_category", pa.string()),
        ("risk_type", pa.string()),
        ("risk_greek", pa.string()),
        ("underlying", pa.string()),
        ("product", pa.string()),
        ("portfolio", pa.string()),
        ("mapping_status", pa.string()),
        ("pl", pa.float64()),
    ]
)
```

---

## 10. Example date-partitioned writer

```python
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds


def write_daily_dataset(
    frame: pd.DataFrame,
    *,
    root: str | Path,
    partition_column: str,
    schema: pa.Schema,
    replace_date: bool = False,
) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")

    missing = [name for name in schema.names if name not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    table = pa.Table.from_pandas(
        frame.loc[:, schema.names],
        schema=schema,
        preserve_index=False,
        safe=True,
    )

    parquet_format = ds.ParquetFileFormat()
    write_options = parquet_format.make_write_options(compression="zstd")

    ds.write_dataset(
        table,
        base_dir=str(Path(root)),
        format=parquet_format,
        file_options=write_options,
        partitioning=[partition_column],
        partitioning_flavor="hive",
        basename_template=f"snapshot-{uuid4().hex}-{{i}}.parquet",
        existing_data_behavior=(
            "delete_matching" if replace_date else "overwrite_or_ignore"
        ),
    )
```

Use:

- `overwrite_or_ignore` with unique file names for new immutable data;
- `delete_matching` only when replacing a complete authoritative date partition.

---

## 11. Example reads

### IR Delta history

```python
from datetime import date

import pyarrow as pa
import pyarrow.dataset as ds

market = ds.dataset(
    "history/market",
    format="parquet",
    partitioning=ds.partitioning(
        pa.schema([("market_date", pa.date32())]),
        flavor="hive",
    ),
)

result = market.to_table(
    columns=[
        "market_date",
        "tenor_swap",
        "tenor_swap_order",
        "current",
    ],
    filter=(
        (ds.field("market_date") >= date(2026, 8, 1))
        & (ds.field("market_date") <= date(2026, 8, 20))
        & (ds.field("risk_type") == "IR")
        & (ds.field("risk_greek") == "Delta")
        & (ds.field("underlying") == "USD-SOFR")
    ),
)

delta_history = result.to_pandas()
```

### Two Stock dates

```python
stock = ds.dataset(
    "history/stock",
    format="parquet",
    partitioning=ds.partitioning(
        pa.schema([("stock_date", pa.date32())]),
        flavor="hive",
    ),
)

result = stock.to_table(
    filter=ds.field("stock_date").isin(
        [date(2026, 8, 19), date(2026, 8, 20)]
    )
)

stock_frame = result.to_pandas()

prior = stock_frame.loc[
    stock_frame["stock_date"].eq(date(2026, 8, 19))
].drop(columns="stock_date")

current = stock_frame.loc[
    stock_frame["stock_date"].eq(date(2026, 8, 20))
].drop(columns="stock_date")
```

The two resulting frames can go directly into the existing Stock comparison logic.

### P&L history

```python
pnl = ds.dataset(
    "history/pnl",
    format="parquet",
    partitioning=ds.partitioning(
        pa.schema([("market_date", pa.date32())]),
        flavor="hive",
    ),
)

result = pnl.to_table(
    columns=[
        "market_date",
        "pnl_type",
        "activity",
        "risk_type",
        "pl",
    ],
    filter=(
        (ds.field("market_date") >= date(2026, 8, 1))
        & (ds.field("market_date") <= date(2026, 8, 20))
        & (ds.field("activity") == "Rates")
    ),
)

pnl_history = result.to_pandas()
```

---

## Recommended daily workflow

```text
1. Download MarketBook
2. Download Stock
3. Obtain full Predict/Risk snapshot
4. Download Colossus
5. Download dated Portfolio mapping
6. Validate schemas and uniqueness keys
7. Build canonical P&L history rows
8. Write staged Parquet partitions
9. Verify row counts and checksums
10. Publish the date commit manifest
```

The minimum datasets required by the app are:

```text
market
stock
pnl
portfolio_authority
```

The recommended audited store also keeps:

```text
risk
colossus_raw
```

## Final recommendation

Use:

> **one logical dataset per domain, with one complete immutable Parquet partition per date.**

Do not use one giant appended file.
