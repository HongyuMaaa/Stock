"""Performance statistics and bull/bear regime detection."""
from __future__ import annotations

import numpy as np
import pandas as pd

TD = 244  # A-share trading days per year
RF = 0.02  # annual risk-free rate used for Sharpe / Sortino


def drawdown(eq: pd.Series) -> pd.Series:
    return eq / eq.cummax() - 1


def perf_stats(eq: pd.Series, bench: pd.Series | None = None) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(TD)
    ex = r - RF / TD
    dd = drawdown(eq)
    downside = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(TD)
    trough = dd.idxmin()
    peak = eq.loc[:trough].idxmax()
    rec = eq.loc[trough:][eq.loc[trough:] >= eq.loc[peak]]
    m = eq.resample("ME").last().pct_change().dropna()
    y = eq.resample("YE").last().pct_change().dropna()
    out = {
        "CAGR": cagr, "Vol": vol, "Sharpe": ex.mean() * TD / vol if vol > 0 else np.nan,
        "Sortino": ex.mean() * TD / downside if downside > 0 else np.nan,
        "MaxDD": dd.min(), "Calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
        "DD peak": peak.date(), "DD trough": trough.date(),
        "DD recovery days": (rec.index[0] - peak).days if len(rec) else np.nan,
        "Win months": (m > 0).mean(), "Worst month": m.min() if len(m) else np.nan,
        "Worst year": y.min() if len(y) else np.nan, "Total return": eq.iloc[-1] / eq.iloc[0] - 1,
    }
    if bench is not None:
        b = bench.reindex(eq.index).ffill().pct_change().dropna()
        rr, bb = r.align(b, join="inner")
        beta = np.cov(rr, bb)[0, 1] / bb.var()
        act = rr - bb
        out.update({
            "Beta": beta, "Alpha": (rr.mean() - RF / TD - beta * (bb.mean() - RF / TD)) * TD,
            "Info ratio": act.mean() / act.std() * np.sqrt(TD) if act.std() > 0 else np.nan,
        })
    return out


def find_regimes(px: pd.Series, threshold: float = 0.20, min_days: int = 60) -> pd.DataFrame:
    """Split a price series into alternating bull / bear phases.

    A bear starts at a peak once price has fallen `threshold` from it; a bull starts at a
    trough once price has risen threshold/(1-threshold) (i.e. recovered the same distance).
    Classic 20% rule, applied mechanically. Phases shorter than `min_days` trading days are merged.
    """
    px = px.dropna()
    up = threshold / (1 - threshold)
    turns = [(px.index[0], None)]
    hi = lo = px.iloc[0]; hi_d = lo_d = px.index[0]
    state = None
    for d, p in px.items():
        if state in (None, "bull"):
            if p > hi:
                hi, hi_d = p, d
            if p <= hi * (1 - threshold):
                if state is None:
                    turns[0] = (px.index[0], "bull")
                turns.append((hi_d, "bear")); state = "bear"; lo, lo_d = p, d
                continue
        if state in (None, "bear"):
            if p < lo:
                lo, lo_d = p, d
            if p >= lo * (1 + up):
                if state is None:
                    turns[0] = (px.index[0], "bear")
                turns.append((lo_d, "bull")); state = "bull"; hi, hi_d = p, d
    if turns[0][1] is None:
        turns[0] = (px.index[0], "bull")
    # merge phases shorter than `min_days` into their neighbours, then re-anchor the
    # surrounding turning points on the true extreme of the merged span
    def n_days(i):
        e = turns[i + 1][0] if i + 1 < len(turns) else px.index[-1]
        return px.loc[turns[i][0]:e].shape[0] - 1

    def reanchor(k):
        if 0 < k < len(turns):
            lo_d = turns[k - 1][0]
            hi_d = turns[k + 1][0] if k + 1 < len(turns) else px.index[-1]
            seg = px.loc[lo_d:hi_d].iloc[1:-1] if k + 1 < len(turns) else px.loc[lo_d:hi_d].iloc[1:]
            if len(seg):
                turns[k] = (seg.idxmax() if turns[k][1] == "bear" else seg.idxmin(), turns[k][1])

    while True:
        short = [(n_days(i), i) for i in range(1, len(turns) - 1) if n_days(i) < min_days]
        if not short:
            break
        _, i = min(short)
        del turns[i:i + 2]
        reanchor(i - 1); reanchor(i)
    rows = []
    for i, (d, s) in enumerate(turns):
        e = turns[i + 1][0] if i + 1 < len(turns) else px.index[-1]
        rows.append(dict(regime=s, start=d, end=e, days=px.loc[d:e].shape[0] - 1, index_return=px.loc[e] / px.loc[d] - 1))
    df = pd.DataFrame(rows)
    return df[df["days"] > 0].reset_index(drop=True)


def regime_table(regimes: pd.DataFrame, equities: dict[str, pd.Series]) -> pd.DataFrame:
    """Total return of each strategy inside each market phase."""
    rows = []
    for _, g in regimes.iterrows():
        row = {"Phase": f"{g.regime.upper()} {g.start:%Y-%m}→{g.end:%Y-%m}", "Index": g.index_return}
        for n, eq in equities.items():
            seg = eq.loc[g.start:g.end]
            row[n] = seg.iloc[-1] / seg.iloc[0] - 1 if len(seg) > 1 else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("Phase")


def regime_summary(regimes: pd.DataFrame, equities: dict[str, pd.Series]) -> pd.DataFrame:
    """Annualised return per strategy over all bull days and over all bear days."""
    out = {}
    for n, eq in equities.items():
        r = eq.pct_change()
        d = {}
        for kind in ("bull", "bear"):
            mask = pd.Series(False, index=eq.index)
            for _, g in regimes[regimes.regime == kind].iterrows():
                mask.loc[g.start:g.end] = True
            mask.loc[regimes.start.iloc[0]] = False
            rr = r[mask].dropna()
            d[f"{kind} ann. return"] = (1 + rr).prod() ** (TD / max(len(rr), 1)) - 1
            d[f"{kind} ann. vol"] = rr.std() * np.sqrt(TD)
        out[n] = d
    return pd.DataFrame(out).T
