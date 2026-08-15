"""Preservation and isolation tests for the recovered pipeline fragment."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import s03_publish as publishing


PROJECT = Path(__file__).resolve().parents[1]
ARCHIVE = PROJECT / "core" / "_disabled" / "s02_pipeline_part_1.py.disabled"
EXPECTED_SHA256 = "0aeb6be35c87496b3c7e76b943efca6f980cafe2a3b552cee4a5f6959972124a"


def _recover_original() -> str:
    text = ARCHIVE.read_text(encoding="utf-8")
    assert text.endswith("\n")
    lines = text.splitlines()
    assert lines
    assert all(line == "#" or line.startswith("# ") for line in lines)
    return "\n".join("" if line == "#" else line[2:] for line in lines) + "\n"


def test_recovered_pipeline_fragment_is_exact_and_non_importable() -> None:
    assert ARCHIVE.is_file()
    assert ARCHIVE.name.endswith(".py.disabled")
    assert importlib.util.spec_from_file_location("disabled_pipeline", ARCHIVE) is None

    recovered = _recover_original()
    assert hashlib.sha256(recovered.encode("utf-8")).hexdigest() == EXPECTED_SHA256
    for contract in (
        "class ProductSpec:",
        "PRODUCT_SPECS: dict[str, ProductSpec]",
        '"irdelta", "ir/delta", "IR", "Delta", (SWAP_AXIS,), "bp", "minusabsolute"',
        "def risk_date_for(",
        "if selected_age > 0:",
        "selected_age -= 1",
        'MRX_FILE = "MRX File"',
    ):
        assert contract in recovered


def test_disabled_pipeline_manifest_records_the_runtime_boundary() -> None:
    manifest = PROJECT / "core" / "_disabled" / "MANIFEST.txt"
    text = manifest.read_text(encoding="utf-8")

    assert all(line.startswith("#") for line in text.splitlines())
    assert "ProductSpec P&L-formula metadata" in text
    assert "RiskChecker Age business-day arithmetic" in text
    assert "MRX File naming contract" in text
    assert "not active, imported" in text


def test_disabled_pipeline_fragment_is_excluded_from_plotly_bundle(
    tmp_path: Path,
) -> None:
    staged = publishing.stage_bundle(tmp_path / "runtime")

    assert "_disabled" in publishing.IGNORED_NAMES
    assert not (staged / "core" / "_disabled").exists()
    assert not any(staged.rglob("*.disabled"))
