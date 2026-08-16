"""Cross-surface visual contracts for expandable hierarchy controls."""

from __future__ import annotations

from pathlib import Path

from ui.s02_constants import ROW_TOGGLE_CLOSED_GLYPH, ROW_TOGGLE_OPEN_GLYPH


_ROOT = Path(__file__).resolve().parents[1]


def test_python_table_toggles_share_one_authoritative_glyph_contract() -> None:
    assert ROW_TOGGLE_OPEN_GLYPH == "−"
    assert ROW_TOGGLE_CLOSED_GLYPH == "▸"

    for relative_path in (
        Path("ui/s04_components.py"),
        Path("ui/s10_stock.py"),
        Path("ui/s12_plhistory.py"),
    ):
        source = (_ROOT / relative_path).read_text(encoding="utf-8")
        assert "ROW_TOGGLE_OPEN_GLYPH" in source
        assert "ROW_TOGGLE_CLOSED_GLYPH" in source
        assert '"−"' not in source
        assert '"▸"' not in source


def _css_rule(source: str, selector: str) -> str:
    start = source.index(selector)
    opening = source.index("{", start)
    closing = source.index("}", opening)
    return source[opening + 1 : closing]


def test_row_toggles_share_fixed_glyph_geometry() -> None:
    source = (_ROOT / "assets" / "s01_style.css").read_text(encoding="utf-8")
    rule = _css_rule(
        source,
        ".row-toggle,\n.aggregate-row-toggle,\n.quick-search-hierarchy-toggle",
    )

    for declaration in (
        "width: 20px",
        "height: 20px",
        "margin: 0 6px 0 0",
        "font-family: inherit",
        "font-size: 14px",
        "font-weight: 900",
        "line-height: 1",
    ):
        assert declaration in rule

    spacer = _css_rule(source, ".quick-search-hierarchy-toggle-spacer")
    assert "width: 20px" in spacer
    assert "height: 20px" in spacer
    assert "margin: 0 6px 0 0" in spacer
    assert ".stock-hierarchy-level-label" not in source


def test_quick_risk_client_sync_preserves_canonical_disclosure_state() -> None:
    source = (_ROOT / "assets" / "s02_app.js").read_text(encoding="utf-8")
    start = source.index("const syncQuickSearchHierarchy")
    end = source.index("const toggleQuickSearchHierarchy", start)
    sync = source[start:end]

    assert 'toggle.textContent = expanded ? "\\u2212" : "\\u25b8";' in sync
    assert 'row.setAttribute("aria-expanded", String(expanded));' in sync
    assert 'toggle.setAttribute("aria-expanded", String(expanded));' in sync
    assert "\\u25be" not in sync
    assert "\\u203a" not in sync
