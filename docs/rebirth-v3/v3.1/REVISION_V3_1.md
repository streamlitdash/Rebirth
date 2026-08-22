# Revision V3.1

V3.1 corrects the first V3 prototype after comparing it again with the current V1 implementation.

## What was wrong

The first prototype compressed several independent V1 concepts into one simplified screen. In doing so it obscured direct Cross/SplitVA switching, removed visible page filters, used Product as a visual depth category, and underrepresented the historical P&L and Stock tables.

## Corrected design

### Filter Views

Filter Views own Activity, Signoff Group, Portfolio, Category, Sub Category, and include/exclude mode. The immutable default is `Default - Activities 1-3`.

### Risk Views

Risk Views own the pivot layout and are independently saved. Built-in choices are Cross, SplitVA, and Credit. A custom Risk View stores Rows, Columns, Metrics, Local Filters, Sort, Totals, Display, and viewport settings.

### Chart semantics

Product is retained as a table dimension but is never invented as a 3-D chart axis. One-tenor full history is Tenor × Date × Value. Two-tenor playback is Swap Tenor × Option Tenor × Value with date as the frame. Fixed-tenor modes retain a real 3-D surface by using the remaining tenor and date axes.

### Playback

Every historical 3-D panel owns a local player. It stops when navigation, tab, identity, metric, period, view, fixed tenor, revision, browser visibility, or Clear Cache changes.

### Historical ownership

Data owns Risk History and Market History. P&L history remains on P&L. Stock history remains on Stock. Their hierarchy tables, exact daily rows, charts, editors, validation, comparison rows, and saved filters remain part of the acceptance contract.

### Failure model

Optional adapter or history failure degrades only the affected feature. Invalid financial schemas, duplicate identities, writes, snapshot commits, adjustments, and sends continue to fail closed.