"""Executable examples for every checked-in personal adapter."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from adapters.s02_ir import (
    IR_DELTA_CURRENT,
    IR_DELTA_OPEN,
    IR_DELTA_RISK,
    IR_DELTAVEGA_CURRENT,
    IR_DELTAVEGA_OPEN,
    IR_DELTAVEGA_RISK,
    build_ir_adapters,
)
from adapters.s08_commo import (
    COMMO_DELTA_CURRENT,
    COMMO_DELTA_OPEN,
    COMMO_DELTA_RISK,
    build_commo_adapter,
)
from adapters.s04_credit import (
    CREDIT_DELTA_CURRENT,
    CREDIT_DELTA_OPEN,
    CREDIT_DELTA_RISK,
    CREDIT_DELTA_RISK_BASE,
    CREDIT_DELTA_RISK_REGION_BASE,
    build_credit_adapter,
)
from core.s02_pipeline import (
    MARKET_STATUS,
    OFFICIAL,
    PRODUCT_SPECS,
    get_product_market_status,
    get_product_risk,
)


ADAPTERS = Path(__file__).resolve().parents[1] / "adapters"


def test_adapter_module_numbers_are_unique_and_sequential() -> None:
    expected = [
        "s01_common.py",
        "s02_ir.py",
        "s03_fx.py",
        "s04_credit.py",
        "s05_stock.py",
        "s06_new_positions.py",
        "s07_cross_gamma.py",
        "s08_commo.py",
    ]

    assert sorted(path.name for path in ADAPTERS.glob("s[0-9][0-9]_*.py")) == expected


def _frame(columns: tuple[str, ...], rows: list[list[object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(columns))


def _market_source(frame: pd.DataFrame, calls: list[tuple]) -> Callable:
    def source(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        calls.append((pd.Timestamp(market_date), underlying, market_status))
        return frame.loc[frame["Underlying"].eq(underlying)].reset_index(drop=True)

    return source


def test_ir_delta_adapter_passes_dates_underlying_and_dynamic_status() -> None:
    risk_calls: list[pd.Timestamp] = []
    market_calls: list[tuple] = []
    risk = _frame(
        IR_DELTA_RISK,
        [
            [
                "USD-SOFR",
                "10Y",
                "BOOK_A",
                "FAKE_REPLACE_ME - Custom Desk Group",
                12.0,
                2.0,
            ]
        ],
    )
    opened = _frame(IR_DELTA_OPEN, [["USD-SOFR", "10Y", 0, 4.1]])
    current = _frame(IR_DELTA_CURRENT, [["USD-SOFR", "10Y", 0, 4.3]])

    def risk_source(risk_date: pd.Timestamp) -> pd.DataFrame:
        risk_calls.append(pd.Timestamp(risk_date))
        return risk

    adapter = build_ir_adapters(
        delta_risk=risk_source,
        delta_open=_market_source(opened, market_calls),
        delta_current=_market_source(current, market_calls),
        deltavega_risk=lambda _date: _frame(IR_DELTAVEGA_RISK, []),
        deltavega_open=_market_source(_frame(IR_DELTAVEGA_OPEN, []), []),
        deltavega_current=_market_source(_frame(IR_DELTAVEGA_CURRENT, []), []),
    )["ir/delta"]

    validated_risk = get_product_risk(
        PRODUCT_SPECS["irdelta"],
        "2026-07-17",
        lambda: adapter.risk(pd.Timestamp("2026-07-17")),
    )
    status_frame = adapter.market_status(
        pd.Timestamp("2026-07-20"),
        "USD-SOFR",
        market_status=OFFICIAL,
    )
    validated_status = get_product_market_status(
        PRODUCT_SPECS["irdelta"],
        "2026-07-20",
        status_frame,
        market_status=OFFICIAL,
    )

    assert risk_calls == [pd.Timestamp("2026-07-17")]
    assert market_calls == [(pd.Timestamp("2026-07-20"), "USD-SOFR", OFFICIAL)]
    assert validated_risk[["Underlying", "Tenor Swap"]].values.tolist() == [
        ["USD-SOFR", "10Y"]
    ]
    assert validated_risk["Group"].tolist() == ["FAKE_REPLACE_ME - Custom Desk Group"]
    assert validated_status[MARKET_STATUS].eq(OFFICIAL).all()


def test_ir_deltavega_adapter_accepts_an_arbitrary_two_by_three_surface() -> None:
    swaps = ("1Y", "5Y")
    options = ("1M", "6M", "2Y")
    risk_rows = [
        ["USD-SWAPTION", swap, option, "BOOK_A", "Any Group", 10.0, 1.0]
        for swap in swaps
        for option in options
    ]
    open_rows = [
        ["USD-SWAPTION", swap, option, swap_index, option_index, 20.0 + option_index]
        for swap_index, swap in enumerate(swaps)
        for option_index, option in enumerate(options)
    ]
    current_rows = [row[:-1] + [float(row[-1]) + 0.5] for row in open_rows]
    adapter = build_ir_adapters(
        delta_risk=lambda _date: _frame(IR_DELTA_RISK, []),
        delta_open=_market_source(_frame(IR_DELTA_OPEN, []), []),
        delta_current=_market_source(_frame(IR_DELTA_CURRENT, []), []),
        deltavega_risk=lambda _date: _frame(IR_DELTAVEGA_RISK, risk_rows),
        deltavega_open=_market_source(_frame(IR_DELTAVEGA_OPEN, open_rows), []),
        deltavega_current=_market_source(
            _frame(IR_DELTAVEGA_CURRENT, current_rows), []
        ),
    )["ir/deltavega"]

    risk = adapter.risk(pd.Timestamp("2026-07-17"))
    current = adapter.market_status(
        pd.Timestamp("2026-07-20"),
        "USD-SWAPTION",
        market_status="Live",
    )

    assert risk.shape == (6, len(IR_DELTAVEGA_RISK))
    assert set(risk["Tenor Swap"]) == set(swaps)
    assert set(risk["Tenor Option"]) == set(options)
    assert current.shape[0] == 6
    assert current[MARKET_STATUS].eq("Live").all()


def test_commo_delta_example_has_one_curve_axis_and_no_batching() -> None:
    calls: list[tuple] = []
    adapter = build_commo_adapter(
        risk=lambda _date: _frame(
            COMMO_DELTA_RISK,
            [["BRENT", "DEC26", "BOOK_C", "Energy Custom", 100.0, -4.0]],
        ),
        open_market=_market_source(
            _frame(COMMO_DELTA_OPEN, [["BRENT", "DEC26", 0, 73.0]]),
            calls,
        ),
        current_market=_market_source(
            _frame(COMMO_DELTA_CURRENT, [["BRENT", "DEC26", 0, 74.0]]),
            calls,
        ),
    )

    result = adapter.market_open(
        pd.Timestamp("2026-07-20"), "BRENT", market_status="Live"
    )

    assert result.columns.tolist() == list(COMMO_DELTA_OPEN)
    assert calls == [(pd.Timestamp("2026-07-20"), "BRENT", "Live")]


def test_credit_delta_example_preserves_every_credit_measure_dimension() -> None:
    risk_row = ["CDX-IG", "5Y", "BOOK_CR", "Index", 20.0, 3.0] + [
        float(index) for index in range(len(CREDIT_DELTA_RISK) - 6)
    ]
    adapter = build_credit_adapter(
        risk=lambda _date: _frame(CREDIT_DELTA_RISK, [risk_row]),
        open_market=_market_source(
            _frame(CREDIT_DELTA_OPEN, [["CDX-IG", "5Y", 0, 55.0]]), []
        ),
        current_market=_market_source(
            _frame(CREDIT_DELTA_CURRENT, [["CDX-IG", "5Y", 0, 56.0]]), []
        ),
    )

    result = adapter.risk(pd.Timestamp("2026-07-17"))

    assert tuple(result.columns) == CREDIT_DELTA_RISK
    assert result.loc[0, "Risk SP01"] == 0.0
    assert result.loc[0, "dRisk Theta"] == 9.0
    assert result.loc[0, "Risk JTD"] == 10.0
    assert result.loc[0, "dRisk JTD"] == 11.0


def test_credit_delta_example_accepts_optional_complete_measure_pairs() -> None:
    columns = (*CREDIT_DELTA_RISK_BASE, "Risk SP01", "dRisk SP01")
    adapter = build_credit_adapter(
        risk=lambda _date: _frame(
            columns,
            [["CDX-IG", "5Y", "BOOK_CR", "Index", 20.0, 3.0, 4.0, 0.5]],
        ),
        open_market=_market_source(_frame(CREDIT_DELTA_OPEN, []), []),
        current_market=_market_source(_frame(CREDIT_DELTA_CURRENT, []), []),
    )

    raw = adapter.risk(pd.Timestamp("2026-07-17"))
    validated = get_product_risk(
        PRODUCT_SPECS["creditdelta"],
        "2026-07-17",
        raw,
    )

    assert tuple(raw.columns) == columns
    assert validated[["Risk SP01", "dRisk SP01"]].iloc[0].tolist() == [4.0, 0.5]


def test_credit_delta_preserves_optional_connector_owned_region() -> None:
    columns = (*CREDIT_DELTA_RISK_REGION_BASE, "Risk PSP01", "dRisk PSP01")
    adapter = build_credit_adapter(
        risk=lambda _date: _frame(
            columns,
            [
                [
                    "CDX-IG",
                    "5Y",
                    "BOOK_CR",
                    "Index",
                    "North America",
                    20.0,
                    3.0,
                    4.0,
                    0.5,
                ]
            ],
        ),
        open_market=_market_source(_frame(CREDIT_DELTA_OPEN, []), []),
        current_market=_market_source(_frame(CREDIT_DELTA_CURRENT, []), []),
    )

    raw = adapter.risk(pd.Timestamp("2026-07-17"))
    validated = get_product_risk(
        PRODUCT_SPECS["creditdelta"],
        "2026-07-17",
        raw,
    )

    assert tuple(raw.columns) == columns
    assert validated["Region"].tolist() == ["North America"]
    assert validated[["Risk PSP01", "dRisk PSP01"]].iloc[0].tolist() == [4.0, 0.5]


def test_credit_measure_risk_and_drisk_must_be_supplied_as_a_pair() -> None:
    orphan = _frame(
        (*CREDIT_DELTA_RISK_BASE, "Risk SP01"),
        [["CDX-IG", "5Y", "BOOK_CR", "Index", 20.0, 3.0, 4.0]],
    )

    with pytest.raises(
        ValueError, match="must supply both 'Risk SP01' and 'dRisk SP01'"
    ):
        get_product_risk(
            PRODUCT_SPECS["creditdelta"],
            "2026-07-17",
            orphan,
        )


def test_example_adapters_fail_closed_on_schema_drift() -> None:
    bad_risk = pd.DataFrame(
        [["USD-SOFR", "10Y", "BOOK_A", 1.0]],
        columns=["Underlying", "Curve Tenor", "Portfolio", "Value"],
    )
    adapter = build_commo_adapter(
        risk=lambda _date: bad_risk,
        open_market=_market_source(_frame(COMMO_DELTA_OPEN, []), []),
        current_market=_market_source(_frame(COMMO_DELTA_CURRENT, []), []),
    )

    with pytest.raises(ValueError, match="columns must be exactly"):
        adapter.risk(pd.Timestamp("2026-07-17"))


@pytest.mark.parametrize("status", ["live", "Official", "", None])
def test_example_adapters_do_not_guess_market_status(status: object) -> None:
    adapter = build_commo_adapter(
        risk=lambda _date: _frame(COMMO_DELTA_RISK, []),
        open_market=_market_source(_frame(COMMO_DELTA_OPEN, []), []),
        current_market=_market_source(_frame(COMMO_DELTA_CURRENT, []), []),
    )

    with pytest.raises(ValueError, match="exactly 'Live' or 'OFFICIAL'"):
        adapter.market_status(
            pd.Timestamp("2026-07-20"),
            "BRENT",
            market_status=status,  # type: ignore[arg-type]
        )
