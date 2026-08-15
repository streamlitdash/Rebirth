"""Credit adapter contract with an inline, comment-only recovered connector."""

from __future__ import annotations

# === REAL CREDIT CONNECTOR (COMMENTED OUT) ===================================
# SWITCH TO REAL: uncomment the required private imports and recovered builder,
# then uncomment its REAL registration in ``feeds/s01_sources.py`` and comment
# the adjacent CSV fallback registration.
# Leave the recovered ``from __future__ import annotations`` line commented;
# this module already enables it above so the inline switch remains compilable.
# === END SWITCH INSTRUCTIONS =================================================
# from __future__ import annotations
#
# from core.s01_schema import (
#     TENOR_OPTION,
#     TENOR_OPTION_ORDER,
#     TENOR_SWAP,
#     TENOR_SWAP_ORDER,
# )
# from core.s02_pipeline import (
#     CREDIT_MEASURE_COLUMNS,
#     CREDIT_MEASURES,
#     ProductConnectorAdapter,
# )
# from .s01_common import MarketSource, RiskSource, exact_frame, market_frame, run_async
#
# # ----------------------------------------LIBRARIES----------------------------------------#
#
# # generics
# import asyncio
# import boto3
# import colossus
# import concerto
# import credentials_wrapper as cw
# import dataframe_image as dfi
# import datetime as dt
# import io
# import json
# import mailer
# import mrx
# import numpy as np
# import os
# import pandas as pd
# import pathlib
# import re
# import requests
# import streamlit as st
# import string
# import time as time_time
# import warnings
# import xva.rplmlib.ver as rplm
# warnings.filterwarnings("ignore")
#
# # specifics
# from awacs_poc import configmanager as cm
# from base64 import b64encode
# from collections import Counter
# from cpypdl2 import pypdl
# from cpypdl2.pypdl import pdl_read, gpp_exec
# from dataclasses import dataclass
# from datetime import *
# from functools import reduce
# from IPython.display import Image, display
# from numpy import *
# from pandas.tseries.offsets import BDay
# from PIL import Image as PilImage
# from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode, ColumnsAutoSizeMode
# from typing import Literal, Tuple, Dict, List
#
# # xva
# from xva.boli_local import *
# from xva.boli_local import bkew_functions
# from xva.boli_local import qcd
# from xva.boli_local.utils import dates
# from xva.rplmlib.data_attributes import DataAttributes
# from xva.rplmlib.mercury_xva_api import MercuryXvaApi
# from xva.rplmlib.pal.pal_enums import PalServer, XvaScope, XvaCode
# from xva.rplmlib.pal.pal_credit_data import PalCreditData
#
#
# CREDIT_DELTA_RISK_BASE = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Region", "Risk", "dRisk",)
# CREDIT_DELTA_RISK = (*CREDIT_DELTA_RISK_BASE, *CREDIT_MEASURE_COLUMNS,)
# CREDIT_DELTA_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
# CREDIT_DELTA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")
#
# # get ramp data as dictionary via async
# async def get_ramp_read_dict(ramp_name, market, date):
#     if market == "Live":
#         data = await qcd.ramp_read_item(ramp_name, "LIVE", use_columns = True)
#     elif market == "LIVE FXVOL ROLLBACK DATA":
#         data = await qcd.ramp_read_item(ramp_name, market, pd.to_datetime('01/01/2001'), use_columns = True)
#     else:
#         data = await qcd.ramp_read_item(ramp_name, market, pd.to_datetime(date), use_columns = True)
#     return data
#
# # map ramp read from dict to df
# def get_ramp_read(data):
#     return pd.DataFrame.from_dict(data, orient = 'index')
#
# async def nexus_load_report(keys, values, filt, date):
#     return await qcd.nexus_load_report(keys, values, filt, date)
#
# def build_credit_delta_adapter() -> ProductConnectorAdapter:
#
#     def get_delta_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         values = ['SP01 = CRIndex.Delta * FX.EUR']
#         full_keys = ['Underlying = CRIndex.Issuer', 'CurveName = CRIndex.CurveName']
#         filt = (f"Trade.Type in (CDS, ContingentSwap, Index, IndexOption, xVA) AND Portfolio.SignOffGroup In ('CIT XVA', 'CIT XVA Hedges', 'CVA1', 'CVA2', 'CVA3', 'FVA', 'RWA', 'CVI', 'COLVA')")
#         issuer_mapping = run_async(nexus_load_report(full_keys, values, filt, risk_date))
#         issuer_mapping = issuer_mapping[['Underlying', 'CurveName']]
#         issuer_mapping.to_csv('data/s10_creditmap.csv')
#
#         values = ['SP01 = CRIndex.Delta * 100']
#         full_keys = ['Underlying = CRIndex.Issuer', 'TradeID = Trade.ID', '*CRIndex.ID']
#         filt = (f"Trade.Type in (Index) AND Portfolio.SignOffGroup In ('CIT XVA', 'CIT XVA Hedges', 'CVA1', 'CVA2', 'CVA3', 'FVA', 'RWA', 'CVI', 'COLVA')")
#         index_schema = run_async(nexus_load_report(full_keys, values, filt, risk_date))
#         index_schema = index_schema.groupby(['Underlying'], as_index = False).first()
#
#         index_pmrb = pd.DataFrame()
#         for i in range(len(index_schema['TradeID'])):
#
#             index_id = index_schema['TradeID'][i]
#             index_udl = index_schema['Underlying'][i]
#
#             values = ['PMRB = Market(Official):CR.PricingMethodRatesBumps * 100']
#             full_keys = ['Underlying = CR.Issuer', 'Tenor Swap = Tenor']
#             filt = (f"Trade.ID in ({index_id})")
#             data = run_async(nexus_load_report(full_keys, values, filt, risk_date))
#
#             number_constituents = len(data['Underlying'].unique())
#             temp = pd.DataFrame(data.groupby(['Tenor Swap'], sort = False).sum()['PMRB']) / number_constituents
#             temp = temp.reset_index(drop = False)
#             temp['Underlying'] = index_udl
#
#             index_pmrb = pd.concat([index_pmrb, temp], axis = 0)
#
#         values = ['PMRB = Market(Official):CRIndex.PricingMethodRatesBumps * 100']
#         full_keys = ['Underlying = CRIndex.Issuer', 'Tenor Swap = Tenor', 'TradeType = Trade.Type', '*CRIndex.ID']
#         filt = (f"Trade.Type in (CDS, ContingentSwap, Index, IndexOption, xVA) AND Portfolio.SignOffGroup In ('CIT XVA', 'CIT XVA Hedges', 'CVA1', 'CVA2', 'CVA3', 'FVA', 'RWA', 'CVI', 'COLVA')")
#         ramp_pmrb = run_async(nexus_load_report(full_keys, values, filt, risk_date))
#
#         ramp_pmrb = ramp_pmrb[~ramp_pmrb['Underlying'].isin(index_schema['Underlying'])].groupby(['Underlying', 'Tenor Swap'], as_index = False).first()
#         ramp_pmrb = ramp_pmrb[['Underlying', 'Tenor Swap', 'PMRB']]
#
#         all_pmrb = pd.concat([ramp_pmrb, index_pmrb], axis = 0)
#
#         values = ['Risk = CRIndex.Delta * FX.EUR', 'dRisk = CRIndex.Delta * FX.EUR - SET(T-1):CRIndex.Delta * FX.EUR']
#         full_keys = ['Portfolio = Portfolio', 'Underlying = CRIndex.Issuer', 'Group = Trade.xVACreditClass', 'Region = Trade.xVARegion', 'Tenor Swap = Tenor', '*CRIndex.ID']
#         filt = (f"Trade.Type in (CDS, ContingentSwap, Index, IndexOption, xVA) AND Portfolio.ID Not In (34504) AND Portfolio.SignOffGroup In ('CIT XVA', 'CIT XVA Hedges', 'CVA1', 'CVA2', 'CVA3', 'FVA', 'RWA', 'CVI', 'COLVA')")
#         all_risk = run_async(nexus_load_report(full_keys, values, filt, risk_date))
#         all_risk['Region'] = np.where(all_risk['Group'] == 'RAMP', all_risk['Region'], all_risk['Group'])
#         all_risk = all_risk.groupby(['Portfolio', 'Underlying', 'Group', 'Region', 'Tenor Swap'], as_index = False, sort = False).sum()
#
#         values = ['5Y = Market(SOD):CRIndex.Rates.5Y', 'p5Y = SET(T-1):Market(SOD):CRIndex.Rates.5Y']
#         full_keys = ['Underlying = CRIndex.Issuer', '*CRIndex.ID']
#         all_risk5y = run_async(nexus_load_report(full_keys, values, filt, risk_date))
#
#         all_risk = pd.merge(all_risk, all_risk5y, on = 'Underlying', how = 'outer')
#         all_risk = pd.merge(all_risk, all_pmrb, on = ['Underlying', 'Tenor Swap'], how = 'outer').fillna(0)
#         all_risk['Risk'], all_risk['dRisk'] = all_risk['Risk'] / 1e3, all_risk['dRisk'] / 1e3
#
#         all_risk['Risk SP01'], all_risk['dRisk SP01'] = all_risk['Risk'], all_risk['dRisk']
#         all_risk['Risk PSP01'], all_risk['dRisk PSP01'] = all_risk['Risk'] * all_risk['5Y'], all_risk['dRisk'] * all_risk['p5Y']
#         all_risk['Risk PM01'], all_risk['dRisk PM01'] = all_risk['Risk'] * all_risk['PMRB'], all_risk['dRisk'] * all_risk['PMRB']
#         all_risk['Risk PM01P'], all_risk['dRisk PM01P'] = all_risk['Risk'] * all_risk['PMRB'] * all_risk['5Y'], all_risk['dRisk'] * all_risk['PMRB'] * all_risk['p5Y']
#
#         all_risk['Risk Theta'], all_risk['dRisk Theta'], all_risk['Risk JTD'], all_risk['dRisk JTD'] = 0, 0, 0, 0
#         data = all_risk[all_risk['Underlying'] != '']
#         data = data[~((data['Risk'] == 0) & (data['dRisk'] == 0))]
#
#         result = data.loc[:, list(CREDIT_DELTA_RISK)]
#         return exact_frame(result, columns=CREDIT_DELTA_RISK, label="Credit Delta risk")
#
#
#     def get_delta_open(market_date: pd.Timestamp, underlying: str, *,market_status: str,) -> pd.DataFrame:
#
#         issuer_mapping = pd.read_csv('data/s10_creditmap.csv')
#         mapping = issuer_mapping[issuer_mapping['Underlying'] == underlying]
#         curvename = mapping['CurveName'].iloc[0]
#
#         market_open = run_async(get_ramp_read_dict(curvename, 'OFFICIAL OPEN', market_date))
#         try:
#             market_open_table = pd.DataFrame([market_open[curvename.lower().replace(' ','-') + '_quotes.tenor'],
#                                               market_open[curvename.lower().replace(' ','-') + '_quotes.bid'],
#                                               market_open[curvename.lower().replace(' ','-') + '_quotes.offer']]).T.rename(columns = {0: 'Tenor Swap', 1: 'Open Bid', 2: 'Open Offer'})
#             market_open_table.iloc[0] = ['0M', 0, 0]
#             market_open_table['Open'] = (market_open_table['Open Bid'] + market_open_table['Open Offer']) / 2 * 100
#         except:
#             market_open_table = pd.DataFrame(columns = ['Tenor Swap', 'Open'])
#         market_open_table['Tenor Swap Order'] = range(len(market_open_table['Tenor Swap']))
#         market_open_table['Underlying'] = underlying
#
#         result = market_open_table.loc[:, list(CREDIT_DELTA_OPEN)]
#
#         return exact_frame(result, columns=CREDIT_DELTA_OPEN, label="Credit Delta Open")
#
#
#     def get_delta_current(market_date: pd.Timestamp, underlying: str, *,market_status: str,) -> pd.DataFrame:
#
#         issuer_mapping = pd.read_csv('data/s10_creditmap.csv')
#         mapping = issuer_mapping[issuer_mapping['Underlying'] == underlying]
#         curvename = mapping['CurveName'].iloc[0]
#
#         try:
#             market_close = run_async(get_ramp_read_dict(curvename, market_status, market_date))
#             market_close_table = pd.DataFrame([market_close[curvename.lower().replace(' ','-') + '_quotes.tenor'],
#                                                market_close[curvename.lower().replace(' ','-') + '_quotes.bid'],
#                                                market_close[curvename.lower().replace(' ','-') + '_quotes.offer']]).T.rename(columns = {0: 'Tenor Swap', 1: 'Current Bid', 2: 'Current Offer'})
#             market_close_table.iloc[0] = ['0M', 0, 0]
#             market_close_table['Current'] = (market_close_table['Current Bid'] + market_close_table['Current Offer']) / 2 * 100
#         except:
#             market_close_table = pd.DataFrame(columns = ['Tenor Swap', 'Current'])
#         market_close_table['Tenor Swap Order'] = range(len(market_close_table['Tenor Swap']))
#         market_close_table['Underlying'] = underlying
#
#         result = market_close_table.loc[:, list(CREDIT_DELTA_CURRENT)]
#
#         return exact_frame(result, columns=CREDIT_DELTA_CURRENT, label="Credit Delta current")
#
#     return ProductConnectorAdapter(
#         risk = get_delta_risk,
#         market_open = get_delta_open,
#         market_status = get_delta_current,
#     )
#
#
# __all__ = [
#     "build_credit_delta_adapter",
# ]


