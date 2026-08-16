"""Canonical fake-fixture generator contract tests."""

from __future__ import annotations

from core.s01_schema import TENOR_OPTION, TENOR_SWAP, TENOR_SWAP_ORDER
from core.s02_pipeline import CURRENT, PRODUCT_SPECS_BY_SOURCE_TYPE
from feeds.s01_sources import _FAKE_CSV_SCHEMAS
from tools.s01_fixtures import SCHEMAS, build_datasets, validate_datasets


FILE_TO_FEED_DATASET = {
    "s01_readiness.csv": "risk_readiness",
    "s02_checker.csv": "risk_checker",
    "s03_risk.csv": "risk",
    "s04_open.csv": "market_open",
    "s05_current.csv": "market_status",
    "s06_portfolios.csv": "portfolio_config",
    "s07_thresholds.csv": "risk_thresholds",
}


def test_generated_schemas_are_the_exact_feed_contracts() -> None:
    assert {
        filename: _FAKE_CSV_SCHEMAS[dataset]
        for filename, dataset in FILE_TO_FEED_DATASET.items()
    } == SCHEMAS


def test_generator_uses_canonical_axes_and_current_field() -> None:
    datasets = build_datasets()
    validate_datasets(datasets)

    assert PRODUCT_SPECS_BY_SOURCE_TYPE["ir/gamma"].tenor_columns == [TENOR_SWAP]
    assert PRODUCT_SPECS_BY_SOURCE_TYPE["credit/vega"].tenor_columns == [TENOR_SWAP]
    assert CURRENT in SCHEMAS["s05_current.csv"]
    assert "Live" not in SCHEMAS["s05_current.csv"]
    assert "Group" in SCHEMAS["s03_risk.csv"]

    for source_type in ("ir/gamma", "credit/vega"):
        source_rows = [
            row for row in datasets["s03_risk.csv"] if row["Source Type"] == source_type
        ]
        assert len({row[TENOR_SWAP] for row in source_rows}) >= 3
        assert all(not row[TENOR_OPTION] for row in source_rows)


def test_risk_fixture_supplies_connector_owned_groups() -> None:
    risk = build_datasets()["s03_risk.csv"]

    assert {
        row["Group"]
        for row in risk
        if row["Source Type"] == "credit/delta"
        and row["Underlying"].endswith("Ford CDS")
    } == {"Single Name"}
    assert {row["Group"] for row in risk if row["Source Type"] == "commo/delta"} == {
        "Oil",
        "Precious",
        "Gas",
    }


def test_full_market_keeps_ordered_tenors_not_present_in_risk() -> None:
    datasets = build_datasets()
    source_type = "ir/delta"
    risk = {
        (row["Underlying"], row[TENOR_SWAP])
        for row in datasets["s03_risk.csv"]
        if row["Source Type"] == source_type
    }
    market = [
        row for row in datasets["s04_open.csv"] if row["Source Type"] == source_type
    ]
    market_keys = {(row["Underlying"], row[TENOR_SWAP]) for row in market}

    assert risk < market_keys
    for underlying in {row["Underlying"] for row in market}:
        ranks = [
            int(row[TENOR_SWAP_ORDER])
            for row in market
            if row["Underlying"] == underlying
        ]
        assert ranks == list(range(len(ranks)))
