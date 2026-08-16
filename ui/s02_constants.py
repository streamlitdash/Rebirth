"""Shared schema, hierarchy, and metric constants for the risk cube UI."""

from __future__ import annotations

from core.s01_schema import PORTFOLIO_COLUMN, PORTFOLIO_FIELDS, PortfolioField
from core.s09_cross_gamma import (
    CROSS_GAMMA_SOURCE_SPLIT,
    XGAMMA_RISK_GREEK,
    XGAMMA_SOURCE_RISK_GREEKS,
    XGAMMA_VEGA_RISK_GREEK,
)

PORTFOLIO_UI_FIELD = PortfolioField(
    PORTFOLIO_COLUMN,
    "portfolio",
    "Portfolio",
    roles=frozenset({"view_dimension", "filter_dimension"}),
    dashboard_section="position",
    filter_id="portfolio-filter",
)
VIEW_DIMENSION_FIELDS = (
    PORTFOLIO_UI_FIELD,
    *(field for field in PORTFOLIO_FIELDS if "view_dimension" in field.roles),
)
_FILTER_FIELD_BY_KEY = {
    field.key: field
    for field in (
        PORTFOLIO_UI_FIELD,
        *(field for field in PORTFOLIO_FIELDS if "filter_dimension" in field.roles),
    )
}
FILTER_DIMENSION_ORDER = (
    "activity",
    "signoffgroup",
    "portfolio",
    "category",
    "subcategory",
)
if set(_FILTER_FIELD_BY_KEY) != set(FILTER_DIMENSION_ORDER):
    raise RuntimeError("Filter dimension order must cover every governed filter field")
FILTER_DIMENSION_FIELDS = tuple(
    _FILTER_FIELD_BY_KEY[key] for key in FILTER_DIMENSION_ORDER
)
ROW_TOGGLE_OPEN_GLYPH = "−"
ROW_TOGGLE_CLOSED_GLYPH = "▸"
VIEW_DIMENSIONS = tuple(field.key for field in VIEW_DIMENSION_FIELDS)
FILTER_COLUMNS = [field.key for field in FILTER_DIMENSION_FIELDS]
DIMENSION_LABELS = {field.key: field.label for field in VIEW_DIMENSION_FIELDS}
DIMENSION_FILTER_IDS = {
    field.key: field.dash_filter_id for field in FILTER_DIMENSION_FIELDS
}
DEFAULT_VIEW_DIMENSION = next(
    field.key for field in VIEW_DIMENSION_FIELDS if "default_view" in field.roles
)

BASE_GROUPS = [
    "risk greek",
    "display bucket",
    "region",
    "group",
    "reported underlying",
    "underlying",
    "tenor swap",
    "tenor option",
    "split",
    DEFAULT_VIEW_DIMENSION,
]

# When promotion is disabled, these groups are used (display bucket is skipped)
BASE_GROUPS_NO_PROMOTION = [
    "risk greek",
    "region",
    "group",
    "reported underlying",
    "underlying",
    "tenor swap",
    "tenor option",
    "split",
    DEFAULT_VIEW_DIMENSION,
]


def get_active_groups(
    promotion_enabled: bool,
    region_enabled: bool = True,
    *,
    region_available: bool = True,
) -> list[str]:
    """Return the hierarchy groups based on promotion and region settings."""
    groups = BASE_GROUPS if promotion_enabled else BASE_GROUPS_NO_PROMOTION
    if not region_enabled or not region_available:
        groups = [g for g in groups if g != "region"]
    return groups


ALT_GROUPS = BASE_GROUPS[:-1]


def get_active_alt_groups(
    promotion_enabled: bool,
    region_enabled: bool = True,
    *,
    region_available: bool = True,
) -> list[str]:
    """Return ALT_GROUPS adjusted for promotion and region settings."""
    active = get_active_groups(
        promotion_enabled,
        region_enabled,
        region_available=region_available,
    )
    return active[:-1]


PRODUCT_LABELS = {"xva": "XVA", "hedges": "Hedges"}
PRODUCT_ORDER = {
    label: position for position, label in enumerate(PRODUCT_LABELS.values())
}
SPLIT_ORDER = ("Risk", "New Trades", "Gamma", "XGAMMA")

TOP_EXPOSURE_LABELS = ("Big Risk", "Big dRisk", "Big PL")
TOP_EXPOSURE_GROUPS = ["label", "risk type", "risk greek", "reported underlying"]
ROW_KEY_COLUMNS = ["label", "risk type", *ALT_GROUPS, *VIEW_DIMENSIONS]
METRIC_COLUMNS = ["risk", "drisk", "pl"]
UNDERLYING_SORT_METRICS = tuple(METRIC_COLUMNS)
DEFAULT_UNDERLYING_SORT_METRIC = "pl"
# Cross starts with exactly four metric columns. Each header can reveal its
# related detail columns without changing the row hierarchy.

GRID_METRIC_COLUMNS = ["risk", "drisk", "pl", "move"]
PLOT_METRICS = [
    "risk",
    "risk expo",
    "risk hedges",
    "drisk",
    "drisk expo",
    "drisk hedges",
    "pl",
    "pl expo",
    "pl hedges",
    "move",
    "open",
    "current",
]
DETAIL_MEASURES = ("risk", "drisk", "pl", "move")
DETAIL_COMPONENTS = {
    "risk": ("total", "expo", "hedges"),
    "drisk": ("total", "expo", "hedges"),
    "pl": ("total", "expo", "hedges"),
    "move": ("move", "open", "market_status"),
}
DETAIL_COMPONENT_LABELS = {
    "total": "Total",
    "expo": "XVA",
    "hedges": "Hedges",
    "move": "Move",
    "open": "Open",
    "market_status": "Market Status",
}


