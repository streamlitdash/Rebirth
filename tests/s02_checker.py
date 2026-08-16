"""Checker, readiness, and business-date contract tests."""

from __future__ import annotations

import pandas as pd
import pytest

from core.s02_pipeline import (
    AGE,
    AGE_DEFAULTED,
    MMM_FILE,
    PRODUCT_SPECS,
    RISK_GREEK,
    RISK_TYPE,
    SOURCE_TYPE,
    RiskRefreshManager,
    checker_date_for,
    market_date_for,
    risk_date_for,
)


@pytest.mark.parametrize(
    ("calendar_date", "expected_market_date"),
    [
        ("2026-08-14", "2026-08-14"),  # Friday
        ("2026-08-15", "2026-08-14"),  # Saturday
        ("2026-08-16", "2026-08-14"),  # Sunday
        ("2026-08-17", "2026-08-17"),  # Monday
    ],
)
def test_market_date_is_always_the_latest_weekday(
    calendar_date: str,
    expected_market_date: str,
) -> None:
    assert market_date_for(calendar_date) == pd.Timestamp(expected_market_date)


def test_sunday_market_and_default_t_minus_one_chain() -> None:
    market_date = market_date_for("2026-08-16")
    checker_date = checker_date_for(market_date)

    assert market_date == pd.Timestamp("2026-08-14")
    assert checker_date == pd.Timestamp("2026-08-13")
    assert risk_date_for(checker_date, 0) == pd.Timestamp("2026-08-13")
    assert risk_date_for(checker_date, 1) == pd.Timestamp("2026-08-12")
    # Direct callers receive the same weekend-aware chain.
    assert checker_date_for("2026-08-16") == pd.Timestamp("2026-08-13")


def test_checker_date_is_prior_business_day_and_age_is_applied_afterward() -> None:
    checker_date = checker_date_for("2026-07-20")  # Monday

    assert checker_date == pd.Timestamp("2026-07-17")
    assert risk_date_for(checker_date, 0) == pd.Timestamp("2026-07-17")
    assert risk_date_for(checker_date, 1) == pd.Timestamp("2026-07-16")
    assert risk_date_for(checker_date, 3) == pd.Timestamp("2026-07-14")


@pytest.mark.parametrize("age", [True, -1, 1.5, float("nan")])
def test_risk_date_rejects_ambiguous_age(age: object) -> None:
    with pytest.raises((TypeError, ValueError), match="Age"):
        risk_date_for("2026-07-17", age)  # type: ignore[arg-type]


def test_missing_known_readiness_pairs_are_completed_with_age_zero() -> None:
    supplied = pd.DataFrame(
        [{RISK_TYPE: "IR", RISK_GREEK: "Delta", AGE: 2}],
        columns=[RISK_TYPE, RISK_GREEK, AGE],
    )

    result = RiskRefreshManager._validate_risk_readiness(supplied)
    ir_delta = result.loc[result[SOURCE_TYPE].eq("ir/delta")].iloc[0]
    fx_gamma = result.loc[result[SOURCE_TYPE].eq("fx/gamma")].iloc[0]

    assert len(result) == len(PRODUCT_SPECS)
    assert int(ir_delta[AGE]) == 2
    assert bool(ir_delta[AGE_DEFAULTED]) is False
    assert int(fx_gamma[AGE]) == 0
    assert bool(fx_gamma[AGE_DEFAULTED]) is True


def test_empty_exact_readiness_schema_defaults_the_whole_catalogue() -> None:
    empty = pd.DataFrame(columns=[RISK_TYPE, RISK_GREEK, AGE])

    result = RiskRefreshManager._validate_risk_readiness(empty)

    assert len(result) == len(PRODUCT_SPECS)
    assert result[AGE].eq(0).all()
    assert result[AGE_DEFAULTED].eq(True).all()


def test_readiness_schema_is_exact_and_does_not_accept_source_type_aliases() -> None:
    wrong = pd.DataFrame(
        [["ir/delta", "IR", "Delta", 0]],
        columns=[SOURCE_TYPE, RISK_TYPE, RISK_GREEK, AGE],
    )

    with pytest.raises(ValueError, match="columns must be exactly"):
        RiskRefreshManager._validate_risk_readiness(wrong)


@pytest.mark.parametrize(
    "row, message",
    [
        ({RISK_TYPE: "IR", RISK_GREEK: "Delta", AGE: True}, "booleans"),
        ({RISK_TYPE: "IR", RISK_GREEK: "MadeUp", AGE: 0}, "unknown"),
    ],
)
def test_readiness_rejects_invalid_values(row: dict[str, object], message: str) -> None:
    frame = pd.DataFrame([row], columns=[RISK_TYPE, RISK_GREEK, AGE])

    with pytest.raises(ValueError, match=message):
        RiskRefreshManager._validate_risk_readiness(frame)


