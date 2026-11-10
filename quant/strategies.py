"""Daily-bar strategies. Every function returns `targets` for backtest.run_stock_strategy
({signal_day_index: (stock_idx, weights)}) or a weights DataFrame for index strategies.

All inputs at signal day t use data up to the close of t only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAIN = ("main",)
ALL_BOARDS = ("main", "chinext", "star")


# --------------------------------------------------------------------------- building blocks
class Factors:
    """Cross-sectional raw factors, computed once for the whole panel (higher = better)."""

    def __init__(self, panel):
        self.p = p = panel
        px = pd.DataFrame(p.adj_close_ff)
        ret = pd.DataFrame(p.ret)
        with np.errstate(divide="ignore", invalid="ignore"):
            self.size = -np.log(p.float_cap)                                    # small caps
            self.rev20 = -(px / px.shift(20) - 1).to_numpy()                    # 1-month reversal
            self.lowvol = -ret.rolling(60, min_periods=40).std().to_numpy()     # low volatility
            self.lowturn = -pd.DataFrame(p.turnover).rolling(20, min_periods=10).mean().to_numpy()
            self.mom = (px.shift(20) / px.shift(250) - 1).to_numpy()            # 12-1 momentum
        self.amt20 = pd.DataFrame(p.amount).rolling(20, min_periods=10).mean().to_numpy()
        self.ndays20 = pd.DataFrame(p.traded.astype(np.float32)).rolling(20, min_periods=1).sum().to_numpy()

    def universe(self, t: int, boards=MAIN, min_listed=250, min_price=2.0, liq_floor_pct=0.10,
                 top_cap: int | None = None) -> np.ndarray:
        """Eligible stocks at the close of day t."""
        p = self.p
        m = np.isin(p.board, boards) & p.traded[t] & (p.days_listed[t] >= min_listed)
        m &= (self.ndays20[t] >= 15) & ~p.st_like[t] & (p.close[t] >= min_price)
        m &= np.isfinite(p.float_cap[t])
        if m.sum() > 20 and liq_floor_pct > 0:
            m &= self.amt20[t] >= np.nanquantile(self.amt20[t][m], liq_floor_pct)
        if top_cap is not None and m.sum() > top_cap:
            cap = np.where(m, p.float_cap[t], -np.inf)
            keep = np.argsort(-cap)[:top_cap]
            mm = np.zeros_like(m); mm[keep] = True
            m &= mm
        return m


def _rank(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Percentile rank within mask (0..1), NaN elsewhere."""
    out = np.full(x.shape, np.nan)
    ok = mask & np.isfinite(x)
    n = ok.sum()
    if n:
        out[ok] = (np.argsort(np.argsort(x[ok])) + 0.5) / n
    return out


def rebalance_days(panel, start, every=20) -> np.ndarray:
    t0 = panel.loc(start) - 1          # first signal the day before `start`
    return np.arange(max(t0, 0), len(panel.dates) - 1, every)


def market_index(panel, fac: Factors, boards=MAIN) -> pd.Series:
    """Equal-weighted total-return index of seasoned stocks: the 'average stock'."""
    ok = np.isin(panel.board, boards)[None, :] & (panel.days_listed > 60)
    r = np.where(ok & np.isfinite(panel.ret), panel.ret, 0.0).astype(np.float64)
    n = (ok & np.isfinite(panel.ret)).sum(1)
    return pd.Series(np.cumprod(1 + r.sum(1) / np.maximum(n, 1)), index=panel.dates)


def cap_weighted_index(panel, boards=ALL_BOARDS) -> pd.Series:
    """Float-cap-weighted total-return index of all A shares (market benchmark)."""
    ok = np.isin(panel.board, boards)[None, :] & (panel.days_listed > 20)
    cap = np.vstack([panel.float_cap[:1], panel.float_cap[:-1]])
    w = np.where(ok & np.isfinite(panel.ret) & np.isfinite(cap), cap, 0.0).astype(np.float64)
    r = (w * np.nan_to_num(panel.ret)).sum(1) / np.maximum(w.sum(1), 1e-12)
    return pd.Series(np.cumprod(1 + r), index=panel.dates)


