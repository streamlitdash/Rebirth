"""FX adapter contracts with inline, comment-only recovered connectors."""

from __future__ import annotations

# === REAL FX CONNECTORS (COMMENTED OUT) ======================================
# SWITCH TO REAL: uncomment the required private imports/builders below, then
# uncomment their REAL registration in ``feeds/s01_sources.py`` and comment the
# adjacent CSV fallback registration. The recovered code remains non-executable.
# Leave the recovered ``from __future__ import annotations`` line commented;
# this module already enables it above so the inline switch remains compilable.
# === END SWITCH INSTRUCTIONS =================================================
# """Working FX Delta curve and FX Vega surface adapter examples."""
#
# from __future__ import annotations
#
# from core.s01_schema import (
#     TENOR_OPTION,
#     TENOR_OPTION_ORDER,
#     TENOR_SWAP,
#     TENOR_SWAP_ORDER,
# )
# from core.s02_pipeline import ProductConnectorAdapter
# from .s01_common import MarketSource, RiskSource, exact_frame, market_frame, run_async
#
# # ---------------------------------------LIBRARIES---------------------------------------#
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
# from awacs.poc import configmanager as cm
# from base64 import b64encode
# from collections import Counter
# from cyppd12 import pyppd1
# from cyppd12.pyppd1 import pdl_read, pdl_exec
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
# from xva.boli_local import bkevent_functions
# from xva.boli_local import gcd
# from xva.boli_local.utils import dates
# from xva.rplmlib.data_attributes import DataAttributes
# from xva.rplmlib.mercury_xva_api import MercuryXVAApi
# from xva.rplmlib.pal.pal_enums import PalServer, XVAScope, xvacode
# from xva.rplmlib.pal.pal_credit_data import PalCreditData
#
#
# FX_DELTA_RISK = ("Underlying", "Portfolio", "Group", "Risk", "dRisk",)
# FX_DELTA_OPEN = ("Underlying", "Open")
# FX_DELTA_CURRENT = ("Underlying", "Current")
#
# FX_GAMMA_RISK = ("Underlying", "Portfolio", "Group", "Risk", "dRisk",)
# FX_GAMMA_OPEN = ("Underlying", "Open")
# FX_GAMMA_CURRENT = ("Underlying", "Current")
#
# FX_VEGA_RISK = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk",)
# FX_VEGA_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
# FX_VEGA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")
#
#
# # get ramp data as dictionary via async
# async def get_ramp_read_dict(ramp_name, market, date):
#     if market == "LIVE":
#         data = await qcd.ramp_read_item(ramp_name, "LIVE", use_columns=True)
#     elif market == "LIVE FXVOL ROLLBACK DATA":
#         data = await qcd.ramp_read_item(ramp_name, market, pd.to_datetime("01/01/2001"), use_columns=True)
#     else:
#         data = await qcd.ramp_read_item(ramp_name, market, pd.to_datetime(date), use_columns=True)
#     return data
#
#
# # map ramp read from dict to df
# def get_ramp_read(data):
#     return pd.DataFrame.from_dict(data, orient="index")
#
#
# def build_fx_delta_adapter() -> ProductConnectorAdapter:
#
#     def get_delta_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/fx/delta.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total": "Risk", "Total (diff)": "dRisk"})
#         data = data[data["Underlying"] != "EUR"]
#
#         g10_list = ["EUR", "USD", "GBP", "AUD", "CAD", "JPY", "NZD", "CHF", "NOK", "SEK"]
#         emea_list = ["ILS", "PLN", "CZK", "HUF", "DKK", "RON", "RUB", "HRK", "ISK", "KWD", "AED", "SAR", "QAR", "UAH", "OMR",
#                      "TND", "ZAR", "GHS", "ZMW", "XRH", "EGP", "TRY"]
#         latam_list = ["MXN", "CLP", "PEN", "BRL", "BRO", "ARO", "ARS", "COO", "COP", "BRU"]
#         asia_list = ["HKD", "SGD", "MYO", "MYR", "CNH", "CNO", "CNY", "IDO", "IDR", "THB", "THO", "TWD", "TWO", "VND", "VNO", "KRO", "KRW", "PHP", "INO", "INR"]
#         precious_list = ["XAG", "XAU", "XPD", "XPT", "XRH"]
#
#         data["Group"] = np.select(
#             [
#                 data["Underlying"].isin(g10_list),
#                 data["Underlying"].isin(emea_list),
#                 data["Underlying"].isin(latam_list),
#                 data["Underlying"].isin(asia_list),
#                 data["Underlying"].isin(precious_list),
#             ],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e6, data["dRisk"] / 1e6
#         result = data.loc[:, list(FX_DELTA_RISK)]
#
#         return exact_frame(result, columns=FX_DELTA_RISK, label="FX Delta risk")
#
#     def get_delta_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         market_open = get_ramp_read(run_async(get_ramp_read_dict("SPOT FX RATES", "OFFICIAL OPEN", market_date)))
#         market_open_table = pd.DataFrame((market_open.loc["spot_fx_rates.currency_pair", 0],
#                                           market_open.loc["spot_fx_rates.bid", 0],
#                                           market_open.loc["spot_fx_rates.ask", 0])).T.rename(columns={0: "Underlying", 1: "Open Bid", 2: "Open Offer"})
#         market_open_table["Open"] = (market_open_table["Open Bid"] + market_open_table["Open Offer"]) / 2
#         market_open_table["Underlying"] = market_open_table["Underlying"].str.replace("_", "")
#         market_open_table = market_open_table.set_index("Underlying")
#         market_open_table.loc["EUREUR"] = [1.0, 1.0, 1.0]
#
#         eur_usd = market_open_table.loc["EURUSD"]
#         market_move = pd.DataFrame(columns=["Open Bid", "Open Offer", "Open"])
#         target_pair = f"EUR{underlying}"
#
#         try:
#             if target_pair in market_open_table.index:
#                 market_move.loc[underlying] = market_open_table.loc[target_pair]
#
#             else:
#                 pair_a = f"{underlying}USD"
#                 pair_b = f"USD{underlying}"
#
#                 if pair_a in market_open_table.index:
#                     val_a = market_open_table.loc[pair_a]
#                     market_move.loc[underlying] = eur_usd / val_a
#
#                 elif pair_b in market_open_table.index:
#                     val_b = market_open_table.loc[pair_b]
#                     market_move.loc[underlying] = eur_usd * val_b
#         except:
#             market_move.loc[underlying] = [0, 0, 0]
#
#         market_move = market_move.reset_index().rename(columns={"index": "Underlying"})
#         result = market_move.loc[:, list(FX_DELTA_OPEN)]
#
#         return exact_frame(result, columns=FX_DELTA_OPEN, label="FX Delta Open")
#
#     def get_delta_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         market_close = get_ramp_read(run_async(get_ramp_read_dict("SPOT FX RATES", market_status, market_date)))
#         market_close_table = pd.DataFrame((market_close.loc["spot_fx_rates.currency_pair", 0],
#                                            market_close.loc["spot_fx_rates.bid", 0],
#                                            market_close.loc["spot_fx_rates.ask", 0])).T.rename(columns={0: "Underlying", 1: "Current Bid", 2: "Current Offer"})
#         market_close_table["Current"] = (market_close_table["Current Bid"] + market_close_table["Current Offer"]) / 2
#         market_close_table["Underlying"] = market_close_table["Underlying"].str.replace("_", "")
#         market_close_table = market_close_table.set_index("Underlying")
#         market_close_table.loc["EUREUR"] = [1.0, 1.0, 1.0]
#
#         eur_usd = market_close_table.loc["EURUSD"]
#         market_move = pd.DataFrame(columns=["Current Bid", "Current Offer", "Current"])
#         target_pair = f"EUR{underlying}"
#
#         try:
#             if target_pair in market_close_table.index:
#                 market_move.loc[underlying] = market_close_table.loc[target_pair]
#
#             else:
#                 pair_a = f"{underlying}USD"
#                 pair_b = f"USD{underlying}"
#
#                 if pair_a in market_close_table.index:
#                     val_a = market_close_table.loc[pair_a]
#                     market_move.loc[underlying] = eur_usd / val_a
#
#                 elif pair_b in market_close_table.index:
#                     val_b = market_close_table.loc[pair_b]
#                     market_move.loc[underlying] = eur_usd * val_b
#         except:
#             market_move.loc[underlying] = [0, 0, 0]
#
#         market_move = market_move.reset_index().rename(columns={"index": "Underlying"})
#         result = market_move.loc[:, list(FX_DELTA_CURRENT)]
#
#         return exact_frame(result, columns=FX_DELTA_CURRENT, label="FX Delta current")
#
#     return ProductConnectorAdapter(
#         risk=get_delta_risk,
#         market_open=get_delta_open,
#         market_status=get_delta_current,
#     )
#
#
# def build_fx_gamma_adapter() -> ProductConnectorAdapter:
#
#     def get_gamma_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/fx/gamma.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total": "Risk", "Total (diff)": "dRisk"})
#
#         g10_list = ["AUDCHF", "AUDJPY", "AUDUSD", "CHFJPY", "EURAUD", "EURCHF", "EURGBP", "EURJPY", "EURUSD", "GBPAUD", "GBPCHF", "GBPJPY",
#                     "GBPUSD", "USDCHF", "USDJPY", "USDCAD", "EURNOK", "EURSEK"]
#         emea_list = ["EURHUF", "USDTRY"]
#         latam_list = ["USDBRL", "USDMXN"]
#         asia_list = ["AUDCNH", "AUDCNO", "AUDHKD", "AUDKRO", "AUDKRW", "CHFCNH", "CHFCNO", "CHFKHD", "CHFKRO", "CHFKRW",
#                      "CNOCNH", "EURCNH", "EURCNO", "EURHKD", "EURKRO", "EURKRW", "GBPCNH", "GBPCNO", "GBPHKD", "GBPKRO",
#                      "GBPKRW", "HKDJPY", "HKDKRO", "HKDKRW", "JPYCNO", "KROCNO", "KROKRW", "USDCNH", "USDCNO", "USDHKD", "USDKRO", "USDKRW", "USDINR", "USDTHB"]
#         precious_list = ["XAUUSD"]
#
#         data["Group"] = np.select(
#             [
#                 data["Underlying"].isin(g10_list),
#                 data["Underlying"].isin(emea_list),
#                 data["Underlying"].isin(latam_list),
#                 data["Underlying"].isin(asia_list),
#                 data["Underlying"].isin(precious_list),
#             ],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e6, data["dRisk"] / 1e6
#         result = data.loc[:, list(FX_GAMMA_RISK)]
#
#         return exact_frame(result, columns=FX_GAMMA_RISK, label="FX Gamma risk")
#
#     def get_gamma_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         market_open = get_ramp_read(run_async(get_ramp_read_dict("SPOT FX RATES", "OFFICIAL OPEN", market_date)))
#         market_open_table = pd.DataFrame((market_open.loc["spot_fx_rates.currency_pair", 0],
#                                           market_open.loc["spot_fx_rates.bid", 0],
#                                           market_open.loc["spot_fx_rates.ask", 0])).T.rename(columns={0: "Underlying", 1: "Open Bid", 2: "Open Offer"})
#         market_open_table["Open"] = (market_open_table["Open Bid"] + market_open_table["Open Offer"]) / 2
#         market_open_table["Underlying"] = market_open_table["Underlying"].str.replace("_", "")
#         market_open_table = market_open_table.set_index("Underlying")
#         market_open_table.loc["EUREUR"] = [1.0, 1.0, 1.0]
#
#         market_move = pd.DataFrame(columns=["Open Bid", "Open Offer", "Open"])
#
#         try:
#             if underlying in market_open_table.index:
#                 market_move.loc[underlying] = market_open_table.loc[underlying]
#
#             else:
#                 pair_a = f"EUR{underlying[:3]}"
#                 pair_b = f"EUR{underlying[3:]}"
#
#                 if pair_a in market_open_table.index:
#                     if pair_b in market_open_table.index:
#                         val_a = market_open_table.loc[pair_a]
#                         val_b = market_open_table.loc[pair_b]
#
#                         if val_a != 0:
#                             market_move.loc[underlying] = val_b / val_a
#                         else:
#                             market_move.loc[underlying] = 0
#         except:
#             market_move.loc[underlying] = [0, 0, 0]
#
#         market_move = market_move.reset_index().rename(columns={"index": "Underlying"})
#         result = market_move.loc[:, list(FX_GAMMA_OPEN)]
#
#         return exact_frame(result, columns=FX_GAMMA_OPEN, label="FX Gamma Open")
#
#     def get_gamma_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         market_close = get_ramp_read(run_async(get_ramp_read_dict("SPOT FX RATES", market_status, market_date)))
#         market_close_table = pd.DataFrame((market_close.loc["spot_fx_rates.currency_pair", 0],
#                                            market_close.loc["spot_fx_rates.bid", 0],
#                                            market_close.loc["spot_fx_rates.ask", 0])).T.rename(columns={0: "Underlying", 1: "Current Bid", 2: "Current Offer"})
#         market_close_table["Current"] = (market_close_table["Current Bid"] + market_close_table["Current Offer"]) / 2
#         market_close_table["Underlying"] = market_close_table["Underlying"].str.replace("_", "")
#         market_close_table = market_close_table.set_index("Underlying")
#         market_close_table.loc["EUREUR"] = [1.0, 1.0, 1.0]
#
#         market_move = pd.DataFrame(columns=["Current Bid", "Current Offer", "Current"])
#
#         try:
#             if underlying in market_close_table.index:
#                 market_move.loc[underlying] = market_close_table.loc[underlying]
#
#             else:
#                 pair_a = f"EUR{underlying[:3]}"
#                 pair_b = f"EUR{underlying[3:]}"
#
#                 if pair_a in market_close_table.index:
#
#                     if pair_b in market_close_table.index:
#                         val_a = market_close_table.loc[pair_a]
#                         val_b = market_close_table.loc[pair_b]
#
#                         if val_a != 0:
#                             market_move.loc[underlying] = val_b / val_a
#                         else:
#                             market_move.loc[underlying] = 0
#         except:
#             market_move.loc[underlying] = [0, 0, 0]
#
#         market_move = market_move.reset_index().rename(columns={"index": "Underlying"})
#         result = market_move.loc[:, list(FX_GAMMA_CURRENT)]
#
#         return exact_frame(result, columns=FX_GAMMA_CURRENT, label="FX Gamma current")
#
#     return ProductConnectorAdapter(
#         risk=get_gamma_risk,
#         market_open=get_gamma_open,
#         market_status=get_gamma_current,
#     )
#
#
# def build_fx_vega_adapter() -> ProductConnectorAdapter:
#
#     def get_vega_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/fx/vega.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total": "Risk", "Total (diff)": "dRisk", "Tnr (Sw)": "Tenor Swap"})
#
#         g10_list = ["AUDUSD", "EURCHF", "EURGBP", "EURSEK", "EURUSD", "EURAUD", "EURNOK", "GBPUSD", "NZDUSD", "USDCHF", "USDCAD", "USDJPY", "USDNOK", "USDSEK"]
#         emea_list = ["EURHUF", "USDHUF", "USDILS", "USDPLN", "USDRUB", "USDTRY", "USDZAR", "USDSAR", "USDAED", "USDQAR"]
#         latam_list = ["USDBRO", "USDBRL", "USDBRU", "USDMXN"]
#         asia_list = ["EURTHK", "USDHKD", "USDIDO", "USDIDR", "USDTHO", "USDTHB", "USDCNO", "USDCNY", "USDCNH", "USDKRO", "USDKRW", "USDINO", "USDINR",
#                      "USDSGD", "USDPHP", "USDTWO"]
#         precious_list = ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"]
#
#         data["Group"] = np.select(
#             [
#                 data["Underlying"].isin(g10_list),
#                 data["Underlying"].isin(emea_list),
#                 data["Underlying"].isin(latam_list),
#                 data["Underlying"].isin(asia_list),
#                 data["Underlying"].isin(precious_list),
#             ],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(FX_VEGA_RISK)]
#
#         return exact_frame(result, columns=FX_VEGA_RISK, label="FX Vega risk")
#
#     def get_vega_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         if market_status == "LIVE":
#             market_open = get_ramp_read(run_async(get_ramp_read_dict(f"FXO {underlying} VOL MKT VOLS", "OFFICIAL OPEN", market_date)))
#         else:
#             market_open = get_ramp_read(run_async(get_ramp_read_dict(f"FXO {underlying} VOL MKT VOLS", "OFFICIAL FXVOL 1DCUT ROLL", market_date)))
#         market_open_table = pd.DataFrame((market_open.loc["fxo_" + underlying.lower() + "_vol_mkt_vols_vols1.tenor_list", 0],
#                                           market_open.loc["fxo_" + underlying.lower() + "_vol_mkt_vols_vols1.atm_list", 0])).T.rename(columns={0: "Tenor Swap", 1: "Open"})
#
#         market_open_table["Tenor Swap Order"] = market_open_table["Tenor Swap"].map(
#             {t: i for i, t in enumerate(market_open_table["Tenor Swap"].unique())}
#         )
#         market_open_table["Underlying"] = underlying
#         result = market_open_table.loc[:, list(FX_VEGA_OPEN)]
#
#         return exact_frame(result, columns=FX_VEGA_OPEN, label="FX Vega open")
#
#     def get_vega_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         if market_status == "LIVE":
#             market_close = get_ramp_read(run_async(get_ramp_read_dict(f"FXO {underlying} VOL MKT VOLS", "LIVE FXVOL ROLLBACK DATA", market_date)))
#         else:
#             market_close = get_ramp_read(run_async(get_ramp_read_dict(f"FXO {underlying} VOL MKT VOLS", market_status, market_date)))
#         market_close_table = pd.DataFrame((market_close.loc["fxo_" + underlying.lower() + "_vol_mkt_vols_vols1.tenor_list", 0],
#                                            market_close.loc["fxo_" + underlying.lower() + "_vol_mkt_vols_vols1.atm_list", 0])).T.rename(columns={0: "Tenor Swap", 1: "Current"})
#
#         market_close_table["Tenor Swap Order"] = market_close_table["Tenor Swap"].map(
#             {t: i for i, t in enumerate(market_close_table["Tenor Swap"].unique())}
#         )
#         market_close_table["Underlying"] = underlying
#         result = market_close_table.loc[:, list(FX_VEGA_CURRENT)]
#
#         return exact_frame(result, columns=FX_VEGA_CURRENT, label="FX Vega current")
#
#     return ProductConnectorAdapter(
#         risk=get_vega_risk,
#         market_open=get_vega_open,
#         market_status=get_vega_current,
#     )
#
#
# __all__ = [
#     "build_fx_delta_adapter",
#     "build_fx_vega_adapter",
# ]


