# Part IV - Data, pipeline and resilience contracts

## 17. Adapter boundary

V3 keeps adapters. The distinction is clearer:

- `adapters/contracts.py` defines replaceable I/O ports;
- `adapters/fake.py` supplies deterministic local data;
- `adapters/production.py` wires existing site-owned connector functions;
- page callbacks never import adapters;
- domain and pipeline code receive adapters through the composition root.

Risk and Market remain separate calls because their dates and failure behavior differ. Stock and Colossus have their own grains. P&L senders are write boundaries and are never inferred from read adapters.

## 18. Product and axis authority

`ProductSpec` remains authoritative for:

```text
Source Type
Risk Type
Risk Greek
Axis list and order columns
Market unit
P&L formula
Special scaling
```

Two-axis products are not identified by name matching. They are identified by:

```python
len(product_spec.axes) == 2
```

This automatically covers DeltaVega, XCCYVega and InflationVega without duplicated UI conditionals.

## 19. Snapshot and availability model

```python
@dataclass(frozen=True)
class AvailabilityEntry:
    state: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE", "STALE"]
    reason: str | None
    source_date: date | None
    updated_at: datetime | None

@dataclass(frozen=True)
class RiskSnapshot:
    revision: int
    date_token: DateToken
    risk_frame: pd.DataFrame
    market_frame: pd.DataFrame
    dashboard_frame: pd.DataFrame
    combined_pl: pd.DataFrame
    unmapped_frame: pd.DataFrame
    search_catalog: SearchCatalog
    availability: Mapping[str, AvailabilityEntry]
    issues: tuple[PipelineIssue, ...]
```

A failed optional product can be absent from the new revision if its availability state and issue are explicit. A structurally invalid frame is never marked AVAILABLE.

## 20. Pipeline flow

```text
Resolve fresh DateToken
  -> read readiness/inventory
  -> read Risk product partitions independently
  -> validate successful Risk partitions
  -> read Market partitions independently
  -> validate successful quote partitions
  -> combine Open/Current and derive Move
  -> calculate Predict P&L where inputs are available
  -> attach Portfolio and Reported Underlying mappings
  -> attach thresholds
  -> calculate baseline promotion once
  -> build exact search and pivot indexes
  -> create AvailabilityMap and issues
  -> validate the coherent available subset
  -> atomic snapshot commit
```

If a recoverable product fails, dependent rows are unavailable and aggregate totals are explicitly partial. The UI never presents a partial total without a visible degraded flag.

## 21. Risk history schema

Risk history stores committed current risk at source identity grain, not the rendered hierarchy:

```text
Snapshot Date
Risk Date
Revision
Source Type
Risk Type
Risk Greek
Underlying
Tenor Swap
Tenor Option
Tenor Swap Order
Tenor Option Order
Portfolio
Product
Activity
Signoff Group
Category
Sub Category
Split
Region
Group
Risk
dRisk
P&L
Mapping Status
```

Recommended partition:

```text
data/history/risk/snapshot_date=YYYY-MM-DD/part-*.parquet
```

Unique key:

```text
Snapshot Date
+ Source Type
+ Underlying
+ Tenor Swap
+ Tenor Option
+ Portfolio
+ Split
```

If source Risk identity requires more fields, add them to the key; never aggregate distinct source rows solely to fit this proposed key.

## 22. Market history schema

```text
Market Date
Source Type
Risk Type
Risk Greek
Underlying
Tenor Swap
Tenor Option
Tenor Swap Order
Tenor Option Order
Open
Current
Move
Market Status
Market Data Status
```

Partition by `market_date=YYYY-MM-DD`. The raw quote key is Market Date plus Source Type, Risk Type, Risk Greek, Underlying and both tenor labels. Missing values remain null.

## 23. History repository interface

```python
class HistoryRepository(Protocol):
    def available_dates(self, dataset: HistoryDataset, *, identity: HistoryIdentity | None = None) -> tuple[date, ...]: ...
    def read_range(self, request: HistoryRangeRequest) -> pd.DataFrame: ...
    def read_dates(self, request: HistoryDatesRequest) -> pd.DataFrame: ...
    def append_partition(self, request: HistoryAppendRequest) -> CommitManifest: ...
```

`HistoryRangeRequest` contains:

```text
dataset
start_date
end_date
exact identity
metric columns
additional filters
required revision/date semantics
```

The Parquet implementation must project only requested columns and push date/identity filters into PyArrow before converting the bounded result to pandas.

## 24. Chart matrix builders

Chart builders consume small matrices, not broad source frames.

### Current two-axis matrix

```python
@dataclass(frozen=True)
class SurfaceMatrix:
    x_labels: tuple[str, ...]
    y_labels: tuple[str, ...]
    z: tuple[tuple[float | None, ...], ...]
    cmin: float
    cmax: float
    zmin: float
    zmax: float
```

### Current one-axis market ridge

```python
x = tenor
rows = ("Open", "Current")
z = [[open values], [current values]]
colour = current - open
```

### Current one-axis risk contribution

```python
x = tenor
rows = selected contribution groups
z = grouped selected metric
```

### Historical playback bundle

```python
@dataclass(frozen=True)
class PlaybackBundle:
    dates: tuple[date, ...]
    frames: tuple[SurfaceMatrix, ...]
    selected_index: int
    identity: HistoryIdentity
    metric: str
    period: str
```

The browser store receives the playback bundle only after the user requests history. The date slider callback is clientside.

## 25. Promotion contract

Baseline promotion remains a pipeline output. Ordinary filter, tab, pivot and chart changes never invoke it.

The committed promotion table contains:

```text
Promotion generation
Revision
Risk Type
Risk Greek
Reported Underlying
Risk
dRisk
P&L
Risk threshold
dRisk threshold
P&L threshold
Reason flags
Score
```

Top Promotions ranks this table. A session override is built from a current filtered scope only after explicit confirmation and is separately identified. Failure of the override keeps baseline available and shows the error.

## 26. Clear Cache/reset contract

Clear Cache advances `ResetGeneration`, clears reconstructable caches and session stores, reruns DateAuthority and starts a new refresh with the new generation. A refresh commit must satisfy:

```text
attempt.reset_generation == shared_reset_generation
```

otherwise it is discarded as stale.

Durable history, saved views, adjustments and theme preference are not deleted. The UI shows phases `Resetting`, `Refreshing`, `Ready` or `Failed`, and offers Retry after a failure.

## 27. Saved view versioning

Saved views add schema versioning and preserve unknown future fields on round-trip where possible. A Risk saved view contains filters and pivot configuration, not transient disclosure or playback state.

The immutable system default is produced by code/config rather than stored in the user repository. Users save a copy under a new identity.
