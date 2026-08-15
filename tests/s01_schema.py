"""High-signal tests for the governed schema and product catalogue."""

from __future__ import annotations

from core.s01_schema import (
    PL_SIGNOFF_COLUMN,
    PORTFOLIO_CONFIG_COLUMNS,
    PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
    PORTFOLIO_FIELDS,
    PORTFOLIO_OPTIONAL_METADATA_COLUMNS,
    TENOR_OPTION,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
)
from core.s02_pipeline import (
    CREDIT_MEASURE_COLUMNS,
    CREDIT_MEASURES,
    PRODUCT_SPECS,
    PRODUCT_SPECS_BY_SOURCE_TYPE,
)


def test_portfolio_registry_has_one_unambiguous_meaning_per_column() -> None:
    external = [field.external_name for field in PORTFOLIO_FIELDS]
    keys = [field.key for field in PORTFOLIO_FIELDS]

    assert len(external) == len(set(external))
    assert len(keys) == len(set(keys))
    assert PORTFOLIO_CONFIG_COLUMNS == ("Portfolio", *external)
    assert PORTFOLIO_CONFIG_REQUIRED_COLUMNS == (
        "Portfolio",
        "Product",
        "Activity",
        "SignoffGroup",
        "Category",
    )
    assert PORTFOLIO_OPTIONAL_METADATA_COLUMNS == ("Sub Category",)
    assert PL_SIGNOFF_COLUMN == "SignoffGroup"
    product_field = next(
        field for field in PORTFOLIO_FIELDS if field.external_name == "Product"
    )
    assert product_field.allowed_values == ("XVA", "Hedges")


def test_product_catalogue_has_unique_source_and_business_identities() -> None:
    sources = [spec.source_type for spec in PRODUCT_SPECS.values()]
    pairs = [(spec.risk_type, spec.risk_greek) for spec in PRODUCT_SPECS.values()]

    assert len(sources) == len(set(sources))
    assert len(pairs) == len(set(pairs))
    assert set(PRODUCT_SPECS_BY_SOURCE_TYPE) == set(sources)


def test_one_axis_products_use_the_canonical_swap_axis() -> None:
    for key in ("irdelta", "irgamma", "creditdelta", "creditvega", "commodelta"):
        spec = PRODUCT_SPECS[key]
        assert spec.tenor_columns == [TENOR_SWAP]
        assert spec.tenor_order_columns == [TENOR_SWAP_ORDER]


def test_ir_gamma_and_credit_vega_are_not_modelled_as_surfaces() -> None:
    assert PRODUCT_SPECS["irgamma"].tenor_columns == [TENOR_SWAP]
    assert PRODUCT_SPECS["creditvega"].tenor_columns == [TENOR_SWAP]
    assert PRODUCT_SPECS["irgamma"].pl_formula == "taylor_gamma"


def test_true_vega_surfaces_declare_swap_and_option_axes_without_grid_size() -> None:
    for key in ("irdeltavega", "xccyvega", "inflationvega"):
        spec = PRODUCT_SPECS[key]
        assert spec.tenor_columns == [TENOR_SWAP, TENOR_OPTION]
        assert len(spec.axes) == 2
        assert not hasattr(spec, "surface_rows")
        assert not hasattr(spec, "surface_columns")


def test_credit_measure_contract_contains_every_settled_risk_drisk_pair() -> None:
    assert CREDIT_MEASURES == ("SP01", "PSP01", "PM01", "PM01P", "Theta", "JTD")
    assert CREDIT_MEASURE_COLUMNS == tuple(
        f"{metric} {measure}"
        for measure in CREDIT_MEASURES
        for metric in ("Risk", "dRisk")
    )
