"""Preservation and isolation tests for recovered private connector source."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pandas as pd

from feeds import s01_sources as sources


PROJECT = Path(__file__).resolve().parents[1]
ARCHIVES = {
    PROJECT / "adapters" / "_disabled" / "s01_common.py.disabled": {
        "sha256": "458fc3964603842681ee6c470d83eefa4d6b462c5dae872e9d6c00f11fd6954e",
        "symbols": (
            "run_async",
            "exact_frame",
            "exact_status",
            "exact_underlying",
            "market_frame",
        ),
    },
    PROJECT / "adapters" / "_disabled" / "s02_ir.py.disabled": {
        "sha256": "07ee062e6ac5ac8aa50ac5592e4a619f5ef85c73f3356808f8d3e0958a320b0c",
        "symbols": (
            "build_ir_delta_adapter",
            "build_ir_deltavega_adapter",
            "build_ir_xccy_adapter",
            "build_ir_basis_adapter",
            "build_ir_inflation_adapter",
            "build_ir_inflationvega_adapter",
            "build_ir_bond_adapter",
        ),
    },
    PROJECT / "adapters" / "_disabled" / "s03_fx.py.disabled": {
        "sha256": "ade8a0be99e2291484bba33d3778dfd588f84cd778d3a76d750ed13295dc35bb",
        "symbols": (
            "build_fx_delta_adapter",
            "build_fx_gamma_adapter",
            "build_fx_vega_adapter",
        ),
    },
    PROJECT / "adapters" / "_disabled" / "s04_credit.py.disabled": {
        "sha256": "c8df28c483810e02ab32934a27377ed4a01e3b68908741ea4bea971192be5bcc",
        "symbols": ("build_credit_delta_adapter",),
    },
    PROJECT / "feeds" / "_disabled" / "s01_sources.py.disabled": {
        "sha256": "b4de2c3a2caf7b7f475fd942b7f92b642c1fab9c273fad99caa49d727325594f",
        "symbols": (
            "get_risk_checker",
            "get_portfolio_config",
            "get_product_connector_adapters",
            "build_production_refresh_manager",
        ),
    },
}


def _recover_original(archive: Path) -> str:
    text = archive.read_text(encoding="utf-8")
    assert text.endswith("\n")
    lines = text.splitlines()
    assert lines
    assert all(line == "#" or line.startswith("# ") for line in lines)
    return "\n".join("" if line == "#" else line[2:] for line in lines) + "\n"


def test_recovered_connector_bodies_are_exact_commented_archives() -> None:
    for archive, expected in ARCHIVES.items():
        assert archive.is_file()
        assert archive.name.endswith(".py.disabled")
        assert (
            importlib.util.spec_from_file_location("disabled_connector", archive)
            is None
        )

        recovered = _recover_original(archive)
        assert (
            hashlib.sha256(recovered.encode("utf-8")).hexdigest() == expected["sha256"]
        )
        for symbol in expected["symbols"]:
            assert f"def {symbol}" in recovered


def test_disabled_manifests_record_runtime_and_commodity_boundaries() -> None:
    adapter_manifest = PROJECT / "adapters" / "_disabled" / "MANIFEST.txt"
    feed_manifest = PROJECT / "feeds" / "_disabled" / "MANIFEST.txt"

    for manifest in (adapter_manifest, feed_manifest):
        lines = manifest.read_text(encoding="utf-8").splitlines()
        assert lines
        assert all(line.startswith("#") for line in lines)
        assert "fake CSV" in manifest.read_text(encoding="utf-8")

    adapter_notes = adapter_manifest.read_text(encoding="utf-8")
    assert "run_async is not active or imported" in adapter_notes
    assert "no private Commodity adapter body" in adapter_notes
    assert (PROJECT / "adapters" / "s03_commo.py").is_file()


def test_active_product_registration_still_reads_the_fake_csv_boundary() -> None:
    adapter = sources.get_product_connector_adapters()["ir/delta"]

    assert adapter.risk.__module__ == "feeds.s01_sources"
    assert adapter.market_open.__module__ == "feeds.s01_sources"
    assert adapter.market_status.__module__ == "feeds.s01_sources"

    risk = adapter.risk(pd.Timestamp("2026-08-15"))
    assert not risk.empty
    assert risk["Underlying"].str.contains("FAKE_REPLACE_ME", regex=False).all()
    assert risk["Portfolio"].str.contains("FAKE_REPLACE_ME", regex=False).all()