def trend_filter(index: pd.Series, ma: int = 120, buffer: float = 0.0) -> pd.Series:
    """True when the index closes above its moving average (with hysteresis buffer)."""
    m = index.rolling(ma).mean()
    on = np.zeros(len(index), dtype=bool)
    state = False
    for i, (p, a) in enumerate(zip(index.to_numpy(), m.to_numpy())):
        if np.isfinite(a):
            if p > a * (1 + buffer):
                state = True
            elif p < a * (1 - buffer):
                state = False
        on[i] = state
    return pd.Series(on, index=index.index)


# --------------------------------------------------------------------------- stock strategies
def factor_portfolio(panel, fac: Factors, weights: dict, start, n_hold=50, every=20, boards=MAIN,
                     top_cap=None, risk_on: pd.Series | None = None, **uni_kw) -> dict:
    """Hold the `n_hold` best stocks by a weighted average of factor percentile ranks,
    equal-weighted, refreshed every `every` trading days.

    risk_on: optional boolean Series; when False the portfolio is moved to cash at the next
    open, and re-entered when it flips back.
    """
    targets = {}
    rb = set(rebalance_days(panel, start, every).tolist())
    on = None if risk_on is None else risk_on.reindex(panel.dates).fillna(False).to_numpy()
    t_first = min(rb)
    prev_on = True
    for t in range(t_first, len(panel.dates) - 1):
        is_on = True if on is None else bool(on[t])
        if not (t in rb or is_on != prev_on):
            continue
        if not is_on:
            if prev_on or t == t_first:
                targets[t] = (np.array([], dtype=int), np.array([]))
            prev_on = is_on
            continue
        prev_on = is_on
        m = fac.universe(t, boards=boards, top_cap=top_cap, **uni_kw)
        if m.sum() < n_hold:
            continue
        score = np.zeros(len(m)); wsum = 0.0
        for k, w in weights.items():
            score += w * np.nan_to_num(_rank(getattr(fac, k)[t], m), nan=0.5)
            wsum += w
        score = np.where(m, score / wsum, -np.inf)
        pick = np.argsort(-score)[:n_hold]
        targets[t] = (pick, np.full(n_hold, 1.0 / n_hold))
    return targets


def equal_weight_universe(panel, fac: Factors, start, every=20, boards=MAIN, **uni_kw) -> dict:
    targets = {}
    for t in rebalance_days(panel, start, every):
        idx = np.flatnonzero(fac.universe(t, boards=boards, **uni_kw))
        if idx.size:
            targets[int(t)] = (idx, np.full(idx.size, 1.0 / idx.size))
    return targets


# --------------------------------------------------------------------------- index (ETF) strategies
def index_buy_hold(panel, code="000300") -> pd.DataFrame:
    return pd.DataFrame({code: 1.0}, index=panel.dates)


def index_ma_timing(panel, code="000300", ma=120, buffer=0.01) -> pd.DataFrame:
    """Long the index ETF while it closes above its MA, otherwise cash."""
    on = trend_filter(panel.index[code], ma, buffer)
    return pd.DataFrame({code: on.astype(float)})


def index_rotation(panel, codes=("000300", "000905"), lookback=20, check_every=5) -> pd.DataFrame:
    """'2-8 rotation': every `check_every` days hold the index with the best `lookback`-day
    return, or cash if none is positive."""
    px = panel.index[list(codes)]
    mom = px / px.shift(lookback) - 1
    w = pd.DataFrame(np.nan, index=px.index, columns=list(codes))
    first = px.dropna().index[0]
    for i in range(px.index.get_loc(first) + lookback, len(px), check_every):
        row = mom.iloc[i]
        w.iloc[i] = 0.0
        if row.max() > 0:
            w.iloc[i, int(np.argmax(row.to_numpy()))] = 1.0
    return w.ffill().fillna(0.0)