# === ACTIVE VALIDATED CONTRACT (CSV RUNTIME IS SELECTED IN FEEDS) ============
# Strict active FX Delta, Gamma, and Vega adapter contracts.

import pandas as pd

from core.s01_schema import TENOR_SWAP, TENOR_SWAP_ORDER
from core.s02_pipeline import ProductConnectorAdapter
from .s01_common import MarketSource, RiskSource, exact_frame, market_frame


FX_DELTA_RISK = ("Underlying", "Portfolio", "Group", "Risk", "dRisk")
FX_DELTA_OPEN = ("Underlying", "Open")
FX_DELTA_CURRENT = ("Underlying", "Current")

FX_GAMMA_RISK = ("Underlying", "Portfolio", "Group", "Risk", "dRisk")
FX_GAMMA_OPEN = ("Underlying", "Open")
FX_GAMMA_CURRENT = ("Underlying", "Current")

FX_VEGA_RISK = (
    "Underlying",
    TENOR_SWAP,
    "Portfolio",
    "Group",
    "Risk",
    "dRisk",
)
FX_VEGA_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
FX_VEGA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")


def _scalar_adapter(
    *,
    risk_source: RiskSource,
    open_source: MarketSource,
    current_source: MarketSource,
    risk_columns: tuple[str, ...],
    open_columns: tuple[str, ...],
    current_columns: tuple[str, ...],
    label: str,
) -> ProductConnectorAdapter:
    def get_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
        return exact_frame(
            risk_source(risk_date), columns=risk_columns, label=f"{label} risk"
        )

    def get_open(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            open_source,
            market_date,
            underlying,
            market_status=market_status,
            columns=open_columns,
            label=f"{label} Open",
        )

    def get_current(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            current_source,
            market_date,
            underlying,
            market_status=market_status,
            columns=current_columns,
            label=f"{label} current",
            attach_status=True,
        )

    return ProductConnectorAdapter(
        risk=get_risk,
        market_open=get_open,
        market_status=get_current,
    )


