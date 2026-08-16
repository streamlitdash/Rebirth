"""Inline preservation and isolation tests for recovered connector source."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from feeds import s01_sources as sources


PROJECT = Path(__file__).resolve().parents[1]

INLINE_ADAPTERS = {
    PROJECT / "adapters" / "s01_common.py": {
        "start": "=== REAL CONNECTOR IMPLEMENTATION (COMMENTED OUT)",
        "end": "=== ACTIVE FIXTURE/CSV COMPATIBILITY HELPERS",
        "symbols": ("run_async", "max_workers=1", "asyncio.new_event_loop"),
    },
    PROJECT / "adapters" / "s02_ir.py": {
        "start": "=== REAL IR CONNECTORS (COMMENTED OUT)",
        "end": "=== ACTIVE VALIDATED CONTRACT (CSV RUNTIME IS SELECTED IN FEEDS)",
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
    PROJECT / "adapters" / "s03_fx.py": {
        "start": "=== REAL FX CONNECTORS (COMMENTED OUT)",
        "end": "=== ACTIVE VALIDATED CONTRACT (CSV RUNTIME IS SELECTED IN FEEDS)",
        "symbols": (
            "build_fx_delta_adapter",
            "build_fx_gamma_adapter",
            "build_fx_vega_adapter",
        ),
    },
    PROJECT / "adapters" / "s04_credit.py": {
        "start": "=== REAL CREDIT CONNECTOR (COMMENTED OUT)",
        "end": "=== ACTIVE VALIDATED CONTRACT (CSV RUNTIME IS SELECTED IN FEEDS)",
        "symbols": ("build_credit_delta_adapter",),
    },
}


def _comment_only_region(text: str, start: str, end: str) -> str:
    marker_offset = text.index(start)
    start_offset = text.rfind("\n", 0, marker_offset) + 1
    end_marker = text.index(end, marker_offset)
    end_offset = text.find("\n", end_marker)
    if end_offset == -1:
        end_offset = len(text)
    region = text[start_offset:end_offset]
    executable = [
        line
        for line in region.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert executable == []
    return region


def test_recovered_adapter_bodies_are_inline_and_comment_only() -> None:
    for path, expected in INLINE_ADAPTERS.items():
        text = path.read_text(encoding="utf-8")
        assert text.count("\nfrom __future__ import annotations\n") == 1
        assert text.index("from __future__ import annotations") < text.index(
            expected["start"]
        )
        region = _comment_only_region(text, expected["start"], expected["end"])
        assert "SWITCH TO REAL" in region
        assert "Leave the recovered ``from __future__ import annotations``" in region
        assert "# from __future__ import annotations" in region
        for symbol in expected["symbols"]:
            assert symbol in region

    assert not any((PROJECT / "adapters" / "_disabled").glob("**/*"))


def test_recovered_adapter_body_can_be_uncommented_without_future_import_error() -> (
    None
):
    for path, expected in INLINE_ADAPTERS.items():
        text = path.read_text(encoding="utf-8")
        region_start = text.index(expected["start"])
        recovered_future = text.index(
            "# from __future__ import annotations",
            region_start,
        )
        body_start = text.index("\n", recovered_future) + 1
        active_marker = text.index(expected["end"], body_start)
        body_end = text.rfind("\n", 0, active_marker) + 1
        body = text[body_start:body_end]
        uncommented = "\n".join(
            "" if line == "#" else line[2:] if line.startswith("# ") else line
            for line in body.splitlines()
        )
        switched = text[:body_start] + uncommented + "\n" + text[body_end:]

        compile(switched, str(path), "exec")


def test_recovered_feed_blocks_are_adjacent_comment_only_switches() -> None:
    text = (PROJECT / "feeds" / "s01_sources.py").read_text(encoding="utf-8")
    regions = (
        ("=== REAL PRODUCT IMPORTS", "=== END REAL PRODUCT IMPORTS"),
        ("=== REAL RISK CHECKER", "=== END REAL RISK CHECKER"),
        ("=== REAL PORTFOLIO MAPPING", "=== END REAL PORTFOLIO MAPPING"),
        ("=== REAL PRODUCT REGISTRATION", "=== END REAL PRODUCT REGISTRATION"),
    )
    for start, end in regions:
        region = _comment_only_region(text, start, end)
        assert "SWITCH" in region or start == "=== REAL PRODUCT IMPORTS"

    for recovered_text in (
        'mrx.MRXView(r"mrx/static/age.tsv")',
        'cm.get("XVA.IM Optin.PnL.Ann.Ptf List")',
        "colossus_connection.raw_request(",
        'adapters["credit/delta"] = build_credit_delta_adapter()',
        'adapters["credit/vega"] = build_credit_vega_adapter()  # unavailable',
        'adapters["fx/gamma"] = build_fx_gamma_adapter()',
        'adapters["ir/delta"], adapters["ir/gamma"]',
        'adapters["ir/xccyvega"] = build_ir_xccyvega_adapter()  # unavailable',
        'submit_endpoint = "/api/svc/predict/submitPredictByPortfolio"',
        "No private Commodity body was recovered",
    ):
        assert recovered_text in text

    assert not any((PROJECT / "feeds" / "_disabled").glob("**/*"))


def test_each_feed_switch_is_immediately_followed_by_its_active_fallback() -> None:
    text = (PROJECT / "feeds" / "s01_sources.py").read_text(encoding="utf-8")
    risk_end = text.index("=== END REAL RISK CHECKER")
    portfolio_end = text.index("=== END REAL PORTFOLIO MAPPING")
    assert text.index("=== ACTIVE CSV FALLBACK", risk_end) > risk_end
    assert text.index("=== ACTIVE CSV FALLBACK", portfolio_end) > portfolio_end
    registration_end = text.index("=== END REAL PRODUCT REGISTRATION")
    active_return = text.index("return _get_csv_product_connector_adapters()")
    assert active_return > registration_end


def test_active_product_registration_still_reads_the_fake_csv_boundary() -> None:
    adapter = sources.get_product_connector_adapters()["ir/delta"]

    assert adapter.risk.__module__ == "feeds.s01_sources"
    assert adapter.market_open.__module__ == "feeds.s01_sources"
    assert adapter.market_status.__module__ == "feeds.s01_sources"

    risk = adapter.risk(pd.Timestamp("2026-08-14"))
    assert not risk.empty
    assert risk["Underlying"].str.contains("FAKE_REPLACE_ME", regex=False).all()
    assert risk["Portfolio"].str.contains("FAKE_REPLACE_ME", regex=False).all()
