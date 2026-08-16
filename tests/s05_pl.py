"""Governed P&L aggregation, overlay, history, and adjustment-storage tests."""

from __future__ import annotations

import pandas as pd
import pytest

from core.s04_pl import (
    ACTIVITY,
    ADJUSTMENT,
    BOOK,
    CATEGORY,
    COLOSSUS_TYPE,
    CONCERTO_FIELD,
    HISTORICAL_PL_COLUMNS,
    HISTO_TYPE,
    HISTORY_FILE_COLUMNS,
    HISTORY_MAPPING_STATUS,
    HISTORY_TYPE,
    PL_HISTORY_COLUMNS,
    PL_HISTORY_DAILY_PERIOD,
    PL_HISTORY_MTD_PERIOD,
    PL_HISTORY_PERIOD_COLUMNS,
    PL_HISTORY_WTD_PERIOD,
    PL_HISTORY_YTD_PERIOD,
    PL_SEND_COLUMNS,
    PLSendValidationError,
    PREDICT_TYPE,
    PREDICTED_TYPE,
    PORTFOLIO,
    PRODUCT,
    RISK_GREEK,
    RISK_TYPE,
    SIGNOFF_GROUP,
    SUB_CATEGORY,
    UNDERLYING,
    apply_adjustment_overlay,
    build_pl_send_base,
    empty_pl_send_frame,
    load_historical_pl,
    load_pl_history,
    normalize_pl_history_types,
    pl_history_period_bounds,
    pl_history_period_values,
    select_pl_history_series,
)
from core.s05_storage import AdjustmentPersistenceError, LocalCsvAdjustmentRepository
from core.s09_cross_gamma import XGAMMA_RISK_GREEK, XGAMMA_VEGA_RISK_GREEK


MARKET_DATE = "2026-07-20"


def _mapping() -> pd.DataFrame:
    return pd.DataFrame(
        [["IR", "Delta", "irdeltaeffect"]],
        columns=["Risk Type", "Risk Greek", CONCERTO_FIELD],
    )


def _governance() -> pd.DataFrame:
    return pd.DataFrame(
        [["BOOK_A", "SOG_A"], ["BOOK_B", "SOG_B"]],
        columns=["Portfolio", "SignoffGroup"],
    )


def _raw_pl() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [MARKET_DATE, "IR", "Delta", "BOOK_A", "SOG_A", 10.0, True],
            [MARKET_DATE, "IR", "Delta", "BOOK_A", "SOG_A", 5.0, True],
            [MARKET_DATE, "IR", "Delta", "BOOK_B", "SOG_B", 7.0, True],
        ],
        columns=[
            "Market Date",
            "Risk Type",
            "Risk Greek",
            "Portfolio",
            "SignoffGroup",
            "PL",
            "Portfolio Mapped",
        ],
    )


def _adjustments(*rows: tuple[str, float]) -> pd.DataFrame:
    records = [
        [
            MARKET_DATE,
            "IR",
            "Delta",
            portfolio,
            "SOG_A" if portfolio == "BOOK_A" else "SOG_B",
            "irdeltaeffect",
            value,
            True,
        ]
        for portfolio, value in rows
    ]
    return pd.DataFrame(records, columns=list(PL_SEND_COLUMNS))


def _historical_pl() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["2026-07-19", "BOOK_B", "irdeltaeffect", -4.5],
            ["2026-07-18", "BOOK_A", "irdeltaeffect", "10.25"],
            ["2026-07-19", "BOOK_A", "fxdeltaeffect", 3.0],
        ],
        columns=list(HISTORICAL_PL_COLUMNS),
    )


def _history_leaf(root, market_date: str, *, duplicate: bool = False):
    leaf = root / market_date
    leaf.mkdir(parents=True)
    histo_rows = [
        ["IR", "Delta", "EUR", "XVA", "BOOK_A", 10.0],
        ["FX", "Delta", "EUR/USD", "Hedges", "BOOK_B", -4.0],
    ]
    if duplicate:
        histo_rows.append(histo_rows[0])
    predicted_rows = [
        ["IR", "Delta", "EUR", "XVA", "BOOK_A", 9.5],
        ["FX", "Delta", "EUR/USD", "Hedges", "BOOK_B", -3.5],
    ]
    pd.DataFrame(histo_rows, columns=HISTORY_FILE_COLUMNS).to_csv(
        leaf / "histo.csv", index=False
    )
    pd.DataFrame(predicted_rows, columns=HISTORY_FILE_COLUMNS).to_csv(
        leaf / "predicted.csv", index=False
    )
    return leaf


