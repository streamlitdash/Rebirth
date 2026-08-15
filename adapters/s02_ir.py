"""IR adapter contracts with inline, comment-only recovered connectors."""

from __future__ import annotations

# === REAL IR CONNECTORS (COMMENTED OUT) ======================================
# SWITCH TO REAL: uncomment the required private imports/builders below, then
# uncomment their REAL registration in ``feeds/s01_sources.py`` and comment the
# adjacent CSV fallback registration. All recovered IR builders remain here.
# Leave the recovered ``from __future__ import annotations`` line commented;
# this module already enables it above so the inline switch remains compilable.
# === END SWITCH INSTRUCTIONS =================================================
# """Working IR Delta curve and IR DeltaVega surface adapter examples."""
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
# # -------------------------------------------LIBRARIES--------------------------------------------#
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
# import xva.rpmlib.ver as rplm
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
# from xva.rpmlib.data_attributes import DataAttributes
# from xva.rpmlib.mercury_xva_api import MercuryXvaApi
# from xva.rpmlib.pal.pal_enums import PalServer, XvaScope, XvaCode
# from xva.rpmlib.pal.pal_credit_data import PalCreditData
#
#
# IR_DELTA_RISK = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk",)
# IR_GAMMA_RISK = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk",)
# IR_DELTA_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
# IR_DELTA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")
#
# IR_DELTAVEGA_RISK = ("Underlying", TENOR_SWAP, TENOR_OPTION, "Portfolio", "Group", "Risk", "dRisk",)
# IR_DELTAVEGA_OPEN = ("Underlying", TENOR_SWAP, TENOR_OPTION, TENOR_SWAP_ORDER, TENOR_OPTION_ORDER, "Open")
# IR_DELTAVEGA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_OPTION, TENOR_SWAP_ORDER, TENOR_OPTION_ORDER, "Current")
#
# IR_XCCY_RISK = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk",)
# IR_XCCY_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
# IR_XCCY_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")
#
# IR_XCCYVEGA_RISK = ("Underlying", TENOR_SWAP, TENOR_OPTION, "Portfolio", "Group", "Risk", "dRisk",)
# IR_XCCYVEGA_OPEN = ("Underlying", TENOR_SWAP, TENOR_OPTION, TENOR_SWAP_ORDER, TENOR_OPTION_ORDER, "Open")
# IR_XCCYVEGA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_OPTION, TENOR_SWAP_ORDER, TENOR_OPTION_ORDER, "Current")
#
# IR_INFLATION_RISK = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk",)
# IR_INFLATION_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
# IR_INFLATION_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")
#
# IR_INFLATIONVEGA_RISK = ("Underlying", TENOR_SWAP, TENOR_OPTION, "Portfolio", "Group", "Risk", "dRisk",)
# IR_INFLATIONVEGA_OPEN = ("Underlying", TENOR_SWAP, TENOR_OPTION, TENOR_SWAP_ORDER, TENOR_OPTION_ORDER, "Open")
# IR_INFLATIONVEGA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_OPTION, TENOR_SWAP_ORDER, TENOR_OPTION_ORDER, "Current")
#
# IR_BASIS_RISK = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk",)
# IR_BASIS_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
# IR_BASIS_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")
#
# IR_BOND_RISK = ("Underlying", TENOR_SWAP, "Portfolio", "Group", "Risk", "dRisk",)
# IR_BOND_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
# IR_BOND_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")
#
#
# # get ramp data as dictionary via async
# async def get_ramp_read_dict(ramp_name, market, date):
#     if market == "Live":
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
# def build_ir_delta_adapter() -> ProductConnectorAdapter:
#
#     def get_delta_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/delta.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap"})
#
#         g10_list = ["EUR", "USD", "GBP", "AUD", "CAD", "JPY", "NZD", "CHF", "NOK", "SEK"]
#         emea_list = ["ILS", "PLN", "CZK", "HUF", "DKK", "RON", "RUB", "HRK", "ISK", "KWD", "AED", "SAR", "QAR", "UAH", "OMR",
#                      "TND", "ZAR", "GHS", "ZMW", "XRH", "EGP", "TRY"]
#         latam_list = ["MXN", "CLP", "PEN", "BRL", "BRO", "ARO", "ARS", "COO", "COP", "BRU"]
#         asia_list = ["HKD", "SGD", "MYO", "MYR", "CNH", "CNO", "CNY", "IDO", "IDR", "THB", "THO", "TWD", "TWO", "VND", "VNO", "KRO", "KRW", "PHP", "INO", "INR"]
#         precious_list = ["XAG", "XAU", "XPD", "XPT", "XRH"]
#
#         data["Group"] = np.select(
#             [data["Underlying"].isin(g10_list),
#              data["Underlying"].isin(emea_list),
#              data["Underlying"].isin(latam_list),
#              data["Underlying"].isin(asia_list),
#              data["Underlying"].isin(precious_list)],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_DELTA_RISK)]
#
#         return exact_frame(result, columns=IR_DELTA_RISK, label="IR Delta risk")
#
#     def get_gamma_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/gamma.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap"})
#
#         g10_list = ["EUR", "USD", "GBP", "AUD", "CAD", "JPY", "NZD", "CHF", "NOK", "SEK"]
#         emea_list = ["ILS", "PLN", "CZK", "HUF", "DKK", "RON", "RUB", "HRK", "ISK", "KWD", "AED", "SAR", "QAR", "UAH", "OMR",
#                      "TND", "ZAR", "GHS", "ZMW", "XRH", "EGP", "TRY"]
#         latam_list = ["MXN", "CLP", "PEN", "BRL", "BRO", "ARO", "ARS", "COO", "COP", "BRU"]
#         asia_list = ["HKD", "SGD", "MYO", "MYR", "CNH", "CNO", "CNY", "IDO", "IDR", "THB", "THO", "TWD", "TWO", "VND", "VNO", "KRO", "KRW", "PHP", "INO", "INR"]
#         precious_list = ["XAG", "XAU", "XPD", "XPT", "XRH"]
#
#         data["Group"] = np.select(
#             [data["Underlying"].isin(g10_list),
#              data["Underlying"].isin(emea_list),
#              data["Underlying"].isin(latam_list),
#              data["Underlying"].isin(asia_list),
#              data["Underlying"].isin(precious_list)],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_GAMMA_RISK)]
#
#         return exact_frame(result, columns=IR_GAMMA_RISK, label="IR Gamma risk")
#
#
#     def get_delta_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         if underlying == "BRU":
#             ycRamp = "USD MESA BRODI"
#         else:
#             temp = run_async(get_ramp_read_dict(underlying + " MESA META DATA", "OFFICIAL", market_date))
#             yc = temp.get(list(temp.keys())[list(temp.values()).index("YIELDCURVE")]).split(".")[0] + ".string_value"
#             ycRamp = yc.split(" ")[0] + " MESA"
#             for i in range(1, len(yc.split(" "))):
#                 ycRamp = ycRamp + " " + yc.split(" ")[i]
#
#         market_open = run_async(get_ramp_read_dict(ycRamp, "OFFICIAL OPEN", market_date))
#         market_open_table = pd.DataFrame([market_open[ycRamp.lower().replace(" ", "_") + "_rates.tenor"],
#                                           market_open[ycRamp.lower().replace(" ", "_") + "_rates.rate"],
#                                           market_open[ycRamp.lower().replace(" ", "_") + "_rates.inst_type"],
#                                           market_open[ycRamp.lower().replace(" ", "_") + "_rates.rate_adj"],
#                                          ]).T.rename(columns={0: "Tenor Swap", 1: "Open", 2: "Instrument", 3: "Adjustment"})
#
#         market_open_table["Open"] = market_open_table["Open"] * 10000
#
#         mask = market_open_table["Instrument"] == "FUT"
#         market_open_table.loc[mask, "Open"] = (10000 - market_open_table.loc[mask, "Open"] + market_open_table.loc[mask, "Adjustment"])
#         market_open_table["Tenor Swap"] = market_open_table["Tenor Swap"].str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False)
#
#         market_open_table["Underlying"] = underlying
#         market_open_table["Tenor Swap Order"] = range(len(market_open_table["Tenor Swap"]))
#
#         result = market_open_table.loc[:, list(IR_DELTA_OPEN)]
#
#         return exact_frame(result, columns=IR_DELTA_OPEN, label="IR Delta Open")
#
#
#     def get_delta_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         if underlying == "BRU":
#             ycRamp = "USD MESA BRODI"
#         else:
#             temp = run_async(get_ramp_read_dict(underlying + " MESA META DATA", "OFFICIAL", market_date))
#             yc = temp.get(list(temp.keys())[list(temp.values()).index("YIELDCURVE")]).split(".")[0] + ".string_value"
#             ycRamp = yc.split(" ")[0] + " MESA"
#             for i in range(1, len(yc.split(" "))):
#                 ycRamp = ycRamp + " " + yc.split(" ")[i]
#
#         market_close = run_async(get_ramp_read_dict(ycRamp, market_status, market_date))
#         market_close_table = pd.DataFrame([market_close[ycRamp.lower().replace(" ", "_") + "_rates.tenor"],
#                                            market_close[ycRamp.lower().replace(" ", "_") + "_rates.rate"],
#                                            market_close[ycRamp.lower().replace(" ", "_") + "_rates.inst_type"],
#                                            market_close[ycRamp.lower().replace(" ", "_") + "_rates.rate_adj"],
#                                           ]).T.rename(columns={0: "Tenor Swap", 1: "Current", 2: "Instrument", 3: "Adjustment"})
#
#         market_close_table["Current"] = market_close_table["Current"] * 10000
#
#         mask = market_close_table["Instrument"] == "FUT"
#         market_close_table.loc[mask, "Current"] = (10000 - market_close_table.loc[mask, "Current"] + market_close_table.loc[mask, "Adjustment"])
#         market_close_table["Tenor Swap"] = market_close_table["Tenor Swap"].str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False)
#
#         market_close_table["Underlying"] = underlying
#         market_close_table["Tenor Swap Order"] = range(len(market_close_table["Tenor Swap"]))
#
#         result = market_close_table.loc[:, list(IR_DELTA_CURRENT)]
#
#         return exact_frame(result, columns=IR_DELTA_CURRENT, label="IR Delta current")
#
#     return ProductConnectorAdapter(
#         risk=get_delta_risk,
#         market_open=get_delta_open,
#         market_status=get_delta_current,
#     ), ProductConnectorAdapter(
#         risk=get_gamma_risk,
#         market_open=get_delta_open,
#         market_status=get_delta_current,
#     )
#
#
# def build_ir_deltavega_adapter() -> ProductConnectorAdapter:
#
#     def get_deltavega_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/deltavega.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap","Tnr (Opt)":"Tenor Option"})
#
#         mask = data["Underlying"].astype(str).str.len() == 3
#         suffix = pd.Series(" IR SABRAF VOL", index=data.index)
#         suffix.loc[data["Underlying"] == "EUR"] = " IR SABRAF VOL SR"
#         suffix.loc[data["Underlying"] == "GBP"] = " IR SABRAF VOL"
#         suffix.loc[data["Underlying"] == "USD"] = " IR SABRAF VOL"
#         data.loc[mask, "Underlying"] = (data.loc[mask, "Underlying"] + suffix.loc[mask])
#
#         data["Underlying_ccy"] = data["Underlying"].str[:3]
#
#         g10_list = ["EUR", "USD", "GBP", "AUD", "CAD", "JPY", "NZD", "CHF", "NOK", "SEK"]
#         emea_list = ["ILS", "PLN", "CZK", "HUF", "DKK", "RON", "RUB", "HRK", "ISK", "KWD", "AED", "SAR", "QAR", "UAH", "OMR", "TND", "ZAR", "GHS", "ZMW", "XRH", "EGP", "TRY"]
#         latam_list = ["MXN", "CLP", "PEN", "BRL", "BRO", "ARO", "ARS", "COO", "COP", "BRU"]
#         asia_list = ["HKD", "SGD", "MYO", "MYR", "CNH", "CNO", "CNY", "IDO", "IDR", "THB", "THO", "TWD", "TWO", "VND", "VNO", "KRO", "KRW", "PHP", "INO", "INR"]
#         precious_list = ["XAG", "XAU", "XPD", "XPT", "XRH"]
#
#         data["Group"] = np.select(
#             [data["Underlying_ccy"].isin(g10_list),
#              data["Underlying_ccy"].isin(emea_list),
#              data["Underlying_ccy"].isin(latam_list),
#              data["Underlying_ccy"].isin(asia_list),
#              data["Underlying_ccy"].isin(precious_list)],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#
#         data = data.groupby(["Underlying", TENOR_SWAP, TENOR_OPTION, "Portfolio", "Group"], as_index=False).sum()
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_DELTAVEGA_RISK)]
#
#         return exact_frame(result, columns=IR_DELTAVEGA_RISK, label="IR DeltaVega risk")
#
#
#     def get_deltavega_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         temp = run_async(get_ramp_read_dict(underlying, "OFFICIAL OPEN", market_date))
#         temp_table = pd.DataFrame(index=temp[underlying.lower().replace(" ", "_") + ".exercise_date_list"],
#                                   columns=temp[underlying.lower().replace(" ", "_") + ".maturity_list"])
#
#         k = 0
#         for i in temp_table.index:
#             for j in temp_table.columns:
#                 temp_table.loc[i,j] = temp[underlying.lower().replace(" ", "_") + ".sigma_normal"][k]
#                 k = k + 1
#
#         market_open_table = pd.DataFrame(temp_table.stack().reset_index())
#         market_open_table.columns = ["Tenor Option", "Tenor Swap", "Open"]
#         market_open_table["Open"] = market_open_table["Open"] * 100
#         market_open_table["Underlying"] = underlying
#
#         if market_open_table.duplicated(["Tenor Option", "Tenor Swap"]).any():
#             market_open_table = market_open_table.drop_duplicates(["Tenor Option", "Tenor Swap"], keep="first")
#         market_open_table = market_open_table.reset_index(drop=True)
#         market_open_table["Tenor Swap Order"] = market_open_table["Tenor Swap"].map(
#             {t: i for i, t in enumerate(market_open_table["Tenor Swap"].unique())}
#         )
#         market_open_table["Tenor Option Order"] = market_open_table["Tenor Option"].map(
#             {t: i for i, t in enumerate(market_open_table["Tenor Option"].unique())}
#         )
#
#         result = market_open_table.loc[:, list(IR_DELTAVEGA_OPEN)]
#
#         return exact_frame(result, columns=IR_DELTAVEGA_OPEN, label="IR DeltaVega open")
#
#
#     def get_deltavega_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         temp = run_async(get_ramp_read_dict(underlying, market_status, market_date))
#         temp_table = pd.DataFrame(index=temp[underlying.lower().replace(" ", "_") + ".exercise_date_list"],
#                                   columns=temp[underlying.lower().replace(" ", "_") + ".maturity_list"])
#
#         k = 0
#         for i in temp_table.index:
#             for j in temp_table.columns:
#                 temp_table.loc[i,j] = temp[underlying.lower().replace(" ", "_") + ".sigma_normal"][k]
#                 k = k + 1
#
#         market_close_table = pd.DataFrame(temp_table.stack().reset_index())
#         market_close_table.columns = ["Tenor Option", "Tenor Swap", "Current"]
#         market_close_table["Current"] = market_close_table["Current"] * 100
#         market_close_table["Underlying"] = underlying
#         # Deduplicate any duplicate Tenor Swap + Tenor Option combinations
#         if market_close_table.duplicated(["Tenor Option", "Tenor Swap"]).any():
#             market_close_table = market_close_table.drop_duplicates(["Tenor Option", "Tenor Swap"], keep="first")
#         market_close_table = market_close_table.reset_index(drop=True)
#         market_close_table["Tenor Swap Order"] = market_close_table["Tenor Swap"].map(
#             {t: i for i, t in enumerate(market_close_table["Tenor Swap"].unique())}
#         )
#         market_close_table["Tenor Option Order"] = market_close_table["Tenor Option"].map(
#             {t: i for i, t in enumerate(market_close_table["Tenor Option"].unique())}
#         )
#
#         result = market_close_table.loc[:, list(IR_DELTAVEGA_CURRENT)]
#
#         return exact_frame(result, columns=IR_DELTAVEGA_CURRENT, label="IR DeltaVega current")
#
#     return ProductConnectorAdapter(
#         risk=get_deltavega_risk,
#         market_open=get_deltavega_open,
#         market_status=get_deltavega_current,
#     )
#
#
# def build_ir_xccy_adapter() -> ProductConnectorAdapter:
#
#     def get_xccy_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/basis.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap"})
#         data = data[data["Underlying"].str.contains("XCCY")]
#         data["Underlying"] = data["Underlying"].str.split(" ").str[0]
#
#         g10_list = ["EUR", "USD", "GBP", "AUD", "CAD", "JPY", "NZD", "CHF", "NOK", "SEK"]
#         emea_list = ["ILS", "PLN", "CZK", "HUF", "DKK", "RON", "RUB", "HRK", "ISK", "KWD", "AED", "SAR", "QAR", "UAH", "OMR",
#                      "TND", "ZAR", "GHS", "ZMW", "XRH", "EGP", "TRY"]
#         latam_list = ["MXN", "CLP", "PEN", "BRL", "BRO", "ARO", "ARS", "COO", "COP", "BRU"]
#         asia_list = ["HKD", "SGD", "MYO", "MYR", "CNH", "CNO", "CNY", "IDO", "IDR", "THB", "THO", "TWD", "TWO", "VND", "VNO", "KRO", "KRW", "PHP", "INO", "INR"]
#         precious_list = ["XAG", "XAU", "XPD", "XPT", "XRH"]
#
#         data["Group"] = np.select(
#             [data["Underlying"].isin(g10_list),
#              data["Underlying"].isin(emea_list),
#              data["Underlying"].isin(latam_list),
#              data["Underlying"].isin(asia_list),
#              data["Underlying"].isin(precious_list)],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_XCCY_RISK)]
#
#         return exact_frame(result, columns=IR_XCCY_RISK, label="IR XCCY risk")
#
#
#     def get_xccy_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         xccyname = underlying + " MESA XCCY"
#
#         market_open = run_async(get_ramp_read_dict(xccyname, "OFFICIAL OPEN", market_date))
#         market_open_table = pd.DataFrame([market_open[xccyname.lower().replace(" ", "_") + "_rates.tenor"],
#                                           market_open[xccyname.lower().replace(" ", "_") + "_rates.rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Open"})
#
#         market_open_table["Underlying"] = underlying
#         market_open_table["Open"] = market_open_table["Open"] * 10000
#
#         market_open_table["Tenor Swap"] = market_open_table["Tenor Swap"].str.replace("BOND_TENOR{", "", regex=False).str.replace("}", "", regex=False).str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False).str.extract(r"\*(.*)", expand=False).fillna(market_open_table["Tenor Swap"])
#         market_open_table["Tenor Swap Order"] = range(len(market_open_table["Tenor Swap"]))
#
#         result = market_open_table.loc[:, list(IR_XCCY_OPEN)]
#
#         return exact_frame(result, columns=IR_XCCY_OPEN, label="IR XCCY open")
#
#
#     def get_xccy_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         xccyname = underlying + " MESA XCCY"
#
#         market_close = run_async(get_ramp_read_dict(xccyname, market_status, market_date))
#         market_close_table = pd.DataFrame([market_close[xccyname.lower().replace(" ", "_") + "_rates.tenor"],
#                                            market_close[xccyname.lower().replace(" ", "_") + "_rates.rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Current"})
#
#         market_close_table["Underlying"] = underlying
#         market_close_table["Current"] = market_close_table["Current"] * 10000
#
#         market_close_table["Tenor Swap"] = market_close_table["Tenor Swap"].str.replace("BOND_TENOR{", "", regex=False).str.replace("}", "", regex=False).str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False).str.extract(r"\*(.*)", expand=False).fillna(market_close_table["Tenor Swap"])
#         market_close_table["Tenor Swap Order"] = range(len(market_close_table["Tenor Swap"]))
#
#         result = market_close_table.loc[:, list(IR_XCCY_CURRENT)]
#
#         return exact_frame(result, columns=IR_XCCY_CURRENT, label="IR XCCY current")
#
#     return ProductConnectorAdapter(
#         risk=get_xccy_risk,
#         market_open=get_xccy_open,
#         market_status=get_xccy_current,
#     )
#
#
# def build_ir_basis_adapter() -> ProductConnectorAdapter:
#
#     def get_basis_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/basis.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap"})
#         data = data[~data["Underlying"].str.contains("XCCY")]
#         data = data[(~data["Underlying"].isin(["EUR UNCOLLAT FUNDING", "USD OIS AVG"])) & (~data["Underlying"].str.contains("LCH", na=False)) &
#                     (~data["Underlying"].str.contains("FND", na=False))]
#         data["Underlying_ccy"] = data["Underlying"].str.split().str[0]
#
#         g10_list = ["EUR", "USD", "GBP", "AUD", "CAD", "JPY", "NZD", "CHF", "NOK", "SEK"]
#         emea_list = ["ILS", "PLN", "CZK", "HUF", "DKK", "RON", "RUB", "HRK", "ISK", "KWD", "AED", "SAR", "QAR", "UAH", "OMR",
#                      "TND", "ZAR", "GHS", "ZMW", "XRH", "EGP", "TRY"]
#         latam_list = ["MXN", "CLP", "PEN", "BRL", "BRO", "ARO", "ARS", "COO", "COP", "BRU"]
#         asia_list = ["HKD", "SGD", "MYO", "MYR", "CNH", "CNO", "CNY", "IDO", "IDR", "THB", "THO", "TWD", "TWO", "VND", "VNO", "KRO", "KRW", "PHP", "INO", "INR"]
#         precious_list = ["XAG", "XAU", "XPD", "XPT", "XRH"]
#
#         data["Group"] = np.select(
#             [data["Underlying_ccy"].isin(g10_list),
#              data["Underlying_ccy"].isin(emea_list),
#              data["Underlying_ccy"].isin(latam_list),
#              data["Underlying_ccy"].isin(asia_list),
#              data["Underlying_ccy"].isin(precious_list)],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_BASIS_RISK)]
#
#         return exact_frame(result, columns=IR_BASIS_RISK, label="IR Basis risk")
#
#
#     def get_basis_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         basisname = underlying.split(" ")[0] + " MESA " + underlying.split(" ")[1]
#         for i in range(2, len(underlying.split(" "))):
#             basisname = basisname + " " + underlying.split(" ")[i]
#
#         market_open = run_async(get_ramp_read_dict(basisname, "OFFICIAL OPEN", market_date))
#         market_open_table = pd.DataFrame([market_open[basisname.lower().replace(" ", "_") + "_rates.tenor"],
#                                           market_open[basisname.lower().replace(" ", "_") + "_rates.rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Open"})
#
#         market_open_table["Underlying"] = underlying
#         market_open_table["Open"] = market_open_table["Open"] * 10000
#
#         market_open_table["Tenor Swap"] = market_open_table["Tenor Swap"].str.replace("BOND_TENOR{", "", regex=False).str.replace("}", "", regex=False).str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False).str.extract(r"\*(.*)", expand=False).fillna(market_open_table["Tenor Swap"])
#         market_open_table["Tenor Swap Order"] = range(len(market_open_table["Tenor Swap"]))
#
#         result = market_open_table.loc[:, list(IR_BASIS_OPEN)]
#
#         return exact_frame(result, columns=IR_BASIS_OPEN, label="IR Basis open")
#
#
#     def get_basis_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         basisname = underlying.split(" ")[0] + " MESA " + underlying.split(" ")[1]
#         for i in range(2, len(underlying.split(" "))):
#             basisname = basisname + " " + underlying.split(" ")[i]
#
#         market_close = run_async(get_ramp_read_dict(basisname, market_status, market_date))
#         market_close_table = pd.DataFrame([market_close[basisname.lower().replace(" ", "_") + "_rates.tenor"],
#                                            market_close[basisname.lower().replace(" ", "_") + "_rates.rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Current"})
#
#         market_close_table["Underlying"] = underlying
#         market_close_table["Current"] = market_close_table["Current"] * 10000
#
#         market_close_table["Tenor Swap"] = market_close_table["Tenor Swap"].str.replace("BOND_TENOR{", "", regex=False).str.replace("}", "", regex=False).str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False).str.extract(r"\*(.*)", expand=False).fillna(market_close_table["Tenor Swap"])
#         market_close_table["Tenor Swap Order"] = range(len(market_close_table["Tenor Swap"]))
#
#         result = market_close_table.loc[:, list(IR_BASIS_CURRENT)]
#
#         return exact_frame(result, columns=IR_BASIS_CURRENT, label="IR Basis current")
#
#     return ProductConnectorAdapter(
#         risk=get_basis_risk,
#         market_open=get_basis_open,
#         market_status=get_basis_current,
#     )
#
#
# def build_ir_inflation_adapter() -> ProductConnectorAdapter:
#
#     def get_inflation_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/inflation.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap"})
#
#         data["Group"] = np.where(data["Risk Type"] == "INF ZC Delta (GEAR)", "Delta", "Spread")
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_INFLATION_RISK)]
#
#         return exact_frame(result, columns=IR_INFLATION_RISK, label="IR Inflation risk")
#
#
#     def get_inflation_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         if underlying in ("EUR", "USD", "GBP", "CLP", "AUD"):
#             temp = run_async(get_ramp_read_dict(underlying + " INF MARKET", "OFFICIAL OPEN", market_date))
#
#             tempcurvename = next((s for s in temp[underlying.lower() + "_inf_market.cpi_curves"] if "DELTA" in s), None)
#             if tempcurvename == None:
#                 tempcurvename = temp[underlying.lower() + "_inf_market.cpi_curves"][0]
#             temp = run_async(get_ramp_read_dict(tempcurvename, "OFFICIAL OPEN", market_date))
#             inflationname = temp[tempcurvename.lower().replace(" ", "_") + ".rates"]
#
#         else:
#             if underlying != "UKRPI":
#                 temp = run_async(get_ramp_read_dict("INF " + underlying, "OFFICIAL OPEN", market_date))
#                 inflationname = temp["inf_" + underlying.lower() + ".rates"]
#             else:
#                 inflationname = "UK RPI ZC"
#
#         market_open = run_async(get_ramp_read_dict(inflationname, "OFFICIAL OPEN", market_date))
#
#         try:
#             market_open_table = pd.DataFrame([market_open[inflationname.lower().replace(" ", "_") + ".tenor"],
#                                               market_open[inflationname.lower().replace(" ", "_") + ".rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Open"})
#         except:
#             market_open_table = pd.DataFrame([market_open[inflationname.lower().replace(" ", "_") + ".pay_date"],
#                                               market_open[inflationname.lower().replace(" ", "_") + ".price"]]).T.rename(columns={0: "Tenor Swap", 1: "Open"})
#             market_open_table["Tenor Swap"] = pd.to_datetime(market_open_table["Tenor Swap"], unit="d", origin="1899-12-30").dt.strftime("%d-%b-%Y").str.upper()
#             market_open_table["Tenor Swap"] = pd.to_datetime(market_open_table["Tenor Swap"])
#
#         market_open_table["Underlying"] = underlying
#         market_open_table["Open"] = market_open_table["Open"] * 10000
#         market_open_table["Tenor Swap Order"] = range(len(market_open_table["Tenor Swap"]))
#
#         result = market_open_table.loc[:, list(IR_INFLATION_OPEN)]
#
#         return exact_frame(result, columns=IR_INFLATION_OPEN, label="IR Inflation open")
#
#
#     def get_inflation_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         if underlying in ("EUR", "USD", "GBP", "CLP", "AUD"):
#             temp = run_async(get_ramp_read_dict(underlying + " INF MARKET", "OFFICIAL OPEN", market_date))
#
#             tempcurvename = next((s for s in temp[underlying.lower() + "_inf_market.cpi_curves"] if "DELTA" in s), None)
#             if tempcurvename == None:
#                 tempcurvename = temp[underlying.lower() + "_inf_market.cpi_curves"][0]
#             temp = run_async(get_ramp_read_dict(tempcurvename, "OFFICIAL OPEN", market_date))
#             inflationname = temp[tempcurvename.lower().replace(" ", "_") + ".rates"]
#
#         else:
#             if underlying != "UKRPI":
#                 temp = run_async(get_ramp_read_dict("INF " + underlying, "OFFICIAL OPEN", market_date))
#                 inflationname = temp["inf_" + underlying.lower() + ".rates"]
#             else:
#                 inflationname = "UK RPI ZC"
#
#         market_close = run_async(get_ramp_read_dict(inflationname, market_status, market_date))
#
#         try:
#             market_close_table = pd.DataFrame([market_close[inflationname.lower().replace(" ", "_") + ".tenor"],
#                                                market_close[inflationname.lower().replace(" ", "_") + ".rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Current"})
#         except:
#             market_close_table = pd.DataFrame([market_close[inflationname.lower().replace(" ", "_") + ".pay_date"],
#                                                market_close[inflationname.lower().replace(" ", "_") + ".price"]]).T.rename(columns={0: "Tenor Swap", 1: "Current"})
#             market_close_table["Tenor Swap"] = pd.to_datetime(market_close_table["Tenor Swap"], unit="d", origin="1899-12-30").dt.strftime("%d-%b-%Y").str.upper()
#             market_close_table["Tenor Swap"] = pd.to_datetime(market_close_table["Tenor Swap"])
#
#         market_close_table["Underlying"] = underlying
#         market_close_table["Current"] = market_close_table["Current"] * 10000
#         market_close_table["Tenor Swap Order"] = range(len(market_close_table["Tenor Swap"]))
#
#         result = market_close_table.loc[:, list(IR_INFLATION_CURRENT)]
#
#         return exact_frame(result, columns=IR_INFLATION_CURRENT, label="IR Inflation current")
#
#     return ProductConnectorAdapter(
#         risk=get_inflation_risk,
#         market_open=get_inflation_open,
#         market_status=get_inflation_current,
#     )
#
#
# def build_ir_inflationvega_adapter() -> ProductConnectorAdapter:
#
#     def get_inflationvega_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/inflationvega.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap","Tnr (Opt)":"Tenor Option"})
#
#         data["Group"] = np.where(data["Underlying"].isin(["EUR", "USD", "GBP"]), "Delta", "Spread")
#
#         data = data.groupby(["Underlying", TENOR_SWAP, TENOR_OPTION, "Portfolio", "Group"], as_index=False).sum()
#         data["Tenor Swap"] = "20Y"
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_INFLATIONVEGA_RISK)]
#
#         return exact_frame(result, columns=IR_INFLATIONVEGA_RISK, label="IR InflationVega risk")
#
#
#     def get_inflationvega_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         inflationveganame = underlying + " INFLATION SABRAF VOL"
#         temp = run_async(get_ramp_read_dict(inflationveganame, "OFFICIAL OPEN", market_date))
#         temp_table = pd.DataFrame(index=temp[inflationveganame.lower().replace(" ", "_") + ".exercise_date_list"],
#                                   columns=temp[inflationveganame.lower().replace(" ", "_") + ".maturity_list"])
#
#         k = 0
#         for i in temp_table.index:
#             for j in temp_table.columns:
#                 temp_table.loc[i,j] = temp[inflationveganame.lower().replace(" ", "_") + ".sigma_normal"][k]
#                 k = k + 1
#
#         market_open_table = pd.DataFrame(temp_table.stack().reset_index())
#         market_open_table.columns = ["Tenor Option", "Tenor Swap", "Open"]
#         market_open_table["Open"] = market_open_table["Open"] * 100
#         market_open_table["Underlying"] = underlying
#
#         if market_open_table.duplicated(["Tenor Option", "Tenor Swap"]).any():
#             market_open_table = market_open_table.drop_duplicates(["Tenor Option", "Tenor Swap"], keep="first")
#         market_open_table = market_open_table.reset_index(drop=True)
#         market_open_table["Tenor Swap Order"] = market_open_table["Tenor Swap"].map(
#             {t: i for i, t in enumerate(market_open_table["Tenor Swap"].unique())}
#         )
#         market_open_table["Tenor Option Order"] = market_open_table["Tenor Option"].map(
#             {t: i for i, t in enumerate(market_open_table["Tenor Option"].unique())}
#         )
#
#         result = market_open_table.loc[:, list(IR_INFLATIONVEGA_OPEN)]
#
#         return exact_frame(result, columns=IR_INFLATIONVEGA_OPEN, label="IR InflationVega open")
#
#
#     def get_inflationvega_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         inflationveganame = underlying + " INFLATION SABRAF VOL"
#         temp = run_async(get_ramp_read_dict(inflationveganame, market_status, market_date))
#         temp_table = pd.DataFrame(index=temp[inflationveganame.lower().replace(" ", "_") + ".exercise_date_list"],
#                                   columns=temp[inflationveganame.lower().replace(" ", "_") + ".maturity_list"])
#
#         k = 0
#         for i in temp_table.index:
#             for j in temp_table.columns:
#                 temp_table.loc[i,j] = temp[inflationveganame.lower().replace(" ", "_") + ".sigma_normal"][k]
#                 k = k + 1
#
#         market_close_table = pd.DataFrame(temp_table.stack().reset_index())
#         market_close_table.columns = ["Tenor Option", "Tenor Swap", "Current"]
#         market_close_table["Current"] = market_close_table["Current"] * 100
#         market_close_table["Underlying"] = underlying
#         # Deduplicate any duplicate Tenor Swap + Tenor Option combinations
#         if market_close_table.duplicated(["Tenor Option", "Tenor Swap"]).any():
#             market_close_table = market_close_table.drop_duplicates(["Tenor Option", "Tenor Swap"], keep="first")
#         market_close_table = market_close_table.reset_index(drop=True)
#         market_close_table["Tenor Swap Order"] = market_close_table["Tenor Swap"].map(
#             {t: i for i, t in enumerate(market_close_table["Tenor Swap"].unique())}
#         )
#         market_close_table["Tenor Option Order"] = market_close_table["Tenor Option"].map(
#             {t: i for i, t in enumerate(market_close_table["Tenor Option"].unique())}
#         )
#
#         result = market_close_table.loc[:, list(IR_INFLATIONVEGA_CURRENT)]
#
#         return exact_frame(result, columns=IR_INFLATIONVEGA_CURRENT, label="IR InflationVega current")
#
#     return ProductConnectorAdapter(
#         risk=get_inflationvega_risk,
#         market_open=get_inflationvega_open,
#         market_status=get_inflationvega_current,
#     )
#
# def build_ir_bond_adapter() -> ProductConnectorAdapter:
#
#     def get_bond_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
#
#         view = mrx.MRXView("mrx/ir/bond.tsv")
#         view += ("Current Date", risk_date.strftime("%Y/%m/%d"))
#         view += ("Previous Date", (risk_date - BDay(1)).strftime("%Y/%m/%d"))
#         data = view.fetch(verify=False)
#         data = data.rename(columns={"Total":"Risk","Total (diff)":"dRisk","Tnr (Sw)":"Tenor Swap"})
#
#         data["Underlying_ccy"] = data["Underlying"].str.split().str[0]
#
#         g10_list = ["EUR", "USD", "GBP", "AUD", "CAD", "JPY", "NZD", "CHF", "NOK", "SEK"]
#         emea_list = ["ILS", "PLN", "CZK", "HUF", "DKK", "RON", "RUB", "HRK", "ISK", "KWD", "AED", "SAR", "QAR", "UAH", "OMR",
#                      "TND", "ZAR", "GHS", "ZMW", "XRH", "EGP", "TRY"]
#         latam_list = ["MXN", "CLP", "PEN", "BRL", "BRO", "ARO", "ARS", "COO", "COP", "BRU"]
#         asia_list = ["HKD", "SGD", "MYO", "MYR", "CNH", "CNO", "CNY", "IDO", "IDR", "THB", "THO", "TWD", "TWO", "VND", "VNO", "KRO", "KRW", "PHP", "INO", "INR"]
#         precious_list = ["XAG", "XAU", "XPD", "XPT", "XRH"]
#
#         data["Group"] = np.select(
#             [data["Underlying_ccy"].isin(g10_list),
#              data["Underlying_ccy"].isin(emea_list),
#              data["Underlying_ccy"].isin(latam_list),
#              data["Underlying_ccy"].isin(asia_list),
#              data["Underlying_ccy"].isin(precious_list)],
#             ["G10", "EMEA", "LATAM", "Asia", "Precious"], default="Other"
#         )
#         data["Risk"], data["dRisk"] = data["Risk"] / 1e3, data["dRisk"] / 1e3
#         result = data.loc[:, list(IR_BOND_RISK)]
#
#         return exact_frame(result, columns=IR_BOND_RISK, label="IR Bond risk")
#
#
#     def get_bond_open(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         bondname = underlying.split(" ")[0] + " MESA " + underlying.split(" ")[1]
#         for i in range(2, len(underlying.split(" "))):
#             bondname = bondname + " " + underlying.split(" ")[i]
#
#         market_open = run_async(get_ramp_read_dict(bondname, "OFFICIAL OPEN", market_date))
#         market_open_table = pd.DataFrame([market_open[bondname.lower().replace(" ", "_") + "_rates.metadata"],
#                                           market_open[bondname.lower().replace(" ", "_") + "_rates.rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Open"})
#
#         market_open_table["Underlying"] = underlying
#         market_open_table["Open"] = market_open_table["Open"] * 10000
#         market_open_table["Tenor Swap Order"] = range(len(market_open_table["Tenor Swap"]))
#
#         market_open_table["Tenor Swap"] = market_open_table["Tenor Swap"].str.replace("BOND_TENOR{", "", regex=False).str.replace("}", "", regex=False).str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False)
#
#         result = market_open_table.loc[:, list(IR_BOND_OPEN)]
#
#         return exact_frame(result, columns=IR_BOND_OPEN, label="IR Bond open")
#
#
#     def get_bond_current(market_date: pd.Timestamp, underlying: str, *, market_status: str,) -> pd.DataFrame:
#
#         bondname = underlying.split(" ")[0] + " MESA " + underlying.split(" ")[1]
#         for i in range(2, len(underlying.split(" "))):
#             bondname = bondname + " " + underlying.split(" ")[i]
#
#         market_close = run_async(get_ramp_read_dict(bondname, market_status, market_date))
#         market_open = run_async(get_ramp_read_dict(bondname, "OFFICIAL OPEN", market_date))
#         market_close_table = pd.DataFrame([market_open[bondname.lower().replace(" ", "_") + "_rates.metadata"],
#                                            market_close[bondname.lower().replace(" ", "_") + "_rates.rate"]]).T.rename(columns={0: "Tenor Swap", 1: "Current"})
#
#         market_close_table["Underlying"] = underlying
#         market_close_table["Current"] = market_close_table["Current"] * 10000
#         market_close_table["Tenor Swap Order"] = range(len(market_close_table["Tenor Swap"]))
#
#         market_close_table["Tenor Swap"] = market_close_table["Tenor Swap"].str.replace("BOND_TENOR{", "", regex=False).str.replace("}", "", regex=False).str.replace("T/N", "", regex=False).str.replace("D:", "", regex=False).str.replace("S:", "", regex=False).str.replace("-", "", regex=False).str.replace("202", "2", regex=False).str.replace("203", "3", regex=False).str.replace("204", "4", regex=False).str.replace("205", "5", regex=False).str.replace("206", "6", regex=False).str.replace("207", "7", regex=False).str.replace("208", "8", regex=False).str.replace("209", "9", regex=False)
#
#         result = market_close_table.loc[:, list(IR_BOND_CURRENT)]
#
#         return exact_frame(result, columns=IR_BOND_CURRENT, label="IR Bond current")
#
#     return ProductConnectorAdapter(
#         risk=get_bond_risk,
#         market_open=get_bond_open,
#         market_status=get_bond_current,
#     )
#
#
# __all__ = [
#     "build_ir_delta_adapter",
#     "build_ir_deltavega_adapter",
#     "build_ir_gamma_adapter",
#     "build_ir_xccy_adapter",
#     "build_ir_xccyvega_adapter",
#     "build_ir_inflation_adapter",
#     "build_ir_inflationvega_adapter",
#     "build_ir_basis_adapter",
#     "build_ir_bond_adapter",
# ]


