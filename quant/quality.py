"""Fundamental 'quality at a reasonable price' screen for A-shares.

Six criteria, each scored 2 (meets), 1 (partly), 0 (fails) from published data. Criteria 5 (governance)
and 6 (industry outlook) can only be proxied with numbers; the screen reports what the data shows and
leaves the qualitative judgement explicit.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FIN = ("银行", "保险", "证券", "多元金融")
COLS = {"股票代码": "code", "股票简称": "name", "每股收益": "eps", "营业总收入-营业总收入": "rev", "净利润-净利润": "np",
        "每股净资产": "bvps", "净资产收益率": "roe", "每股经营现金流量": "ocfps", "销售毛利率": "gm", "所处行业": "industry",
        "营业总收入-同比增长": "rev_yoy", "净利润-同比增长": "np_yoy"}


def _report(path) -> pd.DataFrame:
    d = pd.read_parquet(path).rename(columns=COLS)[list(COLS.values())]
    d = d[d["code"].str.match(r"^(60|00|30|68)\d{4}$")].drop_duplicates("code").set_index("code")
    for c in d.columns.difference(["name", "industry"]):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


def load(data_dir="data", latest="20260930") -> dict:
    f = Path(data_dir) / "fundamentals"
    ly = int(latest[:4])
    annual = {int(p.stem[-8:-4]): _report(p) for p in sorted(f.glob("reports_*1231.parquet"))}
    q_now, q_prev = _report(f / f"reports_{latest}.parquet"), _report(f / f"reports_{ly - 1}{latest[4:]}.parquet")
    div = []
    for p in sorted(f.glob("dividends_*.parquet")):
        d = pd.read_parquet(p)
        d = d.rename(columns={"代码": "code", "现金分红-现金分红比例": "cash10", "方案进度": "status", "除权除息日": "ex_date"})
        d["fy"] = int(p.stem[-8:-4]); d["period"] = p.stem[-4:]
        div.append(d[["code", "cash10", "status", "ex_date", "fy", "period"]])
    div = pd.concat(div) if div else pd.DataFrame(columns=["code", "cash10", "status", "ex_date", "fy", "period"])
    div["cash10"] = pd.to_numeric(div["cash10"], errors="coerce").fillna(0.0)
    div["ex_date"] = pd.to_datetime(div["ex_date"], errors="coerce")
    spot = pd.read_parquet(f / "spot_tencent.parquet").set_index("code")
    for c in spot.columns.difference(["name", "date"]):
        spot[c] = pd.to_numeric(spot[c], errors="coerce")
    bal = None
    if (f / f"balance_{latest}.parquet").exists():
        bal = pd.read_parquet(f / f"balance_{latest}.parquet")
    pledge = pd.read_parquet(f / "pledge.parquet") if (f / "pledge.parquet").exists() else None
    return dict(annual=annual, q_now=q_now, q_prev=q_prev, div=div, spot=spot, bal=bal, pledge=pledge, latest=latest)


def build_table(D: dict, last_fy: int | None = None) -> pd.DataFrame:
    A = D["annual"]; last_fy = last_fy or max(A)
    yrs10 = [y for y in range(last_fy - 9, last_fy + 1) if y in A]
    yrs5 = yrs10[-5:]
    base = A[last_fy]
    t = pd.DataFrame(index=base.index)
    t["name"] = base["name"]; t["industry"] = base["industry"]
    t["is_fin"] = t["industry"].astype(str).str.startswith(FIN)
    roe = pd.DataFrame({y: A[y]["roe"] for y in yrs10}).reindex(t.index)
    npf = pd.DataFrame({y: A[y]["np"] for y in yrs10}).reindex(t.index)
    rev = pd.DataFrame({y: A[y]["rev"] for y in yrs10}).reindex(t.index)
    eps = pd.DataFrame({y: A[y]["eps"] for y in yrs10}).reindex(t.index)
    ocf = pd.DataFrame({y: A[y]["ocfps"] for y in yrs10}).reindex(t.index)
    t["years"] = roe.notna().sum(axis=1)
    t["roe_mean"] = roe.mean(axis=1); t["roe_min"] = roe.min(axis=1); t["roe_last"] = roe[last_fy]
    t["roe_cv"] = roe.std(axis=1) / roe.mean(axis=1).abs()
    t["loss_years"] = (npf <= 0).sum(axis=1)
    t["worst_np_yoy"] = npf.pct_change(axis=1).where(npf.shift(axis=1) > 0).min(axis=1)
    t["np_cagr5"] = (npf[last_fy] / npf[last_fy - 5]).where(npf[last_fy - 5] > 0) ** 0.2 - 1 if last_fy - 5 in npf else np.nan
    t["rev_cagr5"] = (rev[last_fy] / rev[last_fy - 5]).where(rev[last_fy - 5] > 0) ** 0.2 - 1 if last_fy - 5 in rev else np.nan
    t["cash_conv5"] = ocf[yrs5].sum(axis=1) / eps[yrs5].sum(axis=1).where(eps[yrs5].sum(axis=1) > 0)
    t["rev"] = rev[last_fy]; t["np"] = npf[last_fy]; t["gm"] = base["gm"]
    qn, qp = D["q_now"].reindex(t.index), D["q_prev"].reindex(t.index)
    t["np_ttm"] = t["np"] + qn["np"] - qp["np"]
    t["np_ytd_yoy"] = qn["np_yoy"] / 100; t["rev_ytd_yoy"] = qn["rev_yoy"] / 100
    s = D["spot"].reindex(t.index)
    t["price"] = s["price"]; t["mktcap_bn"] = s["mktcap_yi"] / 10; t["pe_ttm"] = s["pe_ttm"]; t["pb"] = s["pb"]
    t["yield_quote"] = s["div_yield"] / 100
    # ---- dividends per fiscal year (implemented or approved plans), per share
    dv = D["div"]
    dv = dv[~dv["status"].astype(str).str.contains("取消|未通过|不分配")]
    dps = dv.groupby(["code", "fy"])["cash10"].sum().unstack().reindex(t.index).fillna(0.0) / 10
    for y in yrs10:
        if y not in dps:
            dps[y] = 0.0
    dps = dps[yrs10]
    t["dps_last"] = dps[last_fy]; t["dps_prev"] = dps[last_fy - 1]
    t["div_years10"] = (dps > 0).sum(axis=1)
    streak = (dps[yrs10[::-1]] > 0).to_numpy()
    t["div_streak"] = np.where(streak.all(1), len(yrs10), streak.argmin(1))
    t["payout"] = (t["dps_last"] / eps[last_fy]).where(eps[last_fy] > 0)
    t["yield_fy"] = t["dps_last"] / t["price"]
    # last fiscal year's total cash distribution (final + interim) over today's price; the quote feed's
    # figure misses interim payments, so it is only a fallback
    t["yield"] = t["yield_fy"].where(t["yield_fy"] > 0, t["yield_quote"])
    # ---- industry position (largest by revenue / profit / market value inside the Eastmoney industry)
    g = t.groupby("industry")
    t["rank_rev"] = g["rev"].rank(ascending=False); t["rank_np"] = g["np"].rank(ascending=False)
    t["rank_cap"] = g["mktcap_bn"].rank(ascending=False); t["n_ind"] = g["rev"].transform("size")
    t["rev_share"] = t["rev"] / g["rev"].transform("sum")
    t["ind_roe_med"] = g["roe_mean"].transform("median")
    # ---- industry growth: revenue of companies present in both years
    both = rev[last_fy].notna() & rev[last_fy - 5].notna() & (rev[last_fy - 5] > 0)
    ig = (rev[last_fy][both].groupby(t["industry"][both]).sum() / rev[last_fy - 5][both].groupby(t["industry"][both]).sum()) ** 0.2 - 1
    t["ind_rev_cagr5"] = t["industry"].map(ig)
    ig2 = (rev[last_fy][both].groupby(t["industry"][both]).sum() / rev[last_fy - 1].reindex(t.index)[both].groupby(t["industry"][both]).sum()) - 1
    t["ind_rev_yoy"] = t["industry"].map(ig2)
    # ---- balance sheet / pledge
    if D["bal"] is not None:
        b = D["bal"].rename(columns={"股票代码": "code", "资产负债率": "debt_ratio"}).drop_duplicates("code").set_index("code")
        t["debt_ratio"] = pd.to_numeric(b["debt_ratio"], errors="coerce").reindex(t.index) / 100
    else:
        t["debt_ratio"] = np.nan
    if D["pledge"] is not None:
        pl = D["pledge"].rename(columns={"股票代码": "code", "质押比例": "pledge"}).drop_duplicates("code").set_index("code")
        t["pledge"] = pd.to_numeric(pl["pledge"], errors="coerce").reindex(t.index).fillna(0.0) / 100
    else:
        t["pledge"] = np.nan
    t["is_st"] = t["name"].astype(str).str.contains("ST|退")
    return t


def score(t: pd.DataFrame) -> pd.DataFrame:
    """Add c1..c6 (0/1/2) and the total. Thresholds are deliberately plain and listed in the report."""
    s = t.copy()
    fin = s["is_fin"]
    top3 = (s["rank_rev"] <= 3) | (s["rank_np"] <= 3)
    top5 = (s["rank_rev"] <= 5) | (s["rank_np"] <= 5) | (s["rank_cap"] <= 3)
    moat = s["roe_mean"] >= s["ind_roe_med"]
    s["c1"] = np.select([top3 & moat & (s["mktcap_bn"] >= 20), top5 & (s["mktcap_bn"] >= 10)], [2, 1], 0)

    cash_ok = fin | (s["cash_conv5"] >= 0.8)
    full = (s["years"] >= 10) & (s["loss_years"] == 0) & (s["roe_mean"] >= 12) & (s["roe_min"] >= 6) & \
           (s["worst_np_yoy"] >= -0.30) & cash_ok
    full_fin = fin & (s["years"] >= 10) & (s["loss_years"] == 0) & (s["roe_mean"] >= 9) & (s["roe_min"] >= 6) & (s["worst_np_yoy"] >= -0.30)
    part = (s["years"] >= 8) & (s["loss_years"] == 0) & (s["roe_mean"] >= 8) & (s["worst_np_yoy"] >= -0.50) & (fin | (s["cash_conv5"] >= 0.6))
    now_ok = s["np_ytd_yoy"].fillna(0) >= -0.20            # current-year profit not falling sharply
    s["c2"] = np.select([(full | full_fin) & now_ok, part | full | full_fin], [2, 1], 0)

    sustain = s["payout"].between(0.25, 0.90) & (s["dps_last"] >= 0.8 * s["dps_prev"])
    s["c3"] = np.select([(s["yield"] >= 0.03) & (s["div_streak"] >= 10) & sustain,
                         (s["yield"] >= 0.02) & (s["div_streak"] >= 5) & s["payout"].between(0.15, 1.10)], [2, 1], 0)

    pe, pb = s["pe_ttm"], s["pb"]
    s["c4"] = np.select([(pe > 0) & (pe <= 20) & (pb <= 5), (pe > 0) & (pe <= 28) & (pb <= 8)], [2, 1], 0)

    lev_ok = fin | (s["debt_ratio"] <= 0.70) | s["debt_ratio"].isna()
    clean = (~s["is_st"]) & (s["pledge"].fillna(0) <= 0.15) & lev_ok & (s["years"] >= 10) & (s["loss_years"] == 0) & \
            (s["div_streak"] >= 10) & (fin | (s["cash_conv5"] >= 0.8))
    some = (~s["is_st"]) & (s["pledge"].fillna(0) <= 0.30) & (s["loss_years"] <= 1) & (s["div_streak"] >= 5)
    s["c5"] = np.select([clean, some], [2, 1], 0)

    grow = (s["ind_rev_cagr5"] >= 0.05) & (s["rev_cagr5"] >= 0.05) & (s["rev_ytd_yoy"] >= -0.05)
    flat = (s["ind_rev_cagr5"] >= 0.0) & (s["rev_cagr5"] >= 0.0) & (s["rev_ytd_yoy"] >= -0.15)
    s["c6"] = np.select([grow, flat], [2, 1], 0)

    cs = ["c1", "c2", "c3", "c4", "c5", "c6"]
    s["n_full"] = (s[cs] == 2).sum(axis=1); s["n_fail"] = (s[cs] == 0).sum(axis=1); s["total"] = s[cs].sum(axis=1)
    return s