def _analysis_history() -> pd.DataFrame:
    """History with deliberate missing dates/types and more than one leaf."""

    history = pd.DataFrame(
        [
            ["2025-12-31", "Histo", "IR", "Delta", "EUR", "XVA", "BOOK_A", 100.0],
            [
                "2025-12-31",
                "Predicted",
                "IR",
                "Delta",
                "EUR",
                "XVA",
                "BOOK_A",
                90.0,
            ],
            ["2026-01-02", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 1.0],
            ["2026-01-02", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 2.0],
            ["2026-07-31", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 3.0],
            ["2026-07-31", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 4.0],
            ["2026-08-01", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 5.0],
            ["2026-08-01", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 6.0],
            ["2026-08-10", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 10.0],
            ["2026-08-10", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 11.0],
            ["2026-08-10", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_B", 2.0],
            ["2026-08-10", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_B", 3.0],
            ["2026-08-11", COLOSSUS_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 4.0],
            ["2026-08-13", PREDICT_TYPE, "IR", "Delta", "EUR", "XVA", "BOOK_A", 5.0],
            [
                "2026-08-14",
                COLOSSUS_TYPE,
                "FX",
                "Delta",
                "EUR/USD",
                "Hedge",
                "BOOK_C",
                7.0,
            ],
            [
                "2026-08-14",
                PREDICT_TYPE,
                "FX",
                "Delta",
                "EUR/USD",
                "Hedge",
                "BOOK_C",
                8.0,
            ],
        ],
        columns=["Market Date", HISTORY_TYPE, *HISTORY_FILE_COLUMNS],
    )
    history = history.rename(columns={BOOK: PORTFOLIO})
    history[ACTIVITY] = "Rates"
    history[SIGNOFF_GROUP] = history[PORTFOLIO].map(
        {"BOOK_A": "SOG-A", "BOOK_B": "SOG-A", "BOOK_C": "SOG-B"}
    )
    history[CATEGORY] = "Core"
    history[SUB_CATEGORY] = "Synthetic"
    history[HISTORY_MAPPING_STATUS] = "Mapped"
    return history.loc[:, list(PL_HISTORY_COLUMNS)]


def test_historical_pl_normalizes_and_sorts_the_exact_daily_grain(tmp_path) -> None:
    source = tmp_path / "historical.csv"
    _historical_pl().to_csv(source, index=False)

    history = load_historical_pl(source)

    assert list(history.columns) == list(HISTORICAL_PL_COLUMNS)
    assert history[["Market Date", "Portfolio", CONCERTO_FIELD]].values.tolist() == [
        ["2026-07-18", "BOOK_A", "irdeltaeffect"],
        ["2026-07-19", "BOOK_A", "fxdeltaeffect"],
        ["2026-07-19", "BOOK_B", "irdeltaeffect"],
    ]
    assert history["PL"].dtype == float


def test_historical_pl_rejects_any_schema_drift() -> None:
    wrong_order = _historical_pl()[["Portfolio", "Market Date", CONCERTO_FIELD, "PL"]]

    with pytest.raises(PLSendValidationError, match="exactly these columns in order"):
        load_historical_pl(wrong_order)


def test_historical_pl_rejects_duplicate_daily_book_and_concerto_keys() -> None:
    duplicate = pd.concat([_historical_pl(), _historical_pl().iloc[[0]]])

    with pytest.raises(PLSendValidationError, match="duplicate Market Date"):
        load_historical_pl(duplicate)


def test_pl_history_loads_strict_actual_and_predicted_date_partitions(tmp_path) -> None:
    root = tmp_path / "histo"
    _history_leaf(root, "2026-08-15")
    _history_leaf(root, "2026-08-14")

    history = load_pl_history(root)

    assert list(history.columns) == list(PL_HISTORY_COLUMNS)
    assert len(history) == 8
    assert set(history[HISTORY_TYPE]) == {HISTO_TYPE, PREDICTED_TYPE}
    assert history["Market Date"].tolist() == [
        "2026-08-14",
        "2026-08-14",
        "2026-08-14",
        "2026-08-14",
        "2026-08-15",
        "2026-08-15",
        "2026-08-15",
        "2026-08-15",
    ]
    actual = history.loc[
        history[HISTORY_TYPE].eq(HISTO_TYPE)
        & history["Market Date"].eq("2026-08-15")
        & history[PORTFOLIO].eq("BOOK_A")
    ]
    assert actual.iloc[0]["PL"] == 10.0


def test_pl_history_file_names_emit_canonical_user_facing_type_labels(
    tmp_path,
) -> None:
    root = tmp_path / "histo"
    _history_leaf(root, "2026-08-15")

    history = load_pl_history(root)

    assert COLOSSUS_TYPE == "Colossus"
    assert PREDICT_TYPE == "Predict"
    assert HISTO_TYPE == COLOSSUS_TYPE
    assert PREDICTED_TYPE == PREDICT_TYPE
    assert set(history[HISTORY_TYPE]) == {COLOSSUS_TYPE, PREDICT_TYPE}
    assert normalize_pl_history_types(["Predicted", "real", "Histo"]) == (
        COLOSSUS_TYPE,
        PREDICT_TYPE,
    )


def test_pl_history_series_aggregates_exact_path_once_per_observed_day_and_type() -> (
    None
):
    history = _analysis_history()

    series = select_pl_history_series(
        history,
        ("SOG-A", "IR", "Delta", "EUR", "XVA"),
        ("actual", "Predicted"),
    )

    assert list(series.columns) == ["Market Date", HISTORY_TYPE, "PL"]
    assert not series.duplicated(["Market Date", HISTORY_TYPE]).any()
    assert "2026-08-12" not in series["Market Date"].tolist()
    monday = series.loc[series["Market Date"].eq("2026-08-10")]
    assert monday[[HISTORY_TYPE, "PL"]].values.tolist() == [
        [COLOSSUS_TYPE, 12.0],
        [PREDICT_TYPE, 14.0],
    ]
    assert set(series[HISTORY_TYPE]) == {COLOSSUS_TYPE, PREDICT_TYPE}


def test_pl_history_series_supports_total_and_keeps_missing_identity_empty() -> None:
    history = _analysis_history()

    total = select_pl_history_series(history, (), PREDICT_TYPE)
    missing = select_pl_history_series(history, ("SOG-X",))

    latest = total.loc[total["Market Date"].eq("2026-08-14")]
    assert latest[[HISTORY_TYPE, "PL"]].values.tolist() == [[PREDICT_TYPE, 8.0]]
    assert list(missing.columns) == ["Market Date", HISTORY_TYPE, "PL"]
    assert missing.empty


def test_pl_history_period_bounds_use_calendar_monday_month_and_year() -> None:
    assert pl_history_period_bounds("2026-08-12") == {
        PL_HISTORY_DAILY_PERIOD: ("2026-08-12", "2026-08-12"),
        PL_HISTORY_WTD_PERIOD: ("2026-08-10", "2026-08-12"),
        PL_HISTORY_MTD_PERIOD: ("2026-08-01", "2026-08-12"),
        PL_HISTORY_YTD_PERIOD: ("2026-01-01", "2026-08-12"),
    }


def test_pl_history_period_values_use_global_latest_and_required_type_semantics() -> (
    None
):
    values = pl_history_period_values(_analysis_history())

    assert list(values.columns) == list(PL_HISTORY_PERIOD_COLUMNS)
    assert values.loc[
        values["Period"].eq(PL_HISTORY_DAILY_PERIOD), HISTORY_TYPE
    ].tolist() == [PREDICT_TYPE]
    assert values.loc[values["Period"].eq(PL_HISTORY_DAILY_PERIOD), "PL"].tolist() == [
        8.0
    ]
    for period in (
        PL_HISTORY_WTD_PERIOD,
        PL_HISTORY_MTD_PERIOD,
        PL_HISTORY_YTD_PERIOD,
    ):
        assert values.loc[values["Period"].eq(period), HISTORY_TYPE].tolist() == [
            COLOSSUS_TYPE,
            PREDICT_TYPE,
        ]

    totals = {
        (row["Period"], row[HISTORY_TYPE]): row["PL"]
        for row in values.to_dict("records")
    }
    assert totals == {
        (PL_HISTORY_DAILY_PERIOD, PREDICT_TYPE): 8.0,
        (PL_HISTORY_WTD_PERIOD, COLOSSUS_TYPE): 23.0,
        (PL_HISTORY_WTD_PERIOD, PREDICT_TYPE): 27.0,
        (PL_HISTORY_MTD_PERIOD, COLOSSUS_TYPE): 28.0,
        (PL_HISTORY_MTD_PERIOD, PREDICT_TYPE): 33.0,
        (PL_HISTORY_YTD_PERIOD, COLOSSUS_TYPE): 32.0,
        (PL_HISTORY_YTD_PERIOD, PREDICT_TYPE): 39.0,
    }


def test_pl_history_period_values_do_not_fall_back_or_fabricate_missing_daily() -> None:
    history = _analysis_history()

    global_latest = pl_history_period_values(history, ("SOG-A", "IR"))
    explicit_ir_latest = pl_history_period_values(
        history,
        ("SOG-A", "IR"),
        as_of="2026-08-13",
    )
    missing = pl_history_period_values(history, ("SOG-X",))

    assert global_latest.loc[global_latest["Period"].eq(PL_HISTORY_DAILY_PERIOD)].empty
    assert explicit_ir_latest.loc[
        explicit_ir_latest["Period"].eq(PL_HISTORY_DAILY_PERIOD),
        [HISTORY_TYPE, "PL"],
    ].values.tolist() == [[PREDICT_TYPE, 5.0]]
    assert list(missing.columns) == list(PL_HISTORY_PERIOD_COLUMNS)
    assert missing.empty


def test_pl_history_reuses_unchanged_csvs_and_invalidates_on_file_change(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "histo"
    leaf = _history_leaf(root, "2026-08-15")
    real_read_csv = pd.read_csv
    reads: list[object] = []

    def counted_read_csv(*args, **kwargs):
        reads.append(args[0])
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(pd, "read_csv", counted_read_csv)
    first = load_pl_history(root)
    second = load_pl_history(root)

    assert first.equals(second)
    assert len(reads) == 2

    predicted = real_read_csv(leaf / "predicted.csv")
    predicted.loc[0, "PL"] = 1_234_567.0
    predicted.to_csv(leaf / "predicted.csv", index=False)
    refreshed = load_pl_history(root)

    assert len(reads) == 4
    assert 1_234_567.0 in refreshed["PL"].tolist()


def test_pl_history_rejects_legacy_source_without_hierarchy_identity() -> None:
    with pytest.raises(PLSendValidationError, match="paired P&L history requires"):
        load_pl_history(_historical_pl())


def test_pl_history_requires_both_named_files_in_every_date_partition(
    tmp_path,
) -> None:
    leaf = _history_leaf(tmp_path / "histo", "2026-08-15")
    (leaf / "predicted.csv").unlink()

    with pytest.raises(PLSendValidationError, match="missing.*predicted.csv"):
        load_pl_history(tmp_path / "histo")


def test_pl_history_rejects_invalid_calendar_partition(tmp_path) -> None:
    leaf = tmp_path / "histo" / "2026-02-30"
    leaf.mkdir(parents=True)
    for name in ("histo.csv", "predicted.csv"):
        pd.DataFrame(columns=HISTORY_FILE_COLUMNS).to_csv(leaf / name, index=False)

    with pytest.raises(PLSendValidationError, match="not a valid date"):
        load_pl_history(tmp_path / "histo")


def test_pl_history_rejects_the_retired_nested_year_layout(tmp_path) -> None:
    leaf = tmp_path / "histo" / "2026" / "08-15"
    leaf.mkdir(parents=True)
    for name in ("histo.csv", "predicted.csv"):
        pd.DataFrame(columns=HISTORY_FILE_COLUMNS).to_csv(leaf / name, index=False)

    with pytest.raises(PLSendValidationError, match="YYYY-MM-DD"):
        load_pl_history(tmp_path / "histo")


def test_pl_history_rejects_leaf_schema_drift_and_duplicate_grain(tmp_path) -> None:
    root = tmp_path / "histo"
    leaf = _history_leaf(root, "2026-08-15")
    pd.DataFrame(
        [["IR", "Delta", "EUR", "XVA", 10.0, "BOOK_A"]],
        columns=[RISK_TYPE, RISK_GREEK, UNDERLYING, PRODUCT, "PL", BOOK],
    ).to_csv(leaf / "predicted.csv", index=False)
    with pytest.raises(PLSendValidationError, match="exactly these columns in order"):
        load_pl_history(root)

    other_root = tmp_path / "duplicate-histo"
    _history_leaf(other_root, "2026-08-15", duplicate=True)
    with pytest.raises(PLSendValidationError, match="duplicate history identity"):
        load_pl_history(other_root)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("Market Date", "not-a-date", "Market Date"),
        ("Portfolio", "", "nonblank text"),
        (CONCERTO_FIELD, None, "nonblank text"),
        ("PL", float("inf"), "finite numbers"),
        ("PL", True, "finite numbers"),
    ],
)
def test_historical_pl_rejects_invalid_identity_and_values(
    column: str,
    value: object,
    message: str,
) -> None:
    history = _historical_pl()
    history.loc[0, column] = value

    with pytest.raises(PLSendValidationError, match=message):
        load_historical_pl(history)


def test_pl_base_aggregates_each_portfolio_concerto_field_once() -> None:
    base = build_pl_send_base(_raw_pl(), _mapping(), _governance())

    assert base[["Portfolio", "PL"]].values.tolist() == [
        ["BOOK_A", 15.0],
        ["BOOK_B", 7.0],
    ]
    assert base[CONCERTO_FIELD].eq("irdeltaeffect").all()
    assert base[ADJUSTMENT].eq(False).all()


def test_pl_base_excludes_only_zero_pl_cross_gamma_source_sensitivities() -> None:
    mapping = pd.DataFrame(
        [
            ["Credit", "Delta", "creditdeltaeffect"],
            ["IR", "Delta", "irdeltaeffect"],
        ],
        columns=["Risk Type", "Risk Greek", CONCERTO_FIELD],
    )
    raw = pd.DataFrame(
        [
            [
                MARKET_DATE,
                "Credit",
                XGAMMA_RISK_GREEK,
                "Risk",
                "BOOK_A",
                "SOG_A",
                0.0,
                True,
            ],
            [
                MARKET_DATE,
                "Credit",
                "Delta",
                "XGAMMA",
                "BOOK_A",
                "SOG_A",
                0.0,
                True,
            ],
            [
                MARKET_DATE,
                "IR",
                "Delta",
                "Risk",
                "BOOK_B",
                "SOG_B",
                0.0,
                True,
            ],
        ],
        columns=[
            "Market Date",
            "Risk Type",
            "Risk Greek",
            "Split",
            "Portfolio",
            "SignoffGroup",
            "PL",
            "Portfolio Mapped",
        ],
    )

    base = build_pl_send_base(raw, mapping, _governance())

    assert base[["Risk Type", "Risk Greek", CONCERTO_FIELD, "PL"]].values.tolist() == [
        ["Credit", "Delta", "creditdeltaeffect", 0.0],
        ["IR", "Delta", "irdeltaeffect", 0.0],
    ]


@pytest.mark.parametrize("source_greek", [XGAMMA_RISK_GREEK, XGAMMA_VEGA_RISK_GREEK])
def test_pl_base_rejects_nonzero_cross_gamma_source_sensitivity_pl(
    source_greek: str,
) -> None:
    raw = _raw_pl().iloc[[0]].copy()
    raw["Risk Type"] = "Credit"
    raw["Risk Greek"] = source_greek
    raw["Split"] = "Risk"
    raw["PL"] = 1.0

    with pytest.raises(
        PLSendValidationError,
        match="Cross Gamma source-sensitivity rows must have PL=0",
    ):
        build_pl_send_base(raw, _mapping(), _governance())


def test_adjustment_overlay_replaces_same_date_portfolio_and_concerto_field() -> None:
    base = build_pl_send_base(_raw_pl(), _mapping(), _governance())

    effective = apply_adjustment_overlay(
        base,
        _adjustments(("BOOK_A", 99.0)),
        _mapping(),
        _governance(),
    )
    ignored = apply_adjustment_overlay(
        base,
        _adjustments(("BOOK_A", 99.0)),
        _mapping(),
        _governance(),
        include_adjustments=False,
    )

    assert effective[["Portfolio", "PL", ADJUSTMENT]].values.tolist() == [
        ["BOOK_A", 99.0, True],
        ["BOOK_B", 7.0, False],
    ]
    assert ignored[["Portfolio", "PL"]].values.tolist() == [
        ["BOOK_A", 15.0],
        ["BOOK_B", 7.0],
    ]


def test_governed_mapping_cannot_assign_one_pair_to_another_name() -> None:
    rows = _adjustments(("BOOK_A", 1.0))
    rows.loc[0, CONCERTO_FIELD] = "wrongname"

    with pytest.raises(PLSendValidationError, match="contradict"):
        apply_adjustment_overlay(
            build_pl_send_base(_raw_pl(), _mapping(), _governance()),
            rows,
            _mapping(),
            _governance(),
        )


def test_repository_uses_adjustments_date_portfolio_layout(tmp_path) -> None:
    repository = LocalCsvAdjustmentRepository(tmp_path / "adjustments")
    rows = _adjustments(("BOOK_A", 99.0), ("BOOK_B", 8.0))

    date_directory = repository.save(
        MARKET_DATE,
        rows,
        base_revision=4,
        saved_at="2026-07-20T12:00:00Z",
    )

    assert date_directory == tmp_path / "adjustments" / MARKET_DATE
    assert date_directory.is_dir()
    assert len(list(date_directory.glob("*.csv"))) == 2
    assert repository.path_for_portfolio(MARKET_DATE, "BOOK_A").is_file()
    assert repository.path_for_portfolio(MARKET_DATE, "BOOK_B").is_file()
    loaded = repository.load(MARKET_DATE)
    assert loaded[["Portfolio", "PL"]].values.tolist() == [
        ["BOOK_A", 99.0],
        ["BOOK_B", 8.0],
    ]


def test_repository_replaces_only_portfolios_present_in_the_save(tmp_path) -> None:
    repository = LocalCsvAdjustmentRepository(tmp_path / "adjustments")
    repository.save(
        MARKET_DATE,
        _adjustments(("BOOK_A", 10.0), ("BOOK_B", 20.0)),
        base_revision=1,
    )

    repository.save(
        MARKET_DATE,
        _adjustments(("BOOK_A", 11.0)),
        base_revision=2,
    )
    loaded = repository.load(MARKET_DATE)

    assert loaded[["Portfolio", "PL", "Base Revision"]].values.tolist() == [
        ["BOOK_A", 11.0, 2],
        ["BOOK_B", 20.0, 1],
    ]


def test_repository_rejects_rows_for_another_date(tmp_path) -> None:
    repository = LocalCsvAdjustmentRepository(tmp_path / "adjustments")
    rows = _adjustments(("BOOK_A", 1.0))
    rows.loc[0, "Market Date"] = "2026-07-21"

    with pytest.raises(AdjustmentPersistenceError, match="requested Market Date"):
        repository.save(MARKET_DATE, rows, base_revision=1)


def test_repository_can_remove_one_portfolio_final_adjustment_file(tmp_path) -> None:
    repository = LocalCsvAdjustmentRepository(tmp_path / "adjustments")
    repository.save(
        MARKET_DATE,
        _adjustments(("BOOK_A", 10.0), ("BOOK_B", 20.0)),
        base_revision=2,
    )

    repository.save(
        MARKET_DATE,
        empty_pl_send_frame(),
        base_revision=3,
        replace_portfolios={"BOOK_A"},
    )

    assert not repository.path_for_portfolio(MARKET_DATE, "BOOK_A").exists()
    assert repository.load(MARKET_DATE)[["Portfolio", "PL"]].values.tolist() == [
        ["BOOK_B", 20.0]
    ]


def test_repository_rejects_stale_base_revision(tmp_path) -> None:
    repository = LocalCsvAdjustmentRepository(tmp_path / "adjustments")
    repository.save(
        MARKET_DATE,
        _adjustments(("BOOK_A", 10.0)),
        base_revision=7,
    )

    with pytest.raises(AdjustmentPersistenceError, match="older than saved Portfolio"):
        repository.save(
            MARKET_DATE,
            _adjustments(("BOOK_A", 11.0)),
            base_revision=6,
        )

    assert repository.load(MARKET_DATE).loc[0, "PL"] == 10.0


def test_repository_rolls_back_all_target_portfolios_after_publish_error(
    tmp_path, monkeypatch
) -> None:
    repository = LocalCsvAdjustmentRepository(tmp_path / "adjustments")
    repository.save(
        MARKET_DATE,
        _adjustments(("BOOK_A", 10.0), ("BOOK_B", 20.0)),
        base_revision=1,
    )

    from core import s05_storage as storage

    real_replace = storage.os.replace
    published = 0

    def fail_second_publish(source, destination):
        nonlocal published
        if str(source).endswith(".tmp"):
            published += 1
            if published == 2:
                raise OSError("simulated publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", fail_second_publish)
    with pytest.raises(AdjustmentPersistenceError, match="simulated publish failure"):
        repository.save(
            MARKET_DATE,
            _adjustments(("BOOK_A", 11.0), ("BOOK_B", 21.0)),
            base_revision=2,
        )

    assert repository.load(MARKET_DATE)[["Portfolio", "PL"]].values.tolist() == [
        ["BOOK_A", 10.0],
        ["BOOK_B", 20.0],
    ]