# === ACTIVE VALIDATED CONTRACT (CSV RUNTIME IS SELECTED IN FEEDS) ============
# Strict active IR Delta curve and IR DeltaVega surface adapter contracts.

import pandas as pd

from core.s01_schema import (
    TENOR_OPTION,
    TENOR_OPTION_ORDER,
    TENOR_SWAP,
    TENOR_SWAP_ORDER,
)
from core.s02_pipeline import ProductConnectorAdapter
from .s01_common import MarketSource, RiskSource, exact_frame, market_frame


IR_DELTA_RISK = (
    "Underlying",
    TENOR_SWAP,
    "Portfolio",
    "Group",
    "Risk",
    "dRisk",
)
IR_DELTA_OPEN = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Open")
IR_DELTA_CURRENT = ("Underlying", TENOR_SWAP, TENOR_SWAP_ORDER, "Current")

IR_DELTAVEGA_RISK = (
    "Underlying",
    TENOR_SWAP,
    TENOR_OPTION,
    "Portfolio",
    "Group",
    "Risk",
    "dRisk",
)
IR_DELTAVEGA_OPEN = (
    "Underlying",
    TENOR_SWAP,
    TENOR_OPTION,
    TENOR_SWAP_ORDER,
    TENOR_OPTION_ORDER,
    "Open",
)
IR_DELTAVEGA_CURRENT = (
    "Underlying",
    TENOR_SWAP,
    TENOR_OPTION,
    TENOR_SWAP_ORDER,
    TENOR_OPTION_ORDER,
    "Current",
)


