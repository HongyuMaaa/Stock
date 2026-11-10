#!/usr/bin/env python3
"""Iterative development of a strategy for a fixed basket of five securities.

Runs every iteration (from buy-and-hold to the final strategy), the rejected ideas and the
robustness checks, and writes reports/basket/ (results.md, CSV tables, figures).

  python scripts/optimize_basket.py                       # default basket
  python scripts/optimize_basket.py --codes 510300 159915 600660 600406 000895
"""
import argparse, sys, warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")
from quant import basket as B
from quant import strategies as S
from quant.backtest import Costs
from quant.metrics import TD, find_regimes, perf_stats, regime_summary, regime_table
from quant.panel import load_panel
from run_backtests import bar_regime_fig, equity_fig, md_table, pct

DEFAULT = ["510300", "159915", "600066", "600406", "000895"]
IS_END, OOS_START = "2016-12-31", "2017-01-03"


def iterations(b):
    """name -> (what changed, weights, min_trade). Order = order of development."""
    px = b.close
    ens = B.trend_ensemble(b)
    vt = B.vol_target(b, ens, 0.20)
    every20 = pd.Series(np.arange(len(px)) % 20 == 0, px.index)
    return {
        "I0 Buy & hold": ("equal weight, rebalanced monthly", B.equal_weight(px).where(every20, axis=0), 0.0),
        "I1 Trend filter": ("each security held only above its 120-day average, fixed 20% slots",
                            B.slots(B.ma_trend(px, 120), px, 0.20), 0.05),
        "I2 Slow trend + market gate": ("average of 150/200/250-day rules; a stock stays in while it or the CSI 300 is in an uptrend; "
                                        "money is split among what is 'on' (max 34% each)", ens, 0.05),
        "I3 + Volatility target": ("whole portfolio scaled down when its recent volatility exceeds 20%", vt, 0.05),
        "I4 + Dip overlay (final)": ("idle cash buys sharp 2-day sell-offs (RSI2 < 5) in 10% slots, sold on the rebound",
                                     B.with_dip_overlay(vt, px, 0.10), 0.04),
    }


def rejected(b):
    px = b.close
    mom = px / px.shift(120) - 1
    rk = mom.where(B.ma_trend(px, 120)).rank(axis=1, ascending=False)
    every5 = pd.Series(np.arange(len(px)) % 5 == 0, px.index)
    vol = b.ret.rolling(60).std()
    on = sum(B.spread(pd.DataFrame({c: (B.ma_trend(px, k)[c] | B.ma_trend(px, k)["510300"]) if s else B.ma_trend(px, k)[c]
                                    for c, s in zip(px.columns, b.is_stock)}), px, 1.0) > 0 for k in (200,)).astype(bool)
    iv = (on.astype(float) / vol)
    return {
        "R1 Momentum rotation": ("hold the 2 securities with the best 120-day return among those in an uptrend, weekly",
                                 ((rk <= 2).astype(float) * 0.5).where(every5, axis=0), 0.0),
        "R2 Dip-buying only": ("RSI2 < 5 entries in 34% slots, no core holding", B.dip_positions(px) * 0.34, 0.0),
        "R3 Inverse-volatility weights": ("slow trend, weights proportional to 1/volatility instead of equal",
                                          iv.div(iv.sum(axis=1).replace(0, np.nan), axis=0).fillna(0).clip(upper=0.4), 0.05),
        "R4 Buy & hold + vol target": ("no trend rule at all, only the 20% volatility target",
                                       B.vol_target(b, B.equal_weight(px), 0.20), 0.05),
    }


