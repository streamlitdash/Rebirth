"""Focused fake-connector partition-cache tests."""

from __future__ import annotations

import pandas as pd
import pytest

from feeds.s01_sources import (
    FAKE_CSV_FILES,
    _load_fake_csv,
    _load_fake_source_partition,
    get_colossus_pl,
    get_market_open,
    get_market_state,
    get_risk_checker,
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


def test_fake_market_state_becomes_official_at_london_cutoff() -> None:
    market_date = pd.Timestamp("2026-08-17")

    assert (
        get_market_state(
            market_date,
            now=pd.Timestamp("2026-08-17 21:59:59", tz="Europe/London"),
        )
        == "Live"
    )
    assert (
        get_market_state(
            market_date,
            now=pd.Timestamp("2026-08-17 22:00:00", tz="Europe/London"),
        )
        == "OFFICIAL"
    )
    assert (
        get_market_state(
            market_date,
            now=pd.Timestamp("2026-08-18 09:00:00", tz="Europe/London"),
        )
        == "OFFICIAL"
    )


def test_fake_market_state_rolls_sunday_to_the_previous_official_friday() -> None:
    assert (
        get_market_state(
            pd.Timestamp("2026-08-16"),
            now=pd.Timestamp("2026-08-16 12:00:00", tz="Europe/London"),
        )
        == "OFFICIAL"
    )


def test_fake_readiness_rejects_an_explicit_weekend_checker_date() -> None:
    with pytest.raises(ValueError, match="checker_date must be a business day"):
        get_risk_checker(pd.Timestamp("2026-08-16"))


def test_fake_colossus_loader_adapts_histo_partition_to_archive_grain() -> None:
    frame = get_colossus_pl(pd.Timestamp("2026-08-14"))

    assert frame.columns.tolist() == [
        "Portfolio",
        "Underlying",
        "Risk Type",
        "Risk Greek",
        "PL",
    ]
    assert len(frame) == 6
    assert not frame.duplicated(frame.columns[:-1].tolist()).any()
    assert frame["Portfolio"].str.contains("FAKE_REPLACE_ME", regex=False).all()
