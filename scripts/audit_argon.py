#!/usr/bin/env python3
"""Independent audit of the 'Argon' strategy: re-implements its mechanical core (quant/argon.py) on a
point-in-time universe and writes reports/argon/results.md with every experiment.

  python scripts/audit_argon.py        # ~10 minutes
"""
import sys, warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")
from quant import argon as A
from quant import strategies as S
from quant.metrics import find_regimes, perf_stats
from quant.panel import load_panel
from run_backtests import equity_fig, md_table, pct

W = ("2024-10-17", "2026-11-10")          # Argon's reported window
L = ("2005-01-11", None)                  # long history
EARLY = ("NEXT_DAY_FAILURE_CUT", "AGILE_BREAKEVEN_LOCK", "EARLY_SUPPORT_CUT")
PRICE_EXITS = EARLY + ("HARD_STOP_10%", "EXHAUSTION_TOP_SHADOW", "MA20_BREAK", "TRAILING_STOP_LOCK")


def main():
    out = Path("reports/argon"); (out / "figures").mkdir(parents=True, exist_ok=True); (out / "tables").mkdir(exist_ok=True)
    p = load_panel(); fac = S.Factors(p)
    cols = np.flatnonzero(p.board == "main"); T = len(p.dates)
    uni = np.zeros((T, len(cols)), bool); top300 = np.zeros_like(uni); top800 = np.zeros_like(uni)
    for i in range(p.loc("2004-06-01"), T):
        uni[i] = fac.universe(i)[cols]; top300[i] = fac.universe(i, top_cap=300)[cols]; top800[i] = fac.universe(i, top_cap=800)[cols]
    sig = A.build_signals(p, cols, uni)
    bench = S.cap_weighted_index(p); ew = S.market_index(p, fac)
    t0 = p.loc(W[0])

    def row(r, subs=False):
        s = perf_stats(r.equity); st = r.stats
        d = {"Total return": s["Total return"], "CAGR": s["CAGR"], "MaxDD": s["MaxDD"], "Sharpe": s["Sharpe"], "Trades": st.get("trades", 0),
             "Win rate": st.get("win_rate", np.nan), "Profit factor": st.get("profit_factor", np.nan),
             "Avg win": st.get("avg_win", np.nan), "Avg loss": st.get("avg_loss", np.nan), "Exposure": r.exposure.mean()}
        if subs:
            for a, b in (("2005", "2009"), ("2010", "2014"), ("2015", "2019"), ("2020", "2026")):
                e = r.equity.loc[a:b]; d[f"{a}–{b[2:]}"] = (e.iloc[-1] / e.iloc[0]) ** (244 / len(e)) - 1
        return d

    # ---- 1. checkable facts
    ix = p.index.loc[W[0]:W[1]]
    facts = {"trading days in window": len(ix),
             "CSI 300, close 2024-10-17 → close 2026-11-10": ix["000300"].iloc[-1] / ix["000300"].iloc[0] - 1,
             "CSI 300, close 2024-10-16 → close 2026-11-10": ix["000300"].iloc[-1] / p.index["000300"].loc[:"2024-10-16"].iloc[-1] - 1,
             "all A-shares, cap-weighted total return": bench.loc[W[0]:].iloc[-1] / bench.loc[W[0]:].iloc[0] - 1,
             "main board, equal-weight": ew.loc[W[0]:].iloc[-1] / ew.loc[W[0]:].iloc[0] - 1}
    wr = p.adj_close_ff[-1, cols] / p.adj_close_ff[t0, cols] - 1
    alive = np.isfinite(wr)
    facts["median main-board stock"] = np.median(wr[alive]); facts["35th best main-board stock"] = np.sort(wr[alive])[-35]
    facts["89th best main-board stock"] = np.sort(wr[alive])[-89]
    for c, n in (("601288", "Agricultural Bank (named defensive)"), ("601088", "China Shenhua (named defensive)"), ("600900", "Yangtze Power (named defensive)")):
        j = np.flatnonzero(p.codes == c)[0]; facts[n] = p.adj_close_ff[-1, j] / p.adj_close_ff[t0, j] - 1

    # ---- 2. Argon's own window
    win = {}
    for name, el in (("All main board (point-in-time)", uni), ("800 largest (point-in-time)", top800), ("300 largest (point-in-time)", top300)):
        for mode in ("realistic", "idealized"):
            win[f"{name}, {mode}"] = row(A.run_argon(p, sig, el, *W, mode=mode))
    base = top800[t0] & alive
    order = np.argsort(-np.where(base, wr, -np.inf))
    for k in (35, 89):
        el = np.zeros_like(uni); el[:, order[:k]] = True; el &= p.traded[:, cols]
        for mode in ("realistic", "idealized"):
            win[f"HINDSIGHT: {k} best performers of the 800 largest, {mode}"] = row(A.run_argon(p, sig, el, *W, mode=mode))
        win[f"(buy & hold of those {k} names, equal weight)"] = {"Total return": wr[order[:k]].mean()}
    win = pd.DataFrame(win).T
    rng = np.random.default_rng(0); rnd = {}
    for k, pool, lab in ((35, top300[t0], "35 random stocks from the 300 largest"), (89, top800[t0], "89 random stocks from the 800 largest")):
        res = []
        for _ in range(200):
            pick = rng.choice(np.flatnonzero(pool), k, replace=False); el = np.zeros_like(uni); el[:, pick] = True; el &= p.traded[:, cols]
            res.append(A.run_argon(p, sig, el, *W).equity.iloc[-1] - 1)
        res = np.array(res)
        rnd[lab] = {"median": np.median(res), "5th pct": np.quantile(res, .05), "95th pct": np.quantile(res, .95), "best of 200": res.max(),
                    "share above +62.76%": (res > 0.6276).mean(), "share above +165.13%": (res > 1.6513).mean()}
    rnd = pd.DataFrame(rnd).T

    # ---- 3. long history + ablations
    runs = {
        "Core, 800 largest, realistic": dict(el=top800),
        "Core, 800 largest, idealized execution": dict(el=top800, mode="idealized"),
        "Core, 300 largest, realistic": dict(el=top300),
        "Core, all main board, realistic": dict(el=uni),
        "10 positions instead of 2": dict(el=top800, max_pos=10),
        "No breakout gate (trend + score only)": dict(el=top800, use_gate=False),
        "No regime control": dict(el=top800, use_regime=False),
        "No early cuts (exit layers 1–3 off)": dict(el=top800, disable=EARLY),
        "No price-based exits (55-day cap + macro exit only)": dict(el=top800, disable=PRICE_EXITS),
    }
    res = {n: A.run_argon(p, sig, kw.pop("el"), *L, **kw) for n, kw in runs.items()}
    long_ = pd.DataFrame({n: row(r, True) for n, r in res.items()}).T
    ms = perf_stats(bench.loc[L[0]:]); long_.loc["Market (cap-weighted)"] = {"CAGR": ms["CAGR"], "MaxDD": ms["MaxDD"], "Sharpe": ms["Sharpe"], "Total return": ms["Total return"]}
    tr = res["Core, 800 largest, realistic"].trades
    code_ix = pd.Index(p.codes[cols]); fw = []
    for _, x in tr.iterrows():
        j = code_ix.get_loc(x.code); t = p.dates.get_loc(x.exit)
        fw.append(sig.c[t + 20, j] / sig.o[t, j] - 1 - (ew.iloc[t + 20] / ew.iloc[t - 1] - 1) if t + 20 < T else np.nan)
    tr["after20"] = fw
    exits = tr.groupby("reason").agg(Trades=("ret", "size"), **{"Avg return": ("ret", "mean"), "Win rate": ("ret", lambda v: (v > 0).mean()),
                                     "Avg days held": ("days", "mean"), "Stock vs. market, 20 days after exit": ("after20", "mean")})
    tr.to_csv(out / "tables" / "core_trades_2005_2026.csv", index=False)

    # ---- 4. signal evidence
    ics, ev = {}, []
    for t in range(p.loc(L[0]), T - 21, 5):
        m = top800[t]; f = sig.c[t + 20] / sig.o[t + 1] - 1; ok = m & np.isfinite(f) & np.isfinite(sig.score[t])
        if ok.sum() < 200:
            continue
        ics[p.dates[t]] = pd.Series(sig.score[t, ok]).rank().corr(pd.Series(f[ok]).rank())
        g = ok & sig.gate[t]
        if g.sum() >= 3:
            top = np.flatnonzero(g)[np.argsort(-sig.score[t, g])][:2]
            ev.append((p.dates[t], g.sum(), f[g].mean() - f[ok].mean(), f[top].mean() - f[ok].mean()))
    ic = pd.Series(ics); ev = pd.DataFrame(ev, columns=["d", "n", "gate", "top2"]).set_index("d")
    era = lambda s: s.groupby(pd.cut(s.index.year, [2004, 2009, 2014, 2019, 2026], labels=["2005–09", "2010–14", "2015–19", "2020–26"])).mean()
    tstat = lambda s: s.mean() / s.std() * np.sqrt(len(s))
    sigtab = pd.DataFrame({"Rank IC of Argon momentum score vs next 20 days": {**era(ic).to_dict(), "All": ic.mean(), "t-stat": tstat(ic)},
                           "Stocks passing the entry gate: 20-day return minus universe": {**era(ev.gate).to_dict(), "All": ev.gate.mean(), "t-stat": tstat(ev.gate)},
                           "Top 2 by score among gate passers: 20-day return minus universe": {**era(ev.top2).to_dict(), "All": ev.top2.mean(), "t-stat": tstat(ev.top2)}}).T
    r20 = bench.shift(-20) / bench - 1
    reg = pd.DataFrame({"Regime": sig.regime, "r": r20.to_numpy()}, index=p.dates).loc[L[0]:].dropna()
    regtab = reg.groupby("Regime").r.agg(**{"Days": "size", "Market return over next 20 days": "mean"})
    regtab["Exposure cap"] = [A.CAPS[k] for k in regtab.index]

    # ---- 5. alternatives over Argon's window (from the walk-forward study, all out-of-sample)
    alt = {}
    f = Path("reports/ml/tables/equity_curves.csv")
    if f.exists():
        e = pd.read_csv(f, index_col=0, parse_dates=True).loc[W[0]:W[1]]
        for c in ("ML-GBM", "ML-GBM + vol target", "Rule-based SmallCap-MF", "EqualWeight-All", "Market"):
            s = perf_stats(e[c].dropna()); alt[c] = {"Total return": s["Total return"], "CAGR": s["CAGR"], "MaxDD": s["MaxDD"], "Sharpe": s["Sharpe"]}
    for n in ("Core, 800 largest, realistic",):
        s = perf_stats(res[n].equity.loc[W[0]:W[1]]); alt["Argon core (replication), 800 largest"] = {"Total return": s["Total return"], "CAGR": s["CAGR"], "MaxDD": s["MaxDD"], "Sharpe": s["Sharpe"]}
    alt = pd.DataFrame(alt).T

    regimes = find_regimes(bench.loc[L[0]:]); regimes = regimes[regimes["days"] >= 20]
    equity_fig(out / "figures" / "argon_core_long_history.png", "Argon's mechanical core on a point-in-time universe, 2005–2026",
               "Independent re-implementation (no leader labels, sectors or dividend leg). 800 largest main-board stocks. Shaded = bear phases.",
               {"Core, realistic execution": res["Core, 800 largest, realistic"].equity, "Core, idealized execution": res["Core, 800 largest, idealized execution"].equity,
                "No price-based exits": res["No price-based exits (55-day cap + macro exit only)"].equity}, bench.loc[L[0]:], "Market (cap-weighted)", regimes)

    P1 = lambda v: pct(v, 1); F2 = lambda v: "" if not np.isfinite(v) else f"{v:.2f}"; P0 = lambda v: pct(v, 0)
    fm = {"Total return": P1, "CAGR": P1, "MaxDD": P1, "Sharpe": F2, "Trades": lambda v: "" if not np.isfinite(v) else f"{v:.0f}", "Win rate": P0,
          "Profit factor": F2, "Avg win": P1, "Avg loss": P1, "Exposure": P0, "2005–09": P1, "2010–14": P1, "2015–19": P1, "2020–26": P1}
    ft = pd.Series(facts, name="value").to_frame()
    ft["value"] = [f"{v:.0f}" if k.startswith("trading") else pct(v, 2) for k, v in facts.items()]
    for n, t in (("window", win), ("random_universes", rnd), ("long_history", long_), ("exits", exits), ("signals", sigtab), ("regimes", regtab), ("alternatives", alt)):
        t.to_csv(out / "tables" / f"{n}.csv")
    md = [f"<!-- generated by scripts/audit_argon.py; data to {p.dates[-1]:%Y-%m-%d} -->",
          f"### Checkable facts, {W[0]} → {W[1]}", "", md_table(ft.rename_axis("Item")), "",
          "### Replication over Argon's own window", "", md_table(win.rename_axis("Universe, execution"), fm), "",
          "### 200 random fixed universes, realistic execution (total return over the window)", "", md_table(rnd.rename_axis("Universe"), default=P1), "",
          f"### Long history, {L[0]} → {p.dates[-1]:%Y-%m-%d}", "", md_table(long_.rename_axis("Variant"), fm), "",
          "### Exit layers (core, 800 largest, realistic, 2005–2026)", "", md_table(exits.rename_axis("Exit"), {"Trades": lambda v: f"{v:.0f}", "Avg days held": lambda v: f"{v:.1f}", "Win rate": P0}, default=P1), "",
          "### Signal evidence (800 largest, every 5 days, 2005–2026)", "", md_table(sigtab.rename_axis("Measure"), {"t-stat": lambda v: f"{v:.1f}", "Rank IC": F2}, default=lambda v: f"{v:.3f}"), "",
          "### Regime labels (my reconstruction) vs. what the market did next", "", md_table(regtab.rename_axis("Regime"), {"Days": lambda v: f"{v:.0f}", "Exposure cap": P0}, default=P1), "",
          "### Other strategies over Argon's window (walk-forward, out-of-sample)", "", md_table(alt.rename_axis("Strategy"), fm), ""]
    (out / "results.md").write_text("\n".join(md))
    print(f"wrote {out / 'results.md'}")


if __name__ == "__main__":
    main()
