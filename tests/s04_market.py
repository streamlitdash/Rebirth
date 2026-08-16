"""MarketBook, status routing, risk join, and tenor-order tests."""

from __future__ import annotations

import pandas as pd
import pytest

from core.s01_schema import (
    TENOR_OPTION,
    TENOR_OPTION_ORDER,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
)
from core.s02_pipeline import (
    CURRENT,
    MARKET_AVAILABLE,
    MARKET_STATUS,
    OFFICIAL,
    OPEN,
    PL,
    PRODUCT_SPECS,
    apply_thresholds,
    get_product_market,
    get_product_market_status,
    get_product_pl,
    get_product_risk,
)
from core.s03_search import build_search_catalog


SPEC = PRODUCT_SPECS["irdelta"]


def _risk() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["USD-SOFR", "2Y", "BOOK_A", "Connector Rates", 20.0, 2.0],
            ["USD-SOFR", "10Y", "BOOK_A", "Connector Rates", 10.0, 1.0],
        ],
        columns=["Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk"],
    )


def _open() -> pd.DataFrame:
    # The market deliberately owns a non-lexical tenor order.
    return pd.DataFrame(
        [
            ["USD-SOFR", "10Y", 0, 4.00],
            ["USD-SOFR", "2Y", 1, 4.10],
            ["USD-SOFR", "30Y", 2, 4.20],
        ],
        columns=["Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, OPEN],
    )


def _current(status: str = OFFICIAL) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            ["USD-SOFR", "10Y", 0, 4.05],
            ["USD-SOFR", "2Y", 1, 4.20],
            ["USD-SOFR", "30Y", 2, 4.35],
        ],
        columns=["Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, CURRENT],
    )
    frame[MARKET_STATUS] = status
    return frame


def test_risk_group_is_required_but_its_value_is_connector_owned() -> None:
    raw = _risk()
    raw["Group"] = ["Anything I Want", 17]

    result = get_product_risk(SPEC, "2026-07-17", raw)

    assert result["Group"].tolist() == ["Anything I Want", 17]

    with pytest.raises(ValueError, match="missing required columns: \\['Group'\\]"):
        get_product_risk(SPEC, "2026-07-17", raw.drop(columns="Group"))


def test_market_book_keeps_market_only_tenors_and_dynamic_status() -> None:
    market = get_product_market(
        SPEC,
        "2026-07-20",
        _open(),
        _current(),
        market_status=OFFICIAL,
    )

    assert market[TENOR_SWAP].tolist() == ["10Y", "2Y", "30Y"]
    assert market[MARKET_STATUS].eq(OFFICIAL).all()
    assert market[MARKET_AVAILABLE].eq(True).all()
    assert market["Move"].round(8).tolist() == [0.05, 0.10, 0.15]


@pytest.mark.parametrize("status", ["Live", OFFICIAL])
def test_market_status_is_selected_by_the_caller_not_renamed(status: str) -> None:
    raw = _current(status)

    result = get_product_market_status(
        SPEC,
        "2026-07-20",
        raw,
        market_status=status,
    )

    assert CURRENT in result
    assert "Live/Official" not in result
    assert result[MARKET_STATUS].eq(status).all()


def test_market_status_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="must be exactly 'Live'"):
        get_product_market_status(
            SPEC,
            "2026-07-20",
            _current(OFFICIAL),
            market_status="Live",
        )


def test_open_and_current_must_agree_on_market_tenor_order() -> None:
    current = _current()
    current.loc[current[TENOR_SWAP].eq("10Y"), TENOR_SWAP_ORDER] = 9

    with pytest.raises(ValueError, match="disagree on 'Tenor Swap Order'"):
        get_product_market(
            SPEC,
            "2026-07-20",
            _open(),
            current,
            market_status=OFFICIAL,
        )