def row(b, r, bench, start, end=None):
    e = r.equity.loc[start:end]
    st = perf_stats(e, bench)
    return {"CAGR": st["CAGR"], "Vol": st["Vol"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"], "Calmar": st["Calmar"],
            "Win months": st["Win months"], "Worst year": st["Worst year"],
            "Exposure": r.exposure.loc[start:end].mean(), "Turnover/yr": r.turnover.loc[start:end].mean() * TD}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="+", default=DEFAULT)
    ap.add_argument("--start", default="2006-01-04")
    ap.add_argument("--out", default="reports/basket")
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    out = Path(a.out); (out / "figures").mkdir(parents=True, exist_ok=True); (out / "tables").mkdir(exist_ok=True)

    b = B.load_basket(a.codes, a.data_dir)
    p = load_panel(a.data_dir)
    bench = S.cap_weighted_index(p).loc[a.start:]
    regimes = find_regimes(bench)
    regimes = regimes[regimes["days"] >= 20].reset_index(drop=True)
    run = lambda n, w, mt, **kw: B.run_basket(b, n, w, kw.pop("start", a.start), min_trade=mt, **kw)

    its, rej = iterations(b), rejected(b)
    res = {n: run(n, w, mt) for n, (_, w, mt) in {**its, **rej}.items()}
    eq = {n: r.equity for n, r in res.items()}
    pd.DataFrame(eq).join(bench.rename("Market")).to_csv(out / "tables" / "equity_curves.csv")

    def table(names, start, end=None):
        t = {}
        for n in names:
            d = row(b, res[n], bench, start, end)
            ts = B.trade_stats(b, res[n]) if end is None and start == a.start else {}
            d["Trades"] = ts.get("trades", np.nan); d["Trade win rate"] = ts.get("win_rate", np.nan)
            d["Payoff ratio"] = ts.get("payoff", np.nan)
            t[n] = d
        return pd.DataFrame(t).T

    full = table(list(its) + list(rej), a.start)
    is_ = table(list(its), a.start, IS_END); oos = table(list(its), OOS_START)
    etf_start = str(b.dates[int(np.flatnonzero(b.proxy.any(1)).max()) + 1].date()) if b.proxy.any() else a.start
    real = table(list(its), etf_start)
    for n, t in (("iterations_full", full), ("iterations_2006_2016", is_), ("iterations_2017_on", oos), ("iterations_real_etf", real)):
        t.to_csv(out / "tables" / f"{n}.csv")

    final = list(its)[-1]
    rt = regime_table(regimes, {n: eq[n] for n in its}).rename(columns={"Index": "Market"})
    rs = regime_summary(regimes, {**{n: eq[n] for n in its}, "Market": bench})
    rt.to_csv(out / "tables" / "regime_returns.csv"); rs.to_csv(out / "tables" / "regime_summary.csv")

    def by_year(e):
        ye = e.resample("YE").last(); r = ye.pct_change(); r.iloc[0] = ye.iloc[0] / e.iloc[0] - 1
        return r
    yearly = pd.DataFrame({n: by_year(e) for n, e in {**{n: eq[n] for n in its}, "Market": bench}.items()})
    yearly.index = yearly.index.year.astype(str); yearly.to_csv(out / "tables" / "yearly_returns.csv")

    # per-security contribution of the final strategy
    w = res[final].weights
    contrib = (w.shift(1) * b.ret.reindex(w.index)).sum()
    per = pd.DataFrame({"name": b.names, "buy&hold CAGR": [perf_stats(b.close[c].loc[a.start:].dropna())["CAGR"] for c in b.codes],
                        "buy&hold MaxDD": [perf_stats(b.close[c].loc[a.start:].dropna())["MaxDD"] for c in b.codes],
                        "avg weight in final": w.mean().values, "share of final's profit": (contrib / contrib.sum()).values}, index=b.codes)
    per.to_csv(out / "tables" / "per_security.csv")

    # robustness of the final strategy
    px = b.close
    rob = {}
    def add(group, label, wts, mt=0.04, **kw):
        r = run(label, wts, mt, **kw); st = perf_stats(r.equity, bench); ts = B.trade_stats(b, r)
        rob[(group, label)] = {"CAGR": st["CAGR"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"],
                               "Win months": st["Win months"], "Trade win rate": ts.get("win_rate", np.nan)}
    wf = B.final_strategy(b)
    add("baseline", "final strategy", wf)
    for t in (0.15, 0.18, 0.22, 0.25):
        add("volatility target", f"{t:.0%}", B.final_strategy(b, target=t))
    for ks in ((100, 150, 200), (200, 250, 300), (120,), (250,)):
        add("trend lookbacks", "/".join(map(str, ks)), B.final_strategy(b, ks=ks))
    for s_ in (0.0, 0.05, 0.20):
        add("dip slot", f"{s_:.0%}", B.final_strategy(b, slot=s_))
    for th in (3, 10):
        add("dip trigger", f"RSI2 < {th}", B.with_dip_overlay(B.vol_target(b, B.trend_ensemble(b), 0.20), px, 0.10, rsi_below=th))
    for sl in (0.0, 0.003):
        add("costs", f"slippage {sl * 1e4:.0f}bp per side", wf, costs=Costs(slippage=sl))
    add("timing", "trade one day later", wf.shift(1))
    add("timing", "check signals weekly, not daily", wf.where(pd.Series(np.arange(len(px)) % 5 == 0, px.index), axis=0))
    for c in b.codes:
        keep = [x for x in b.codes if x != c]
        bb = B.load_basket(keep, a.data_dir)
        r = B.run_basket(bb, c, B.final_strategy(bb), a.start, min_trade=0.04); st = perf_stats(r.equity, bench)
        rob[("leave one out", f"without {c}" + (" (no market gate)" if c == "510300" else ""))] = {"CAGR": st["CAGR"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"],
                                                  "Win months": st["Win months"], "Trade win rate": B.trade_stats(bb, r).get("win_rate", np.nan)}
    robdf = pd.DataFrame(rob).T; robdf.index.names = ["test", "variant"]
    robdf.to_csv(out / "tables" / "robustness.csv")

    # figures
    sub = "Signals at the close, executed at next open, costs included. ETFs proxied by their index before listing. Shaded = bear phases."
    show = ["I0 Buy & hold", "I2 Slow trend + market gate", "I3 + Volatility target", "I4 + Dip overlay (final)"]
    equity_fig(out / "figures" / "equity_iterations.png", "Five-security basket: from buy & hold to the final strategy", sub,
               {n: eq[n] for n in show}, bench, "Market (cap-weighted)", regimes)
    bar_regime_fig(out / "figures" / "bull_bear_returns.png", rs)

    P1 = lambda v: pct(v, 1); F2 = lambda v: "" if not np.isfinite(v) else f"{v:.2f}"
    fm = {"CAGR": P1, "Vol": P1, "Sharpe": F2, "MaxDD": P1, "Calmar": F2, "Win months": lambda v: pct(v, 0), "Worst year": P1,
          "Exposure": lambda v: pct(v, 0), "Turnover/yr": lambda v: f"{v:.1f}x", "Trades": lambda v: "" if not np.isfinite(v) else f"{v:.0f}",
          "Trade win rate": lambda v: pct(v, 0), "Payoff ratio": lambda v: "" if not np.isfinite(v) else f"{v:.1f}"}
    desc = pd.DataFrame({n: {"What it does": d} for n, (d, _, _) in {**its, **rej}.items()}).T
    md = [f"<!-- generated by scripts/optimize_basket.py; data to {b.dates[-1]:%Y-%m-%d} -->",
          f"Basket: {', '.join(f'{c} {n}' for c, n in zip(b.codes, b.names))}.", "",
          "### Iterations", "", md_table(desc.rename_axis("Step")), "",
          f"### Full period ({a.start} → {b.dates[-1]:%Y-%m-%d})", "", md_table(full.rename_axis("Step"), fm), "",
          f"### Development sample ({a.start} → {IS_END})", "", md_table(is_.drop(columns=["Trades", "Trade win rate", "Payoff ratio"]).rename_axis("Step"), fm), "",
          f"### Later sample ({OOS_START} → {b.dates[-1]:%Y-%m-%d})", "", md_table(oos.drop(columns=["Trades", "Trade win rate", "Payoff ratio"]).rename_axis("Step"), fm), "",
          f"### Real ETF prices only ({etf_start} → {b.dates[-1]:%Y-%m-%d})", "", md_table(real.drop(columns=["Trades", "Trade win rate", "Payoff ratio"]).rename_axis("Step"), fm), "",
          "### Return in each market phase", "", md_table(rt, default=lambda v: pct(v, 0)), "",
          "### Annualised return over all bull / bear days", "", md_table(rs.rename_axis("Step"), default=P1), "",
          "### Calendar years", "", md_table(yearly.rename_axis("Year"), default=lambda v: pct(v, 0)), "",
          "### Per security", "", md_table(per.rename_axis("Code"), {"avg weight in final": P1}, default=P1), "",
          "### Robustness of the final strategy", "",
          md_table(robdf.reset_index(), {"CAGR": P1, "Sharpe": F2, "MaxDD": P1, "Win months": lambda v: pct(v, 0), "Trade win rate": lambda v: pct(v, 0)}, index=False), ""]
    (out / "results.md").write_text("\n".join(md))
    print(f"wrote {out / 'results.md'}")


if __name__ == "__main__":
    main()