def compose_detail_metric(measure: str | None, component: str | None) -> str:
    """Translate the two visible detail pickers to one canonical data column."""
    selected_measure = measure if measure in DETAIL_MEASURES else "risk"
    allowed = DETAIL_COMPONENTS[selected_measure]
    default_component = "move" if selected_measure == "move" else "total"
    selected_component = component if component in allowed else default_component
    if selected_measure == "move":
        return (
            "current" if selected_component == "market_status" else selected_component
        )
    if selected_component == "total":
        return selected_measure
    return f"{selected_measure} {selected_component}"


def split_detail_metric(metric: str | None) -> tuple[str, str]:
    """Translate a clicked canonical metric into picker measure/component values."""
    selected = str(metric or "risk").strip().casefold()
    if selected in ("move", "open", "current"):
        component = "market_status" if selected == "current" else selected
        return "move", component
    for measure in ("risk", "drisk", "pl"):
        if selected == measure:
            return measure, "total"
        for component in ("expo", "hedges"):
            if selected == f"{measure} {component}":
                return measure, component
    return "risk", "total"


META_COLUMNS = ["promotion reason"]
PROMOTION_THRESHOLD_COLUMNS = (
    "risk threshold",
    "drisk threshold",
    "pl threshold",
)
REQUIRED_INPUT_COLUMNS = [
    "risk type",
    *BASE_GROUPS,
    "product",
    "risk",
    "drisk",
    "pl",
    "open",
    "current",
    *PROMOTION_THRESHOLD_COLUMNS,
]
BREAKDOWN_DEFAULTS = {
    "risk expo": "risk",
    "risk hedges": None,
    "drisk expo": "drisk",
    "drisk hedges": None,
    "pl expo": "pl",
    "pl hedges": None,
}
NUMERIC_COLUMNS = [
    "risk",
    "drisk",
    "pl",
    "open",
    "current",
    "move",
    "promotion score",
    *PROMOTION_THRESHOLD_COLUMNS,
    *BREAKDOWN_DEFAULTS,
]
METRIC_BREAKDOWNS = {
    "risk": ["risk expo", "risk hedges"],
    "drisk": ["drisk expo", "drisk hedges"],
    "pl": ["pl expo", "pl hedges"],
    "move": ["open", "current"],
}

EXPANDABLE_METRICS = tuple(GRID_METRIC_COLUMNS)
CREDIT_MEASURES = ("SP01", "PSP01", "PM01", "PM01P", "Theta", "JTD")
CREDIT_MEASURE_KEYS = {
    "SP01": "sp01",
    "PSP01": "psp01",
    "PM01": "pm01",
    "PM01P": "pm01p",
    "Theta": "theta",
    "JTD": "jtd",
}

RISK_TYPE_ORDER = {"Credit": 0, "IR": 1, "FX": 2, "Commo": 3, "Cash Flow": 4}
IR_GREEK_FAMILIES = {
    "delta": ("Delta", "Inflation", "Gamma", "Bond", XGAMMA_RISK_GREEK),
    "basis": ("XCCY", "Basis"),
    "vega": (
        "DeltaVega",
        "InflationVega",
        "XCCYVega",
        XGAMMA_VEGA_RISK_GREEK,
    ),
}
IR_GREEK_FAMILY_LABELS = {
    "delta": "Delta",
    "basis": "Basis",
    "vega": "Vega",
}
if tuple(IR_GREEK_FAMILIES) != tuple(IR_GREEK_FAMILY_LABELS):
    raise RuntimeError("Every IR Greek family must have one visible tab label")


__all__ = [
    "ALT_GROUPS",
    "BASE_GROUPS",
    "BREAKDOWN_DEFAULTS",
    "CROSS_GAMMA_SOURCE_SPLIT",
    "CREDIT_MEASURES",
    "CREDIT_MEASURE_KEYS",
    "DEFAULT_UNDERLYING_SORT_METRIC",
    "DEFAULT_VIEW_DIMENSION",
    "DETAIL_COMPONENTS",
    "DETAIL_COMPONENT_LABELS",
    "DETAIL_MEASURES",
    "DIMENSION_FILTER_IDS",
    "DIMENSION_LABELS",
    "EXPANDABLE_METRICS",
    "FILTER_DIMENSION_FIELDS",
    "FILTER_COLUMNS",
    "FILTER_DIMENSION_ORDER",
    "GRID_METRIC_COLUMNS",
    "IR_GREEK_FAMILY_LABELS",
    "IR_GREEK_FAMILIES",
    "META_COLUMNS",
    "METRIC_BREAKDOWNS",
    "METRIC_COLUMNS",
    "NUMERIC_COLUMNS",
    "PLOT_METRICS",
    "PORTFOLIO_UI_FIELD",
    "PRODUCT_LABELS",
    "PRODUCT_ORDER",
    "PROMOTION_THRESHOLD_COLUMNS",
    "REQUIRED_INPUT_COLUMNS",
    "RISK_TYPE_ORDER",
    "ROW_TOGGLE_CLOSED_GLYPH",
    "ROW_TOGGLE_OPEN_GLYPH",
    "ROW_KEY_COLUMNS",
    "SPLIT_ORDER",
    "TOP_EXPOSURE_GROUPS",
    "TOP_EXPOSURE_LABELS",
    "UNDERLYING_SORT_METRICS",
    "VIEW_DIMENSIONS",
    "VIEW_DIMENSION_FIELDS",
    "XGAMMA_RISK_GREEK",
    "XGAMMA_SOURCE_RISK_GREEKS",
    "XGAMMA_VEGA_RISK_GREEK",
    "BASE_GROUPS_NO_PROMOTION",
    "get_active_groups",
    "get_active_alt_groups",
    "compose_detail_metric",
    "split_detail_metric",
]
