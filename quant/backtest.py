"""Daily-bar portfolio simulator with A-share trading frictions.

Timing convention (no look-ahead, no intraday timing):
  * a strategy computes target weights using data up to and including the CLOSE of day t
  * orders are executed at the OPEN of day t+1
  * a stock that opens at its upper price limit cannot be bought, one that opens at its lower
    limit cannot be sold, a suspended stock cannot be traded at all
  * blocked exits are retried at every following open until filled
  * positions in stocks that stop trading for good (delisting) are written off at the last
    close times (1 - delist_haircut)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# (effective date, buy stamp duty, sell stamp duty)
STAMP_DUTY = [
    ("1990-01-01", 0.004, 0.004),
    ("2001-11-16", 0.002, 0.002),
    ("2005-01-24", 0.001, 0.001),
    ("2007-05-30", 0.003, 0.003),
    ("2008-04-24", 0.001, 0.001),
    ("2008-09-19", 0.0, 0.001),
    ("2023-08-28", 0.0, 0.0005),
]


@dataclass
class Costs:
    commission: float = 0.0003     # per side (retail brokers charge 0.01%-0.03%)
    slippage: float = 0.001        # per side, vs. the opening price
    stamp_duty: bool = True        # False for ETFs / index proxies
    historical_stamp: bool = True  # False -> apply today's 0.05% sell-side duty to all history

    def rates(self, date) -> tuple[float, float]:
        b = s = 0.0
        if self.stamp_duty:
            if self.historical_stamp:
                for d, bb, ss in STAMP_DUTY:
                    if date >= np.datetime64(d):
                        b, s = bb, ss
            else:
                b, s = STAMP_DUTY[-1][1:]
        return self.commission + self.slippage + b, self.commission + self.slippage + s


@dataclass
class Result:
    name: str
    equity: pd.Series
    turnover: pd.Series        # one-way traded value / equity, per day
    n_pos: pd.Series
    exposure: pd.Series        # invested fraction at close
    cost: pd.Series            # costs paid / equity, per day
    stats: dict = field(default_factory=dict)
    holdings: dict = field(default_factory=dict)   # date -> {code: weight} at rebalance

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change().fillna(0.0)


def simulate(name, dates, adj_open, adj_close_ff, traded, cant_buy, cant_sell, targets,
             start, end=None, costs: Costs | None = None, band: float = 0.0,
             delist_haircut: float = 0.0, last_bar=None, codes=None) -> Result:
    """targets: dict {signal_day_index: (idx_array, weight_array)}; executed next open."""
    costs = costs or Costs()
    T, N = adj_close_ff.shape
    t0 = int(dates.searchsorted(pd.Timestamp(start)))
    t1 = T - 1 if end is None else min(T - 1, int(dates.searchsorted(pd.Timestamp(end), side="right")) - 1)
    if (t0 - 1) not in targets:            # carry the most recent earlier target into the start
        prior = [t for t in targets if t < t0 - 1]
        if prior:
            targets = {**targets, t0 - 1: targets[max(prior)]}
    if last_bar is None:
        last_bar = np.full(N, T - 1)
    h = np.zeros(N)                     # shares, in adjusted-price units
    cash = 1.0
    w_tgt = np.zeros(N)
    pending_exit = np.zeros(N, dtype=bool)
    eq = np.empty(t1 - t0 + 1)
    tv = np.zeros_like(eq); npos = np.zeros_like(eq); expo = np.zeros_like(eq); cst = np.zeros_like(eq)
    n_blocked_buy = n_blocked_sell = n_delist = 0
    dvals = dates.values

    for k, t in enumerate(range(t0, t1 + 1)):
        held = np.flatnonzero(h > 0)
        ref = adj_close_ff[t - 1] if t > 0 else adj_close_ff[t]
        # --- delisting write-off
        if held.size:
            gone = held[(last_bar[held] < t) & (last_bar[held] < T - 250)]
            for j in gone:
                cash += h[j] * ref[j] * (1 - delist_haircut)
                h[j] = 0.0; pending_exit[j] = False; n_delist += 1
        bc, sc = costs.rates(dvals[t])
        traded_val = paid = 0.0
        sig = targets.get(t - 1)
        if sig is not None or pending_exit.any():
            po = np.where(traded[t], adj_open[t], ref)
            val = h * np.nan_to_num(po)
            equity_open = cash + val.sum()
            if sig is not None:
                w_tgt = np.zeros(N)
                w_tgt[sig[0]] = sig[1]
                delta = w_tgt * equity_open - val
                if band > 0:
                    small = (np.abs(delta) < band * np.maximum(w_tgt * equity_open, 1e-12)) & (w_tgt > 0) & (h > 0)
                    delta[small] = 0.0
            else:
                delta = np.where(pending_exit, -val, 0.0)
            # sells first
            want_sell = delta < -1e-12
            can = want_sell & ~cant_sell[t]
            n_blocked_sell += int((want_sell & ~can).sum()) if sig is not None else 0
            js = np.flatnonzero(can)
            if js.size:
                amt = -delta[js]
                h[js] -= amt / po[js]
                h[js] = np.where(h[js] * po[js] < 1e-10, 0.0, h[js])
                cash += amt.sum() * (1 - sc)
                traded_val += amt.sum(); paid += amt.sum() * sc
            # buys
            want_buy = delta > 1e-12
            can = want_buy & ~cant_buy[t]
            n_blocked_buy += int((want_buy & ~can).sum())
            jb = np.flatnonzero(can)
            if jb.size and cash > 0:
                amt = delta[jb]
                scale = min(1.0, cash / (amt.sum() * (1 + bc)))
                amt = amt * scale
                h[jb] += amt / po[jb]
                cash -= amt.sum() * (1 + bc)
                traded_val += amt.sum(); paid += amt.sum() * bc
            pending_exit = (h > 0) & (w_tgt == 0)
        pos_val = h * np.nan_to_num(adj_close_ff[t])
        e = cash + pos_val.sum()
        eq[k] = e
        tv[k] = traded_val / 2 / e; cst[k] = paid / e
        npos[k] = (h > 0).sum(); expo[k] = pos_val.sum() / e

    idx = dates[t0:t1 + 1]
    res = Result(name, pd.Series(eq, idx), pd.Series(tv, idx), pd.Series(npos, idx),
                 pd.Series(expo, idx), pd.Series(cst, idx))
    res.stats = dict(blocked_buys=n_blocked_buy, blocked_sells=n_blocked_sell, delisted_while_held=n_delist)
    if codes is not None:
        res.holdings = {dates[t]: dict(zip(codes[i], np.round(w, 5))) for t, (i, w) in targets.items()
                        if t0 - 1 <= t <= t1}
    return res


def run_stock_strategy(panel, name, targets, start, end=None, costs=None, band=0.0, delist_haircut=0.0) -> Result:
    T = len(panel.dates)
    tr = panel.traded
    last_bar = np.where(tr.any(0), T - 1 - np.argmax(tr[::-1], axis=0), -1)
    return simulate(name, panel.dates, panel.adj_open, panel.adj_close_ff, tr, panel.cant_buy_open,
                    panel.cant_sell_open, targets, start, end, costs, band, delist_haircut, last_bar, panel.codes)


def run_index_strategy(panel, name, weights: pd.DataFrame, start, end=None, costs=None) -> Result:
    """weights: DataFrame(dates x index codes) of target weights decided at each close.
    Indices are price indices used as proxies for the corresponding ETFs."""
    cols = list(weights.columns)
    close = panel.index[cols].to_numpy(dtype=float)
    opn = panel.index_open[cols].to_numpy(dtype=float)
    traded = np.isfinite(close)
    from .panel import _ffill
    w = weights.reindex(panel.dates).to_numpy(dtype=float)
    chg = np.ones(len(w), dtype=bool)
    chg[1:] = np.any(np.abs(w[1:] - w[:-1]) > 1e-9, axis=1)
    targets = {t: (np.arange(len(cols)), np.nan_to_num(w[t])) for t in np.flatnonzero(chg)}
    costs = costs or Costs(commission=0.0002, slippage=0.0005, stamp_duty=False)
    return simulate(name, panel.dates, opn, _ffill(close), traded, ~traded, ~traded, targets, start, end, costs)