# === ACTIVE VALIDATED CONTRACT (CSV RUNTIME IS SELECTED IN FEEDS) ============
# Strict active Credit Delta curve adapter contract with optional measures.

import pandas as pd

from core.s01_schema import TENOR_SWAP, TENOR_SWAP_ORDER
from core.s02_pipeline import (
    CREDIT_MEASURE_COLUMNS,
    CREDIT_MEASURES,
    ProductConnectorAdapter,
)
from .s01_common import MarketSource, RiskSource, market_frame


CREDIT_DELTA_RISK_BASE = (
    "Underlying",
    TENOR_SWAP,
    "Portfolio",
    "Group",
    "Risk",
    "dRisk",
)
CREDIT_DELTA_RISK_REGION_BASE = (
    "Underlying",
    TENOR_SWAP,
    "Portfolio",
    "Group",
    "Region",
    "Risk",
    "dRisk",
)
CREDIT_DELTA_RISK = (
    *CREDIT_DELTA_RISK_BASE,
    *CREDIT_MEASURE_COLUMNS,
)
CREDIT_DELTA_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
CREDIT_DELTA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")


def build_credit_adapter(
    *,
    risk: RiskSource,
    open_market: MarketSource,
    current_market: MarketSource,
) -> ProductConnectorAdapter:
    """Bind a fixture or site-owned Credit Delta source to the public contract."""

    def get_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
        value = risk(risk_date)
        if not isinstance(value, pd.DataFrame):
            raise TypeError("Credit Delta risk must return a pandas DataFrame")
        actual = tuple(value.columns)
        base_columns = (
            CREDIT_DELTA_RISK_REGION_BASE
            if "Region" in actual
            else CREDIT_DELTA_RISK_BASE
        )
        unexpected = [
            column
            for column in actual
            if column not in {*base_columns, *CREDIT_MEASURE_COLUMNS}
        ]
        selected_measures = tuple(
            column for column in CREDIT_MEASURE_COLUMNS if column in value
        )
        expected = (*base_columns, *selected_measures)
        if unexpected or actual != expected:
            raise ValueError(
                "Credit Delta risk columns must be the base columns followed by "
                "canonical optional Credit measure pairs; "
                f"found {list(actual)}"
            )
        for measure in CREDIT_MEASURES:
            risk_measure = f"Risk {measure}"
            drisk_measure = f"dRisk {measure}"
            if (risk_measure in value) != (drisk_measure in value):
                raise ValueError(
                    f"Credit Delta optional measure {measure!r} must supply both "
                    f"{risk_measure!r} and {drisk_measure!r}, or omit both"
                )
        return value.copy()

    def get_open(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            open_market,
            market_date,
            underlying,
            market_status=market_status,
            columns=CREDIT_DELTA_OPEN,
            label="Credit Delta Open",
        )

    def get_current(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            current_market,
            market_date,
            underlying,
            market_status=market_status,
            columns=CREDIT_DELTA_CURRENT,
            label="Credit Delta current",
            attach_status=True,
        )

    return ProductConnectorAdapter(
        risk=get_risk,
        market_open=get_open,
        market_status=get_current,
    )


__all__ = [
    "CREDIT_DELTA_CURRENT",
    "CREDIT_DELTA_OPEN",
    "CREDIT_DELTA_RISK",
    "CREDIT_DELTA_RISK_BASE",
    "CREDIT_DELTA_RISK_REGION_BASE",
    "build_credit_adapter",
]
