#!/usr/bin/env python3
"""Train the data-driven stock-selection model with purged walk-forward validation, backtest the
out-of-sample scores, run the leakage checks and write reports/ml/.

  python scripts/train_ml.py            # ~1 hour: features, 22 yearly refits (GBM + ridge + placebo), backtests
  python scripts/train_ml.py --reuse    # only redo the backtests / report from cached scores
"""
import argparse, sys, time, warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")
from quant import ml
from quant import strategies as S
from quant.backtest import Costs, run_stock_strategy
from quant.catalog import BUFFER, MF
from quant.metrics import TD, find_regimes, perf_stats, regime_summary, regime_table
from quant.panel import load_panel
from run_backtests import bar_regime_fig, equity_fig, md_table, pct

N_HOLD, EVERY = 50, 20


def leakage_checks(p, fac, ds, log):
    """1) features recomputed on a panel cut off at day t must equal the full-history features at t
       2) the universe mask at t must be identical on the truncated panel"""
    rng = np.random.default_rng(1)
    worst, uni_ok = 0.0, True
    picks = sorted(rng.choice(np.arange(60, len(ds.rows) - 10), 6, replace=False))
    for i in picks:
        t = int(ds.rows[i])
        pt = ml.truncate(p, t + 1)
        ft = S.Factors(pt)
        raw_full = ml.compute_features(p, ds.cols, np.array([t]))
        raw_cut = ml.compute_features(pt, ds.cols, np.array([t]))
        u_cut = ft.universe(t)[ds.cols]
        uni_ok &= bool((u_cut == ds.uni[i]).all())
        for n in ds.names:
            a, b = raw_full[n][0][ds.uni[i]], raw_cut[n][0][ds.uni[i]]
            both = np.isfinite(a) & np.isfinite(b)
            assert (np.isfinite(a) == np.isfinite(b)).all(), f"NaN pattern differs for {n} at {p.dates[t].date()}"
            if both.any():
                worst = max(worst, float(np.nanmax(np.abs(a[both] - b[both]) / (np.abs(a[both]) + 1e-6))))
        log(f"  truncation check at {p.dates[t].date()}: ok")
    return dict(dates_checked=len(picks), max_rel_feature_diff=worst, universe_identical=uni_ok)


