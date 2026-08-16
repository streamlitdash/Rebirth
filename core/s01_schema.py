"""Authoritative portfolio-configuration and reporting-field schema.

The risk pipeline, connector boundary, exports, and demo-data generator all
derive their portfolio metadata contract from this registry.  Product and
SignoffGroup retain their domain-specific roles; ordinary reporting fields can
be added here without duplicating column lists throughout those layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PORTFOLIO_COLUMN = "Portfolio"
PORTFOLIO_MAPPED_COLUMN = "Portfolio Mapped"
UNMAPPED_VALUE = "Unmapped"
UNSPECIFIED_VALUE = "Unspecified"
TENOR_SWAP = "Tenor Swap"
TENOR_OPTION = "Tenor Option"
TENOR_SWAP_ORDER = "Tenor Swap Order"
TENOR_OPTION_ORDER = "Tenor Option Order"
TENOR_COLUMNS = (TENOR_SWAP, TENOR_OPTION)
TENOR_ORDER_COLUMNS = (TENOR_SWAP_ORDER, TENOR_OPTION_ORDER)
TENOR_ORDER_BY_COLUMN = {
    TENOR_SWAP: TENOR_SWAP_ORDER,
    TENOR_OPTION: TENOR_OPTION_ORDER,
}

DashboardSection = Literal["position", "portfolio"]


@dataclass(frozen=True)
class PortfolioField:
    """Describe one governed portfolio-config field and its reporting role."""

    external_name: str
    key: str
    label: str
    required: bool = True
    default_value: str = UNSPECIFIED_VALUE
    allowed_values: tuple[str, ...] = ()
    roles: frozenset[str] = frozenset()
    dashboard_section: DashboardSection = "portfolio"
    filter_id: str | None = None

    @property
    def dash_filter_id(self) -> str:
        """Return a stable Dash id while giving new fields a safe default."""
        return self.filter_id or f"{self.key}-filter"


PORTFOLIO_FIELDS = (
    PortfolioField(
        "Product",
        "product",
        "Product",
        allowed_values=("XVA", "Hedges"),
        roles=frozenset({"expo_hedges_partition", "view_dimension"}),
        dashboard_section="position",
    ),
    PortfolioField(
        "Activity",
        "activity",
        "Activity",
        roles=frozenset({"view_dimension", "filter_dimension", "default_view"}),
        dashboard_section="position",
        filter_id="activity-filter",
    ),
    PortfolioField(
        "SignoffGroup",
        "signoffgroup",
        "Signoff Group",
        roles=frozenset({"view_dimension", "filter_dimension", "pl_signoff"}),
        filter_id="signoff-filter",
    ),
    PortfolioField(
        "Category",
        "category",
        "Category",
        roles=frozenset({"view_dimension", "filter_dimension"}),
        filter_id="category-filter",
    ),
    PortfolioField(
        "Sub Category",
        "subcategory",
        "Sub Category",
        required=False,
        roles=frozenset({"view_dimension", "filter_dimension"}),
    ),
)


def _validate_registry() -> None:
    external_names = [field.external_name for field in PORTFOLIO_FIELDS]
    keys = [field.key for field in PORTFOLIO_FIELDS]
    if len(external_names) != len(set(external_names)):
        raise RuntimeError("Portfolio field external names must be unique")
    if len(keys) != len(set(keys)):
        raise RuntimeError("Portfolio field keys must be unique")
    defaults = [field for field in PORTFOLIO_FIELDS if "default_view" in field.roles]
    if len(defaults) != 1:
        raise RuntimeError("Exactly one portfolio field must define default_view")
    signoff = [field for field in PORTFOLIO_FIELDS if "pl_signoff" in field.roles]
    if len(signoff) != 1:
        raise RuntimeError("Exactly one portfolio field must define pl_signoff")


_validate_registry()

PORTFOLIO_FIELD_BY_EXTERNAL = {field.external_name: field for field in PORTFOLIO_FIELDS}
PORTFOLIO_FIELD_BY_KEY = {field.key: field for field in PORTFOLIO_FIELDS}
PORTFOLIO_METADATA_COLUMNS = tuple(field.external_name for field in PORTFOLIO_FIELDS)
PORTFOLIO_REQUIRED_METADATA_COLUMNS = tuple(
    field.external_name for field in PORTFOLIO_FIELDS if field.required
)
PORTFOLIO_OPTIONAL_METADATA_COLUMNS = tuple(
    field.external_name for field in PORTFOLIO_FIELDS if not field.required
)
PORTFOLIO_CONFIG_REQUIRED_COLUMNS = (
    PORTFOLIO_COLUMN,
    *PORTFOLIO_REQUIRED_METADATA_COLUMNS,
)
PORTFOLIO_CONFIG_COLUMNS = (PORTFOLIO_COLUMN, *PORTFOLIO_METADATA_COLUMNS)
PORTFOLIO_POSITION_COLUMNS = tuple(
    field.external_name
    for field in PORTFOLIO_FIELDS
    if field.dashboard_section == "position"
)
PORTFOLIO_REPORTING_COLUMNS = tuple(
    field.external_name
    for field in PORTFOLIO_FIELDS
    if field.dashboard_section == "portfolio"
)
PL_SIGNOFF_COLUMN = next(
    field.external_name for field in PORTFOLIO_FIELDS if "pl_signoff" in field.roles
)
__all__ = [
    "PL_SIGNOFF_COLUMN",
    "PORTFOLIO_COLUMN",
    "PORTFOLIO_CONFIG_COLUMNS",
    "PORTFOLIO_CONFIG_REQUIRED_COLUMNS",
    "PORTFOLIO_FIELDS",
    "PORTFOLIO_FIELD_BY_EXTERNAL",
    "PORTFOLIO_FIELD_BY_KEY",
    "PORTFOLIO_MAPPED_COLUMN",
    "PORTFOLIO_METADATA_COLUMNS",
    "PORTFOLIO_OPTIONAL_METADATA_COLUMNS",
    "PORTFOLIO_POSITION_COLUMNS",
    "PORTFOLIO_REPORTING_COLUMNS",
    "PORTFOLIO_REQUIRED_METADATA_COLUMNS",
    "PortfolioField",
    "TENOR_COLUMNS",
    "TENOR_OPTION",
    "TENOR_OPTION_ORDER",
    "TENOR_ORDER_BY_COLUMN",
    "TENOR_ORDER_COLUMNS",
    "TENOR_SWAP",
    "TENOR_SWAP_ORDER",
    "UNMAPPED_VALUE",
    "UNSPECIFIED_VALUE",
]
