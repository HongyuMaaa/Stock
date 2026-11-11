"""Independent re-implementation of the mechanical core of the 'Argon' strategy, for auditing.

Implemented from the written specification: regime-capped exposure, relative-momentum score,
breakout entry gate, at most two positions, the nine-layer exit stack.
NOT implemented because the inputs are discretionary or not point-in-time: sector quadrants,
'recognised leader' / 'clarity' bonuses, financial-quality score, hard-coded event windows, and the
dividend leg (idle capital stays in cash).

Two execution modes:
  realistic : decide at the close, trade at the next open; no buying at a limit-up open, no selling
              at a limit-down open; the 10% hard stop fills at min(open, stop) from day 2 (T+1)
  idealized : decide and trade at the same close, even at limit-up / limit-down; stop always fills at
              the stop price (the assumptions the Argon audit itself flagged)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import Costs
from .panel import Panel, _ffill

INDICES = ["000300", "000905", "000852", "399006", "000688", "000016"]
CAPS = {"MAINLINE_BULL": 0.85, "CHOP_RANGE": 0.50, "OVERHEATED_TRIM": 0.40, "FULL_DEFENSE": 0.30}


@dataclass
class Signals:
    cols: np.ndarray
    o: np.ndarray; h: np.ndarray; l: np.ndarray; c: np.ndarray   # adjusted OHLC (c forward filled)
    ma5: np.ndarray; ma20: np.ndarray; ma60: np.ndarray
    score: np.ndarray; gate: np.ndarray; volr: np.ndarray; shadow: np.ndarray
    regime: np.ndarray; cap: np.ndarray; breadth: np.ndarray


def build_signals(p: Panel, cols: np.ndarray, universe: np.ndarray | None = None) -> Signals:
    """universe: optional bool [T, len(cols)] used for the breadth measure."""
    D = lambda a: pd.DataFrame(np.asarray(a[:, cols], dtype=np.float64))
    adj = D(p.adj)
    c = pd.DataFrame(np.asarray(p.adj_close_ff[:, cols], dtype=np.float64))
    o, h, l = D(p.open) * adj, D(p.high) * adj, D(p.low) * adj
    ma5, ma20, ma60 = (c.rolling(k, min_periods=k).mean() for k in (5, 20, 60))
    b = p.index["000300"]
    rel = {k: (c / c.shift(k)).div((b / b.shift(k)).to_numpy(), axis=0) - 1 for k in (20, 60, 120)}
    score = 0.35 * rel[20] + 0.40 * rel[60] + 0.25 * rel[120] - 0.15 * (c > 1.12 * ma20)
    turn = D(p.turnover)
    volr = turn / turn.shift(1).rolling(5, min_periods=3).mean()
    rng = (h - l)
    shadow = ((h - np.maximum(o, c)) / rng.where(rng > 0)).fillna(0.0)
    hi20 = h.rolling(20, min_periods=10).max()
    traded = pd.DataFrame(p.traded[:, cols])
    gate = traded & (c >= ma60) & (rel[60] > 0) & (c >= 0.99 * hi20) & (volr >= 1.10) & (shadow <= 0.58)
    # ---- regime
    ix = p.index[INDICES]
    i20, i60 = ix.rolling(20).mean(), ix.rolling(60).mean()
    aligned = ((ix > i20) & (i20 > i60)).where(ix.notna() & i60.notna())
    align = aligned.mean(axis=1).to_numpy()
    above = (b > b.rolling(60).mean()).to_numpy()
    over = (b / b.rolling(60).mean() - 1).to_numpy()
    u = traded.to_numpy() if universe is None else universe
    br = np.where(u.sum(1) > 0, ((c >= ma60).to_numpy() & u).sum(1) / np.maximum(u.sum(1), 1), np.nan)
    regime = np.full(len(b), "CHOP_RANGE", dtype=object)
    regime[above & (align >= 0.5) & (br >= 0.5)] = "MAINLINE_BULL"
    regime[above & (over > 0.10) & (br >= 0.8)] = "OVERHEATED_TRIM"
    regime[~above & (br < 0.3)] = "FULL_DEFENSE"
    cap = np.array([CAPS[r] for r in regime])
    cap[~above & (align <= 0.25)] = np.minimum(cap[~above & (align <= 0.25)], 0.50)       # false-rebound filter
    f = lambda x: x.to_numpy()
    return Signals(cols, f(o), f(h), f(l), f(c), f(ma5), f(ma20), f(ma60), f(score), f(gate), f(volr), f(shadow),
                   regime, cap, br)


@dataclass
class ArgonResult:
    equity: pd.Series
    trades: pd.DataFrame
    exposure: pd.Series
    stats: dict = field(default_factory=dict)


def run_argon(p: Panel, s: Signals, eligible: np.ndarray, start, end=None, mode="realistic", costs: Costs | None = None,
              max_pos=2, use_regime=True, use_gate=True, min_score=None, slot_frac=None, disable=()) -> ArgonResult:
    """eligible: bool [T, len(cols)] point-in-time candidate set at each close."""
    costs = costs or Costs()
    ideal = mode == "idealized"
    cols = s.cols
    traded = p.traded[:, cols]
    cant_buy, cant_sell = p.cant_buy_open[:, cols], p.cant_sell_open[:, cols]
    t0 = p.loc(start); t1 = len(p.dates) - 1 if end is None else p.dates.searchsorted(pd.Timestamp(end), side="right") - 1
    cash, pos = 1.0, {}     # j -> dict(sh, cost, peak, t_in, below20)
    eq, expo, trades = [], [], []
    pending_buy, pending_sell = [], {}
    dv = p.dates.values

    def sell(j, price, t, reason):
        nonlocal cash
        q = pos.pop(j)
        _, sc = costs.rates(dv[t])
        cash += q["sh"] * price * (1 - sc)
        trades.append(dict(code=p.codes[cols[j]], entry=p.dates[q["t_in"]], exit=p.dates[t], days=t - q["t_in"],
                           ret=price * (1 - sc) / q["cost"] - 1, reason=reason, w=q["w"]))

    def buy(j, price, t, w):
        nonlocal cash
        bc, _ = costs.rates(dv[t])
        equity = cash + sum(q["sh"] * s.c[t - 1, k] for k, q in pos.items())
        amt = min(w * equity, cash / (1 + bc))
        if amt <= 0 or not np.isfinite(price):
            return
        cash -= amt * (1 + bc)
        pos[j] = dict(sh=amt / price, cost=price * (1 + bc), peak=price, t_in=t, below20=0, w=w)

    for t in range(t0, t1 + 1):
        # ---- open: realistic mode executes yesterday's decisions
        if not ideal:
            for j, reason in list(pending_sell.items()):
                if j in pos and traded[t, j] and not cant_sell[t, j]:
                    sell(j, s.o[t, j], t, reason); pending_sell.pop(j)
            pending_sell = {j: r for j, r in pending_sell.items() if j in pos}
            for j, w in pending_buy:
                if j not in pos and len(pos) < max_pos and traded[t, j] and not cant_buy[t, j]:
                    buy(j, s.o[t, j], t, w)
            pending_buy = []
        # ---- intraday hard stop (T+1: not on the entry day)
        for j in list(pos):
            q = pos[j]
            if "HARD_STOP_10%" not in disable and t > q["t_in"] and traded[t, j] and s.l[t, j] <= 0.90 * q["cost"]:
                locked = (s.h[t, j] == s.l[t, j]) and cant_sell[t, j]
                if ideal:
                    sell(j, 0.90 * q["cost"], t, "HARD_STOP_10%")
                elif not locked:
                    sell(j, min(s.o[t, j], 0.90 * q["cost"]), t, "HARD_STOP_10%"); pending_sell.pop(j, None)
        # ---- close: evaluate exits
        for j in list(pos):
            q = pos[j]
            if not traded[t, j] or j in pending_sell:
                continue
            c, cost = s.c[t, j], q["cost"]
            q["peak"] = max(q["peak"], s.h[t, j])
            held = t - q["t_in"]; gain = c / cost - 1; peak_gain = q["peak"] / cost - 1; dd = c / q["peak"] - 1
            q["below20"] = q["below20"] + 1 if c < 0.97 * s.ma20[t, j] else 0
            trail = 0.15 if (peak_gain > 0.50 and held < 20) else (0.13 if peak_gain > 0.30 and held < 20 else 0.11)
            reason = None
            if held == 0 and gain <= -0.035 and c < s.o[t, j]: reason = "NEXT_DAY_FAILURE_CUT"
            elif peak_gain >= 0.05 and gain <= 0.018: reason = "AGILE_BREAKEVEN_LOCK"
            elif held >= 2 and gain <= -0.048 and c < s.ma5[t, j]: reason = "EARLY_SUPPORT_CUT"
            elif peak_gain >= 0.20 and s.shadow[t, j] > 0.65 and s.volr[t, j] >= 1.35: reason = "EXHAUSTION_TOP_SHADOW"
            elif q["below20"] >= 3: reason = "MA20_BREAK"
            elif dd <= -trail or (peak_gain >= 0.30 and dd <= -0.085 and c < s.ma5[t, j] and s.volr[t, j] >= 1.0): reason = "TRAILING_STOP_LOCK"
            elif held >= 55: reason = "TIME_CAP_ROTATE"
            elif use_regime and s.regime[t] == "FULL_DEFENSE": reason = "MACRO_DEFENSE"
            if reason in disable:
                reason = "TIME_CAP_ROTATE" if held >= 55 else None
            if reason:
                if ideal and held > 0:
                    sell(j, c, t, reason)
                else:
                    pending_sell[j] = reason
        # ---- close: entries
        cap = s.cap[t] if use_regime else 0.85
        allow = (not use_regime) or s.regime[t] in ("MAINLINE_BULL", "CHOP_RANGE")
        free = max_pos - len(pos) + (0 if ideal else len(pending_sell))
        if allow and free > 0 and t < t1:
            ok = eligible[t] & (s.gate[t] if use_gate else (traded[t] & (s.c[t] >= s.ma60[t]))) & np.isfinite(s.score[t])
            if min_score is not None:
                ok &= s.score[t] >= min_score
            for j in pos:
                ok[j] = False
            cand = np.flatnonzero(ok)
            cand = cand[np.argsort(-s.score[t, cand])][:free]
            w = (cap / max_pos) if slot_frac is None else slot_frac
            if ideal:
                for j in cand:
                    if len(pos) < max_pos:
                        buy(j, s.c[t, j], t + 0, w)      # same-close fill, even at limit-up
                        pos[j]["t_in"] = t
            else:
                pending_buy = [(j, w) for j in cand]
        v = sum(q["sh"] * s.c[t, j] for j, q in pos.items())
        eq.append(cash + v); expo.append(v / (cash + v))
    idx = p.dates[t0:t1 + 1]
    tr = pd.DataFrame(trades)
    st = {}
    if len(tr):
        w_, l_ = tr.ret[tr.ret > 0], tr.ret[tr.ret <= 0]
        st = dict(trades=len(tr), win_rate=float((tr.ret > 0).mean()), avg_win=float(w_.mean()) if len(w_) else 0.0,
                  avg_loss=float(l_.mean()) if len(l_) else 0.0,
                  profit_factor=float((w_ * tr.w[w_.index]).sum() / -(l_ * tr.w[l_.index]).sum()) if len(l_) and l_.sum() < 0 else np.inf,
                  avg_days=float(tr.days.mean()))
    return ArgonResult(pd.Series(eq, idx), tr, pd.Series(expo, idx), st)
