"""Focused pure contracts for portfolio-level XGAMMA development."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from adapters.s04_credit import CREDIT_DELTA_CURRENT, CREDIT_DELTA_OPEN
from adapters.s07_cross_gamma import build_cross_gamma_adapter, get_cross_gamma
from core.s01_schema import TENOR_OPTION_ORDER, TENOR_SWAP, TENOR_SWAP_ORDER
from core.s02_pipeline import (
    CURRENT,
    DRISK,
    GROUP,
    MARKET_AVAILABLE,
    MARKET_DATA_STATUS,
    MARKET_MOVE,
    MARKET_STATUS,
    OFFICIAL,
    OPEN,
    PL,
    PORTFOLIO,
    RISK,
    RISK_GREEK,
    RISK_TYPE,
    SOURCE_TYPE,
    SPLIT,
    UNDERLYING,
    ProductConnectorAdapter,
    RiskRefreshManager,
)
from core.s09_cross_gamma import (
    CROSS_GAMMA_COLUMNS,
    CROSS_GAMMA_RELEASE_COLUMNS,
    CROSS_GAMMA_SENSITIVITY,
    CROSS_GAMMA_SOURCE_SPLIT,
    INPUT_RISK_GREEK,
    INPUT_RISK_TYPE,
    INPUT_TENOR_OPTION,
    INPUT_TENOR_SWAP,
    INPUT_UNDERLYING,
    OUTPUT_RISK_GREEK,
    OUTPUT_RISK_TYPE,
    OUTPUT_TENOR_OPTION,
    OUTPUT_TENOR_SWAP,
    OUTPUT_UNDERLYING,
    XGAMMA_SPLIT,
    XGAMMA_RISK_GREEK,
    XGAMMA_SOURCE_RISK_GREEKS,
    XGAMMA_VEGA_RISK_GREEK,
    build_cross_gamma_rows,
    cross_gamma_market_scope,
    validate_cross_gamma_rows,
)
from feeds.s01_sources import (
    get_portfolio_config,
    get_product_connector_adapters,
    get_reported_underlyings,
    get_risk_checker,
    get_risk_thresholds,
)
from ui.s03_aggregate import (
    apply_credit_measure,
    credit_measure_available,
    filter_ir_family,
    prepare_risk_data,
)


XGAMMA_INPUT_A = "XGAMMA-ONLY INPUT A"
XGAMMA_INPUT_B = "XGAMMA-ONLY INPUT B"
XGAMMA_OUTPUT = "XGAMMA-ONLY OUTPUT"


def _matrix_row(
    *,
    input_risk_type: str = "Credit",
    input_risk_greek: str = "Delta",
    risk_greek: str = XGAMMA_RISK_GREEK,
    input_underlying: str = "INPUT-A",
    input_tenor: str = "1Y",
    output_underlying: str = "OUTPUT",
    output_tenor: str = "5Y",
    sensitivity: float = 10.0,
) -> dict[str, object]:
    return {
        PORTFOLIO: "BOOK-A",
        GROUP: "Index",
        INPUT_RISK_TYPE: input_risk_type,
        INPUT_RISK_GREEK: input_risk_greek,
        RISK_GREEK: risk_greek,
        INPUT_UNDERLYING: input_underlying,
        INPUT_TENOR_SWAP: input_tenor,
        INPUT_TENOR_OPTION: "",
        OUTPUT_RISK_TYPE: "Credit",
        OUTPUT_RISK_GREEK: "Delta",
        OUTPUT_UNDERLYING: output_underlying,
        OUTPUT_TENOR_SWAP: output_tenor,
        OUTPUT_TENOR_OPTION: "",
        CROSS_GAMMA_SENSITIVITY: sensitivity,
    }


def _matrix(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(CROSS_GAMMA_COLUMNS))


def _credit_market(
    *rows: tuple[str, str, int, float, bool],
    risk_greek: str = "Delta",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                "Credit",
                risk_greek,
                underlying,
                tenor,
                order,
                100.0,
                101.0,
                OFFICIAL,
                available,
                "Available" if available else "Missing Current (Live/OFFICIAL)",
                move,
            ]
            for underlying, tenor, order, move, available in rows
        ],
        columns=[
            RISK_TYPE,
            RISK_GREEK,
            UNDERLYING,
            TENOR_SWAP,
            TENOR_SWAP_ORDER,
            OPEN,
            CURRENT,
            MARKET_STATUS,
            MARKET_AVAILABLE,
            MARKET_DATA_STATUS,
            MARKET_MOVE,
        ],
    )


def _integration_matrix(*, missing_input: bool = False) -> pd.DataFrame:
    if missing_input:
        result = _matrix(
            _matrix_row(
                input_underlying="XGAMMA-MISSING INPUT",
                output_underlying=XGAMMA_OUTPUT,
                sensitivity=10.0,
            )
        )
    else:
        result = _matrix(
            _matrix_row(
                input_underlying=XGAMMA_INPUT_A,
                output_underlying=XGAMMA_OUTPUT,
                sensitivity=10.0,
            ),
            _matrix_row(
                input_underlying=XGAMMA_INPUT_B,
                input_tenor="3Y",
                output_underlying=XGAMMA_OUTPUT,
                sensitivity=-4.0,
            ),
        )
    result[PORTFOLIO] = "FAKE_REPLACE_ME - BOOK_A"
    return result


def _integration_manager(
    matrix_loader: object,
    events: list[str],
) -> RiskRefreshManager:
    if not callable(matrix_loader):
        raise TypeError("matrix_loader must be callable")
    adapters = dict(get_product_connector_adapters())
    base_credit = adapters["credit/delta"]
    quotes = {
        XGAMMA_INPUT_A: ("1Y", 0, 100.0, 102.0),
        XGAMMA_INPUT_B: ("3Y", 1, 200.0, 197.0),
        XGAMMA_OUTPUT: ("5Y", 2, 300.0, 301.0),
    }

    def credit_open(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        events.append(f"open:{underlying}")
        if underlying not in quotes:
            return base_credit.market_open(
                market_date, underlying, market_status=market_status
            )
        tenor, order, opened, _current = quotes[underlying]
        return pd.DataFrame(
            [[underlying, tenor, order, opened]],
            columns=list(CREDIT_DELTA_OPEN),
        )

    def credit_status(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        events.append(f"status:{underlying}")
        if underlying not in quotes:
            return base_credit.market_status(
                market_date, underlying, market_status=market_status
            )
        tenor, order, _opened, current = quotes[underlying]
        frame = pd.DataFrame(
            [[underlying, tenor, order, current]],
            columns=list(CREDIT_DELTA_CURRENT),
        )
        frame[MARKET_STATUS] = market_status
        return frame

    adapters["credit/delta"] = ProductConnectorAdapter(
        risk=base_credit.risk,
        market_open=credit_open,
        market_status=credit_status,
    )
    return RiskRefreshManager(
        get_portfolio_config,
        thresholds=get_risk_thresholds,
        reported_underlyings=get_reported_underlyings,
        risk_checker_loader=get_risk_checker,
        market_status_resolver=lambda _date: OFFICIAL,
        cross_gamma_matrix_loader=matrix_loader,
        connector_adapters=adapters,
        clock=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )


def test_adapter_normalizes_date_and_exposes_exact_fake_credit_contract() -> None:
    calls: list[pd.Timestamp] = []

    def source(market_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(market_date)
        return _matrix(_matrix_row())

    adapter = build_cross_gamma_adapter(sensitivities=source)
    result = adapter(pd.Timestamp("2026-07-20 13:30", tz="Europe/London"))
    fake = get_cross_gamma(pd.Timestamp("2026-07-20"))

    assert calls == [pd.Timestamp("2026-07-20")]
    assert result.columns.tolist() == list(CROSS_GAMMA_COLUMNS)
    assert len(fake) == 3
    assert fake[INPUT_RISK_TYPE].eq("Credit").all()
    assert fake[INPUT_RISK_GREEK].tolist() == ["Delta", "Delta", "Vega"]
    assert fake[RISK_GREEK].tolist() == [
        XGAMMA_RISK_GREEK,
        XGAMMA_RISK_GREEK,
        XGAMMA_VEGA_RISK_GREEK,
    ]
    assert fake[OUTPUT_RISK_GREEK].eq("Delta").all()
    assert (
        fake.astype(str)
        .apply(lambda column: column.str.contains("FAKE_REPLACE_ME").any())
        .any()
    )


def test_fake_matrix_develops_delta_and_vega_inputs_in_stored_move_units() -> None:
    rows = get_cross_gamma(pd.Timestamp("2026-07-20"))
    credit_delta = _credit_market(
        ("FAKE_REPLACE_ME - CDX IG", "FAKE_REPLACE_ME - 1Y", 0, 2.3, True),
        (
            "FAKE_REPLACE_ME - iTraxx Main",
            "FAKE_REPLACE_ME - 3Y",
            1,
            -1.8,
            True,
        ),
        ("FAKE_REPLACE_ME - Ford CDS", "FAKE_REPLACE_ME - 5Y", 2, 1.0, True),
        ("FAKE_REPLACE_ME - CDX IG", "FAKE_REPLACE_ME - 5Y", 2, 1.0, True),
    )
    credit_vega = _credit_market(
        (
            "FAKE_REPLACE_ME - Ford CDS Vol",
            "FAKE_REPLACE_ME - 3M",
            1,
            -1.2,
            True,
        ),
        risk_greek="Vega",
    )

    result = build_cross_gamma_rows(
        rows,
        {
            "credit/delta": credit_delta,
            "credit/vega": credit_vega,
        },
    )

    sources = result.loc[result[SPLIT].eq(CROSS_GAMMA_SOURCE_SPLIT)]
    assert sources[RISK_GREEK].tolist() == [
        XGAMMA_RISK_GREEK,
        XGAMMA_RISK_GREEK,
        XGAMMA_VEGA_RISK_GREEK,
    ]
    vega_source = sources.loc[sources[RISK_GREEK].eq(XGAMMA_VEGA_RISK_GREEK)].iloc[0]
    assert vega_source[SOURCE_TYPE] == "credit/vega"
    assert vega_source[UNDERLYING] == "FAKE_REPLACE_ME - Ford CDS Vol"
    assert vega_source[TENOR_SWAP] == "FAKE_REPLACE_ME - 3M"
    assert vega_source[RISK] == pytest.approx(4_000.0)
    assert vega_source[MARKET_MOVE] == pytest.approx(-1.2)

    developed = result.loc[result[SPLIT].eq(XGAMMA_SPLIT)]
    developed_risk = {
        (row[PORTFOLIO], row[GROUP], row[UNDERLYING], row[TENOR_SWAP]): row[RISK]
        for _, row in developed.iterrows()
    }
    assert developed_risk == pytest.approx(
        {
            (
                "FAKE_REPLACE_ME - BOOK_A",
                "Index",
                "FAKE_REPLACE_ME - Ford CDS",
                "FAKE_REPLACE_ME - 5Y",
            ): 42_250.0,
            (
                "FAKE_REPLACE_ME - BOOK_C",
                "Single Name",
                "FAKE_REPLACE_ME - CDX IG",
                "FAKE_REPLACE_ME - 5Y",
            ): -4_800.0,
        }
    )


def test_validator_enforces_product_axes_and_full_matrix_uniqueness() -> None:
    missing_sensitivity_greek = _matrix(_matrix_row()).drop(columns=RISK_GREEK)
    with pytest.raises(ValueError, match="columns must be exactly"):
        validate_cross_gamma_rows(missing_sensitivity_greek)

    missing_credit_tenor = _matrix(_matrix_row(input_tenor=""))
    with pytest.raises(ValueError, match="requires 'Input Tenor Swap'"):
        validate_cross_gamma_rows(missing_credit_tenor)

    duplicate = _matrix(
        _matrix_row(sensitivity=10.0),
        _matrix_row(sensitivity=25.0),
    )
    with pytest.raises(ValueError, match="duplicate full matrix cells"):
        validate_cross_gamma_rows(duplicate)

    cash_flow = _matrix(_matrix_row())
    cash_flow.loc[0, INPUT_RISK_TYPE] = "Cash Flow"
    cash_flow.loc[0, INPUT_RISK_GREEK] = "New"
    cash_flow.loc[0, INPUT_TENOR_SWAP] = ""
    with pytest.raises(ValueError, match="cannot use Cash Flow/New"):
        validate_cross_gamma_rows(cash_flow)


@pytest.mark.parametrize(
    ("input_risk_greek", "source_risk_greek", "expected"),
    [
        ("Delta", XGAMMA_VEGA_RISK_GREEK, XGAMMA_RISK_GREEK),
        ("Vega", XGAMMA_RISK_GREEK, XGAMMA_VEGA_RISK_GREEK),
        ("Delta", "Cross Gamma", XGAMMA_RISK_GREEK),
    ],
)
def test_validator_enforces_authoritative_raw_risk_greek_mapping(
    input_risk_greek: str,
    source_risk_greek: str,
    expected: str,
) -> None:
    rows = _matrix(
        _matrix_row(
            input_risk_greek=input_risk_greek,
            risk_greek=source_risk_greek,
        )
    )

    with pytest.raises(
        ValueError,
        match="does not match.*expected.*" + expected,
    ):
        validate_cross_gamma_rows(rows)


def test_market_scope_unions_ordered_input_and_output_underlyings() -> None:
    rows = _matrix(
        _matrix_row(input_underlying="INPUT-A", output_underlying="OUTPUT"),
        _matrix_row(
            input_underlying="OUTPUT",
            input_tenor="3Y",
            output_underlying="INPUT-B",
            output_tenor="7Y",
        ),
    )

    assert cross_gamma_market_scope(rows) == {
        "credit/delta": ("INPUT-A", "OUTPUT", "INPUT-B")
    }


def test_development_uses_stored_move_and_sums_distinct_inputs() -> None:
    rows = _matrix(
        _matrix_row(input_underlying="INPUT-A", sensitivity=10.0),
        _matrix_row(
            input_underlying="INPUT-B",
            input_tenor="3Y",
            sensitivity=-4.0,
        ),
    )
    # Current - Open is 1.0 on every row. Deliberately different stored Move
    # values prove the XGAMMA contract consumes MarketBook Move as-is.
    market = _credit_market(
        ("INPUT-A", "1Y", 0, 2.0, True),
        ("INPUT-B", "3Y", 1, -3.0, True),
        ("OUTPUT", "5Y", 2, 99.0, True),
    )

    result = build_cross_gamma_rows(rows, {"credit/delta": market})

    assert result.columns.tolist() == list(CROSS_GAMMA_RELEASE_COLUMNS)
    assert len(result) == 3
    source = result.loc[result[RISK_GREEK].eq(XGAMMA_RISK_GREEK)]
    assert source[SPLIT].eq(CROSS_GAMMA_SOURCE_SPLIT).all()
    assert source[RISK_TYPE].eq("Credit").all()
    assert source[UNDERLYING].tolist() == ["INPUT-A", "INPUT-B"]
    assert source[TENOR_SWAP].tolist() == ["1Y", "3Y"]
    assert source[RISK].tolist() == pytest.approx([10.0, -4.0])
    assert source[MARKET_MOVE].tolist() == pytest.approx([2.0, -3.0])
    assert source[SOURCE_TYPE].eq("credit/delta").all()
    assert source[PL].eq(0.0).all()
    assert source[DRISK].isna().all()

    developed = result.loc[result[SPLIT].eq(XGAMMA_SPLIT)]
    assert len(developed) == 1
    row = developed.iloc[0]
    assert row[RISK] == pytest.approx(32.0)
    assert row[SPLIT] == XGAMMA_SPLIT
    assert row[SOURCE_TYPE] == "credit/delta"
    assert row[RISK_TYPE] == "Credit"
    assert row[RISK_GREEK] == "Delta"
    assert row[MARKET_MOVE] == 99.0
    assert row[PL] == 0.0
    assert pd.isna(row[DRISK])


def test_vega_source_label_keeps_actual_input_greek_as_market_driver() -> None:
    rows = _matrix(
        _matrix_row(
            input_risk_greek="Vega",
            risk_greek=XGAMMA_VEGA_RISK_GREEK,
            sensitivity=6.0,
        )
    )
    market = _credit_market(
        ("INPUT-A", "1Y", 0, -2.5, True),
        risk_greek="Vega",
    )

    result = build_cross_gamma_rows(rows, {"credit/vega": market})

    source = result.loc[result[SPLIT].eq(CROSS_GAMMA_SOURCE_SPLIT)].iloc[0]
    developed = result.loc[result[SPLIT].eq(XGAMMA_SPLIT)].iloc[0]
    assert source[RISK_TYPE] == "Credit"
    assert source[RISK_GREEK] == XGAMMA_VEGA_RISK_GREEK
    assert source[SOURCE_TYPE] == "credit/vega"
    assert source[RISK] == 6.0
    assert source[MARKET_MOVE] == -2.5
    assert developed[RISK_GREEK] == "Delta"
    assert developed[RISK] == -15.0


def test_source_sensitivity_grain_is_one_row_per_full_matrix_cell() -> None:
    rows = _matrix(
        _matrix_row(output_underlying="OUTPUT-A", sensitivity=10.0),
        _matrix_row(
            output_underlying="OUTPUT-B",
            output_tenor="7Y",
            sensitivity=25.0,
        ),
    )
    market = _credit_market(
        ("INPUT-A", "1Y", 0, 2.0, True),
        ("OUTPUT-A", "5Y", 1, 1.0, True),
        ("OUTPUT-B", "7Y", 2, 1.0, True),
    )

    result = build_cross_gamma_rows(rows, {"credit/delta": market})

    source = result.loc[result[RISK_GREEK].eq(XGAMMA_RISK_GREEK)]
    developed = result.loc[result[SPLIT].eq(XGAMMA_SPLIT)]
    assert len(source) == 2
    assert source[UNDERLYING].eq("INPUT-A").all()
    assert source[RISK].tolist() == pytest.approx([10.0, 25.0])
    assert source[MARKET_MOVE].tolist() == pytest.approx([2.0, 2.0])
    assert len(developed) == 2
    assert set(developed[UNDERLYING]) == {"OUTPUT-A", "OUTPUT-B"}


@pytest.mark.parametrize(
    "market",
    [
        _credit_market(("OUTPUT", "5Y", 2, 1.0, True)),
        _credit_market(
            ("INPUT-A", "1Y", 0, np.nan, False),
            ("OUTPUT", "5Y", 2, 1.0, True),
        ),
    ],
)
def test_missing_or_unavailable_input_quote_fails_closed(market: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="input quote is missing or unavailable"):
        build_cross_gamma_rows(
            _matrix(_matrix_row()),
            {"credit/delta": market},
        )


def test_missing_output_quote_retains_developed_risk_as_unavailable() -> None:
    market = _credit_market(("INPUT-A", "1Y", 0, 2.5, True))

    result = build_cross_gamma_rows(
        _matrix(_matrix_row(sensitivity=8.0)),
        {"credit/delta": market},
    )

    source = result.loc[result[RISK_GREEK].eq(XGAMMA_RISK_GREEK)].iloc[0]
    assert source[RISK] == 8.0
    assert bool(source[MARKET_AVAILABLE]) is True
    assert source[MARKET_MOVE] == 2.5
    row = result.loc[result[SPLIT].eq(XGAMMA_SPLIT)].iloc[0]
    assert row[RISK] == 20.0
    assert bool(row[MARKET_AVAILABLE]) is False
    assert row[MARKET_DATA_STATUS] == "No matching market row"
    assert pd.isna(row[OPEN])
    assert pd.isna(row[CURRENT])
    assert pd.isna(row[MARKET_MOVE])
    assert pd.isna(row[TENOR_SWAP_ORDER])
    assert pd.isna(row[TENOR_OPTION_ORDER])
    assert row[PL] == 0.0


def test_cross_product_output_resolves_its_own_spec_without_a_quote() -> None:
    rows = _matrix(_matrix_row(sensitivity=3.0))
    rows.loc[0, OUTPUT_RISK_TYPE] = "FX"
    rows.loc[0, OUTPUT_RISK_GREEK] = "Delta"
    rows.loc[0, OUTPUT_UNDERLYING] = "EUR/USD"
    rows.loc[0, OUTPUT_TENOR_SWAP] = ""
    market = _credit_market(("INPUT-A", "1Y", 0, 4.0, True))

    result = build_cross_gamma_rows(rows, {"credit/delta": market})

    source = result.loc[result[RISK_GREEK].eq(XGAMMA_RISK_GREEK)].iloc[0]
    assert source[RISK_TYPE] == "Credit"
    assert source[RISK] == 3.0
    assert source[MARKET_MOVE] == 4.0
    row = result.loc[result[SPLIT].eq(XGAMMA_SPLIT)].iloc[0]
    assert row[RISK] == 12.0
    assert row[SOURCE_TYPE] == "fx/delta"
    assert row[RISK_TYPE] == "FX"
    assert row[UNDERLYING] == "EUR/USD"
    assert row[TENOR_SWAP] == "Spot"
    assert bool(row[MARKET_AVAILABLE]) is False


def test_duplicate_market_quote_keys_are_not_silently_collapsed() -> None:
    market = _credit_market(
        ("INPUT-A", "1Y", 0, 2.0, True),
        ("INPUT-A", "1Y", 0, 2.0, True),
        ("OUTPUT", "5Y", 2, 1.0, True),
    )

    with pytest.raises(ValueError, match="duplicate canonical quote keys"):
        build_cross_gamma_rows(
            _matrix(_matrix_row()),
            {"credit/delta": market},
        )


def test_manager_loads_raw_matrix_before_market_and_publishes_summed_xgamma() -> None:
    events: list[str] = []

    def matrix_loader(_market_date: pd.Timestamp) -> pd.DataFrame:
        events.append("matrix")
        return _integration_matrix()

    manager = _integration_manager(matrix_loader, events)
    snapshot = manager.refresh(force_risk=True, force_pl=True)

    assert snapshot.errors == ()
    market_event_indexes = [
        index
        for index, event in enumerate(events)
        if event.startswith(("open:", "status:"))
    ]
    assert events.index("matrix") < min(market_event_indexes)
    for underlying in (XGAMMA_INPUT_A, XGAMMA_INPUT_B, XGAMMA_OUTPUT):
        assert f"open:{underlying}" in events
        assert f"status:{underlying}" in events
    market_scope = snapshot.market_frame.loc[
        snapshot.market_frame[SOURCE_TYPE].eq("credit/delta"), UNDERLYING
    ]
    assert {XGAMMA_INPUT_A, XGAMMA_INPUT_B, XGAMMA_OUTPUT}.issubset(set(market_scope))
    ordinary_risk_underlyings = snapshot.combined_pl.loc[
        snapshot.combined_pl[SPLIT].eq("Risk"), UNDERLYING
    ]
    assert XGAMMA_INPUT_A in set(ordinary_risk_underlyings)
    assert XGAMMA_INPUT_B in set(ordinary_risk_underlyings)

    source = snapshot.dashboard_frame.loc[
        snapshot.dashboard_frame[RISK_GREEK].eq(XGAMMA_RISK_GREEK)
    ]
    assert len(source) == 2
    assert source[SPLIT].eq("Risk").all()
    assert source[UNDERLYING].tolist() == [XGAMMA_INPUT_A, XGAMMA_INPUT_B]
    assert source[RISK].tolist() == pytest.approx([10.0, -4.0])

    released = snapshot.dashboard_frame.loc[
        snapshot.dashboard_frame[SPLIT].eq(XGAMMA_SPLIT)
    ]
    assert len(released) == 1
    row = released.iloc[0]
    assert row[PORTFOLIO] == "FAKE_REPLACE_ME - BOOK_A"
    assert row[UNDERLYING] == XGAMMA_OUTPUT
    assert row[RISK] == pytest.approx(32.0)
    assert row["Risk SP01"] == pytest.approx(32.0)
    assert pd.isna(row["dRisk SP01"])
    assert row[SPLIT] == "XGAMMA"
    assert row[PL] == 0.0
    assert pd.isna(row[DRISK])


def test_ir_xgamma_source_has_a_dedicated_visible_family() -> None:
    frame = pd.DataFrame(
        {
            "risk type": ["IR", "IR", "IR", "IR"],
            "risk greek": [
                "Delta",
                XGAMMA_RISK_GREEK,
                XGAMMA_VEGA_RISK_GREEK,
                "XCCY",
            ],
        }
    )

    selected = filter_ir_family(frame, "IR", "xgamma")

    assert selected["risk greek"].tolist() == [XGAMMA_RISK_GREEK]
    assert filter_ir_family(frame, "IR", "xgamma_vega")["risk greek"].tolist() == [
        XGAMMA_VEGA_RISK_GREEK
    ]
    assert filter_ir_family(frame, "IR", "delta")["risk greek"].tolist() == ["Delta"]


@pytest.mark.parametrize("measure", ["SP01", "PSP01"])
def test_credit_cross_gamma_source_uses_generic_risk_without_measure_pollution(
    measure: str,
) -> None:
    events: list[str] = []
    manager = _integration_manager(lambda _date: _integration_matrix(), events)
    snapshot = manager.refresh(force_risk=True, force_pl=True)
    prepared = prepare_risk_data(snapshot.dashboard_frame)
    credit_risk = prepared.loc[
        prepared["risk type"].eq("Credit") & prepared["split"].eq("Risk")
    ]
    source_mask = credit_risk["risk greek"].eq(XGAMMA_RISK_GREEK)
    connector_mask = ~source_mask
    selected_column = f"risk {measure.casefold()}"

    assert source_mask.sum() == 2
    assert connector_mask.any()
    assert credit_risk.loc[source_mask, selected_column].isna().all()
    assert credit_risk.loc[connector_mask, selected_column].notna().all()
    assert credit_measure_available(credit_risk, measure)

    displayed = apply_credit_measure(credit_risk, measure)

    assert displayed.loc[source_mask, "risk"].tolist() == pytest.approx([10.0, -4.0])
    assert displayed.loc[source_mask, "drisk"].isna().all()
    assert displayed.loc[source_mask, selected_column].isna().all()
    assert displayed.loc[connector_mask, "risk"].tolist() == pytest.approx(
        credit_risk.loc[connector_mask, selected_column].tolist()
    )


def test_credit_developed_output_keeps_ordinary_measure_handling() -> None:
    events: list[str] = []
    manager = _integration_manager(lambda _date: _integration_matrix(), events)
    snapshot = manager.refresh(force_risk=True, force_pl=True)
    prepared = prepare_risk_data(snapshot.dashboard_frame)
    developed = prepared.loc[
        prepared["risk type"].eq("Credit") & prepared["split"].eq(XGAMMA_SPLIT)
    ]

    assert developed["risk greek"].eq("Delta").all()
    assert apply_credit_measure(developed, "SP01")["risk"].tolist() == pytest.approx(
        developed["risk sp01"].tolist()
    )
    assert apply_credit_measure(developed, "PSP01")["risk"].isna().all()


def test_failed_xgamma_input_retains_last_good_snapshot_atomically() -> None:
    events: list[str] = []
    calls = 0

    def matrix_loader(_market_date: pd.Timestamp) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        events.append("matrix")
        return _integration_matrix(missing_input=calls > 1)

    manager = _integration_manager(matrix_loader, events)
    first = manager.refresh(force_risk=True, force_pl=True)
    first_released = first.dashboard_frame.loc[
        first.dashboard_frame[SPLIT].eq(XGAMMA_SPLIT)
    ]
    assert len(first_released) == 1
    assert first_released.iloc[0][RISK] == pytest.approx(32.0)

    retained = manager.refresh(force_pl=True, expected_revision=first.revision)

    assert calls == 2
    assert retained.revision == first.revision
    assert retained.errors
    assert manager.progress.function_name == "build_cross_gamma_rows"
    pd.testing.assert_frame_equal(retained.dashboard_frame, first.dashboard_frame)
    pd.testing.assert_frame_equal(retained.combined_pl, first.combined_pl)
    pd.testing.assert_frame_equal(retained.market_frame, first.market_frame)
    retained_released = retained.dashboard_frame.loc[
        retained.dashboard_frame[SPLIT].eq(XGAMMA_SPLIT)
    ]
    assert len(retained_released) == 1
    assert retained_released.iloc[0][RISK] == pytest.approx(32.0)


def test_removed_xgamma_scope_removes_stale_quotes_from_committed_marketbook() -> None:
    events: list[str] = []
    calls = 0

    def matrix_loader(_market_date: pd.Timestamp) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        events.append("matrix")
        return _integration_matrix() if calls == 1 else _matrix()

    manager = _integration_manager(matrix_loader, events)
    first = manager.refresh(force_risk=True, force_pl=True)
    supplemental = {XGAMMA_INPUT_A, XGAMMA_INPUT_B, XGAMMA_OUTPUT}
    first_credit_market = first.market_frame.loc[
        first.market_frame[SOURCE_TYPE].eq("credit/delta"), UNDERLYING
    ]
    assert supplemental.issubset(set(first_credit_market))
    second_event_start = len(events)

    second = manager.refresh(force_pl=True, expected_revision=first.revision)

    assert second.errors == ()
    assert second.revision == first.revision + 1
    second_credit_market = second.market_frame.loc[
        second.market_frame[SOURCE_TYPE].eq("credit/delta"), UNDERLYING
    ]
    assert supplemental.isdisjoint(set(second_credit_market))
    assert second.dashboard_frame[SPLIT].ne(XGAMMA_SPLIT).all()
    assert ~second.dashboard_frame[RISK_GREEK].isin(XGAMMA_SOURCE_RISK_GREEKS).any()
    second_events = events[second_event_start:]
    for underlying in supplemental:
        assert f"open:{underlying}" not in second_events
        assert f"status:{underlying}" not in second_events
    assert manager.search_market_udl_options("XGAMMA-ONLY") == ()