def position_win_rate(p, targets):
    """Share of individual holdings that made money / beat the median stock over their holding period."""
    ts = sorted(targets); wins = beat = n = 0
    for a, b in zip(ts[:-1], ts[1:]):
        idx = targets[a][0]
        if len(idx) == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            r_all = p.adj_open[b + 1] / p.adj_open[a + 1] - 1 if b + 1 < len(p.dates) else p.adj_close_ff[b] / p.adj_open[a + 1] - 1
        r = r_all[idx]; ok = np.isfinite(r)
        med = np.nanmedian(r_all[np.isin(p.board, ["main"])])
        wins += (r[ok] > 0).sum(); beat += (r[ok] > med).sum(); n += ok.sum()
    return wins / max(n, 1), beat / max(n, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="reports/ml")
    ap.add_argument("--first-test-year", type=int, default=2005)
    ap.add_argument("--reuse", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); (out / "figures").mkdir(parents=True, exist_ok=True); (out / "tables").mkdir(exist_ok=True)
    cache = Path(a.data_dir) / "ml"; cache.mkdir(exist_ok=True)
    log = lambda *x: print(*x, flush=True)
    t0 = time.time()
    p = load_panel(a.data_dir); fac = S.Factors(p)
    ds = ml.build_dataset(p, fac)
    log(f"dataset {ds.X.shape} in {time.time() - t0:.0f}s")

    if a.reuse and (cache / "scores.npz").exists():
        z = np.load(cache / "scores.npz", allow_pickle=True)
        sc = {k: z[k] for k in ("gbm", "ridge", "placebo")}
        info = pd.read_csv(cache / "walk_forward.csv"); imp = pd.read_csv(cache / "importance.csv", index_col=0)
        checks = pd.read_csv(cache / "checks.csv", index_col=0)["0"].to_dict()
    else:
        checks = leakage_checks(p, fac, ds, log)
        log("leakage checks:", checks)
        sc = {}
        sc["gbm"], info, imp = ml.walk_forward(p, ds, a.first_test_year, "gbm", log=log)
        sc["ridge"], _, _ = ml.walk_forward(p, ds, a.first_test_year, "ridge", log=log)
        sc["placebo"], _, _ = ml.walk_forward(p, ds, a.first_test_year, "gbm", log=log, shuffle_labels=True, train_step=4)
        np.savez(cache / "scores.npz", **sc); info.to_csv(cache / "walk_forward.csv", index=False)
        imp.to_csv(cache / "importance.csv"); pd.Series(checks).to_csv(cache / "checks.csv")
    log(f"scores ready at {time.time() - t0:.0f}s")

    # ---------------- out-of-sample signal quality
    years = p.dates[ds.rows].year
    first = int(np.flatnonzero(years >= a.first_test_year)[0])
    start = p.dates[ds.rows[first] + 1]                       # first execution day
    ics = {k: ml.daily_ic(ds, v, p) for k, v in sc.items()}
    ic_year = pd.DataFrame({k: v.groupby(v.index.year).mean() for k, v in ics.items()})
    ic_sum = pd.DataFrame({k: {"mean IC": v.mean(), "IC IR (annualised)": v.mean() / v.std() * np.sqrt(TD / ml.STEP),
                               "share of days IC>0": (v > 0).mean(), "worst year": ic_year[k].min()} for k, v in ics.items()}).T
    dec = pd.DataFrame({k: ml.decile_returns(ds, v) for k, v in sc.items()})
    dec.index = [f"D{i + 1}" for i in dec.index]
    ic_year.to_csv(out / "tables" / "ic_by_year.csv"); dec.to_csv(out / "tables" / "deciles.csv")

    # ---------------- backtests of the out-of-sample scores
    row_of = {int(t): i for i, t in enumerate(ds.rows)}
    def score_fn(scores):
        def f(t):
            i = row_of.get(int(t))
            if i is None or not np.isfinite(scores[i]).any():
                return None
            full = np.full(len(p.codes), np.nan); full[ds.cols] = scores[i]
            return full
        return f
    tg = {k: S.factor_portfolio(p, fac, {}, start, n_hold=N_HOLD, every=EVERY, buffer=BUFFER, score_fn=score_fn(v))
          for k, v in sc.items()}
    mkt_ew = S.market_index(p, fac)
    expo = S.vol_exposure(mkt_ew, 0.20)
    strategies = {
        "ML-GBM": tg["gbm"],
        "ML-GBM + vol target": S.apply_exposure(tg["gbm"], expo),
        "ML-Ridge (linear)": tg["ridge"],
        "Placebo (shuffled labels)": tg["placebo"],
        "Rule-based SmallCap-MF": S.factor_portfolio(p, fac, MF, start, n_hold=N_HOLD, every=EVERY, buffer=BUFFER),
        "EqualWeight-All": S.equal_weight_universe(p, fac, start, every=EVERY),
    }
    res = {n: run_stock_strategy(p, n, t, start) for n, t in strategies.items()}
    bench = S.cap_weighted_index(p).loc[start:]
    regimes = find_regimes(bench); regimes = regimes[regimes["days"] >= 20].reset_index(drop=True)
    eq = {n: r.equity for n, r in res.items()}
    pd.DataFrame(eq).join(bench.rename("Market")).to_csv(out / "tables" / "equity_curves.csv")

    def stats(n, s=None, e=None):
        r = res[n]; q = r.equity.loc[s:e]; st = perf_stats(q, bench)
        m = q.resample("ME").last().pct_change().dropna(); bm = bench.reindex(q.index).resample("ME").last().pct_change().dropna()
        d = {k: st[k] for k in ("CAGR", "Vol", "Sharpe", "MaxDD", "Calmar", "Worst year", "Win months", "Beta", "Alpha")}
        d["Months beating market"] = (m > bm.reindex(m.index)).mean()
        d["Turnover/yr"] = r.turnover.loc[s:e].mean() * TD; d["Costs/yr"] = r.cost.loc[s:e].mean() * TD
        d["Exposure"] = r.exposure.loc[s:e].mean()
        return d
    full = pd.DataFrame({n: stats(n) for n in strategies}).T
    mst = perf_stats(bench, bench)
    full.loc["Market"] = {**{k: mst[k] for k in ("CAGR", "Vol", "Sharpe", "MaxDD", "Calmar", "Worst year", "Win months", "Beta", "Alpha")}}
    pw = {n: position_win_rate(p, strategies[n]) for n in ("ML-GBM", "ML-Ridge (linear)", "Placebo (shuffled labels)", "Rule-based SmallCap-MF")}
    full["Positions that made money"] = pd.Series({n: v[0] for n, v in pw.items()})
    full["Positions beating median stock"] = pd.Series({n: v[1] for n, v in pw.items()})
    full.to_csv(out / "tables" / "performance.csv")
    periods = [("2005-01-01", "2009-12-31"), ("2010-01-01", "2014-12-31"), ("2015-01-01", "2019-12-31"), ("2020-01-01", None)]
    sub = {}
    for s_, e_ in periods:
        lab = f"{s_[:4]}–{(e_ or str(p.dates[-1].year))[:4]}"
        for n in ("ML-GBM", "ML-GBM + vol target", "ML-Ridge (linear)", "Rule-based SmallCap-MF", "EqualWeight-All"):
            st = stats(n, s_, e_); sub[(lab, n)] = {k: st[k] for k in ("CAGR", "Sharpe", "MaxDD", "Win months")}
        st = perf_stats(bench.loc[s_:e_]); sub[(lab, "Market")] = {k: st[k] for k in ("CAGR", "Sharpe", "MaxDD", "Win months")}
    subdf = pd.DataFrame(sub).T; subdf.index.names = ["period", "strategy"]; subdf.to_csv(out / "tables" / "sub_periods.csv")

    show = ["ML-GBM", "ML-GBM + vol target", "ML-Ridge (linear)", "Rule-based SmallCap-MF", "EqualWeight-All"]
    rt = regime_table(regimes, {n: eq[n] for n in show}).rename(columns={"Index": "Market"})
    rs = regime_summary(regimes, {**{n: eq[n] for n in show}, "Market": bench})
    rt.to_csv(out / "tables" / "regime_returns.csv"); rs.to_csv(out / "tables" / "regime_summary.csv")
    def by_year(e):
        ye = e.resample("YE").last(); r = ye.pct_change(); r.iloc[0] = ye.iloc[0] / e.iloc[0] - 1
        return r
    yearly = pd.DataFrame({n: by_year(e) for n, e in {**{n: eq[n] for n in show}, "Market": bench}.items()})
    yearly.index = yearly.index.year.astype(str); yearly.to_csv(out / "tables" / "yearly_returns.csv")

    # robustness of the ML portfolio (same out-of-sample scores, different execution assumptions)
    rob = {}
    def add(g, lab, t, **kw):
        r = run_stock_strategy(p, lab, t, start, **kw); st = perf_stats(r.equity, bench)
        rob[(g, lab)] = {"CAGR": st["CAGR"], "Sharpe": st["Sharpe"], "MaxDD": st["MaxDD"], "Turnover/yr": r.turnover.mean() * TD}
    g = tg["gbm"]
    add("baseline", "ML-GBM: 50 names, 20-day rebalance", g)
    for sl in (0.0, 0.003, 0.005):
        add("costs", f"slippage {sl * 1e4:.0f}bp per side", g, costs=Costs(slippage=sl))
    add("timing", "execute one day later", {t + 1: v for t, v in g.items()})
    add("timing", "execute five days later", {t + 5: v for t, v in g.items()})
    for n in (20, 100, 200):
        add("breadth", f"{n} names", S.factor_portfolio(p, fac, {}, start, n_hold=n, every=EVERY, buffer=BUFFER, score_fn=score_fn(sc["gbm"])))
    for ev in (10, 40):
        add("frequency", f"rebalance every {ev} days", S.factor_portfolio(p, fac, {}, start, n_hold=N_HOLD, every=ev, buffer=BUFFER, score_fn=score_fn(sc["gbm"])))
    add("universe", "only the 1,500 largest stocks", S.factor_portfolio(p, fac, {}, start, n_hold=N_HOLD, every=EVERY, buffer=BUFFER, top_cap=1500, score_fn=score_fn(sc["gbm"])))
    add("universe", "only the 800 largest stocks", S.factor_portfolio(p, fac, {}, start, n_hold=N_HOLD, every=EVERY, buffer=BUFFER, top_cap=800, score_fn=score_fn(sc["gbm"])))
    add("universe", "exclude least liquid 30%", S.factor_portfolio(p, fac, {}, start, n_hold=N_HOLD, every=EVERY, buffer=BUFFER, liq_floor_pct=0.30, score_fn=score_fn(sc["gbm"])))
    for tv in (0.15, 0.25):
        add("vol target", f"target {tv:.0%}", S.apply_exposure(g, S.vol_exposure(mkt_ew, tv)))
    robdf = pd.DataFrame(rob).T; robdf.index.names = ["test", "variant"]; robdf.to_csv(out / "tables" / "robustness.csv")

    # figures
    subt = "Every score is out-of-sample: the model for year Y is fitted on data ending before Y. Costs, price limits, suspensions included. Shaded = bear phases."
    equity_fig(out / "figures" / "equity_ml.png", f"Walk-forward ML stock selection, {start:%Y}–{p.dates[-1]:%Y} (all out-of-sample)", subt,
               {n: eq[n] for n in show}, bench, "Market (cap-weighted)", regimes)
    bar_regime_fig(out / "figures" / "bull_bear_returns.png", rs)

    P1 = lambda v: pct(v, 1); F2 = lambda v: "" if not np.isfinite(v) else f"{v:.2f}"; P0 = lambda v: pct(v, 0)
    fm = {"CAGR": P1, "Vol": P1, "Sharpe": F2, "MaxDD": P1, "Calmar": F2, "Worst year": P1, "Win months": P0, "Beta": F2, "Alpha": P1,
          "Months beating market": P0, "Turnover/yr": lambda v: "" if not np.isfinite(v) else f"{v:.1f}x", "Costs/yr": lambda v: pct(v, 2),
          "Exposure": P0, "Positions that made money": P0, "Positions beating median stock": P0}
    top = (imp.div(imp.sum()).mean(axis=1).sort_values(ascending=False) if imp is not None else pd.Series(dtype=float))
    md = [f"<!-- generated by scripts/train_ml.py; data to {p.dates[-1]:%Y-%m-%d} -->",
          f"Features: {len(ds.names)}. Cross-sections: {len(ds.rows)} (every {ml.STEP} days), {int(np.isfinite(ds.y).sum()):,} labelled stock-days. "
          f"Label: return from the open of t+1 to the close of t+{ml.H}. Out-of-sample from {start:%Y-%m-%d}.", "",
          "### Leakage checks", "", md_table(pd.Series(checks, name="result").to_frame().rename_axis("check")), "",
          "### Walk-forward schedule", "", md_table(info, {"val_ic": lambda v: f"{v:.3f}"}, index=False), "",
          "### Out-of-sample rank IC (score vs. next 20-day return)", "", md_table(ic_sum.rename_axis("Model"), {"share of days IC>0": P0, "IC IR (annualised)": F2}, default=lambda v: f"{v:.3f}"), "",
          "### IC by year", "", md_table(ic_year.rename_axis("Year").rename(index=str), default=lambda v: f"{v:.3f}"), "",
          "### Mean 20-day return relative to the average stock, by score decile (D10 = highest score)", "", md_table(dec.rename_axis("Decile"), default=lambda v: pct(v, 2)), "",
          f"### Backtest of out-of-sample scores ({start:%Y-%m-%d} → {p.dates[-1]:%Y-%m-%d})", "", md_table(full.rename_axis("Strategy"), fm), "",
          "### Sub-periods", "", md_table(subdf.reset_index(), {"CAGR": P1, "Sharpe": F2, "MaxDD": P1, "Win months": P0}, index=False), "",
          "### Market phases", "", md_table(rt, default=P0), "",
          "### Annualised return over all bull / bear days", "", md_table(rs.rename_axis("Strategy"), default=P1), "",
          "### Calendar years", "", md_table(yearly.rename_axis("Year"), default=P0), "",
          "### Robustness", "", md_table(robdf.reset_index(), {"CAGR": P1, "Sharpe": F2, "MaxDD": P1, "Turnover/yr": fm["Turnover/yr"]}, index=False), "",
          "### Feature importance (share of split gain, average over yearly models)", "", md_table(top.head(20).rename("share").to_frame().rename_axis("Feature"), default=P1), ""]
    (out / "results.md").write_text("\n".join(md))
    log(f"wrote {out / 'results.md'} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
