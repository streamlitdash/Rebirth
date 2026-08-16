"""Official flat Risk archive, projection, and scheduler contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import core.s11_risk_archive as archive_module
from core.s04_pl import (
    COLOSSUS_TYPE,
    HISTORY_FILE_COLUMNS,
    HISTORY_TYPE,
    PL_HISTORY_COLUMNS,
    PREDICT_TYPE,
)
from core.s11_risk_archive import (
    ARCHIVE_FILE_NAMES,
    COLOSSUS_COLUMNS,
    ArchiveResult,
    RiskArchive,
    RiskArchiveValidationError,
    archive_from_manager,
    archive_official_snapshot,
    list_completed_market_dates,
    load_risk_archive,
    load_shared_pl_history,
    project_archive_to_pl_history,
)
from tools.s03_archive_official_risk import (
    DEFAULT_ARCHIVE_ROOT,
    resolve_archive_root,
    run_scheduled_archive,
)


def _risk() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Portfolio": "BOOK-A",
                "Underlying": "EUR",
                "Risk Type": "IR",
                "Risk Greek": "Delta",
                "Product": "XVA",
                "Tenor Swap": "1Y",
                "PL": 10.0,
                "Risk": 100.0,
                "dRisk": 1.0,
            },
            {
                "Portfolio": "BOOK-A",
                "Underlying": "EUR",
                "Risk Type": "IR",
                "Risk Greek": "Delta",
                "Product": "XVA",
                "Tenor Swap": "5Y",
                "PL": 15.0,
                "Risk": 200.0,
                "dRisk": 2.0,
            },
            {
                "Portfolio": "BOOK-B",
                "Underlying": "EUR/USD",
                "Risk Type": "FX",
                "Risk Greek": "Delta",
                "Product": "Hedges",
                "Tenor Swap": "Spot",
                "PL": -4.0,
                "Risk": -40.0,
                "dRisk": -0.4,
            },
        ]
    )


def _colossus() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["BOOK-A", "EUR", "IR", "Delta", 24.0],
            ["BOOK-B", "EUR/USD", "FX", "Delta", -3.5],
        ],
        columns=list(COLOSSUS_COLUMNS),
    )


def _snapshot(
    *,
    market_status: str = "OFFICIAL",
    market_date: str = "2026-08-14",
    system_date: str = "2026-08-14",
    errors: tuple[str, ...] = (),
    risk: pd.DataFrame | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        revision=7,
        refreshed_at=datetime(2026, 8, 14, 22, 5, tzinfo=timezone.utc),
        market_date=pd.Timestamp(market_date),
        system_date=pd.Timestamp(system_date),
        market_status=market_status,
        errors=errors,
        dashboard_frame=_risk() if risk is None else risk,
    )


def test_official_archive_is_atomic_complete_and_idempotent(tmp_path: Path) -> None:
    calls: list[pd.Timestamp] = []

    def load_colossus(market_date: pd.Timestamp) -> pd.DataFrame:
        calls.append(market_date)
        return _colossus()

    first = archive_official_snapshot(_snapshot(), load_colossus, tmp_path)

    assert first == ArchiveResult(
        status="archived",
        reason="Official Risk Explorer and Colossus P&L archived.",
        market_date="2026-08-14",
        path=tmp_path.resolve() / "2026-08-14",
        risk_rows=3,
        colossus_rows=2,
    )
    assert calls == [pd.Timestamp("2026-08-14")]
    assert {path.name for path in first.path.iterdir()} == set(ARCHIVE_FILE_NAMES)
    assert list_completed_market_dates(tmp_path) == ("2026-08-14",)

    loaded = load_risk_archive(tmp_path, "2026-08-14")
    assert loaded.market_date == "2026-08-14"
    assert loaded.risk.columns.tolist() == _risk().columns.tolist()
    assert loaded.risk["Portfolio"].tolist() == _risk()["Portfolio"].tolist()
    pd.testing.assert_frame_equal(loaded.colossus, _colossus())

    second = archive_official_snapshot(
        _snapshot(),
        lambda _date: (_ for _ in ()).throw(AssertionError("must not reload")),
        tmp_path,
    )
    assert second.status == "already_archived"
    assert second.risk_rows == 3
    assert second.colossus_rows == 2
    assert calls == [pd.Timestamp("2026-08-14")]


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (_snapshot(market_status="Live"), "not OFFICIAL"),
        (
            _snapshot(market_date="2026-08-13", system_date="2026-08-14"),
            "not the current natural",
        ),
        (_snapshot(errors=("retained last good",)), "refresh errors"),
    ],
)
def test_ineligible_snapshots_skip_without_loading_colossus(
    tmp_path: Path,
    snapshot: SimpleNamespace,
    reason: str,
) -> None:
    result = archive_official_snapshot(
        snapshot,
        lambda _date: (_ for _ in ()).throw(AssertionError("must not load")),
        tmp_path,
    )

    assert result.status == "skipped"
    assert reason in result.reason
    assert not (tmp_path / "2026-08-14").exists()


def test_loader_failure_or_invalid_grain_never_publishes_partial_leaf(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="source unavailable"):
        archive_official_snapshot(
            _snapshot(),
            lambda _date: (_ for _ in ()).throw(RuntimeError("source unavailable")),
            tmp_path,
        )
    assert list_completed_market_dates(tmp_path) == ()
    assert not (tmp_path / "2026-08-14").exists()

    empty = pd.DataFrame(columns=list(COLOSSUS_COLUMNS))
    with pytest.raises(RiskArchiveValidationError, match="at least one"):
        archive_official_snapshot(_snapshot(), lambda _date: empty, tmp_path)
    assert list_completed_market_dates(tmp_path) == ()
    assert not (tmp_path / "2026-08-14").exists()

    duplicate = pd.concat([_colossus(), _colossus().iloc[[0]]], ignore_index=True)
    with pytest.raises(RiskArchiveValidationError, match="duplicate four-key"):
        archive_official_snapshot(_snapshot(), lambda _date: duplicate, tmp_path)
    assert list_completed_market_dates(tmp_path) == ()
    assert not (tmp_path / "2026-08-14").exists()


def test_incomplete_leaf_is_hidden_but_a_completed_corrupt_leaf_fails_closed(
    tmp_path: Path,
) -> None:
    incomplete = tmp_path / "2026-08-13"
    incomplete.mkdir(parents=True)
    (incomplete / "risk.csv").write_text("PL\n1\n", encoding="utf-8")
    assert list_completed_market_dates(tmp_path) == ()

    invalid_marker = tmp_path / "2026-08-12"
    invalid_marker.mkdir(parents=True)
    (invalid_marker / "risk.csv").write_text("PL\n1\n", encoding="utf-8")
    (invalid_marker / "colossus.csv").write_text("PL\n1\n", encoding="utf-8")
    (invalid_marker / "_SUCCESS").write_text("{}\n", encoding="utf-8")
    with pytest.raises(RiskArchiveValidationError, match="marker is invalid"):
        list_completed_market_dates(tmp_path)
    for path in invalid_marker.iterdir():
        path.unlink()
    invalid_marker.rmdir()

    result = archive_official_snapshot(_snapshot(), lambda _date: _colossus(), tmp_path)
    (result.path / "risk.csv").write_text("PL\n999\n", encoding="utf-8")
    with pytest.raises(RiskArchiveValidationError, match="completion marker"):
        load_risk_archive(tmp_path, "2026-08-14")


def test_csv_round_trip_preserves_numeric_looking_identity_text(tmp_path: Path) -> None:
    risk = _risk()
    risk["Activity"] = ["001", "002", "003"]
    risk.loc[0, "Portfolio"] = "001"
    risk.loc[0, "Underlying"] = "007"
    colossus = _colossus()
    colossus.loc[0, "Portfolio"] = "001"
    colossus.loc[0, "Underlying"] = "007"

    result = archive_official_snapshot(
        _snapshot(risk=risk),
        lambda _date: colossus,
        tmp_path,
    )
    loaded = load_risk_archive(tmp_path, result.market_date)

    assert loaded.risk.loc[0, "Portfolio"] == "001"
    assert loaded.risk.loc[0, "Underlying"] == "007"
    assert loaded.risk.loc[0, "Activity"] == "001"
    assert loaded.colossus.loc[0, "Portfolio"] == "001"
    assert loaded.colossus.loc[0, "Underlying"] == "007"


def test_completed_marker_must_retain_official_status_and_colossus_schema(
    tmp_path: Path,
) -> None:
    result = archive_official_snapshot(_snapshot(), lambda _date: _colossus(), tmp_path)
    marker = result.path / "_SUCCESS"
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    manifest["market_status"] = "Live"
    marker.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RiskArchiveValidationError, match="marker is invalid"):
        list_completed_market_dates(tmp_path)
    with pytest.raises(RiskArchiveValidationError, match="not OFFICIAL"):
        load_risk_archive(tmp_path, result.market_date)

    manifest["market_status"] = "OFFICIAL"
    manifest["colossus_columns"] = ["wrong"]
    marker.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RiskArchiveValidationError, match="marker is invalid"):
        list_completed_market_dates(tmp_path)
    with pytest.raises(RiskArchiveValidationError, match="Colossus archive columns"):
        load_risk_archive(tmp_path, result.market_date)


def test_projection_sums_predict_once_and_attaches_product_to_colossus() -> None:
    archive = RiskArchive(
        market_date="2026-08-14",
        path=Path("unused"),
        risk=_risk(),
        colossus=_colossus(),
    )

    history = project_archive_to_pl_history(archive)

    assert list(history.columns) == list(PL_HISTORY_COLUMNS)
    assert len(history) == 4
    ir = history.loc[
        history["Risk Type"].eq("IR") & history["Book"].eq("BOOK-A")
    ].set_index(HISTORY_TYPE)["PL"]
    assert ir.to_dict() == {COLOSSUS_TYPE: 24.0, PREDICT_TYPE: 25.0}
    fx = history.loc[history["Risk Type"].eq("FX") & history["Book"].eq("BOOK-B")]
    assert fx["Product"].unique().tolist() == ["Hedges"]


def test_projection_omits_incomplete_predict_group_without_zero_or_partial_sum() -> (
    None
):
    risk = _risk()
    risk.loc[1, "PL"] = pd.NA
    archive = RiskArchive("2026-08-14", Path("unused"), risk, _colossus())

    history = project_archive_to_pl_history(archive)

    ir_predict = history.loc[
        history["Risk Type"].eq("IR") & history[HISTORY_TYPE].eq(PREDICT_TYPE)
    ]
    assert ir_predict.empty
    assert history.loc[
        history["Risk Type"].eq("IR") & history[HISTORY_TYPE].eq(COLOSSUS_TYPE),
        "PL",
    ].tolist() == [24.0]


def test_projection_rejects_ambiguous_or_missing_product_authority() -> None:
    ambiguous = pd.concat(
        [
            _risk(),
            _risk().iloc[[0]].assign(Product="Hedges", PL=1.0),
        ],
        ignore_index=True,
    )
    with pytest.raises(RiskArchiveValidationError, match="exactly one Product"):
        project_archive_to_pl_history(
            RiskArchive("2026-08-14", Path("unused"), ambiguous, _colossus())
        )

    unknown = pd.concat(
        [
            _colossus(),
            pd.DataFrame(
                [["BOOK-Z", "GBP", "IR", "Delta", 1.0]],
                columns=list(COLOSSUS_COLUMNS),
            ),
        ],
        ignore_index=True,
    )
    with pytest.raises(RiskArchiveValidationError, match="BOOK-Z"):
        project_archive_to_pl_history(
            RiskArchive("2026-08-14", Path("unused"), _risk(), unknown)
        )


def test_all_completed_dates_project_to_one_canonical_history(tmp_path: Path) -> None:
    archive_official_snapshot(_snapshot(), lambda _date: _colossus(), tmp_path)
    archive_official_snapshot(
        _snapshot(market_date="2026-08-15", system_date="2026-08-15"),
        lambda _date: _colossus(),
        tmp_path,
    )

    history = load_shared_pl_history(tmp_path)

    assert list(history.columns) == list(PL_HISTORY_COLUMNS)
    assert history["Market Date"].drop_duplicates().tolist() == [
        "2026-08-14",
        "2026-08-15",
    ]
    assert len(history) == 8


def test_one_history_root_combines_legacy_demo_and_official_archive_dates(
    tmp_path: Path,
) -> None:
    legacy_leaf = tmp_path / "2026-08-13"
    legacy_leaf.mkdir(parents=True)
    legacy = pd.DataFrame(
        [["IR", "Delta", "EUR", "XVA", "BOOK-A", 7.0]],
        columns=list(HISTORY_FILE_COLUMNS),
    )
    legacy.to_csv(legacy_leaf / "histo.csv", index=False)
    legacy.assign(PL=8.0).to_csv(legacy_leaf / "predicted.csv", index=False)
    archive_official_snapshot(_snapshot(), lambda _date: _colossus(), tmp_path)

    history = load_shared_pl_history(tmp_path)

    assert list(history.columns) == list(PL_HISTORY_COLUMNS)
    assert history["Market Date"].drop_duplicates().tolist() == [
        "2026-08-13",
        "2026-08-14",
    ]
    assert history.loc[history["Market Date"].eq("2026-08-13"), "PL"].tolist() == [
        7.0,
        8.0,
    ]
    assert len(history.loc[history["Market Date"].eq("2026-08-14")]) == 4


def test_shared_history_rejects_the_retired_nested_year_layout(
    tmp_path: Path,
) -> None:
    nested_leaf = tmp_path / "2026" / "08-13"
    nested_leaf.mkdir(parents=True)

    with pytest.raises(RiskArchiveValidationError, match="YYYY-MM-DD leaves"):
        load_shared_pl_history(tmp_path)


def test_shared_history_catalog_detects_a_new_atomic_official_leaf(
    tmp_path: Path,
) -> None:
    legacy_leaf = tmp_path / "2026-08-13"
    legacy_leaf.mkdir(parents=True)
    legacy = pd.DataFrame(
        [["IR", "Delta", "EUR", "XVA", "BOOK-A", 7.0]],
        columns=list(HISTORY_FILE_COLUMNS),
    )
    legacy.to_csv(legacy_leaf / "histo.csv", index=False)
    legacy.to_csv(legacy_leaf / "predicted.csv", index=False)

    before = load_shared_pl_history(tmp_path)
    archive_official_snapshot(_snapshot(), lambda _date: _colossus(), tmp_path)
    after = load_shared_pl_history(tmp_path)

    assert before["Market Date"].drop_duplicates().tolist() == ["2026-08-13"]
    assert after["Market Date"].drop_duplicates().tolist() == [
        "2026-08-13",
        "2026-08-14",
    ]


def test_shared_history_hides_partial_official_leaf_without_success_marker(
    tmp_path: Path,
) -> None:
    partial = tmp_path / "2026-08-14"
    partial.mkdir(parents=True)
    _risk().to_csv(partial / "risk.csv", index=False)

    history = load_shared_pl_history(tmp_path)

    assert history.empty
    assert list(history.columns) == list(PL_HISTORY_COLUMNS)


def test_shared_history_rejects_one_date_mixing_legacy_and_official_files(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "2026-08-14"
    leaf.mkdir(parents=True)
    legacy = pd.DataFrame(
        [["IR", "Delta", "EUR", "XVA", "BOOK-A", 7.0]],
        columns=list(HISTORY_FILE_COLUMNS),
    )
    legacy.to_csv(leaf / "histo.csv", index=False)
    legacy.to_csv(leaf / "predicted.csv", index=False)
    _risk().to_csv(leaf / "risk.csv", index=False)

    with pytest.raises(RiskArchiveValidationError, match="mixes legacy and official"):
        load_shared_pl_history(tmp_path)


def test_shared_history_delegates_corrupt_completed_leaf_to_archive_validation(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "2026-08-14"
    leaf.mkdir(parents=True)
    (leaf / "_SUCCESS").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RiskArchiveValidationError, match="incomplete or invalid"):
        load_shared_pl_history(tmp_path)


def test_shared_history_ignores_pending_leaf_that_disappears_during_catalog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pending = tmp_path / ".2026-08-14.pending-race"
    pending.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def racing_iterdir(path: Path):
        entries = list(original_iterdir(path))
        if path == tmp_path and pending.exists():
            pending.rmdir()
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", racing_iterdir)

    history = load_shared_pl_history(tmp_path)

    assert history.empty


def test_scheduler_refuses_existing_legacy_date_before_loading_colossus(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "2026-08-14"
    leaf.mkdir(parents=True)
    legacy = pd.DataFrame(
        [["IR", "Delta", "EUR", "XVA", "BOOK-A", 7.0]],
        columns=list(HISTORY_FILE_COLUMNS),
    )
    legacy.to_csv(leaf / "histo.csv", index=False)
    legacy.to_csv(leaf / "predicted.csv", index=False)

    with pytest.raises(RiskArchiveValidationError, match="incomplete or invalid"):
        archive_official_snapshot(
            _snapshot(),
            lambda _date: (_ for _ in ()).throw(
                AssertionError("Colossus must not be called")
            ),
            tmp_path,
        )


def test_pl_history_projection_caches_each_immutable_leaf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_official_snapshot(_snapshot(), lambda _date: _colossus(), tmp_path)
    archive_module._project_completed_leaf_cached.cache_clear()
    original = archive_module._load_completed_leaf
    calls = 0

    def counted(path: Path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(archive_module, "_load_completed_leaf", counted)

    first = load_shared_pl_history(tmp_path)
    second = load_shared_pl_history(tmp_path)

    pd.testing.assert_frame_equal(first, second)
    assert calls == 1


def test_manager_and_scheduler_wrapper_force_one_coherent_refresh(
    tmp_path: Path,
) -> None:
    class Manager:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def refresh(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            return _snapshot()

    manager = Manager()
    direct = archive_from_manager(
        manager,
        lambda _date: _colossus(),
        tmp_path / "direct",
    )
    assert direct.status == "archived"
    assert manager.calls == [
        {
            "force_risk": True,
            "force_pl": True,
            "reason": "scheduled_official_archive",
        }
    ]

    scheduled_manager = Manager()
    result = run_scheduled_archive(
        environ={"PL_HISTORICAL_PATH": str(tmp_path / "scheduled")},
        manager_factory=lambda: scheduled_manager,
        colossus_loader=lambda _date: _colossus(),
    )
    assert result.status == "archived"
    assert result.path == (tmp_path / "scheduled" / "2026-08-14").resolve()
    assert resolve_archive_root({}) == DEFAULT_ARCHIVE_ROOT.resolve()
    assert DEFAULT_ARCHIVE_ROOT.parts[-2:] == ("data", "histo")
    assert (
        resolve_archive_root({"PL_HISTORICAL_PATH": "relative-history"}).name
        == "relative-history"
    )