def build_ir_adapters(
    *,
    delta_risk: RiskSource,
    delta_open: MarketSource,
    delta_current: MarketSource,
    deltavega_risk: RiskSource,
    deltavega_open: MarketSource,
    deltavega_current: MarketSource,
) -> dict[str, ProductConnectorAdapter]:
    """Bind six functions to ``ir/delta`` and ``ir/deltavega``.

    In fixture mode these functions are the narrow CSV views registered by
    ``feeds.s01_sources``. The refresh framework calls each market function once
    per unique Risk Underlying and validates arbitrary M x N Vega surfaces.
    """

    def get_delta_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
        return exact_frame(
            delta_risk(risk_date), columns=IR_DELTA_RISK, label="IR Delta risk"
        )

    def get_delta_open(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            delta_open,
            market_date,
            underlying,
            market_status=market_status,
            columns=IR_DELTA_OPEN,
            label="IR Delta Open",
        )

    def get_delta_current(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            delta_current,
            market_date,
            underlying,
            market_status=market_status,
            columns=IR_DELTA_CURRENT,
            label="IR Delta current",
            attach_status=True,
        )

    def get_deltavega_risk(risk_date: pd.Timestamp) -> pd.DataFrame:
        return exact_frame(
            deltavega_risk(risk_date),
            columns=IR_DELTAVEGA_RISK,
            label="IR DeltaVega risk",
        )

    def get_deltavega_open(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            deltavega_open,
            market_date,
            underlying,
            market_status=market_status,
            columns=IR_DELTAVEGA_OPEN,
            label="IR DeltaVega Open",
        )

    def get_deltavega_current(
        market_date: pd.Timestamp,
        underlying: str,
        *,
        market_status: str,
    ) -> pd.DataFrame:
        return market_frame(
            deltavega_current,
            market_date,
            underlying,
            market_status=market_status,
            columns=IR_DELTAVEGA_CURRENT,
            label="IR DeltaVega current",
            attach_status=True,
        )

    return {
        "ir/delta": ProductConnectorAdapter(
            risk=get_delta_risk,
            market_open=get_delta_open,
            market_status=get_delta_current,
        ),
        "ir/deltavega": ProductConnectorAdapter(
            risk=get_deltavega_risk,
            market_open=get_deltavega_open,
            market_status=get_deltavega_current,
        ),
    }


__all__ = [
    "IR_DELTA_CURRENT",
    "IR_DELTA_OPEN",
    "IR_DELTA_RISK",
    "IR_DELTAVEGA_CURRENT",
    "IR_DELTAVEGA_OPEN",
    "IR_DELTAVEGA_RISK",
    "build_ir_adapters",
]