def build_fx_adapters(
    *,
    delta_risk: RiskSource,
    delta_open: MarketSource,
    delta_current: MarketSource,
    gamma_risk: RiskSource,
    gamma_open: MarketSource,
    gamma_current: MarketSource,
    vega_risk: RiskSource,
    vega_open: MarketSource,
    vega_current: MarketSource,
) -> dict[str, ProductConnectorAdapter]:
    """Bind fixture or site-owned sources to the three FX contracts."""

    return {
        "fx/delta": _scalar_adapter(
            risk_source=delta_risk,
            open_source=delta_open,
            current_source=delta_current,
            risk_columns=FX_DELTA_RISK,
            open_columns=FX_DELTA_OPEN,
            current_columns=FX_DELTA_CURRENT,
            label="FX Delta",
        ),
        "fx/gamma": _scalar_adapter(
            risk_source=gamma_risk,
            open_source=gamma_open,
            current_source=gamma_current,
            risk_columns=FX_GAMMA_RISK,
            open_columns=FX_GAMMA_OPEN,
            current_columns=FX_GAMMA_CURRENT,
            label="FX Gamma",
        ),
        "fx/vega": _scalar_adapter(
            risk_source=vega_risk,
            open_source=vega_open,
            current_source=vega_current,
            risk_columns=FX_VEGA_RISK,
            open_columns=FX_VEGA_OPEN,
            current_columns=FX_VEGA_CURRENT,
            label="FX Vega",
        ),
    }


__all__ = [
    "FX_DELTA_CURRENT",
    "FX_DELTA_OPEN",
    "FX_DELTA_RISK",
    "FX_GAMMA_CURRENT",
    "FX_GAMMA_OPEN",
    "FX_GAMMA_RISK",
    "FX_VEGA_CURRENT",
    "FX_VEGA_OPEN",
    "FX_VEGA_RISK",
    "build_fx_adapters",
]
