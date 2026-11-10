#!/usr/bin/env python3
"""Run every strategy, the robustness checks and write reports/ (tables as CSV + figures + results.md)."""
import argparse, sys, time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant import strategies as S
from quant.backtest import Costs, run_stock_strategy
from quant.catalog import BUFFER, MF, STOCK_STRATEGIES, INDEX_STRATEGIES, risk_on_signal, run_all
from quant.metrics import TD, drawdown, find_regimes, perf_stats, regime_summary, regime_table
from quant.panel import load_panel

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
BENCH_COLOR = "#8a8984"
plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                     "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
                     "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
                     "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
                     "legend.frameon": False, "lines.linewidth": 2})


# ----------------------------------------------------------------------------- helpers
def pct(x, d=1):
    return "" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.{d}f}%"


def md_table(df: pd.DataFrame, fmt: dict | None = None, default=None, index=True) -> str:
    fmt = fmt or {}
    d = df.reset_index() if index else df
    cols = list(d.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join(["---"] + ["---:"] * (len(cols) - 1)) + "|"]
    for _, r in d.iterrows():
        cells = []
        for c in cols:
            f = fmt.get(c, default)
            v = r[c]
            cells.append(f(v) if f and isinstance(v, (int, float, np.floating)) else str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def shade_bears(ax, regimes):
    for _, g in regimes[regimes.regime == "bear"].iterrows():
        ax.axvspan(g.start, g.end, color="#000000", alpha=0.055, lw=0)


def equity_fig(path, title, subtitle, curves: dict, bench: pd.Series, bench_name, regimes):
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 7.6), height_ratios=[3, 1.25], sharex=True)
    start = min(c.index[0] for c in curves.values())
    b = bench.loc[start:] / bench.loc[start:].iloc[0]
    ax.plot(b.index, b, color=BENCH_COLOR, lw=1.6, label=bench_name)
    ends = [(b.iloc[-1], bench_name, BENCH_COLOR)]
    for (n, eq), col in zip(curves.items(), PALETTE):
        e = eq / eq.iloc[0]
        ax.plot(e.index, e, color=col, label=n)
        ax2.plot(e.index, drawdown(e) * 100, color=col, lw=1.3)
        ends.append((e.iloc[-1], n, col))
    ax2.plot(b.index, drawdown(b) * 100, color=BENCH_COLOR, lw=1.1)
    ax.set_yscale("log")
    shade_bears(ax, regimes); shade_bears(ax2, regimes)
    # direct labels at line ends, nudged apart in log space
    ends.sort()
    ys = []
    for v, n, col in ends:
        y = np.log10(v)
        if ys and y - ys[-1] < 0.085:
            y = ys[-1] + 0.085
        ys.append(y)
        ax.annotate(f"{n}  {v:,.1f}x", (b.index[-1], 10 ** y), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=INK2)
        ax.plot([b.index[-1]], [v], marker="o", ms=4, color=col)
    ax.set_title(title, pad=24)
    ax.text(0, 1.015, subtitle, transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom", ha="left")
    ax.set_ylabel("growth of 1 CNY (log scale)")
    ax.legend(loc="upper left", fontsize=8.5, ncols=2)
    ax2.set_ylabel("drawdown, %")
    ax2.set_title("Drawdown from previous peak", fontsize=10)
    fig.subplots_adjust(right=0.80, hspace=0.16, top=0.90, bottom=0.06, left=0.07)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def bar_regime_fig(path, summ: pd.DataFrame):
    summ = summ.iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 0.62 * len(summ) + 1.4))
    y = np.arange(len(summ)); h = 0.36
    for off, col, colr, lab in ((h / 2 + 0.01, "bull ann. return", PALETTE[0], "bull phases"),
                                (-h / 2 - 0.01, "bear ann. return", PALETTE[1], "bear phases")):
        v = summ[col].to_numpy() * 100
        ax.barh(y + off, v, h, color=colr, label=lab, zorder=3)
        for yy, vv in zip(y + off, v):
            ax.annotate(f"{vv:+.0f}%", (vv, yy), xytext=(4 if vv >= 0 else -4, 0), textcoords="offset points",
                        ha="left" if vv >= 0 else "right", va="center", fontsize=8.5, color=INK2)
    ax.axvline(0, color=INK2, lw=0.8, zorder=4)
    ax.set_yticks(y, summ.index)
    ax.set_xlabel("annualised return, %")
    ax.set_title("Annualised return inside bull phases vs. bear phases of the A-share market")
    ax.grid(axis="y", visible=False)
    ax.margins(x=0.12)
    ax.legend(loc="lower right")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def factor_ic(p, fac, start, every=20, horizon=20):
    """Rank IC of each factor vs. the next-`horizon`-day return (entered at next open)."""
    rows = []
    ao = p.adj_open
    for t in S.rebalance_days(p, start, every):
        if t + 1 + horizon >= len(p.dates):
            break
        m = fac.universe(int(t))
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p.adj_close_ff[t + horizon] / ao[t + 1] - 1
        ok = m & np.isfinite(fwd)
        if ok.sum() < 100:
            continue
        fr = pd.Series(fwd[ok]).rank()
        row = {"date": p.dates[t]}
        for k in ("size", "rev20", "lowvol", "lowturn", "mom"):
            x = getattr(fac, k)[t][ok]
            row[k] = pd.Series(x).rank().corr(fr)
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--start", default="2000-01-04")
    ap.add_argument("--index-start", default="2006-01-04")
    ap.add_argument("--skip-robustness", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); (out / "figures").mkdir(parents=True, exist_ok=True); (out / "tables").mkdir(exist_ok=True)
    t0 = time.time()
    p = load_panel(a.data_dir)
    fac = S.Factors(p)
    print(f"panel {p.close.shape}, factors ready in {time.time() - t0:.0f}s")

    bench = S.cap_weighted_index(p).loc[a.start:]
    bench.name = "A-share market (float-cap weighted, total return)"
    regimes = find_regimes(bench)
    res = run_all(p, fac, a.start, a.index_start)
    print(f"strategies done in {time.time() - t0:.0f}s")
    eq = {n: r.equity for n, r in res.items()}
    stock_names, index_names = list(STOCK_STRATEGIES), list(INDEX_STRATEGIES)
    pd.DataFrame(eq).join(bench.rename("Market")).to_csv(out / "tables" / "equity_curves.csv")

    # ---- headline tables
    def table(names, start, extra=True):
        rows = {}
        for n in names:
            e = eq[n].loc[start:]
            st = perf_stats(e, bench)
            r = res[n]
            if extra:
                st["Turnover/yr"] = r.turnover.loc[start:].mean() * TD
                st["Costs/yr"] = r.cost.loc[start:].mean() * TD
                st["Avg exposure"] = r.exposure.loc[start:].mean()
            rows[n] = st
        rows["Market"] = perf_stats(bench.loc[start:], bench)
        return pd.DataFrame(rows).T

    full = table(stock_names, a.start)
    common = table(stock_names + index_names, a.index_start)
    full.to_csv(out / "tables" / "performance_full.csv"); common.to_csv(out / "tables" / "performance_common.csv")

    def by_year(e):
        ye = e.resample("YE").last()
        r = ye.pct_change()
        r.iloc[0] = ye.iloc[0] / e.iloc[0] - 1      # first (partial) year
        return r
    yearly = pd.DataFrame({n: by_year(e) for n, e in {**eq, "Market": bench}.items()})
    yearly.index = yearly.index.year
    yearly.to_csv(out / "tables" / "yearly_returns.csv")

    rt = regime_table(regimes, {**eq})
    rt = rt.rename(columns={"Index": "Market"})
    rs = regime_summary(regimes, {**eq, "Market": bench})
    regimes.to_csv(out / "tables" / "regimes.csv", index=False); rt.to_csv(out / "tables" / "regime_returns.csv")
    rs.to_csv(out / "tables" / "regime_summary.csv")

    ic = factor_ic(p, fac, a.start)
    ic_tab = pd.DataFrame({"mean IC": ic.mean(), "IC IR (annualised)": ic.mean() / ic.std() * np.sqrt(TD / 20),
                           "IC>0 share": (ic > 0).mean(),
                           "mean IC 2000-12": ic.loc[:"2012"].mean(), "mean IC 2013-19": ic.loc["2013":"2019"].mean(),
                           "mean IC 2020-": ic.loc["2020":].mean()})
    ic_tab.to_csv(out / "tables" / "factor_ic.csv")

    # ---- figures
    sub = "Signals at the close, executed at next open; costs, price limits, suspensions and delistings included. Shaded = bear phases."
    equity_fig(out / "figures" / "equity_stock_strategies.png", "Stock-selection strategies, 2000–2026", sub,
               {n: eq[n] for n in ["SmallCap-MF", "SmallCap-MF+Trend", "LargeCap-Defensive", "Momentum-12-1", "EqualWeight-All"]},
               bench, "Market (cap-weighted)", regimes)
    equity_fig(out / "figures" / "equity_microcap.png", "Why not simply buy the smallest stocks? MicroCap-50 vs. the multi-factor versions", sub,
               {n: eq[n] for n in ["SmallCap-MF", "SmallCap-MF+Trend", "MicroCap-50"]}, bench, "Market (cap-weighted)", regimes)
    equity_fig(out / "figures" / "equity_index_strategies.png", "Index (ETF) strategies, 2006–2026",
               "Price indices as ETF proxies (dividends excluded). " + sub.split(";")[0] + ". Shaded = bear phases.",
               {n: eq[n] for n in index_names}, bench.loc[a.index_start:], "Market (cap-weighted)",
               regimes[regimes.end > a.index_start])
    bar_regime_fig(out / "figures" / "bull_bear_returns.png", rs)

    # ---- robustness
    rob = {}
    if not a.skip_robustness:
        on = risk_on_signal(p, fac)
        base = lambda **kw: S.factor_portfolio(p, fac, MF, a.start, **{**dict(n_hold=50, every=20, buffer=BUFFER), **kw})

        def run(label, targets, costs=None, start=a.start, group=""):
            r = run_stock_strategy(p, label, targets, start, costs=costs)
            st = perf_stats(r.equity, bench)
            rob[(group, label)] = {"CAGR": st["CAGR"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"],
                                   "Turnover/yr": r.turnover.mean() * TD}
        tb = base()
        run("base: 50 names, 20d, 10bp slippage, 2N buffer", tb, group="baseline")
        for sl in (0.0, 0.003, 0.005):
            run(f"slippage {sl * 1e4:.0f}bp per side", tb, Costs(slippage=sl), group="costs")
        run("today's stamp duty applied to all history", tb, Costs(historical_stamp=False), group="costs")
        run("no holding buffer (full re-selection each month)", base(buffer=1.0), group="turnover control")
        run("wider buffer (keep while in top 3N)", base(buffer=3.0), group="turnover control")
        for n in (10, 20, 100, 200):
            run(f"{n} names", base(n_hold=n), group="breadth")
        for ev in (5, 10, 40, 60):
            run(f"rebalance every {ev}d", base(every=ev), group="frequency")
        for k in MF:
            w = {kk: v for kk, v in MF.items() if kk != k}
            run(f"drop factor '{k}'", S.factor_portfolio(p, fac, w, a.start, n_hold=50, every=20, buffer=BUFFER), group="factor mix")
        run("main + ChiNext + STAR", base(boards=S.ALL_BOARDS), group="universe")
        run("exclude smallest 30% by liquidity", base(liq_floor_pct=0.30), group="universe")
        run("only the 1,500 largest stocks", base(top_cap=1500), group="universe")
        for ma in (60, 250):
            ro = S.trend_filter(S.market_index(p, fac), ma, 0.02)
            run(f"+Trend with MA{ma}", base(risk_on=ro), group="trend filter")
        run("+Trend with MA120, no band", base(risk_on=S.trend_filter(S.market_index(p, fac), 120, 0.0)), group="trend filter")
        run("execute one day later (open of t+2)", {t + 1: v for t, v in tb.items()}, group="timing")
        run("execute five days later", {t + 5: v for t, v in tb.items()}, group="timing")
        r = run_stock_strategy(p, "x", tb, a.start, delist_haircut=1.0)
        st = perf_stats(r.equity, bench)
        rob[("delisting", "delisted holdings written off 100%")] = {"CAGR": st["CAGR"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"],
                                                                     "Turnover/yr": r.turnover.mean() * TD}
        for s_, e_ in (("2000-01-04", "2009-12-31"), ("2010-01-04", "2016-12-31"), ("2017-01-03", "2021-12-31"), ("2022-01-04", None)):
            for n in ("SmallCap-MF", "SmallCap-MF+Trend", "LargeCap-Defensive"):
                e = eq[n].loc[s_:e_]
                st = perf_stats(e, bench)
                rob[(f"sub-period {s_[:4]}–{(e_ or str(e.index[-1].year))[:4]}", n)] = {
                    "CAGR": st["CAGR"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"],
                    "Turnover/yr": res[n].turnover.loc[s_:e_].mean() * TD}
            b = bench.loc[s_:e_]; st = perf_stats(b)
            rob[(f"sub-period {s_[:4]}–{(e_ or str(b.index[-1].year))[:4]}", "Market")] = {"CAGR": st["CAGR"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"], "Turnover/yr": np.nan}
        robdf = pd.DataFrame(rob).T
        robdf.index.names = ["test", "variant"]
        robdf.to_csv(out / "tables" / "robustness.csv")
        print(f"robustness done in {time.time() - t0:.0f}s")

    # ---- markdown
    P1 = lambda v: pct(v, 1)
    F2 = lambda v: f"{v:.2f}"
    fm = {"CAGR": P1, "Vol": P1, "Sharpe": F2, "Sortino": F2, "MaxDD": P1, "Calmar": F2, "Win months": lambda v: pct(v, 0),
          "Worst month": P1, "Worst year": P1, "Beta": F2, "Alpha": P1, "Info ratio": F2,
          "Turnover/yr": lambda v: "" if not np.isfinite(v) else f"{v:.1f}x", "Costs/yr": lambda v: pct(v, 2), "Avg exposure": lambda v: pct(v, 0),
          "Total return": lambda v: f"{v + 1:,.1f}x"}
    cols = ["CAGR", "Vol", "Sharpe", "MaxDD", "Calmar", "Worst year", "Win months", "Beta", "Alpha", "Turnover/yr", "Costs/yr", "Avg exposure", "Total return"]
    n_listed = int((p.traded[-5:].any(0)).sum())
    stats_extra = pd.DataFrame({n: res[n].stats for n in stock_names}).T
    md = [f"<!-- generated by scripts/run_backtests.py on data to {p.dates[-1]:%Y-%m-%d} -->",
          f"Data: {p.close.shape[1]:,} stocks ({n_listed:,} trading in the last week, {p.close.shape[1] - n_listed:,} delisted or suspended), "
          f"{p.dates[0]:%Y-%m-%d} → {p.dates[-1]:%Y-%m-%d}.", "",
          f"### Full period ({a.start} → {p.dates[-1]:%Y-%m-%d})", "", md_table(full[cols].rename_axis("Strategy"), fm), "",
          f"### Common period incl. index strategies ({a.index_start} → {p.dates[-1]:%Y-%m-%d})", "", md_table(common[cols].rename_axis("Strategy"), fm), "",
          "### Max drawdown details", "",
          md_table(full[["MaxDD", "DD peak", "DD trough", "DD recovery days", "Worst month"]].rename_axis("Strategy"),
                   {"MaxDD": P1, "Worst month": P1, "DD recovery days": lambda v: "not yet" if not np.isfinite(v) else f"{v:.0f}"}), "",
          "### Execution frictions encountered", "", md_table(stats_extra.rename_axis("Strategy")), "",
          "### Market phases (20% rule on the cap-weighted market index) and strategy return in each", "",
          md_table(rt, default=lambda v: pct(v, 0)), "",
          "### Annualised return over all bull days / all bear days", "", md_table(rs.rename_axis("Strategy"), default=P1), "",
          "### Calendar-year returns", "", md_table(yearly.rename_axis("Year").rename(index=str), default=lambda v: pct(v, 0)), "",
          "### Factor rank IC (20-day horizon, main-board universe)", "", md_table(ic_tab.rename_axis("Factor"), {"IC IR (annualised)": F2, "IC>0 share": lambda v: pct(v, 0)}, default=lambda v: f"{v:.3f}"), ""]
    if rob:
        md += ["### Robustness of SmallCap-MF", "", md_table(robdf.reset_index(), {"CAGR": P1, "Sharpe": F2, "MaxDD": P1, "Turnover/yr": fm["Turnover/yr"]}, index=False), ""]
    (out / "results.md").write_text("\n".join(md))
    print(f"wrote {out / 'results.md'} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
