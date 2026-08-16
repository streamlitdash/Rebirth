"""Pure validation and dual-leg development of portfolio Cross Gamma risk.

Cross Gamma sensitivities are expressed per unit of the existing product
MarketBook ``Move``.  The calculator therefore consumes that stored value
directly: it does not infer direction, rescale basis points, or manufacture a
separate bump convention.  Every raw matrix cell releases its input sensitivity
under the adapter-authoritative ``Risk Greek`` and also
contributes to the aggregated ``XGAMMA`` output-risk leg.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from core.s01_schema import (
    TENOR_OPTION,
    TENOR_OPTION_ORDER,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
)

if TYPE_CHECKING:
    from core.s02_pipeline import ProductSpec


# These are the canonical public labels owned by ``core.s02_pipeline``.  They
# remain local literals here so the pipeline can top-level import this pure
# module during manager integration without creating a circular import.
RISK_TYPE = "Risk Type"
SOURCE_TYPE = "Source Type"
RISK_GREEK = "Risk Greek"
SPLIT = "Split"
UNDERLYING = "Underlying"
PORTFOLIO = "Portfolio"
GROUP = "Group"
RISK = "Risk"
DRISK = "dRisk"
OPEN = "Open"
CURRENT = "Current"
MARKET_STATUS = "Market Status"
MARKET_MOVE = "Move"
MARKET_AVAILABLE = "Market Available"
MARKET_DATA_STATUS = "Market Data Status"
PL = "PL"


INPUT_RISK_TYPE = "Input Risk Type"
INPUT_RISK_GREEK = "Input Risk Greek"
INPUT_UNDERLYING = "Input Underlying"
INPUT_TENOR_SWAP = "Input Tenor Swap"
INPUT_TENOR_OPTION = "Input Tenor Option"
OUTPUT_RISK_TYPE = "Output Risk Type"
OUTPUT_RISK_GREEK = "Output Risk Greek"
OUTPUT_UNDERLYING = "Output Underlying"
OUTPUT_TENOR_SWAP = "Output Tenor Swap"
OUTPUT_TENOR_OPTION = "Output Tenor Option"
CROSS_GAMMA_SENSITIVITY = "Cross Gamma Sensitivity"
XGAMMA_RISK_GREEK = "XGamma"
XGAMMA_VEGA_RISK_GREEK = "XGamma Vega"
XGAMMA_SOURCE_RISK_GREEKS = (XGAMMA_RISK_GREEK, XGAMMA_VEGA_RISK_GREEK)
XGAMMA_VEGA_INPUT_GREEKS = frozenset(("Vega", "DeltaVega", "InflationVega", "XCCYVega"))
CROSS_GAMMA_SOURCE_SPLIT = "Risk"
XGAMMA_SPLIT = "XGAMMA"

CROSS_GAMMA_COLUMNS = (
    PORTFOLIO,
    GROUP,
    INPUT_RISK_TYPE,
    INPUT_RISK_GREEK,
    RISK_GREEK,
    INPUT_UNDERLYING,
    INPUT_TENOR_SWAP,
    INPUT_TENOR_OPTION,
    OUTPUT_RISK_TYPE,
    OUTPUT_RISK_GREEK,
    OUTPUT_UNDERLYING,
    OUTPUT_TENOR_SWAP,
    OUTPUT_TENOR_OPTION,
    CROSS_GAMMA_SENSITIVITY,
)

CROSS_GAMMA_CELL_COLUMNS = tuple(
    column for column in CROSS_GAMMA_COLUMNS if column != CROSS_GAMMA_SENSITIVITY
)

CROSS_GAMMA_RELEASE_COLUMNS = (
    RISK_TYPE,
    RISK_GREEK,
    SPLIT,
    UNDERLYING,
    PORTFOLIO,
    GROUP,
    RISK,
    DRISK,
    OPEN,
    CURRENT,
    MARKET_STATUS,
    MARKET_AVAILABLE,
    MARKET_DATA_STATUS,
    MARKET_MOVE,
    PL,
    SOURCE_TYPE,
    TENOR_SWAP,
    TENOR_OPTION,
    TENOR_SWAP_ORDER,
    TENOR_OPTION_ORDER,
)

_REQUIRED_TEXT_COLUMNS = (
    PORTFOLIO,
    GROUP,
    INPUT_RISK_TYPE,
    INPUT_RISK_GREEK,
    RISK_GREEK,
    INPUT_UNDERLYING,
    OUTPUT_RISK_TYPE,
    OUTPUT_RISK_GREEK,
    OUTPUT_UNDERLYING,
)
_TENOR_COLUMNS = (
    INPUT_TENOR_SWAP,
    INPUT_TENOR_OPTION,
    OUTPUT_TENOR_SWAP,
    OUTPUT_TENOR_OPTION,
)
_INPUT_COLUMNS_BY_AXIS = {
    TENOR_SWAP: INPUT_TENOR_SWAP,
    TENOR_OPTION: INPUT_TENOR_OPTION,
}
_OUTPUT_COLUMNS_BY_AXIS = {
    TENOR_SWAP: OUTPUT_TENOR_SWAP,
    TENOR_OPTION: OUTPUT_TENOR_OPTION,
}
_MARKET_VALUE_COLUMNS = (
    OPEN,
    CURRENT,
    MARKET_STATUS,
    MARKET_AVAILABLE,
    MARKET_DATA_STATUS,
    MARKET_MOVE,
)
_CONTRIBUTION = "__Cross Gamma Contribution__"
_RAW_ROW = "__Cross Gamma Raw Row__"
_SOURCE_RISK_GREEK = "__Cross Gamma Source Risk Greek__"


def _product_by_pair() -> dict[tuple[str, str], ProductSpec]:
    # Deferred deliberately: ``core.s02_pipeline`` may import this module to
    # wire the manager after its ProductSpec catalogue has been constructed.
    from core.s02_pipeline import PRODUCT_SPECS

    return {(spec.risk_type, spec.risk_greek): spec for spec in PRODUCT_SPECS.values()}


def _normalize_text(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return value


def _require_text(frame: pd.DataFrame, column: str, *, allow_blank: bool) -> None:
    is_text = frame[column].map(lambda value: isinstance(value, str))
    invalid = ~is_text
    if not allow_blank:
        invalid |= frame[column].map(lambda value: isinstance(value, str) and not value)
    if invalid.any():
        rows = frame.index[invalid].tolist()[:5]
        requirement = "text or blank" if allow_blank else "nonblank text"
        raise ValueError(
            f"Cross Gamma column {column!r} must contain {requirement} at rows {rows}"
        )


def _validate_axes(
    frame: pd.DataFrame,
    *,
    row_index: object,
    spec: ProductSpec,
    side: str,
) -> None:
    columns = _INPUT_COLUMNS_BY_AXIS if side == "Input" else _OUTPUT_COLUMNS_BY_AXIS
    declared_axes = set(spec.tenor_columns)
    for axis, column in columns.items():
        value = frame.at[row_index, column]
        if axis in declared_axes and not value:
            raise ValueError(
                f"Cross Gamma {side} pair "
                f"{(spec.risk_type, spec.risk_greek)!r} requires {column!r} at "
                f"row {row_index}"
            )
        if axis not in declared_axes and value:
            raise ValueError(
                f"Cross Gamma {side} pair "
                f"{(spec.risk_type, spec.risk_greek)!r} does not use {column!r}; "
                f"row {row_index} must leave it blank"
            )


def validate_cross_gamma_rows(raw: object) -> pd.DataFrame:
    """Return normalized raw matrix rows after enforcing the full contract.

    Product identities are resolved exclusively through ``PRODUCT_SPECS``.
    Tenor order is deliberately absent because the validated MarketBook owns
    display ranks.  One full portfolio/input/output matrix cell may occur only
    once; callers must provide the authoritative sensitivity rather than rely
    on an accidental duplicate aggregation.
    """

    if not isinstance(raw, pd.DataFrame):
        raise TypeError("Cross Gamma source must return a pandas DataFrame")
    actual_columns = tuple(raw.columns)
    if actual_columns != CROSS_GAMMA_COLUMNS:
        raise ValueError(
            "Cross Gamma columns must be exactly "
            f"{list(CROSS_GAMMA_COLUMNS)} in that order; "
            f"found {list(actual_columns)}"
        )

    result = raw.copy()
    for column in (*_REQUIRED_TEXT_COLUMNS, *_TENOR_COLUMNS):
        result[column] = result[column].map(_normalize_text)
    for column in _REQUIRED_TEXT_COLUMNS:
        _require_text(result, column, allow_blank=False)
    for column in _TENOR_COLUMNS:
        _require_text(result, column, allow_blank=True)

    raw_sensitivity = result[CROSS_GAMMA_SENSITIVITY]
    boolean_sensitivity = raw_sensitivity.map(
        lambda value: isinstance(value, (bool, np.bool_))
    )
    converted_sensitivity = pd.to_numeric(raw_sensitivity, errors="coerce")
    invalid_sensitivity = (
        boolean_sensitivity
        | converted_sensitivity.isna()
        | ~converted_sensitivity.map(np.isfinite)
    )
    if invalid_sensitivity.any():
        rows = result.index[invalid_sensitivity].tolist()[:5]
        raise ValueError(
            f"{CROSS_GAMMA_SENSITIVITY!r} must contain finite numbers at rows {rows}"
        )
    result[CROSS_GAMMA_SENSITIVITY] = converted_sensitivity.astype(float)

    pair_catalogue = _product_by_pair()
    for row_index, row in result.iterrows():
        for side, type_column, greek_column in (
            ("Input", INPUT_RISK_TYPE, INPUT_RISK_GREEK),
            ("Output", OUTPUT_RISK_TYPE, OUTPUT_RISK_GREEK),
        ):
            pair = (row[type_column], row[greek_column])
            if pair == ("Cash Flow", "New"):
                raise ValueError(
                    f"Cross Gamma {side} cannot use Cash Flow/New at row {row_index}"
                )
            try:
                spec = pair_catalogue[pair]
            except KeyError as exc:
                raise ValueError(
                    f"Cross Gamma {side} pair {pair!r} is not in PRODUCT_SPECS "
                    f"at row {row_index}"
                ) from exc
            _validate_axes(result, row_index=row_index, spec=spec, side=side)

    expected_sensitivity_greek = result[INPUT_RISK_GREEK].map(
        lambda greek: (
            XGAMMA_VEGA_RISK_GREEK
            if greek in XGAMMA_VEGA_INPUT_GREEKS
            else XGAMMA_RISK_GREEK
        )
    )
    invalid_sensitivity_greek = result[RISK_GREEK].ne(expected_sensitivity_greek)
    if invalid_sensitivity_greek.any():
        details = [
            {
                "row": row_index,
                INPUT_RISK_GREEK: result.at[row_index, INPUT_RISK_GREEK],
                "expected": expected_sensitivity_greek.at[row_index],
                "found": result.at[row_index, RISK_GREEK],
            }
            for row_index in result.index[invalid_sensitivity_greek][:5]
        ]
        raise ValueError(
            f"{RISK_GREEK!r} does not match {INPUT_RISK_GREEK!r}: {details}"
        )

    duplicate_cells = result.duplicated(list(CROSS_GAMMA_CELL_COLUMNS), keep=False)
    if duplicate_cells.any():
        rows = result.index[duplicate_cells].tolist()[:10]
        raise ValueError(
            f"Cross Gamma contains duplicate full matrix cells at rows {rows}"
        )
    return result.reset_index(drop=True)


def cross_gamma_market_scope(raw_or_validated: object) -> dict[str, tuple[str, ...]]:
    """Return ordered input/output Underlyings required from each MarketBook."""

    rows = validate_cross_gamma_rows(raw_or_validated)
    pair_catalogue = _product_by_pair()
    scope: defaultdict[str, list[str]] = defaultdict(list)
    for row in rows.itertuples(index=False, name=None):
        values = dict(zip(CROSS_GAMMA_COLUMNS, row, strict=True))
        for type_column, greek_column, underlying_column in (
            (INPUT_RISK_TYPE, INPUT_RISK_GREEK, INPUT_UNDERLYING),
            (OUTPUT_RISK_TYPE, OUTPUT_RISK_GREEK, OUTPUT_UNDERLYING),
        ):
            spec = pair_catalogue[(values[type_column], values[greek_column])]
            underlying = values[underlying_column]
            if underlying not in scope[spec.source_type]:
                scope[spec.source_type].append(underlying)
    return {source_type: tuple(values) for source_type, values in scope.items()}


def _market_frame(
    market_frames: Mapping[str, pd.DataFrame],
    spec: ProductSpec,
    *,
    required: bool,
) -> pd.DataFrame:
    columns = [
        *spec.market_keys,
        *spec.tenor_order_columns,
        *_MARKET_VALUE_COLUMNS,
    ]
    market = market_frames.get(spec.source_type)
    if market is None:
        if required:
            raise ValueError(
                "Cross Gamma input MarketBook is missing Source Type "
                f"{spec.source_type!r}"
            )
        return pd.DataFrame(columns=columns)
    if not isinstance(market, pd.DataFrame):
        raise TypeError(f"MarketBook {spec.source_type!r} must be a pandas DataFrame")
    missing_columns = [column for column in columns if column not in market]
    if missing_columns:
        raise ValueError(
            f"MarketBook {spec.source_type!r} is missing columns {missing_columns}"
        )
    selected = market.loc[:, columns].copy()
    if selected.duplicated(spec.market_keys).any():
        raise ValueError(
            f"MarketBook {spec.source_type!r} has duplicate canonical quote keys"
        )
    return selected


def _complete_tenor_release_columns(
    result: pd.DataFrame,
    spec: ProductSpec,
) -> pd.DataFrame:
    """Supply canonical display tenors and MarketBook-owned order columns."""

    if TENOR_SWAP not in result:
        result[TENOR_SWAP] = "Spot" if spec.key == "fxdelta" else "N/A"
    if TENOR_OPTION not in result:
        result[TENOR_OPTION] = "N/A"
    for order_column in (TENOR_SWAP_ORDER, TENOR_OPTION_ORDER):
        if order_column not in result:
            result[order_column] = pd.Series(pd.NA, index=result.index, dtype="Int64")
        else:
            result[order_column] = pd.to_numeric(
                result[order_column], errors="raise"
            ).astype("Int64")
    return result


def _build_input_legs(
    rows: pd.DataFrame,
    market_frames: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return raw-cell source rows and their move-developed contributions."""

    pair_catalogue = _product_by_pair()
    contributions: list[pd.DataFrame] = []
    source_rows: list[pd.DataFrame] = []
    grouped = rows.groupby(
        [INPUT_RISK_TYPE, INPUT_RISK_GREEK], sort=False, dropna=False
    )
    for pair, pair_rows in grouped:
        spec = pair_catalogue[pair]
        left = pair_rows.copy()
        left[_RAW_ROW] = left.index
        # MarketBook joins use the canonical Risk Greek key.  Preserve the
        # adapter-owned source label before temporarily replacing that key with
        # the actual input Greek that selects the ProductSpec/MarketBook.
        left[_SOURCE_RISK_GREEK] = left[RISK_GREEK]
        left[RISK_TYPE] = left[INPUT_RISK_TYPE]
        left[RISK_GREEK] = left[INPUT_RISK_GREEK]
        left[UNDERLYING] = left[INPUT_UNDERLYING]
        for axis in spec.tenor_columns:
            left[axis] = left[_INPUT_COLUMNS_BY_AXIS[axis]]

        market = _market_frame(market_frames, spec, required=True)
        joined = left.merge(
            market,
            on=spec.market_keys,
            how="left",
            validate="many_to_one",
            indicator="__Input Market Merge__",
        )
        invalid_move = pd.to_numeric(joined[MARKET_MOVE], errors="coerce")
        missing_input = (
            joined["__Input Market Merge__"].ne("both")
            | ~joined[MARKET_AVAILABLE].fillna(False).astype(bool)
            | invalid_move.isna()
            | ~invalid_move.fillna(0.0).map(np.isfinite)
        )
        if missing_input.any():
            raw_rows = joined.loc[missing_input, _RAW_ROW].astype(int).tolist()[:10]
            raise ValueError(
                "Cross Gamma input quote is missing or unavailable for raw rows "
                f"{raw_rows}"
            )
        joined[_CONTRIBUTION] = joined[CROSS_GAMMA_SENSITIVITY].astype(
            float
        ) * invalid_move.astype(float)
        contribution_rows = joined.copy()
        contribution_rows[RISK_GREEK] = contribution_rows[_SOURCE_RISK_GREEK]
        contributions.append(
            contribution_rows.loc[:, [*CROSS_GAMMA_CELL_COLUMNS, _CONTRIBUTION]]
        )

        # Source sensitivity grain is deliberately one row per validated full
        # input/output matrix cell.  Even when two cells share the same visible
        # input identity, their target-specific sensitivities are not collapsed.
        source = joined.copy()
        source[RISK] = source[CROSS_GAMMA_SENSITIVITY].astype(float)
        source[RISK_GREEK] = source[_SOURCE_RISK_GREEK]
        source[SOURCE_TYPE] = spec.source_type
        source[SPLIT] = CROSS_GAMMA_SOURCE_SPLIT
        source[DRISK] = np.nan
        source[PL] = 0.0
        source = _complete_tenor_release_columns(source, spec)
        source_rows.append(source.loc[:, list(CROSS_GAMMA_RELEASE_COLUMNS)])

    if not contributions:
        return (
            pd.DataFrame(columns=list(CROSS_GAMMA_RELEASE_COLUMNS)),
            pd.DataFrame(columns=[*CROSS_GAMMA_CELL_COLUMNS, _CONTRIBUTION]),
        )
    return (
        pd.concat(source_rows, ignore_index=True, sort=False),
        pd.concat(contributions, ignore_index=True, sort=False),
    )


