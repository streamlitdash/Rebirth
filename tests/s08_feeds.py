"""Focused fake-connector partition-cache tests."""

from __future__ import annotations

import pandas as pd

from feeds.s01_sources import (
    FAKE_CSV_FILES,
    _load_fake_csv,
    _load_fake_source_partition,
    get_market_open,
)


def test_per_underlying_market_calls_reuse_cached_narrow_partitions() -> None:
    raw = pd.read_csv(
        FAKE_CSV_FILES["market_open"],
        dtype="string",
        encoding="utf-8-sig",
        keep_default_na=False,
    )
    underlyings = (
        raw.loc[raw["Source Type"].eq("ir/delta"), "Underlying"]
        .drop_duplicates()
        .tolist()
    )
    assert len(underlyings) >= 2

    _load_fake_csv.cache_clear()
    _load_fake_source_partition.cache_clear()
    try:
        first = get_market_open(
            "ir/delta",
            pd.Timestamp("2026-07-20"),
            underlyings[0],
            market_status="Live",
        )
        second = get_market_open(
            "ir/delta",
            pd.Timestamp("2026-07-20"),
            underlyings[1],
            market_status="Live",
        )
        repeated = get_market_open(
            "ir/delta",
            pd.Timestamp("2026-07-20"),
            underlyings[0],
            market_status="Live",
        )

        full_info = _load_fake_csv.cache_info()
        partition_info = _load_fake_source_partition.cache_info()
        assert not first.empty and not second.empty and not repeated.empty
        assert full_info.misses == 1
        assert full_info.hits == 1
        assert partition_info.misses == 2
        assert partition_info.hits == 1

        first.loc[first.index[0], "Open"] = "MUTATED"
        defensive = get_market_open(
            "ir/delta",
            pd.Timestamp("2026-07-20"),
            underlyings[0],
            market_status="Live",
        )
        assert not defensive["Open"].eq("MUTATED").any()
    finally:
        _load_fake_source_partition.cache_clear()
        _load_fake_csv.cache_clear()
