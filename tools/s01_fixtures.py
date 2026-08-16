"""Generate the canonical deterministic connector fixtures for Cube.

The seven generated CSVs are deliberately visible fake data, never fallback
production data.  Every reporting key carries ``FAKE_REPLACE_ME`` so an
unfinished integration is obvious.  Product axes come from the live
``ProductSpec`` registry: one-dimensional products use ``Tenor Swap`` and only
true surfaces add ``Tenor Option``.

Run from any directory::

    python tools/s01_fixtures.py
    python tools/s01_fixtures.py --check
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


FAKE_NOTICE = "FAKE_REPLACE_ME"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIRECTORY = PROJECT_ROOT / "data"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.s01_schema import (  # noqa: E402 - support execution from any directory
    PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
    PORTFOLIO_FIELD_BY_KEY,
    TENOR_COLUMNS,
    TENOR_OPTION,
    TENOR_ORDER_COLUMNS,
    TENOR_SWAP,
)
from core.s02_pipeline import (  # noqa: E402 - support execution from any directory
    CREDIT_MEASURE_COLUMNS,
    CROSS_GAMMA_INPUT_RISK_PAIRS,
    CURRENT,
    DIRECT_PL_CLASSIFICATIONS,
    PRODUCT_SPECS_BY_SOURCE_TYPE,
)


@dataclass(frozen=True)
class FixtureSource:
    """Demo-only identity and quote scale for one canonical Source Type."""

    source_type: str
    underlyings: tuple[str, str, str]
    groups: tuple[str, str, str]
    market_kind: str


SOURCE_FIXTURES = (
    FixtureSource(
        "fx/delta",
        ("EUR/USD", "USD/JPY", "GBP/USD"),
        ("G10", "G10", "G10"),
        "fx",
    ),
    FixtureSource(
        "fx/gamma",
        ("EUR/USD", "USD/JPY", "GBP/USD"),
        ("G10", "G10", "G10"),
        "fx",
    ),
    FixtureSource(
        "fx/vega",
        ("EUR/USD Vol", "USD/JPY Vol", "GBP/USD Vol"),
        ("G10", "G10", "G10"),
        "vol",
    ),
    FixtureSource(
        "ir/delta",
        ("USD SOFR", "EUR ESTR", "GBP SONIA"),
        ("G10", "G10", "G10"),
        "rate",
    ),
    FixtureSource(
        "ir/gamma",
        ("USD SOFR Gamma", "EUR ESTR Gamma", "GBP SONIA Gamma"),
        ("G10", "G10", "G10"),
        "rate",
    ),
    FixtureSource(
        "ir/deltavega",
        ("USD SOFR Vol", "EUR ESTR Vol", "GBP SONIA Vol"),
        ("G10", "G10", "G10"),
        "vol",
    ),
    FixtureSource(
        "ir/xccy",
        ("EUR/USD XCCY", "GBP/USD XCCY", "USD/JPY XCCY"),
        ("G10", "G10", "G10"),
        "rate",
    ),
    FixtureSource(
        "ir/xccyvega",
        ("EUR/USD XCCY Vol", "GBP/USD XCCY Vol", "USD/JPY XCCY Vol"),
        ("G10", "G10", "G10"),
        "vol",
    ),
    FixtureSource(
        "ir/inflation",
        ("US CPI", "EU HICP", "UK RPI"),
        ("G10", "G10", "G10"),
        "rate",
    ),
    FixtureSource(
        "ir/inflationvega",
        ("US CPI Vol", "EU HICP Vol", "UK RPI Vol"),
        ("G10", "G10", "G10"),
        "vol",
    ),
    FixtureSource(
        "ir/basis",
        ("USD 3M/6M", "EUR 3M/6M", "GBP 3M/6M"),
        ("G10", "G10", "G10"),
        "rate",
    ),
    FixtureSource(
        "ir/bond",
        ("UST", "Bund", "Gilt"),
        ("G10", "G10", "G10"),
        "rate",
    ),
    FixtureSource(
        "credit/delta",
        ("CDX IG", "iTraxx Main", "Ford CDS"),
        ("Index", "Index", "Single Name"),
        "credit",
    ),
    FixtureSource(
        "credit/vega",
        ("CDX IG Vol", "iTraxx Main Vol", "Ford CDS Vol"),
        ("Index", "Index", "Single Name"),
        "credit",
    ),
    FixtureSource(
        "commo/delta",
        ("Brent", "Gold", "TTF Gas"),
        ("Oil", "Precious", "Gas"),
        "commodity",
    ),
    FixtureSource(
        "commo/vega",
        ("Brent Vol", "Gold Vol", "TTF Gas Vol"),
        ("Oil", "Precious", "Gas"),
        "vol",
    ),
)
SOURCE_BY_TYPE = {fixture.source_type: fixture for fixture in SOURCE_FIXTURES}
EXPECTED_SOURCE_TYPES = tuple(fixture.source_type for fixture in SOURCE_FIXTURES)

# This is the complete market-owned, ordered tenor structure for each product.
# Risk deliberately uses a proper subset, defined by ``_risk_axis_values``, so
# the fixtures exercise both the full MarketBook and the risk-only joined view.
FULL_AXIS_VALUES: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "fx/vega": {TENOR_SWAP: ("1M", "3M", "6M", "1Y")},
    "ir/delta": {TENOR_SWAP: ("6M", "1Y", "2Y", "5Y", "10Y", "30Y")},
    "ir/gamma": {TENOR_SWAP: ("1Y", "2Y", "5Y", "10Y")},
    "ir/deltavega": {
        TENOR_SWAP: ("6M", "1Y", "2Y", "5Y", "10Y"),
        TENOR_OPTION: ("1M", "3M", "6M", "1Y"),
    },
    "ir/xccy": {TENOR_SWAP: ("1Y", "2Y", "5Y", "10Y")},
    "ir/xccyvega": {
        TENOR_SWAP: ("1Y", "3Y", "5Y", "10Y", "30Y"),
        TENOR_OPTION: ("3M", "6M", "1Y", "2Y"),
    },
    "ir/inflation": {TENOR_SWAP: ("2Y", "5Y", "10Y", "20Y", "30Y")},
    "ir/inflationvega": {
        TENOR_SWAP: ("2Y", "5Y", "10Y", "20Y", "30Y"),
        TENOR_OPTION: ("6M", "1Y", "2Y"),
    },
    "ir/basis": {TENOR_SWAP: ("3M", "6M", "1Y", "2Y", "5Y")},
    "ir/bond": {TENOR_SWAP: ("2Y", "5Y", "10Y", "30Y")},
    "credit/delta": {TENOR_SWAP: ("1Y", "3Y", "5Y", "7Y", "10Y")},
    "credit/vega": {TENOR_SWAP: ("1M", "3M", "6M", "1Y")},
    "commo/delta": {TENOR_SWAP: ("1M", "3M", "6M", "1Y")},
    "commo/vega": {TENOR_SWAP: ("1M", "3M", "6M", "1Y")},
}

MAPPED_PORTFOLIOS = (
    ("BOOK_A", "XVA", "Macro", "SOG_ALPHA", "Core"),
    ("BOOK_B", "XVA", "Macro", "SOG_ALPHA", "Core"),
    ("BOOK_C", "XVA", "Credit", "SOG_BETA", "Flow"),
    ("BOOK_D", "Hedges", "Hedge", "SOG_BETA", "Hedge"),
    ("BOOK_E", "Hedges", "Hedge", "SOG_GAMMA", "Hedge"),
)
UNMAPPED_PORTFOLIO = "BOOK_UNMAPPED"
RISK_PORTFOLIOS = tuple(row[0] for row in MAPPED_PORTFOLIOS) + (UNMAPPED_PORTFOLIO,)

AGE_ONE_SOURCE_TYPES = frozenset({"ir/inflationvega", "credit/vega", "commo/vega"})
CREDIT_FACTORS = {
    "SP01": 1.0,
    "PSP01": 0.82,
    "PM01": 1.18,
    "PM01P": 0.011,
    "Theta": -0.08,
    "JTD": 0.35,
}

SCHEMAS = {
    "s01_readiness.csv": ("Risk Type", "Risk Greek", "Age"),
    "s02_checker.csv": ("Risk Type", "Risk Greek", "MMMFile", "Product"),
    "s03_risk.csv": (
        "Source Type",
        "Underlying",
        *TENOR_COLUMNS,
        "Portfolio",
        "Group",
        "Risk",
        "dRisk",
        *CREDIT_MEASURE_COLUMNS,
    ),
    "s04_open.csv": (
        "Source Type",
        "Underlying",
        *TENOR_COLUMNS,
        *TENOR_ORDER_COLUMNS,
        "Open",
    ),
    "s05_current.csv": (
        "Source Type",
        "Underlying",
        *TENOR_COLUMNS,
        *TENOR_ORDER_COLUMNS,
        CURRENT,
    ),
    "s06_portfolios.csv": PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
    "s07_thresholds.csv": ("Risk Type", "Risk Greek", "PL", "Risk", "dRisk"),
}


class FixtureValidationError(RuntimeError):
    """Raised when generated or checked fixture data violates its contract."""


def _fake(value: str) -> str:
    return f"{FAKE_NOTICE} - {value}"


def _stable_int(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def _number(value: float, decimals: int = 6) -> str:
    rendered = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _full_axis_values(source_type: str) -> Mapping[str, tuple[str, ...]]:
    spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    expected_axes = tuple(spec.tenor_columns)
    configured = FULL_AXIS_VALUES.get(source_type, {})
    if tuple(configured) != expected_axes:
        raise FixtureValidationError(
            f"{source_type} fixture axes {tuple(configured)} do not match "
            f"ProductSpec axes {expected_axes}"
        )
    return configured


def _risk_axis_values(source_type: str) -> Mapping[str, tuple[str, ...]]:
    """Return several ordered risk layers while preserving market-only tenors."""
    return {
        axis: values[:-1] for axis, values in _full_axis_values(source_type).items()
    }


def _market_keys(source_type: str, *, risk_only: bool) -> list[tuple[str, ...]]:
    fixture = SOURCE_BY_TYPE[source_type]
    spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    axes = (
        _risk_axis_values(source_type) if risk_only else _full_axis_values(source_type)
    )
    keys: list[tuple[str, ...]] = []
    for raw_underlying in fixture.underlyings:
        underlying = _fake(raw_underlying)
        if not spec.axes:
            keys.append((underlying,))
        elif len(spec.axes) == 1:
            keys.extend(
                (underlying, _fake(tenor)) for tenor in axes[spec.axes[0].column]
            )
        elif len(spec.axes) == 2:
            first, second = spec.axes
            keys.extend(
                (underlying, _fake(first_value), _fake(second_value))
                for first_value in axes[first.column]
                for second_value in axes[second.column]
            )
        else:  # pragma: no cover - the registry validation rejects this first
            raise FixtureValidationError(f"{source_type} has unsupported axis count")
    return keys


def _key_fields(source_type: str, key: tuple[str, ...]) -> dict[str, str]:
    spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    return {
        "Underlying": key[0],
        **{axis.column: key[index + 1] for index, axis in enumerate(spec.axes)},
    }


def _risk_values(
    source_type: str,
    market_key: tuple[str, ...],
    portfolio: str,
) -> tuple[float, float]:
    risk_seed = _stable_int(source_type, *market_key, portfolio, "risk")
    drisk_seed = _stable_int(source_type, *market_key, portfolio, "drisk")
    risk_sign = -1.0 if risk_seed % 3 == 0 else 1.0
    drisk_sign = -1.0 if drisk_seed % 2 == 0 else 1.0
    risk = risk_sign * float(250_000 + risk_seed % 4_750_001)
    drisk = drisk_sign * float(10_000 + drisk_seed % 540_001)
    return risk, drisk


def _fixture_group(source_type: str, underlying: str) -> str:
    """Return connector-owned demo Group metadata for one raw Underlying."""

    fixture = SOURCE_BY_TYPE[source_type]
    groups_by_underlying = {
        _fake(raw_underlying): group
        for raw_underlying, group in zip(fixture.underlyings, fixture.groups)
    }
    return groups_by_underlying[underlying]


def _market_values(
    source_type: str, market_key: tuple[str, ...]
) -> tuple[float, float]:
    fixture = SOURCE_BY_TYPE[source_type]
    seed = _stable_int(source_type, *market_key, "market")
    move_seed = _stable_int(source_type, *market_key, "move")
    fraction = (seed % 100_000) / 100_000.0
    sign = -1.0 if move_seed % 2 == 0 else 1.0
    step = 1 + move_seed % 25

    if fixture.market_kind == "fx":
        opening = 0.75 + 0.70 * fraction
        move = sign * step * 0.0001
    elif fixture.market_kind == "rate":
        opening = 0.005 + 0.075 * fraction
        move = sign * step * 0.00005
    elif fixture.market_kind == "vol":
        opening = 0.05 + 0.30 * fraction
        move = sign * step * 0.0005
    elif fixture.market_kind == "credit":
        opening = 40.0 + 400.0 * fraction
        move = sign * step * 0.10
    elif fixture.market_kind == "commodity":
        opening = 25.0 + 125.0 * fraction
        move = sign * step * 0.05
    else:  # pragma: no cover - static source validation covers this
        raise FixtureValidationError(f"Unsupported market kind {fixture.market_kind!r}")
    return opening, opening + move


def _build_risk_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    schema = SCHEMAS["s03_risk.csv"]
    for source_type in EXPECTED_SOURCE_TYPES:
        product = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
        for market_key in _market_keys(source_type, risk_only=True):
            for raw_portfolio in RISK_PORTFOLIOS:
                risk, drisk = _risk_values(source_type, market_key, raw_portfolio)
                row = {column: "" for column in schema}
                row.update(
                    {
                        "Source Type": source_type,
                        **_key_fields(source_type, market_key),
                        "Portfolio": _fake(raw_portfolio),
                        "Group": _fixture_group(source_type, market_key[0]),
                        "Risk": _number(risk, 2),
                        "dRisk": _number(drisk, 2),
                    }
                )
                if product.risk_type == "Credit":
                    for measure, factor in CREDIT_FACTORS.items():
                        row[f"Risk {measure}"] = _number(risk * factor, 2)
                        row[f"dRisk {measure}"] = _number(drisk * factor, 2)
                rows.append(row)
    return rows


def _build_market_rows(value_column: str) -> list[dict[str, str]]:
    filename = "s04_open.csv" if value_column == "Open" else "s05_current.csv"
    rows: list[dict[str, str]] = []
    for source_type in EXPECTED_SOURCE_TYPES:
        product = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
        keys = _market_keys(source_type, risk_only=False)
        axis_orders: dict[str, dict[tuple[str, str], int]] = {}
        for axis in product.axes:
            configured = _full_axis_values(source_type)[axis.column]
            axis_orders[axis.column] = {
                (_fake(underlying), _fake(tenor)): rank
                for underlying in SOURCE_BY_TYPE[source_type].underlyings
                for rank, tenor in enumerate(configured)
            }
        for key in keys:
            opening, current = _market_values(source_type, key)
            fields = _key_fields(source_type, key)
            row = {column: "" for column in SCHEMAS[filename]}
            row.update({"Source Type": source_type, **fields})
            for axis in product.axes:
                row[axis.order_column] = str(
                    axis_orders[axis.column][
                        (fields["Underlying"], fields[axis.column])
                    ]
                )
            row[value_column] = _number(opening if value_column == "Open" else current)
            rows.append(row)
    return rows


def build_datasets() -> dict[str, list[dict[str, str]]]:
    readiness = [
        {
            "Risk Type": product.risk_type,
            "Risk Greek": product.risk_greek,
            "Age": "1" if product.source_type in AGE_ONE_SOURCE_TYPES else "0",
        }
        for product in PRODUCT_SPECS_BY_SOURCE_TYPE.values()
    ]
    checker = [
        {
            "Risk Type": product.risk_type,
            "Risk Greek": product.risk_greek,
            "MMMFile": _fake(
                f"{product.source_type.replace('/', '_')}_{position.casefold()}.mmm"
            ),
            "Product": position,
        }
        for product in PRODUCT_SPECS_BY_SOURCE_TYPE.values()
        for position in ("XVA", "Hedges")
    ]
    portfolio_config = [
        dict(
            zip(
                PORTFOLIO_CONFIG_REQUIRED_COLUMNS,
                (
                    _fake(portfolio),
                    product,
                    _fake(activity),
                    _fake(signoff_group),
                    _fake(category),
                ),
                strict=True,
            )
        )
        for portfolio, product, activity, signoff_group, category in MAPPED_PORTFOLIOS
    ]
    thresholds = [
        {
            "Risk Type": product.risk_type,
            "Risk Greek": product.risk_greek,
            "PL": "25000",
            "Risk": "2500000",
            "dRisk": "250000",
        }
        for product in PRODUCT_SPECS_BY_SOURCE_TYPE.values()
    ]
    thresholds.extend(
        {
            "Risk Type": classification.risk_type,
            "Risk Greek": classification.risk_greek,
            "PL": "25000",
            "Risk": "2500000",
            "dRisk": "250000",
        }
        for classification in DIRECT_PL_CLASSIFICATIONS
    )
    thresholds.extend(
        {
            "Risk Type": risk_type,
            "Risk Greek": risk_greek,
            "PL": "25000",
            "Risk": "2500000",
            "dRisk": "250000",
        }
        for risk_type, risk_greek in sorted(CROSS_GAMMA_INPUT_RISK_PAIRS)
    )
    return {
        "s01_readiness.csv": readiness,
        "s02_checker.csv": checker,
        "s03_risk.csv": _build_risk_rows(),
        "s04_open.csv": _build_market_rows("Open"),
        "s05_current.csv": _build_market_rows(CURRENT),
        "s06_portfolios.csv": portfolio_config,
        "s07_thresholds.csv": thresholds,
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FixtureValidationError(message)


def _finite_numeric(value: str, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FixtureValidationError(
            f"{label} must be numeric; found {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise FixtureValidationError(f"{label} must be finite; found {value!r}")
    return number


def _row_key(source_type: str, row: Mapping[str, str]) -> tuple[str, ...]:
    spec = PRODUCT_SPECS_BY_SOURCE_TYPE[source_type]
    return (row["Underlying"], *(row[axis.column] for axis in spec.axes))


def _validate_static_contract() -> None:
    registered = set(PRODUCT_SPECS_BY_SOURCE_TYPE)
    fixture_sources = set(SOURCE_BY_TYPE)
    _require(
        fixture_sources == registered,
        "Fixture source metadata must exactly cover the ProductSpec registry",
    )
    _require(
        len(SOURCE_FIXTURES) == len(SOURCE_BY_TYPE),
        "Fixture Source Type values must be unique",
    )
    for source_type, spec in PRODUCT_SPECS_BY_SOURCE_TYPE.items():
        axes = tuple(spec.tenor_columns)
        _require(
            axes in {(), (TENOR_SWAP,), tuple(TENOR_COLUMNS)},
            f"{source_type} must be axisless, Tenor Swap-only, or a true surface",
        )
        configured = _full_axis_values(source_type)
        for axis, values in configured.items():
            _require(len(values) >= 3, f"{source_type} {axis} needs several layers")
            _require(
                len(values) == len(set(values)), f"{source_type} {axis} duplicates"
            )
        _require(
            len(set(SOURCE_BY_TYPE[source_type].underlyings)) == 3,
            f"{source_type} must have three unique Underlyings",
        )
    _require(
        PRODUCT_SPECS_BY_SOURCE_TYPE["ir/gamma"].tenor_columns == [TENOR_SWAP],
        "IR Gamma must be Tenor Swap-only",
    )
    _require(
        PRODUCT_SPECS_BY_SOURCE_TYPE["credit/vega"].tenor_columns == [TENOR_SWAP],
        "Credit Vega must be Tenor Swap-only",
    )


def validate_datasets(datasets: Mapping[str, Sequence[Mapping[str, str]]]) -> None:
    _validate_static_contract()
    _require(set(datasets) == set(SCHEMAS), "Generated file set does not match SCHEMAS")
    for filename, schema in SCHEMAS.items():
        rows = datasets[filename]
        _require(bool(rows), f"{filename} must contain rows")
        for index, row in enumerate(rows):
            _require(
                tuple(row) == schema,
                f"{filename} row {index} columns differ from exact schema {schema}",
            )

    product_pairs = {
        (spec.risk_type, spec.risk_greek)
        for spec in PRODUCT_SPECS_BY_SOURCE_TYPE.values()
    }
    readiness = datasets["s01_readiness.csv"]
    readiness_pairs = [(row["Risk Type"], row["Risk Greek"]) for row in readiness]
    _require(
        set(readiness_pairs) == product_pairs,
        "Readiness pair coverage is incomplete",
    )
    _require(len(readiness_pairs) == len(set(readiness_pairs)), "Readiness duplicates")
    _require(all(row["Age"] in {"0", "1"} for row in readiness), "Age must be 0 or 1")

    checker = datasets["s02_checker.csv"]
    checker_keys = [
        (row["Risk Type"], row["Risk Greek"], row["MMMFile"], row["Product"])
        for row in checker
    ]
    _require(len(checker_keys) == len(set(checker_keys)), "Checker rows must be unique")
    _require(
        {(row["Risk Type"], row["Risk Greek"]) for row in checker} == product_pairs,
        "Checker pair coverage is incomplete",
    )
    checker_products: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in checker:
        checker_products[(row["Risk Type"], row["Risk Greek"])].add(row["Product"])
        _require(FAKE_NOTICE in row["MMMFile"], "MMMFile must retain fake notice")
        _require(row["MMMFile"].endswith(".mmm"), "MMMFile must use .mmm")
    _require(
        all(products == {"XVA", "Hedges"} for products in checker_products.values()),
        "Every checker pair must cover XVA and Hedges",
    )

    risk = datasets["s03_risk.csv"]
    market_open = datasets["s04_open.csv"]
    market_current = datasets["s05_current.csv"]
    expected_sources = set(EXPECTED_SOURCE_TYPES)
    for label, rows in (
        ("Risk", risk),
        ("Open", market_open),
        (CURRENT, market_current),
    ):
        _require(
            {row["Source Type"] for row in rows} == expected_sources,
            f"{label} Source Type coverage is incomplete",
        )

    grouped_risk: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    grouped_open: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    grouped_current: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in risk:
        grouped_risk[row["Source Type"]].append(row)
    for row in market_open:
        grouped_open[row["Source Type"]].append(row)
    for row in market_current:
        grouped_current[row["Source Type"]].append(row)

    all_axis_columns = TENOR_COLUMNS
    all_order_columns = TENOR_ORDER_COLUMNS
    for source_type, product in PRODUCT_SPECS_BY_SOURCE_TYPE.items():
        risk_rows = grouped_risk[source_type]
        open_rows = grouped_open[source_type]
        current_rows = grouped_current[source_type]
        risk_keys = {_row_key(source_type, row) for row in risk_rows}
        open_keys = [_row_key(source_type, row) for row in open_rows]
        current_keys = [_row_key(source_type, row) for row in current_rows]
        positions = [
            (*_row_key(source_type, row), row["Portfolio"]) for row in risk_rows
        ]

        _require(
            len(open_keys) == len(set(open_keys)), f"{source_type} Open duplicates"
        )
        _require(
            len(current_keys) == len(set(current_keys)),
            f"{source_type} Current duplicates",
        )
        _require(
            len(positions) == len(set(positions)), f"{source_type} Risk duplicates"
        )
        _require(
            set(open_keys) == set(current_keys), f"{source_type} market legs differ"
        )
        _require(
            risk_keys <= set(open_keys), f"{source_type} Risk is outside MarketBook"
        )
        if product.axes:
            _require(
                risk_keys < set(open_keys),
                f"{source_type} must retain market-only tenor rows",
            )
        else:
            _require(risk_keys == set(open_keys), f"{source_type} axisless keys differ")
        _require(
            {row["Portfolio"] for row in risk_rows}
            == {_fake(portfolio) for portfolio in RISK_PORTFOLIOS},
            f"{source_type} must cover every fake Portfolio",
        )

        for row in risk_rows:
            for column in ("Underlying", *product.tenor_columns, "Portfolio"):
                _require(
                    FAKE_NOTICE in row[column],
                    f"{source_type} Risk {column} lacks fake notice",
                )
            for column in set(all_axis_columns) - set(product.tenor_columns):
                _require(row[column] == "", f"{source_type} must leave {column} blank")
            _finite_numeric(row["Risk"], label=f"{source_type} Risk")
            _finite_numeric(row["dRisk"], label=f"{source_type} dRisk")
            if product.risk_type == "Credit":
                for column in CREDIT_MEASURE_COLUMNS:
                    _finite_numeric(row[column], label=f"{source_type} {column}")
            else:
                _require(
                    all(row[column] == "" for column in CREDIT_MEASURE_COLUMNS),
                    f"{source_type} must leave Credit measures blank",
                )

        for label, rows, value_column in (
            ("Open", open_rows, "Open"),
            (CURRENT, current_rows, CURRENT),
        ):
            for row in rows:
                _finite_numeric(row[value_column], label=f"{source_type} {label}")
                for column in ("Underlying", *product.tenor_columns):
                    _require(
                        FAKE_NOTICE in row[column],
                        f"{source_type} {label} {column} lacks fake notice",
                    )
                for column in set(all_axis_columns) - set(product.tenor_columns):
                    _require(
                        row[column] == "",
                        f"{source_type} {label} must leave {column} blank",
                    )
                for column in set(all_order_columns) - set(product.tenor_order_columns):
                    _require(
                        row[column] == "",
                        f"{source_type} {label} must leave {column} blank",
                    )
            for axis in product.axes:
                expected_order = {
                    _fake(tenor): rank
                    for rank, tenor in enumerate(
                        _full_axis_values(source_type)[axis.column]
                    )
                }
                for row in rows:
                    order = _finite_numeric(
                        row[axis.order_column],
                        label=f"{source_type} {label} {axis.order_column}",
                    )
                    _require(
                        order.is_integer() and order >= 0,
                        f"{source_type} {label} rank must be a non-negative integer",
                    )
                    _require(
                        int(order) == expected_order[row[axis.column]],
                        f"{source_type} {label} does not preserve market tenor order",
                    )

    portfolio_config = datasets["s06_portfolios.csv"]
    portfolios = [row["Portfolio"] for row in portfolio_config]
    _require(
        len(portfolios) == len(set(portfolios)), "Config Portfolios must be unique"
    )
    product_column = PORTFOLIO_FIELD_BY_KEY["product"].external_name
    _require(
        {row[product_column] for row in portfolio_config} == {"XVA", "Hedges"},
        "Config needs XVA and Hedges",
    )
    for row in portfolio_config:
        for column in PORTFOLIO_CONFIG_REQUIRED_COLUMNS:
            if column != product_column:
                _require(
                    FAKE_NOTICE in row[column], f"Config {column} lacks fake notice"
                )

    thresholds = datasets["s07_thresholds.csv"]
    threshold_pairs = [(row["Risk Type"], row["Risk Greek"]) for row in thresholds]
    expected_threshold_pairs = (
        product_pairs
        | {
            (classification.risk_type, classification.risk_greek)
            for classification in DIRECT_PL_CLASSIFICATIONS
        }
        | set(CROSS_GAMMA_INPUT_RISK_PAIRS)
    )
    _require(
        set(threshold_pairs) == expected_threshold_pairs,
        "Threshold coverage is incomplete",
    )
    _require(len(threshold_pairs) == len(set(threshold_pairs)), "Thresholds duplicate")
    for row in thresholds:
        for column in ("PL", "Risk", "dRisk"):
            _require(
                _finite_numeric(row[column], label=f"threshold {column}") > 0,
                "Thresholds must be positive",
            )


def _write_files(datasets: Mapping[str, Sequence[Mapping[str, str]]]) -> None:
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary_paths: list[tuple[Path, Path]] = []
    try:
        for filename, schema in SCHEMAS.items():
            destination = DATA_DIRECTORY / filename
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=schema, lineterminator="\n")
                writer.writeheader()
                writer.writerows(datasets[filename])
            temporary_paths.append((temporary, destination))
        for temporary, destination in temporary_paths:
            temporary.replace(destination)
    finally:
        for temporary, _destination in temporary_paths:
            if temporary.exists():
                temporary.unlink()


def _read_files() -> dict[str, list[dict[str, str]]]:
    datasets: dict[str, list[dict[str, str]]] = {}
    for filename, schema in SCHEMAS.items():
        path = DATA_DIRECTORY / filename
        if not path.is_file():
            raise FixtureValidationError(f"Missing generated fixture {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != schema:
                raise FixtureValidationError(
                    f"{filename} has columns {reader.fieldnames}; expected {list(schema)}"
                )
            datasets[filename] = [dict(row) for row in reader]
    return datasets


def _print_report(
    datasets: Mapping[str, Sequence[Mapping[str, str]]], *, checked_only: bool
) -> None:
    action = "Checked" if checked_only else "Generated and checked"
    print(f"{action} {len(datasets)} deterministic FAKE_ONLY connector CSVs.")
    for filename in SCHEMAS:
        path = DATA_DIRECTORY / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        print(f"  {filename}: {len(datasets[filename])} rows, sha256={digest}")
    risk_rows = datasets["s03_risk.csv"]
    market_rows = datasets["s04_open.csv"]
    credit_rows = [row for row in risk_rows if row["Source Type"].startswith("credit/")]
    print(
        "Validation: "
        f"sources={len(EXPECTED_SOURCE_TYPES)}, "
        f"risk_rows={len(risk_rows)}, "
        f"full_market_keys={len(market_rows)}, "
        f"credit_rows_with_complete_measures={len(credit_rows)}."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate checked-in CSVs against exact deterministic generation.",
    )
    args = parser.parse_args()

    expected = build_datasets()
    validate_datasets(expected)
    if not args.check:
        _write_files(expected)
    actual = _read_files()
    validate_datasets(actual)
    if actual != expected:
        raise FixtureValidationError(
            "Checked-in fixtures differ from deterministic output; rerun the generator."
        )
    _print_report(actual, checked_only=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