def _attach_output_market(
    output_rows: pd.DataFrame,
    spec: ProductSpec,
    market_frames: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    overlay = output_rows.copy()
    overlay[RISK_TYPE] = overlay.pop(OUTPUT_RISK_TYPE)
    overlay[RISK_GREEK] = overlay.pop(OUTPUT_RISK_GREEK)
    overlay[UNDERLYING] = overlay.pop(OUTPUT_UNDERLYING)
    for axis in spec.tenor_columns:
        overlay[axis] = overlay.pop(_OUTPUT_COLUMNS_BY_AXIS[axis])
    for unused_axis in set(_OUTPUT_COLUMNS_BY_AXIS) - set(spec.tenor_columns):
        overlay = overlay.drop(columns=_OUTPUT_COLUMNS_BY_AXIS[unused_axis])

    market = _market_frame(market_frames, spec, required=False)
    result = overlay.merge(
        market,
        on=spec.market_keys,
        how="left",
        validate="many_to_one",
        indicator="__Output Market Merge__",
    )
    no_market_row = result["__Output Market Merge__"].ne("both")
    result[MARKET_AVAILABLE] = result[MARKET_AVAILABLE].fillna(False).astype(bool)
    result.loc[no_market_row, MARKET_DATA_STATUS] = "No matching market row"
    result[MARKET_DATA_STATUS] = result[MARKET_DATA_STATUS].fillna(
        "No matching market row"
    )
    result = result.drop(columns="__Output Market Merge__")

    result[SOURCE_TYPE] = spec.source_type
    result[SPLIT] = XGAMMA_SPLIT
    result[DRISK] = np.nan
    result[PL] = 0.0
    result = _complete_tenor_release_columns(result, spec)
    return result.loc[:, list(CROSS_GAMMA_RELEASE_COLUMNS)]


def build_cross_gamma_rows(
    raw_or_validated: object,
    market_frames: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Release source sensitivities and develop output risk from stored moves.

    Each validated full matrix cell first releases one source row under its
    Input Risk Type with the exact adapter-supplied sensitivity Greek
    (``XGamma`` or ``XGamma Vega``), ``Split = Risk``, and raw sensitivity as
    Risk.  The actual ``Input Risk Greek`` remains the ProductSpec and MarketBook
    join driver.  Contributions from distinct input cells are then summed when
    Portfolio, Group, and the complete output identity agree and are released
    under the real output pair with ``Split = XGAMMA``.  Input quotes are
    mandatory because both the source market context and developed risk use
    their stored ``Move``.  An absent output quote retains calculated risk with
    market data marked unavailable.
    """

    if not isinstance(market_frames, Mapping):
        raise TypeError("market_frames must map Source Type to validated MarketBooks")
    rows = validate_cross_gamma_rows(raw_or_validated)
    if rows.empty:
        return pd.DataFrame(columns=list(CROSS_GAMMA_RELEASE_COLUMNS))

    source_rows, contributions = _build_input_legs(rows, market_frames)
    output_keys = [
        PORTFOLIO,
        GROUP,
        OUTPUT_RISK_TYPE,
        OUTPUT_RISK_GREEK,
        OUTPUT_UNDERLYING,
        OUTPUT_TENOR_SWAP,
        OUTPUT_TENOR_OPTION,
    ]
    developed = (
        contributions.groupby(output_keys, sort=False, as_index=False, dropna=False)[
            _CONTRIBUTION
        ]
        .sum(min_count=1)
        .rename(columns={_CONTRIBUTION: RISK})
    )

    pair_catalogue = _product_by_pair()
    released: list[pd.DataFrame] = []
    grouped = developed.groupby(
        [OUTPUT_RISK_TYPE, OUTPUT_RISK_GREEK], sort=False, dropna=False
    )
    for pair, output_rows in grouped:
        released.append(
            _attach_output_market(
                output_rows.reset_index(drop=True),
                pair_catalogue[pair],
                market_frames,
            )
        )
    developed_rows = pd.concat(released, ignore_index=True, sort=False).loc[
        :, list(CROSS_GAMMA_RELEASE_COLUMNS)
    ]
    return pd.concat([source_rows, developed_rows], ignore_index=True, sort=False).loc[
        :, list(CROSS_GAMMA_RELEASE_COLUMNS)
    ]


__all__ = [
    "CROSS_GAMMA_CELL_COLUMNS",
    "CROSS_GAMMA_COLUMNS",
    "CROSS_GAMMA_RELEASE_COLUMNS",
    "CROSS_GAMMA_SENSITIVITY",
    "CROSS_GAMMA_SOURCE_SPLIT",
    "INPUT_RISK_GREEK",
    "INPUT_RISK_TYPE",
    "INPUT_TENOR_OPTION",
    "INPUT_TENOR_SWAP",
    "INPUT_UNDERLYING",
    "OUTPUT_RISK_GREEK",
    "OUTPUT_RISK_TYPE",
    "OUTPUT_TENOR_OPTION",
    "OUTPUT_TENOR_SWAP",
    "OUTPUT_UNDERLYING",
    "XGAMMA_SPLIT",
    "XGAMMA_RISK_GREEK",
    "XGAMMA_SOURCE_RISK_GREEKS",
    "XGAMMA_VEGA_INPUT_GREEKS",
    "XGAMMA_VEGA_RISK_GREEK",
    "build_cross_gamma_rows",
    "cross_gamma_market_scope",
    "validate_cross_gamma_rows",
]
