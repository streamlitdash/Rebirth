"""Intraday Cashflows connector contract tests."""

from __future__ import annotations

import pandas as pd
import pytest

from core.s06_cashflow import (
    AMOUNT,
    CASHFLOW_TIME,
    INTRADAY_CASHFLOW_COLUMNS,
    STATUS,
    VALUE_DATE,
    IntradayCashflowSchemaError,
    load_intraday_cashflows,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                "CF-1",
                "2026-07-20T12:30:00Z",
                "2026-07-21",
                "BOOK_A",
                "SOG_A",
                "gbp",
                "Coupon",
                "125.5",
                "pending",
            ]
        ],
        columns=list(INTRADAY_CASHFLOW_COLUMNS),
    )


def test_cashflow_loader_receives_normalized_date_and_returns_canonical_types() -> None:
    calls: list[pd.Timestamp] = []

    def loader(selected_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(selected_date)
        return _frame()

    result = load_intraday_cashflows(loader, "2026-07-20T18:00:00Z")

    assert calls == [pd.Timestamp("2026-07-20")]
    assert result.loc[0, STATUS] == "Pending"
    assert result.loc[0, AMOUNT] == 125.5
    assert str(result[CASHFLOW_TIME].dtype) == "datetime64[ns, UTC]"
    assert result.loc[0, VALUE_DATE] == pd.Timestamp("2026-07-21")


def test_cashflow_schema_rejects_aliases_and_nonfinite_amounts() -> None:
    aliased = _frame().rename(columns={"SignoffGroup": "SOG"})
    with pytest.raises(IntradayCashflowSchemaError, match="canonical schema"):
        load_intraday_cashflows(lambda _date: aliased, "2026-07-20")

    nonfinite = _frame()
    nonfinite.loc[0, AMOUNT] = "inf"
    with pytest.raises(IntradayCashflowSchemaError, match="finite numbers"):
        load_intraday_cashflows(lambda _date: nonfinite, "2026-07-20")