def test_risk_join_shows_only_risk_tenors_in_market_order() -> None:
    market = get_product_market(
        SPEC,
        "2026-07-20",
        _open(),
        _current(),
        market_status=OFFICIAL,
    )
    risk = get_product_risk(SPEC, "2026-07-17", _risk())

    joined = get_product_pl(
        SPEC,
        "2026-07-17",
        validated_risk=risk,
        validated_market=market,
        market_date="2026-07-20",
        market_status=OFFICIAL,
    )

    assert joined[TENOR_SWAP].tolist() == ["10Y", "2Y"]
    assert "30Y" not in set(joined[TENOR_SWAP])
    assert joined["Group"].tolist() == ["Connector Rates", "Connector Rates"]
    assert joined[PL].round(8).tolist() == [0.5, 2.0]

    joined["Portfolio Mapped"] = True
    thresholds = pd.DataFrame(
        [["IR", "Delta", 1_000.0, 1_000.0, 1_000.0]],
        columns=["Risk Type", "Risk Greek", "PL", "Risk", "dRisk"],
    )
    enriched = apply_thresholds(joined, thresholds)
    assert enriched["Group"].tolist() == ["Connector Rates", "Connector Rates"]


def test_quick_risk_intersects_tenors_but_quick_market_reads_full_market_book() -> None:
    market = get_product_market(
        SPEC,
        "2026-07-20",
        _open(),
        _current(),
        market_status=OFFICIAL,
    )
    risk = get_product_risk(SPEC, "2026-07-17", _risk())
    catalog = build_search_catalog(
        revision=7,
        risk_frames={SPEC.source_type: risk},
        market_frames={SPEC.source_type: market},
        risk_dates={SPEC.source_type: pd.Timestamp("2026-07-17")},
        market_date=pd.Timestamp("2026-07-20"),
    )
    identity = "IR | Delta | USD-SOFR"

    risk_view = catalog.pivot_combined(identity, index_columns=(TENOR_SWAP,))
    market_view = catalog.pivot_market_exact(identity, index_columns=(TENOR_SWAP,))

    assert catalog.combine_udl_options() == (identity,)
    assert catalog.market_udl_options() == (identity,)
    assert risk_view.frame[TENOR_SWAP].tolist() == ["10Y", "2Y"]
    assert market_view.frame[TENOR_SWAP].tolist() == ["10Y", "2Y", "30Y"]
    assert market_view.frame[TENOR_SWAP_ORDER].tolist() == [0, 1, 2]
    assert market_view.frame[MARKET_STATUS].eq(OFFICIAL).all()
    assert market_view.frame[CURRENT].round(8).tolist() == [4.05, 4.20, 4.35]


def test_exact_market_surface_keeps_every_ranked_connector_tenor() -> None:
    spec = PRODUCT_SPECS["irdeltavega"]
    swaps = [f"SWAP-{index:02d}" for index in range(30)]
    options = [f"OPT-{index:02d}" for index in range(30)]
    open_rows = [
        ["USD-SWAPTION", swap, option, swap_rank, option_rank, 100.0]
        for swap_rank, swap in enumerate(reversed(swaps))
        for option_rank, option in enumerate(reversed(options))
    ]
    current_rows = [
        ["USD-SWAPTION", swap, option, swap_rank, option_rank, 101.0]
        for swap_rank, swap in enumerate(reversed(swaps))
        for option_rank, option in enumerate(reversed(options))
    ]
    open_frame = pd.DataFrame(
        open_rows,
        columns=[
            "Underlying",
            TENOR_SWAP,
            TENOR_OPTION,
            TENOR_SWAP_ORDER,
            TENOR_OPTION_ORDER,
            OPEN,
        ],
    )
    current_frame = pd.DataFrame(
        current_rows,
        columns=[
            "Underlying",
            TENOR_SWAP,
            TENOR_OPTION,
            TENOR_SWAP_ORDER,
            TENOR_OPTION_ORDER,
            CURRENT,
        ],
    )
    current_frame[MARKET_STATUS] = OFFICIAL
    market = get_product_market(
        spec,
        "2026-07-20",
        open_frame,
        current_frame,
        market_status=OFFICIAL,
    )
    catalog = build_search_catalog(
        revision=1,
        risk_frames={},
        market_frames={spec.source_type: market},
        risk_dates={},
        market_date=pd.Timestamp("2026-07-20"),
    )

    result = catalog.pivot_market_exact(
        "IR | DeltaVega | USD-SWAPTION",
        index_columns=(TENOR_SWAP, TENOR_OPTION),
    )

    assert result.total == 900
    assert len(result.frame) == 900
    assert result.frame[TENOR_SWAP_ORDER].min() == 0
    assert result.frame[TENOR_SWAP_ORDER].max() == 29
    assert result.frame[TENOR_OPTION_ORDER].min() == 0
    assert result.frame[TENOR_OPTION_ORDER].max() == 29


