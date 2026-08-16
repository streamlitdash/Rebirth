"""Governed Reported Underlying mapping and post-P&L aggregation tests."""

from __future__ import annotations

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from core.s02_pipeline import apply_thresholds
from core.s03_search import (
    MARKET_RESULT_COLUMNS,
    SearchCatalog,
)
from core.s06_reporting import (
    REPORTED_UNDERLYING,
    REPORTED_UNDERLYING_COLUMNS,
    attach_reported_underlying,
    load_reported_underlying_mapping,
)


MAPPING_COLUMNS = [
    "Risk Type",
    "Risk Greek",
    "Underlying",
    "Reported Underlying",
]
ALLOWED_PAIRS = {
    ("IR", "Delta"),
    ("IR", "Vega"),
    ("FX", "Delta"),
}


def _mapping(*rows: tuple[str, str, str, str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=MAPPING_COLUMNS)


@pytest.mark.parametrize(
    "columns",
    [
        ["Risk Greek", "Risk Type", "Underlying", "Reported Underlying"],
        [*MAPPING_COLUMNS, "Unexpected"],
        MAPPING_COLUMNS[:-1],
    ],
)
def test_mapping_requires_the_exact_schema_in_order(columns: list[str]) -> None:
    source = pd.DataFrame([["value"] * len(columns)], columns=columns)

    with pytest.raises(ValueError):
        load_reported_underlying_mapping(source)


def test_mapping_schema_constant_matches_the_external_csv_contract() -> None:
    assert list(REPORTED_UNDERLYING_COLUMNS) == MAPPING_COLUMNS


def test_mapping_trims_text_and_rejects_blank_values() -> None:
    loaded = load_reported_underlying_mapping(
        _mapping((" IR ", " Delta ", " CNY ", " CNx ")),
        allowed_pairs=ALLOWED_PAIRS,
    )

    assert loaded.iloc[0].to_dict() == {
        "Risk Type": "IR",
        "Risk Greek": "Delta",
        "Underlying": "CNY",
        "Reported Underlying": "CNx",
    }

    for column in MAPPING_COLUMNS:
        invalid = _mapping(("IR", "Delta", "CNY", "CNx"))
        invalid.loc[0, column] = "   "
        with pytest.raises(ValueError):
            load_reported_underlying_mapping(invalid)


def test_mapping_rejects_duplicate_source_keys() -> None:
    source = _mapping(
        ("IR", "Delta", "CNY", "CNx"),
        ("IR", "Delta", "CNY", "Another CN bucket"),
    )

    with pytest.raises(ValueError):
        load_reported_underlying_mapping(source, allowed_pairs=ALLOWED_PAIRS)


def test_mapping_allows_multiple_source_underlyings_to_share_one_target() -> None:
    loaded = load_reported_underlying_mapping(
        _mapping(
            ("IR", "Delta", "CNY", "CNx"),
            ("IR", "Delta", "CNO", "CNx"),
        ),
        allowed_pairs=ALLOWED_PAIRS,
    )

    assert loaded["Reported Underlying"].tolist() == ["CNx", "CNx"]


def test_attachment_is_scoped_by_risk_type_and_greek_with_identity_fallback() -> None:
    source = pd.DataFrame(
        [
            ["row-1", "IR", "Delta", "CNY", 10.0, 1.0, 4.0],
            ["row-2", "IR", "Vega", "CNY", 20.0, 2.0, 5.0],
            ["row-3", "FX", "Delta", "CNY", 30.0, 3.0, 6.0],
            ["row-4", "IR", "Delta", "USD", 40.0, 4.0, 7.0],
        ],
        columns=[
            "Row",
            "Risk Type",
            "Risk Greek",
            "Underlying",
            "Risk",
            "dRisk",
            "PL",
        ],
    )
    original = source.copy(deep=True)
    mapping = _mapping(
        ("IR", "Delta", "CNY", "CNx"),
        ("IR", "Vega", "CNY", "CN Vol"),
        ("FX", "Delta", "CNY", "CN FX"),
    )

    attached = attach_reported_underlying(
        source,
        mapping,
        allowed_pairs=ALLOWED_PAIRS,
    )

    assert_frame_equal(source, original)
    assert attached["Row"].tolist() == original["Row"].tolist()
    assert attached["Underlying"].tolist() == original["Underlying"].tolist()
    assert attached[REPORTED_UNDERLYING].tolist() == [
        "CNx",
        "CN Vol",
        "CN FX",
        "USD",
    ]
    assert_frame_equal(
        attached.loc[:, original.columns],
        original,
        check_dtype=True,
    )


def test_mapping_is_attached_after_each_raw_curve_has_its_own_pl() -> None:
    # These P&Ls deliberately use different raw market moves. Mapping first and
    # applying one CNx curve would not reproduce the correct total of 30.
    calculated = pd.DataFrame(
        [
            ["IR", "Delta", "CNY", 10.0, 1.0, 10.0],
            ["IR", "Delta", "CNO", 10.0, 2.0, 20.0],
        ],
        columns=[
            "Risk Type",
            "Risk Greek",
            "Underlying",
            "Risk",
            "Move",
            "PL",
        ],
    )
    attached = attach_reported_underlying(
        calculated,
        _mapping(
            ("IR", "Delta", "CNY", "CNx"),
            ("IR", "Delta", "CNO", "CNx"),
        ),
        allowed_pairs=ALLOWED_PAIRS,
    )

    reported = attached.groupby(
        REPORTED_UNDERLYING,
        as_index=False,
        sort=False,
        dropna=False,
    )[["Risk", "PL"]].sum(min_count=1)

    assert attached["Underlying"].tolist() == ["CNY", "CNO"]
    assert attached["Move"].tolist() == [1.0, 2.0]
    assert reported.to_dict("records") == [
        {
            REPORTED_UNDERLYING: "CNx",
            "Risk": 20.0,
            "PL": 30.0,
        }
    ]


def test_thresholds_promote_a_reported_underlying_total() -> None:
    positions = pd.DataFrame(
        [
            ["IR", "Delta", "CNY", "CNx", "Asia", True, 60.0, 5.0, 4.0],
            ["IR", "Delta", "CNO", "CNx", "Asia", True, 60.0, 5.0, 4.0],
        ],
        columns=[
            "Risk Type",
            "Risk Greek",
            "Underlying",
            REPORTED_UNDERLYING,
            "Group",
            "Portfolio Mapped",
            "Risk",
            "dRisk",
            "PL",
        ],
    )
    thresholds = pd.DataFrame(
        [["IR", "Delta", 100.0, 100.0, 100.0]],
        columns=["Risk Type", "Risk Greek", "PL", "Risk", "dRisk"],
    )

    promoted = apply_thresholds(positions, thresholds)

    assert promoted["Underlying"].tolist() == ["CNY", "CNO"]
    assert promoted[REPORTED_UNDERLYING].eq("CNx").all()
    assert promoted["Display Bucket"].eq("CNx").all()
    assert promoted["Promotion Reason"].eq("Big Risk").all()
    assert promoted["Promotion Score"].eq(1.2).all()


def test_quick_risk_uses_reported_identity_but_quick_market_stays_raw() -> None:
    market = pd.DataFrame(
        [
            [
                "ir/delta",
                "IR",
                "Delta",
                "CNY",
                "1Y",
                "N/A",
                0,
                pd.NA,
                pd.Timestamp("2026-07-20"),
                10.0,
                11.0,
                1.0,
                "OFFICIAL",
                "Available",
            ],
            [
                "ir/delta",
                "IR",
                "Delta",
                "CNO",
                "1Y",
                "N/A",
                0,
                pd.NA,
                pd.Timestamp("2026-07-20"),
                20.0,
                22.0,
                2.0,
                "OFFICIAL",
                "Available",
            ],
        ],
        columns=list(MARKET_RESULT_COLUMNS),
    )
    risk = pd.DataFrame(
        [
            [
                "ir/delta",
                "IR",
                "Delta",
                "Risk",
                "CNx",
                "CNY",
                "1Y",
                "N/A",
                0,
                pd.NA,
                "BOOK_A",
                10.0,
                1.0,
                10.0,
            ],
            [
                "ir/delta",
                "IR",
                "Delta",
                "XGAMMA",
                "CNx",
                "CNO",
                "1Y",
                "N/A",
                0,
                pd.NA,
                "BOOK_A",
                10.0,
                1.0,
                20.0,
            ],
        ],
        columns=[
            "Source Type",
            "Risk Type",
            "Risk Greek",
            "Split",
            REPORTED_UNDERLYING,
            "Underlying",
            "Tenor Swap",
            "Tenor Option",
            "Tenor Swap Order",
            "Tenor Option Order",
            "Portfolio",
            "Risk",
            "dRisk",
            "PL",
        ],
    )
    catalog = SearchCatalog(
        revision=3,
        risk_dates={"ir/delta": pd.Timestamp("2026-07-17")},
        market_date=pd.Timestamp("2026-07-20"),
        market_frame=market,
        risk_pivot_frame=risk,
    )

    assert catalog.combine_udl_options(identity_mode="reported") == (
        "IR | Delta | CNx",
    )
    assert set(catalog.combine_udl_options(identity_mode="underlying")) == {
        "IR | Delta | CNY",
        "IR | Delta | CNO",
    }
    assert set(catalog.market_udl_options()) == {
        "IR | Delta | CNY",
        "IR | Delta | CNO",
    }

    hierarchy = catalog.pivot_combined_hierarchy(
        "IR | Delta | CNx",
        index_columns=(REPORTED_UNDERLYING, "Underlying", "Tenor Swap"),
    ).frame
    reported_parent = hierarchy.loc[hierarchy["__Hierarchy Depth__"].eq(1)].iloc[0]
    assert reported_parent["Risk"] == 20.0
    assert reported_parent["PL"] == 30.0
    assert pd.isna(reported_parent["Open"])
    assert pd.isna(reported_parent["Current"])
    assert pd.isna(reported_parent["Move"])

    raw_children = hierarchy.loc[hierarchy["__Hierarchy Depth__"].eq(2)]
    assert set(raw_children["Underlying"]) == {"CNY", "CNO"}
    assert set(raw_children["Open"]) == {10.0, 20.0}

    raw_hierarchy = catalog.pivot_combined_hierarchy(
        "IR | Delta | CNY",
        identity_mode="underlying",
        index_columns=("Underlying", "Tenor Swap"),
    ).frame
    assert raw_hierarchy.loc[
        raw_hierarchy["__Hierarchy Depth__"].eq(1), "Risk"
    ].tolist() == [10.0]

    filtered = catalog.pivot_combined_hierarchy(
        "IR | Delta | CNx",
        identity_mode="reported",
        risk_filters={"Split": ["Risk"]},
        index_columns=(REPORTED_UNDERLYING, "Underlying", "Tenor Swap"),
    ).frame
    filtered_children = filtered.loc[filtered["__Hierarchy Depth__"].eq(2)]
    assert filtered_children["Underlying"].tolist() == ["CNY"]
    assert filtered_children["Open"].tolist() == [10.0]

    # Risk Greek remains part of the exact identity and may be a pivot level;
    # it is deliberately not a second generic filter alongside canonical Split.
    with pytest.raises(ValueError, match="Unknown Quick Risk filters"):
        catalog.pivot_combined_hierarchy(
            "IR | Delta | CNx",
            risk_filters={"Risk Greek": ["Delta"]},
            index_columns=("Underlying", "Tenor Swap"),
        )
