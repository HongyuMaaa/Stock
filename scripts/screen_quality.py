#!/usr/bin/env python3
"""Score every A-share company on the six quality criteria and build the stock pool.

  python scripts/download_fundamentals.py     # reports, dividends, balance sheet, pledges (once)
  python scripts/screen_quality.py            # -> reports/quality/results.md, tables/*.csv
(current prices / PE / PB come from data/fundamentals/spot_tencent.parquet, refreshed with --refresh-quotes)
"""
import argparse, sys, time, warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")
from quant import quality as Q
from run_backtests import md_table, pct

CS = ["c1", "c2", "c3", "c4", "c5", "c6"]
LABEL = {"c1": "1 Leader", "c2": "2 Stable", "c3": "3 Dividend", "c4": "4 Valuation", "c5": "5 Governance*", "c6": "6 Growth*"}
WELL_KNOWN = ["600519", "000858", "600036", "601318", "300750", "002594", "600276", "000651", "000333", "600900", "601088",
              "600309", "603288", "600436", "300760", "601888", "000002", "600030", "601012", "002714", "600585", "601166",
              "000895", "600066", "600406", "002415", "600887", "601899", "600028", "601857"]


def refresh_quotes(data_dir):
    import requests
    u = pd.read_csv(Path(data_dir) / "meta" / "stocks.csv", dtype=str); u = u[u.board != "bse"]
    sym = [("sh" if e == "SH" else "sz") + c for c, e in zip(u.code, u.exchange)]
    s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"; rows = []
    for i in range(0, len(sym), 60):
        for _ in range(5):
            try:
                r = s.get("https://qt.gtimg.cn/q=" + ",".join(sym[i:i + 60]), timeout=20); r.encoding = "gbk"; break
            except Exception:
                time.sleep(3)
        for line in r.text.strip().split(";"):
            f = line.split("~")
            if len(f) > 64:
                rows.append(dict(code=f[2], name=f[1], price=f[3], date=f[30][:8], pe_ttm=f[39], float_cap_yi=f[44], mktcap_yi=f[45],
                                 pb=f[46], pe_static=f[53], pe_dyn=f[52], div_yield=f[64]))
    pd.DataFrame(rows).to_parquet(Path(data_dir) / "fundamentals" / "spot_tencent.parquet", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data"); ap.add_argument("--out", default="reports/quality")
    ap.add_argument("--refresh-quotes", action="store_true")
    ap.add_argument("--min-cap", type=float, default=20.0, help="minimum market value for the pool, CNY bn")
    ap.add_argument("--per-industry", type=int, default=2)
    a = ap.parse_args()
    out = Path(a.out); (out / "tables").mkdir(parents=True, exist_ok=True)
    if a.refresh_quotes:
        refresh_quotes(a.data_dir)
    D = Q.load(a.data_dir)
    t = Q.score(Q.build_table(D))
    t = t[t["price"].notna()]
    t["missing"] = t[CS].apply(lambda r: ", ".join(LABEL[c].split(" ", 1)[1] for c in CS if r[c] < 2), axis=1)
    t.round(4).to_csv(out / "tables" / "all_scores.csv")

    show = ["name", "industry", "mktcap_bn", "pe_ttm", "pb", "yield", "payout", "div_streak", "roe_mean", "roe_min", "worst_np_yoy",
            "np_ytd_yoy", "rev_cagr5", "ind_rev_cagr5", "rank_rev", "rank_np", "cash_conv5", "debt_ratio", "pledge"]
    ren = {"name": "Name", "industry": "Industry", "mktcap_bn": "Mkt cap bn", "pe_ttm": "P/E", "pb": "P/B", "yield": "Yield", "payout": "Payout",
           "div_streak": "Div yrs", "roe_mean": "ROE 10y", "roe_min": "ROE min", "worst_np_yoy": "Worst profit yr", "np_ytd_yoy": "Profit 9M26",
           "rev_cagr5": "Rev 5y", "ind_rev_cagr5": "Industry rev 5y", "rank_rev": "Rank rev", "rank_np": "Rank profit", "cash_conv5": "Cash conv",
           "debt_ratio": "Debt ratio", "pledge": "Pledged", "missing": "Not fully met"}
    P0 = lambda v: pct(v, 0); P1 = lambda v: pct(v, 1); F1 = lambda v: "" if not np.isfinite(v) else f"{v:.1f}"; I = lambda v: "" if not np.isfinite(v) else f"{v:.0f}"
    fm = {"Mkt cap bn": I, "P/E": F1, "P/B": F1, "Yield": P1, "Payout": P0, "Div yrs": I, "ROE 10y": F1, "ROE min": F1, "Worst profit yr": P0,
          "Profit 9M26": P0, "Rev 5y": P0, "Industry rev 5y": P0, "Rank rev": I, "Rank profit": I, "Cash conv": F1, "Debt ratio": P0, "Pledged": P0,
          **{LABEL[c]: I for c in CS}}
    def tab(df, extra=()):
        return md_table(df[show + list(extra)].rename(columns={**ren, **LABEL}).rename_axis("Code"), fm)

    big = t[t["mktcap_bn"] >= a.min_cap]
    all6 = big[big["n_full"] == 6].sort_values("mktcap_bn", ascending=False)
    five = big[(big["n_full"] == 5)].sort_values("mktcap_bn", ascending=False)
    small6 = t[(t["mktcap_bn"] < a.min_cap) & (t["n_full"] >= 5) & (t["n_fail"] == 0)].sort_values("mktcap_bn", ascending=False)
    # pool: all-six names first, then five-of-six with no outright failure; cap per industry
    cand = pd.concat([all6, five[five["n_fail"] == 0]])
    cand = cand.sort_values(["n_full", "total", "yield"], ascending=False)
    pool = cand.groupby("industry", sort=False).head(a.per_industry)
    pool.round(4).to_csv(out / "tables" / "pool.csv"); all6.round(4).to_csv(out / "tables" / "all_six.csv"); five.round(4).to_csv(out / "tables" / "five_of_six.csv")
    funnel = pd.Series({"Companies scored (main board, ChiNext, STAR)": len(t), **{f"Meet {LABEL[c]}": int((t[c] == 2).sum()) for c in CS},
                        "Meet all six": int((t["n_full"] == 6).sum()), f"… and market value ≥ {a.min_cap:.0f}bn": len(all6),
                        "Meet five of six": int((t["n_full"] == 5).sum()), f"… and market value ≥ {a.min_cap:.0f}bn ": len(five)}, name="count").to_frame()
    wk = t.reindex([c for c in WELL_KNOWN if c in t.index])
    md = [f"<!-- generated by scripts/screen_quality.py; quotes {D['spot']['date'].iloc[0]}, reports to {D['latest']} -->",
          "### Funnel", "", md_table(funnel.rename_axis("Step"), {"count": I}), "",
          f"### Meet all six (market value ≥ {a.min_cap:.0f}bn)", "", tab(all6), "",
          f"### Meet five of six (market value ≥ {a.min_cap:.0f}bn)", "", tab(five, ["missing"] + CS), "",
          "### Smaller companies meeting at least five with no outright failure", "", tab(small6, ["missing"]), "",
          f"### Pool (max {a.per_industry} per industry)", "", tab(pool, ["missing"]), "",
          "### Well-known names", "", tab(wk, ["missing"] + CS), ""]
    (out / "results.md").write_text("\n".join(md))
    print(funnel.to_string()); print(pool[["name", "industry", "mktcap_bn", "pe_ttm", "pb", "yield", "n_full", "missing"]].round(2).to_string())


if __name__ == "__main__":
    main()
