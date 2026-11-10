"""A small fixed basket of securities (stocks and ETFs) as a mini panel for the simulator.

ETF history before the fund's listing is filled with the index it tracks (price index, clearly
flagged as a proxy) so that earlier market cycles can be included in a backtest.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .backtest import Costs, Result, simulate
from .panel import _ffill, load_panel

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
ETFS = {  # code: (eastmoney secid, sina symbol, tracked index in data/index, name)
    "510300": ("1.510300", "sh510300", "000300", "CSI 300 ETF"),
    "159915": ("0.159915", "sz159915", "399006", "ChiNext ETF"),
}


def download_etf(code: str, data_dir="data") -> pd.DataFrame:
    """Unadjusted daily bars + a multiplicative adjustment factor built from cash distributions."""
    sec, sym, _, _ = ETFS[code]
    def get(url, **kw):
        for i in range(6):
            try:
                s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
                r = s.get(url, timeout=30, **kw)
                if r.status_code == 200:
                    return r
            except requests.RequestException:
                pass
            time.sleep(2 * (i + 1))
        raise RuntimeError(f"cannot fetch {url}")
    d = get(KLINE_URL, params=dict(secid=sec, fields1="f1,f2,f3", fields2="f51,f52,f53,f54,f55,f56,f57",
                                     klt=101, fqt=0, beg="19900101", end="20500101", lmt=1000000)).json()["data"]
    df = pd.DataFrame([k.split(",") for k in d["klines"]], columns=["date", "open", "close", "high", "low", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    df[df.columns[1:]] = df[df.columns[1:]].astype(float)
    txt = get(f"https://finance.sina.com.cn/realstock/company/{sym}/hfq.js").text
    ev = pd.DataFrame(json.loads(re.search(r"=\s*(\{.*\})", txt.split("/*")[0], re.S).group(1))["data"])
    ev = ev[ev["d"] != "1900-01-01"].assign(d=lambda x: pd.to_datetime(x["d"]), u=lambda x: x["u"].astype(float)).sort_values("d")
    ev["div"] = ev["u"].diff().fillna(ev["u"])          # `u` is the cumulative cash paid per unit
    df["adj_factor"] = 1.0
    for d_, div in zip(ev["d"], ev["div"]):
        prev = df.loc[df["date"] < d_, "close"]
        if len(prev) and div > 0:
            df.loc[df["date"] >= d_, "adj_factor"] *= prev.iloc[-1] / (prev.iloc[-1] - div)
    out = Path(data_dir) / "etf"; out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / f"{code}.parquet", index=False)
    return df


@dataclass
class Basket:
    dates: pd.DatetimeIndex
    codes: np.ndarray
    names: list
    adj_open: np.ndarray
    adj_close: np.ndarray      # forward filled through suspensions
    traded: np.ndarray
    cant_buy: np.ndarray
    cant_sell: np.ndarray
    is_stock: np.ndarray       # pays stamp duty
    proxy: np.ndarray          # True where an ETF is represented by its index

    @property
    def close(self) -> pd.DataFrame:
        return pd.DataFrame(self.adj_close, self.dates, self.codes)

    @property
    def ret(self) -> pd.DataFrame:
        return self.close.pct_change()


def load_basket(codes, data_dir="data", refresh_etf=False) -> Basket:
    p = load_panel(data_dir)
    names = pd.read_csv(Path(data_dir) / "meta" / "stocks.csv", dtype=str).set_index("code")["name"]
    T = len(p.dates)
    cols = {k: [] for k in ("ao", "ac", "tr", "cb", "cs", "px")}
    nm, is_stock = [], []
    for c in codes:
        if c in ETFS:
            f = Path(data_dir) / "etf" / f"{c}.parquet"
            df = (download_etf(c, data_dir) if refresh_etf or not f.exists() else pd.read_parquet(f)).set_index("date")
            df = df.reindex(p.dates)
            ao = np.array(df["open"] * df["adj_factor"], dtype=float); ac = np.array(df["close"] * df["adj_factor"], dtype=float)
            first = int(np.argmax(np.isfinite(ac)))
            idx_c = p.index[ETFS[c][2]].to_numpy(); idx_o = p.index_open[ETFS[c][2]].to_numpy()
            k = ac[first] / idx_c[first]                      # splice the index on before listing
            px = np.zeros(T, dtype=bool); px[:first] = np.isfinite(idx_c[:first])
            ao[:first] = idx_o[:first] * k; ac[:first] = idx_c[:first] * k
            tr = np.isfinite(ac)
            cols["ao"].append(ao); cols["ac"].append(ac); cols["tr"].append(tr)
            cols["cb"].append(~tr); cols["cs"].append(~tr); cols["px"].append(px)
            nm.append(ETFS[c][3]); is_stock.append(False)
        else:
            j = int(np.flatnonzero(p.codes == c)[0])
            cols["ao"].append(p.adj_open[:, j].astype(float)); cols["ac"].append(p.adj_close[:, j].astype(float))
            cols["tr"].append(p.traded[:, j]); cols["cb"].append(p.cant_buy_open[:, j]); cols["cs"].append(p.cant_sell_open[:, j])
            cols["px"].append(np.zeros(T, dtype=bool)); nm.append(names.get(c, c)); is_stock.append(True)
    st = lambda k: np.column_stack(cols[k])
    return Basket(p.dates, np.array(codes), nm, st("ao"), _ffill(st("ac")), st("tr"), st("cb"), st("cs"),
                  np.array(is_stock), st("px"))


def run_basket(b: Basket, name: str, weights: pd.DataFrame, start, end=None, costs: Costs | None = None,
               min_trade: float = 0.0) -> Result:
    """weights: DataFrame (dates x codes) of target weights decided at each close (NaN rows = no order).
    min_trade: skip a rebalance when no weight would change by more than this (absolute)."""
    w = weights.reindex(b.dates)[list(b.codes)].to_numpy(dtype=float)
    targets, last = {}, None
    for t in range(len(w)):
        if not np.isfinite(w[t]).all():
            continue
        if last is None or np.abs(w[t] - last).max() > max(min_trade, 1e-9):
            targets[t] = (np.arange(w.shape[1]), w[t].copy()); last = w[t]
    costs = costs or Costs(commission=0.0003, slippage=0.001)
    return simulate(name, b.dates, b.adj_open, b.adj_close, b.traded, b.cant_buy, b.cant_sell, targets,
                    start, end, costs, codes=b.codes, stamp_mask=b.is_stock, record_weights=True)


def trade_stats(b: Basket, res: Result) -> dict:
    """Round-trip trades: from the day a security enters the portfolio to the day it leaves."""
    w = res.weights.to_numpy() > 1e-6
    t0 = b.dates.get_loc(res.equity.index[0])
    rets, lens = [], []
    for j in range(w.shape[1]):
        held = np.concatenate([[False], w[:, j], [False]])
        starts = np.flatnonzero(~held[:-1] & held[1:]); ends = np.flatnonzero(held[:-1] & ~held[1:])
        for s, e in zip(starts, ends):                 # held on rows s..e-1; bought at open s, sold at open e
            entry = b.adj_open[t0 + s, j]
            exit_ = b.adj_open[t0 + e, j] if e < w.shape[0] else b.adj_close[t0 + e - 1, j]
            if np.isfinite(entry) and np.isfinite(exit_):
                rets.append(exit_ / entry - 1 - 0.004); lens.append(e - s)
    r = np.array(rets)
    if not len(r):
        return dict(trades=0)
    return dict(trades=len(r), win_rate=float((r > 0).mean()), avg_win=float(r[r > 0].mean()) if (r > 0).any() else 0.0,
                avg_loss=float(r[r <= 0].mean()) if (r <= 0).any() else 0.0,
                payoff=float(r[r > 0].sum() / -r[r <= 0].sum()) if (r <= 0).any() and r[r <= 0].sum() < 0 else np.inf,
                avg_days=float(np.mean(lens)))


# ============================================================================ strategy blocks
# Everything below works on Basket.close (adjusted, forward-filled closes) and returns a
# DataFrame of target weights decided at each close.

def ma_trend(px: pd.DataFrame, k: int, band: float = 0.02) -> pd.DataFrame:
    """True while the close is above its k-day average; flips only after crossing +-band."""
    m = px.rolling(k).mean()
    out = {}
    for c in px:
        a, mm = px[c].to_numpy(), m[c].to_numpy()
        o = np.zeros(len(a), dtype=bool); s = False
        for i in range(len(a)):
            if np.isfinite(mm[i]):
                if a[i] > mm[i] * (1 + band):
                    s = True
                elif a[i] < mm[i] * (1 - band):
                    s = False
            o[i] = s
        out[c] = o
    return pd.DataFrame(out, index=px.index)


def equal_weight(px: pd.DataFrame) -> pd.DataFrame:
    a = px.notna().astype(float)
    return a.div(a.sum(axis=1).clip(lower=1), axis=0)


def slots(on: pd.DataFrame, px: pd.DataFrame, slot: float) -> pd.DataFrame:
    return (on & px.notna()).astype(float) * slot


def spread(on: pd.DataFrame, px: pd.DataFrame, cap: float = 0.34) -> pd.DataFrame:
    """Split the portfolio equally among the securities that are 'on', at most `cap` each."""
    on = (on & px.notna()).astype(float)
    return on.div(on.sum(axis=1).clip(lower=1), axis=0).clip(upper=cap)


def trend_ensemble(b: Basket, ks=(150, 200, 250), band=0.02, cap=0.34, gate="510300") -> pd.DataFrame:
    """Average of several slow trend rules. An ETF is held while it is above its own average;
    a stock is held while it OR the broad market (`gate`) is above its average."""
    px = b.close
    ws = []
    for k in ks:
        own = ma_trend(px, k, band)
        g = own[gate] if gate in own else False      # no gate security in the basket -> own trend only
        on = pd.DataFrame({c: (own[c] | g) if s else own[c] for c, s in zip(px.columns, b.is_stock)})
        ws.append(spread(on, px, cap))
    return sum(ws) / len(ws)


def vol_target(b: Basket, w: pd.DataFrame, target=0.20, looks=(20, 60)) -> pd.DataFrame:
    """Scale the whole portfolio down (never up) so that its recent volatility, taken as the
    larger of the `looks`-day estimates, does not exceed `target` (annualised)."""
    full = w.div(w.sum(axis=1).replace(0, np.nan), axis=0)
    r = (full.shift(1) * b.ret).sum(axis=1)
    v = pd.concat([r.rolling(k).std() for k in looks], axis=1).max(axis=1) * np.sqrt(244)
    return w.mul((target / v).clip(upper=1.0).fillna(0), axis=0)


def rsi(px: pd.DataFrame, k: int = 2) -> pd.DataFrame:
    d = px.diff()
    up = d.clip(lower=0).ewm(alpha=1 / k, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / k, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def dip_positions(px: pd.DataFrame, rsi_k=2, rsi_below=5, exit_ma=10, max_hold=15) -> pd.DataFrame:
    """1 while a dip trade is open: enter when RSI(rsi_k) < rsi_below, leave when the close is
    back above its exit_ma-day average or after max_hold days."""
    r, m = rsi(px, rsi_k), px.rolling(exit_ma).mean()
    out = {}
    for c in px:
        e = (r[c] < rsi_below).to_numpy() & px[c].notna().to_numpy()
        x = (px[c] > m[c]).to_numpy()
        o = np.zeros(len(e)); inp = False; n = 0
        for i in range(len(e)):
            if inp:
                n += 1
                if x[i] or n >= max_hold:
                    inp = False
            elif e[i]:
                inp, n = True, 0
            o[i] = inp
        out[c] = o
    return pd.DataFrame(out, index=px.index)


def with_dip_overlay(core: pd.DataFrame, px: pd.DataFrame, slot=0.10, **kw) -> pd.DataFrame:
    """Use idle cash (never more than what the core leaves free) for short dip trades."""
    ov = dip_positions(px, **kw) * slot
    room = (1 - core.sum(axis=1)).clip(lower=0)
    ov = ov.mul((room / ov.sum(axis=1).replace(0, np.nan)).clip(upper=1).fillna(0), axis=0)
    return core + ov


def final_strategy(b: Basket, target=0.20, slot=0.10, ks=(150, 200, 250)) -> pd.DataFrame:
    return with_dip_overlay(vol_target(b, trend_ensemble(b, ks), target), b.close, slot)