def test_checker_inventory_is_partial_and_uses_mmm_files() -> None:
    raw = pd.DataFrame(
        [
            ["IR", "Delta", "usd_delta.mmm", "XVA"],
            ["Credit", "Vega", "credit_vega.MMM", "Hedges"],
        ],
        columns=[RISK_TYPE, RISK_GREEK, MMM_FILE, "Product"],
    )

    result = RiskRefreshManager._validate_risk_checker(raw)

    assert len(result) == 2
    assert set(result[MMM_FILE]) == {"usd_delta.mmm", "credit_vega.MMM"}


@pytest.mark.parametrize(
    "frame, message",
    [
        (
            pd.DataFrame(
                [["IR", "Delta", "wrong.ext", "XVA"]],
                columns=[RISK_TYPE, RISK_GREEK, "Wrong File", "Product"],
            ),
            "columns must be exactly",
        ),
        (
            pd.DataFrame(
                [["IR", "Delta", "usd.csv", "XVA"]],
                columns=[RISK_TYPE, RISK_GREEK, MMM_FILE, "Product"],
            ),
            r"\.mmm",
        ),
        (
            pd.DataFrame(
                [["IR", "Delta", "usd.mmm", "Unknown"]],
                columns=[RISK_TYPE, RISK_GREEK, MMM_FILE, "Product"],
            ),
            "XVA.*Hedges",
        ),
    ],
)
def test_checker_inventory_rejects_old_or_ambiguous_contracts(
    frame: pd.DataFrame,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RiskRefreshManager._validate_risk_checker(frame)


def test_combined_checker_function_receives_the_authoritative_checker_date() -> None:
    calls: list[pd.Timestamp] = []

    def checker_loader(checker_date: pd.Timestamp):
        calls.append(pd.Timestamp(checker_date))
        readiness = pd.DataFrame(
            [["IR", "Delta", 1]],
            columns=[RISK_TYPE, RISK_GREEK, AGE],
        )
        inventory = pd.DataFrame(
            [["IR", "Delta", "usd.mmm", "XVA"]],
            columns=[RISK_TYPE, RISK_GREEK, MMM_FILE, "Product"],
        )
        return readiness, inventory

    # Callable governance inputs stay lazy, so this unit test exercises only
    # the combined dated checker boundary and performs no product I/O.
    manager = RiskRefreshManager(
        lambda _date: pd.DataFrame(),
        thresholds=lambda: pd.DataFrame(),
        risk_checker_loader=checker_loader,
        market_status_resolver=lambda _date: "Live",
        risk_loader=lambda _date, _source: pd.DataFrame(),
        market_open_loader=lambda _source, _date, _underlying, **_kwargs: (
            pd.DataFrame()
        ),
        market_status_loader=lambda _source, _date, _underlying, **_kwargs: (
            pd.DataFrame()
        ),
    )
    checker_date = checker_date_for("2026-07-20")

    readiness, inventory = manager._load_risk_checker(checker_date)

    assert calls == [pd.Timestamp("2026-07-17")]
    assert len(readiness) == len(PRODUCT_SPECS)
    assert inventory[[RISK_TYPE, RISK_GREEK, MMM_FILE, "Product"]].values.tolist() == [
        ["IR", "Delta", "usd.mmm", "XVA"]
    ]


@pytest.mark.parametrize(
    ("delay", "error_type", "message"),
    [
        (True, TypeError, "real number"),
        (float("nan"), ValueError, "finite"),
        (float("inf"), ValueError, "finite"),
        (float("-inf"), ValueError, "finite"),
    ],
)
def test_stage_delay_rejects_boolean_and_nonfinite_values(
    delay: object,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        RiskRefreshManager(
            lambda _date: pd.DataFrame(),
            thresholds=lambda: pd.DataFrame(),
            risk_checker_loader=lambda _date: (pd.DataFrame(), pd.DataFrame()),
            market_status_resolver=lambda _date: "Live",
            risk_loader=lambda _date, _source: pd.DataFrame(),
            market_open_loader=lambda _source, _date, _underlying, **_kwargs: (
                pd.DataFrame()
            ),
            market_status_loader=lambda _source, _date, _underlying, **_kwargs: (
                pd.DataFrame()
            ),
            stage_delays={"risk_product": delay},  # type: ignore[dict-item]
        )
