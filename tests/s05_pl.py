"""Governed P&L aggregation, overlay, export, and date/portfolio storage tests."""

from __future__ import annotations

import pandas as pd
import pytest

from core.s04_pl import (
    ADJUSTMENT,
    CONCERTO_FIELD,
    PL_SEND_COLUMNS,
    PLSendValidationError,
    apply_adjustment_overlay,
    build_pl_send_base,
    build_saved_pl_frame,
    empty_pl_send_frame,
)
from core.s05_storage import AdjustmentPersistenceError, LocalCsvAdjustmentRepository


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


def test_pl_base_aggregates_each_portfolio_concerto_field_once() -> None:
    base = build_pl_send_base(_raw_pl(), _mapping(), _governance())

    assert base[["Portfolio", "PL"]].values.tolist() == [
        ["BOOK_A", 15.0],
        ["BOOK_B", 7.0],
    ]
    assert base[CONCERTO_FIELD].eq("irdeltaeffect").all()
    assert base[ADJUSTMENT].eq(False).all()


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


def test_saved_pl_keeps_unadjusted_rows_and_adds_adjustment_rows() -> None:
    saved = build_saved_pl_frame(
        _raw_pl(),
        _mapping(),
        _governance(),
        _adjustments(("BOOK_A", 99.0)),
    )

    assert saved["Record Type"].value_counts().to_dict() == {
        "Unadjusted": 3,
        "Adjustment": 1,
    }
    assert saved.loc[saved["Record Type"].eq("Unadjusted"), "PL"].tolist() == [
        10.0,
        5.0,
        7.0,
    ]
    adjustment = saved.loc[saved["Record Type"].eq("Adjustment")].iloc[0]
    assert adjustment["Portfolio"] == "BOOK_A"
    assert adjustment["PL"] == 99.0
    assert bool(adjustment[ADJUSTMENT]) is True


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
