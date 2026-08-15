"""Targeted committed-state reads avoid unrelated DataFrame copies."""

from __future__ import annotations

import pandas as pd
import pytest

from feeds.s01_sources import build_production_refresh_manager


def test_targeted_manager_reads_copy_only_the_requested_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)
    committed = manager._snapshot
    assert committed is not None

    original_copy = pd.DataFrame.copy
    copied_ids: list[int] = []

    def tracked_copy(frame: pd.DataFrame, deep: bool = True) -> pd.DataFrame:
        copied_ids.append(id(frame))
        return original_copy(frame, deep=deep)

    monkeypatch.setattr(pd.DataFrame, "copy", tracked_copy)

    control = manager.control_snapshot
    assert copied_ids == [id(committed.risk_status)]
    control.risk_status.iloc[0, 0] = "changed by caller"
    assert committed.risk_status.iloc[0, 0] != "changed by caller"

    copied_ids.clear()
    pl = manager.pl_snapshot
    assert copied_ids == [id(committed.combined_pl)]
    pl.combined_pl.iloc[0, 0] = "changed by caller"
    assert committed.combined_pl.iloc[0, 0] != "changed by caller"

    copied_ids.clear()
    checker = manager.read_frame("risk_checker")
    assert copied_ids == [id(committed.risk_checker)]
    assert checker.revision == committed.revision
    checker.frame.iloc[0, 0] = "changed by caller"
    assert committed.risk_checker.iloc[0, 0] != "changed by caller"

    copied_ids.clear()
    dashboard = manager.read_frame("dashboard_frame")
    assert copied_ids == [id(committed.dashboard_frame)]
    assert dashboard.revision == committed.revision


def test_targeted_frame_read_rejects_unknown_names() -> None:
    manager = build_production_refresh_manager()
    manager.refresh(force_risk=True, force_pl=True)

    with pytest.raises(ValueError, match="unknown committed frame"):
        manager.read_frame("not_a_frame")  # type: ignore[arg-type]