def test_market_dropdown_search_is_tokenized_but_selection_is_exact() -> None:
    market = get_product_market(
        SPEC,
        "2026-07-20",
        _open(),
        _current(),
        market_status=OFFICIAL,
    )
    risk = get_product_risk(SPEC, "2026-07-17", _risk())
    catalog = build_search_catalog(
        revision=1,
        risk_frames={SPEC.source_type: risk},
        market_frames={SPEC.source_type: market},
        risk_dates={SPEC.source_type: pd.Timestamp("2026-07-17")},
        market_date=pd.Timestamp("2026-07-20"),
    )

    for typed_query in ("ir delta usd", "IR DELTA USD", "Ir DeLtA UsD"):
        assert catalog.search_market_udl_options(typed_query) == (
            "IR | Delta | USD-SOFR",
        )
    assert catalog.pivot_market_exact(
        "ir delta usd", index_columns=(TENOR_SWAP,)
    ).frame.empty


def test_search_catalog_keeps_exact_indexes_without_row_level_postings() -> None:
    market = get_product_market(
        SPEC,
        "2026-07-20",
        _open(),
        _current(),
        market_status=OFFICIAL,
    )
    risk = get_product_risk(SPEC, "2026-07-17", _risk())
    catalog = build_search_catalog(
        revision=1,
        risk_frames={SPEC.source_type: risk},
        market_frames={SPEC.source_type: market},
        risk_dates={SPEC.source_type: pd.Timestamp("2026-07-17")},
        market_date=pd.Timestamp("2026-07-20"),
    )

    for typed_query in ("ir delta usd", "IR DELTA USD", "Ir DeLtA UsD"):
        assert catalog.search_combine_udl_options(typed_query) == (
            "IR | Delta | USD-SOFR",
        )
    assert catalog.pivot_combined(
        "ir delta usd", index_columns=(TENOR_SWAP,)
    ).frame.empty
    assert not hasattr(catalog, "_risk_postings")
    assert not hasattr(catalog, "_market_postings")
    assert not hasattr(catalog, "_risk_pivot_postings")
    assert not hasattr(catalog, "search_risk")
    assert not hasattr(catalog, "search_market")


def test_market_rollup_uses_only_complete_quote_pairs() -> None:
    open_frame = pd.DataFrame(
        [
            ["USD-SOFR", "1Y", 0, 100.0],
            ["USD-SOFR", "2Y", 1, 1_000.0],
        ],
        columns=["Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, OPEN],
    )
    current_frame = pd.DataFrame(
        [
            ["USD-SOFR", "1Y", 0, 110.0],
            ["USD-SOFR", "3Y", 2, 3_000.0],
        ],
        columns=["Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, CURRENT],
    )
    current_frame[MARKET_STATUS] = OFFICIAL
    market = get_product_market(
        SPEC,
        "2026-07-20",
        open_frame,
        current_frame,
        market_status=OFFICIAL,
    )
    catalog = build_search_catalog(
        revision=1,
        risk_frames={},
        market_frames={SPEC.source_type: market},
        risk_dates={},
        market_date=pd.Timestamp("2026-07-20"),
    )

    parent = catalog.pivot_market_exact(
        "IR | Delta | USD-SOFR",
        index_columns=("Underlying",),
    ).frame.iloc[0]
    leaves = catalog.pivot_market_exact(
        "IR | Delta | USD-SOFR",
        index_columns=(TENOR_SWAP,),
    ).frame.set_index(TENOR_SWAP)

    assert parent[OPEN] == 100.0
    assert parent[CURRENT] == 110.0
    assert parent["Move"] == parent[CURRENT] - parent[OPEN] == 10.0
    assert leaves.loc["1Y", [OPEN, CURRENT, "Move"]].tolist() == [100.0, 110.0, 10.0]
    assert leaves.loc["2Y", [OPEN, CURRENT, "Move"]].isna().all()
    assert leaves.loc["3Y", [OPEN, CURRENT, "Move"]].isna().all()
