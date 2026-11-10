"""Data-driven stock selection: technical features -> gradient-boosted model -> walk-forward scores.

Leakage controls (each one is enforced in code and checked by scripts/train_ml.py --checks):
  * every feature at day t is a causal function of bars up to the close of t (rolling / EWM only,
    backward-adjusted prices whose factors are known at t) - verified by recomputing features on
    a panel truncated at t and comparing
  * cross-sectional preprocessing (ranks) uses only the stocks of the same day
  * the label of day t is the return from the OPEN of t+1 to the close of t+H
  * a model used in year Y is fitted only on samples whose label window ended before the first
    trading day of Y (purged walk-forward); hyper-parameters are chosen on a validation slice that
    lies inside that training window and is itself purged from the inner fit
  * the universe at day t (listing age, liquidity, price, ST flag) uses data up to t and includes
    stocks that were later delisted
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .panel import Panel
from .strategies import Factors

H = 20            # label horizon, trading days
STEP = 5          # one cross-section every STEP days
WARMUP = 260      # bars needed by the slowest feature


# --------------------------------------------------------------------------- features
def compute_features(p: Panel, cols: np.ndarray, rows: np.ndarray | None = None) -> dict:
    """Return {name: array[len(rows) or T, len(cols)]} of raw features for the stock columns `cols`.
    Everything is computed from data up to and including each row's close."""
    D = lambda a: pd.DataFrame(np.asarray(a[:, cols], dtype=np.float64))
    px, ret = D(p.adj_close_ff), D(p.ret)
    adj = D(p.adj)
    hi, lo, op, cl = D(p.high) * adj, D(p.low) * adj, D(p.open) * adj, D(p.close) * adj
    turn, amt = D(p.turnover), D(p.amount)
    prev = px.shift(1)
    f = {}
    keep = (lambda x: x.to_numpy(dtype=np.float32)) if rows is None else (lambda x: x.to_numpy(dtype=np.float32)[rows])

    for k in (1, 5, 10, 20, 60, 120, 250):
        f[f"ret_{k}"] = keep(px / px.shift(k) - 1)
    f["mom_12_1"] = keep(px.shift(20) / px.shift(250) - 1)
    for k in (20, 60):
        f[f"vol_{k}"] = keep(ret.rolling(k, min_periods=k // 2).std())
    f["skew_60"] = keep(ret.rolling(60, min_periods=30).skew())
    f["max_ret_20"] = keep(ret.rolling(20, min_periods=10).max())
    f["min_ret_20"] = keep(ret.rolling(20, min_periods=10).min())
    for k in (5, 20, 60, 120):
        f[f"ma_gap_{k}"] = keep(px / px.rolling(k, min_periods=k // 2).mean() - 1)
    # MACD(12, 26, 9), scaled by price so that it is comparable across stocks
    dif = px.ewm(span=12, adjust=False).mean() - px.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    f["macd_dif"], f["macd_dea"], f["macd_hist"] = keep(dif / px), keep(dea / px), keep((dif - dea) / px)
    f["macd_hist_chg_5"] = keep(((dif - dea) - (dif - dea).shift(5)) / px)
    # RSI
    d = px.diff()
    for k in (6, 14):
        up = d.clip(lower=0).ewm(alpha=1 / k, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / k, adjust=False).mean()
        f[f"rsi_{k}"] = keep(up / (up + dn))
    # stochastic (KDJ)
    hh, ll = hi.rolling(9, min_periods=5).max(), lo.rolling(9, min_periods=5).min()
    rsv = (px - ll) / (hh - ll)
    kk = rsv.ewm(alpha=1 / 3, adjust=False).mean(); dd = kk.ewm(alpha=1 / 3, adjust=False).mean()
    f["kdj_k"], f["kdj_j"] = keep(kk), keep(3 * kk - 2 * dd)
    f["boll_z_20"] = keep((px - px.rolling(20, min_periods=10).mean()) / px.rolling(20, min_periods=10).std())
    tr = pd.concat([hi - lo, (hi - prev).abs(), (lo - prev).abs()]).groupby(level=0).max()
    f["atr_14"] = keep(tr.rolling(14, min_periods=7).mean() / px)
    f["range_20"] = keep(((hi - lo) / cl).rolling(20, min_periods=10).mean())
    loc = (cl - lo) / (hi - lo)
    f["close_loc_5"] = keep(loc.rolling(5, min_periods=3).mean())
    f["close_loc_20"] = keep(loc.rolling(20, min_periods=10).mean())
    f["dist_high_250"] = keep(px / px.rolling(250, min_periods=120).max() - 1)
    f["dist_low_250"] = keep(px / px.rolling(250, min_periods=120).min() - 1)
    t5, t20, t60 = (turn.rolling(k, min_periods=k // 2).mean() for k in (5, 20, 60))
    f["turn_5"], f["turn_20"], f["turn_60"] = keep(t5), keep(t20), keep(t60)
    f["turn_ratio_5_60"] = keep(t5 / t60)
    f["turn_std_20"] = keep(turn.rolling(20, min_periods=10).std() / t20)
    a5, a20, a60 = (amt.rolling(k, min_periods=k // 2).mean() for k in (5, 20, 60))
    f["log_amt_20"] = keep(np.log(a20))
    f["amt_ratio_5_60"] = keep(a5 / a60)
    f["amihud_20"] = keep((ret.abs() / amt * 1e8).rolling(20, min_periods=10).mean())
    f["log_float_cap"] = keep(np.log(D(p.float_cap)))
    f["overnight_20"] = keep((op / prev - 1).rolling(20, min_periods=10).sum())
    f["intraday_20"] = keep((cl / op - 1).rolling(20, min_periods=10).sum())
    f["ret_turn_corr_20"] = keep(ret.rolling(20, min_periods=10).corr(turn))
    f["up_days_20"] = keep((ret > 0).astype(float).where(ret.notna()).rolling(20, min_periods=10).mean())
    return f


def truncate(p: Panel, k: int) -> Panel:
    """The panel as it would have looked at the close of row k-1 (nothing after it exists)."""
    return Panel(p.dates[:k], p.codes, p.board, p.open[:k], p.high[:k], p.low[:k], p.close[:k], p.amount[:k],
                 p.turnover[:k], p.adj[:k], p.index.iloc[:k], p.index_open.iloc[:k])


# --------------------------------------------------------------------------- dataset
@dataclass
class Dataset:
    names: list            # feature names
    cols: np.ndarray       # panel column index of each stock used
    rows: np.ndarray       # panel row index (signal day) of each cross-section
    X: np.ndarray          # [n_rows, n_cols, n_features] cross-sectional percentile ranks, NaN outside universe
    y: np.ndarray          # [n_rows, n_cols] percentile rank of the forward return, NaN if unknown
    fwd: np.ndarray        # raw forward return (open t+1 -> close t+H)
    uni: np.ndarray        # bool universe mask


def _xs_rank(a: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.full(a.shape, np.nan, dtype=np.float32)
    for i in range(a.shape[0]):
        ok = mask[i] & np.isfinite(a[i])
        n = ok.sum()
        if n > 1:
            out[i, ok] = (np.argsort(np.argsort(a[i, ok])) + 0.5) / n
    return out


def build_dataset(p: Panel, fac: Factors, start="2000-01-04", boards=("main",), step=STEP, horizon=H) -> Dataset:
    cols = np.flatnonzero(np.isin(p.board, boards))
    t0 = p.loc(start) - 1
    rows = np.arange(t0, len(p.dates), step)
    uni = np.array([fac.universe(int(t), boards=boards)[cols] for t in rows])
    raw = compute_features(p, cols, rows)
    names = list(raw)
    X = np.empty((len(rows), len(cols), len(names)), dtype=np.float32)
    for j, n in enumerate(names):
        r = _xs_rank(raw[n], uni)
        r[uni & ~np.isfinite(r)] = 0.5           # missing feature inside the universe -> neutral
        X[:, :, j] = r
    del raw
    T = len(p.dates)
    fwd = np.full((len(rows), len(cols)), np.nan, dtype=np.float32)
    for i, t in enumerate(rows):
        if t + horizon < T:                      # label fully realised inside the data
            with np.errstate(invalid="ignore", divide="ignore"):
                fwd[i] = p.adj_close_ff[t + horizon, cols] / p.adj_open[t + 1, cols] - 1
    fwd[~uni] = np.nan
    return Dataset(names, cols, rows, X, _xs_rank(fwd, uni), fwd, uni)


# --------------------------------------------------------------------------- walk-forward
GRID = [dict(num_leaves=15, min_data_in_leaf=800), dict(num_leaves=31, min_data_in_leaf=400),
        dict(num_leaves=63, min_data_in_leaf=200)]
BASE = dict(objective="regression", learning_rate=0.05, feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1,
            lambda_l2=10.0, verbose=-1, num_threads=4, seed=7)
MAX_ROUNDS, VAL_YEARS = 400, 2


def _flat(ds: Dataset, row_mask: np.ndarray, need_label=True):
    X = ds.X[row_mask]; y = ds.y[row_mask]
    ok = ds.uni[row_mask] & (np.isfinite(y) if need_label else True)
    grp = np.repeat(np.flatnonzero(row_mask), ok.sum(1))
    return X[ok], y[ok], grp


def _mean_ic(pred, y, grp):
    df = pd.DataFrame({"p": pred, "y": y, "g": grp})
    return df.groupby("g").apply(lambda d: np.corrcoef(d["p"].rank(), d["y"])[0, 1]).mean()


def walk_forward(p: Panel, ds: Dataset, first_test_year=2005, model="gbm", horizon=H, log=print,
                 shuffle_labels=False, train_step=2):
    """Yearly expanding-window refit. Returns (scores[n_rows, n_cols], info DataFrame).

    For test year Y: training rows are those with row + horizon < first row of Y (purge).
    Hyper-parameters: the last VAL_YEARS of the training window form the validation slice, the inner
    fit stops `horizon` days before it; the winner (by mean daily rank IC) is refitted on the whole
    training window with the number of rounds found by early stopping.
    """
    import lightgbm as lgb
    from sklearn.linear_model import Ridge

    years = p.dates[ds.rows].year.to_numpy()
    scores = np.full(ds.y.shape, np.nan, dtype=np.float32)
    info, imp = [], []
    y_used = ds.y
    if shuffle_labels:                                    # placebo: destroy the feature-label link
        rng = np.random.default_rng(0); y_used = ds.y.copy()
        for i in range(len(y_used)):
            ok = np.isfinite(y_used[i]); y_used[i, ok] = rng.permutation(y_used[i, ok])
        ds = Dataset(ds.names, ds.cols, ds.rows, ds.X, y_used, ds.fwd, ds.uni)
    thin = (np.arange(len(ds.rows)) % train_step == 0)   # thin the training cross-sections
    for Y in range(first_test_year, years.max() + 1):
        test = years == Y
        if not test.any():
            continue
        first_test_row = ds.rows[test][0]
        train = (ds.rows + horizon < first_test_row)
        val_start = ds.rows[train][-1] - VAL_YEARS * 244
        val = train & (ds.rows > val_start)
        inner = train & (ds.rows + horizon < ds.rows[val][0])
        Xt, yt, _ = _flat(ds, test, need_label=False)
        if model == "ridge":
            Xa, ya, _ = _flat(ds, train & thin)
            m = Ridge(alpha=1000.0).fit(Xa - 0.5, ya - 0.5)
            pred = m.predict(Xt - 0.5); best = {}; rounds = 0; vic = np.nan
        else:
            Xi, yi, _ = _flat(ds, inner & thin); Xv, yv, gv = _flat(ds, val)
            best, vic, rounds = None, -9, 0
            for g in GRID:
                b = lgb.train({**BASE, **g}, lgb.Dataset(Xi, yi), MAX_ROUNDS, valid_sets=[lgb.Dataset(Xv, yv)],
                              callbacks=[lgb.early_stopping(40, verbose=False)])
                ic = _mean_ic(b.predict(Xv, num_iteration=b.best_iteration), yv, gv)
                if ic > vic:
                    best, vic, rounds = g, ic, b.best_iteration
            Xa, ya, _ = _flat(ds, train & thin)
            b = lgb.train({**BASE, **best}, lgb.Dataset(Xa, ya), max(int(rounds * 1.1), 20))
            pred = b.predict(Xt)
            imp.append(pd.Series(b.feature_importance("gain"), ds.names, name=Y))
        s = scores[test]; s[ds.uni[test]] = pred; scores[test] = s
        info.append(dict(year=Y, train_rows=int(len(ya)), train_from=p.dates[ds.rows[train][0]].date(),
                         train_to=p.dates[ds.rows[train][-1]].date(), test_from=p.dates[first_test_row].date(),
                         val_ic=vic, rounds=rounds, **best))
        log(f"{model} {Y}: train {info[-1]['train_from']}..{info[-1]['train_to']} ({len(ya):,} rows) "
            f"val IC {vic:.3f} {best} rounds {rounds}")
    return scores, pd.DataFrame(info), (pd.DataFrame(imp).T if imp else None)


def daily_ic(ds: Dataset, scores: np.ndarray, p: Panel) -> pd.Series:
    out = {}
    for i, t in enumerate(ds.rows):
        ok = np.isfinite(scores[i]) & np.isfinite(ds.y[i])
        if ok.sum() > 50:
            out[p.dates[t]] = np.corrcoef(pd.Series(scores[i, ok]).rank(), ds.y[i, ok])[0, 1]
    return pd.Series(out)


def decile_returns(ds: Dataset, scores: np.ndarray, n=10) -> pd.Series:
    """Mean forward return (minus the cross-sectional mean) by score decile, non-overlapping rows."""
    acc = []
    for i in range(0, len(ds.rows), max(1, H // STEP)):
        ok = np.isfinite(scores[i]) & np.isfinite(ds.fwd[i])
        if ok.sum() > 100:
            q = pd.qcut(pd.Series(scores[i, ok]).rank(method="first"), n, labels=False)
            r = pd.Series(ds.fwd[i, ok]); acc.append((r - r.mean()).groupby(q).mean())
    return pd.DataFrame(acc).mean()


def fit_latest(p: Panel, ds: Dataset, horizon=H, log=print):
    """Fit on everything whose label is already known and score the most recent cross-section
    (same procedure as one walk-forward step). Returns scores[n_cols] for ds.rows[-1]."""
    import lightgbm as lgb
    last = ds.rows[-1]
    train = ds.rows + horizon < last
    val = train & (ds.rows > ds.rows[train][-1] - VAL_YEARS * 244)
    inner = train & (ds.rows + horizon < ds.rows[val][0])
    thin = np.arange(len(ds.rows)) % 2 == 0
    Xi, yi, _ = _flat(ds, inner & thin); Xv, yv, gv = _flat(ds, val)
    best, vic, rounds = None, -9, 0
    for g in GRID:
        b = lgb.train({**BASE, **g}, lgb.Dataset(Xi, yi), MAX_ROUNDS, valid_sets=[lgb.Dataset(Xv, yv)],
                      callbacks=[lgb.early_stopping(40, verbose=False)])
        ic = _mean_ic(b.predict(Xv, num_iteration=b.best_iteration), yv, gv)
        if ic > vic:
            best, vic, rounds = g, ic, b.best_iteration
    Xa, ya, _ = _flat(ds, train & thin)
    b = lgb.train({**BASE, **best}, lgb.Dataset(Xa, ya), max(int(rounds * 1.1), 20))
    log(f"trained on {len(ya):,} rows to {p.dates[ds.rows[train][-1]].date()}; validation IC {vic:.3f}; {best}")
    out = np.full(len(ds.cols), np.nan)
    out[ds.uni[-1]] = b.predict(ds.X[-1][ds.uni[-1]])
    return out


def st_mask_from_name_changes(p: Panel, csv_path, current_names: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Point-in-time ST / delisting-risk flag from an exchange's dated name-change history.
    Returns (st[T, N] bool, covered[N] bool). Only codes of that exchange are covered."""
    ch = pd.read_csv(csv_path, dtype=str)
    ch.columns = ["date", "code", "name_now", "before", "after"]
    ch["date"] = pd.to_datetime(ch["date"]); ch = ch.sort_values("date")
    flag = lambda s: isinstance(s, str) and ("ST" in s.upper() or s.upper().startswith("PT") or "退" in s)
    st = np.zeros(p.close.shape, dtype=bool)
    covered = np.array([c[:2] in ("00", "30") for c in p.codes])
    groups = dict(tuple(ch.groupby("code")))
    for j, c in enumerate(p.codes):
        if not covered[j]:
            continue
        g = groups.get(c)
        if g is None:
            st[:, j] = flag(current_names.get(c)); continue
        pos = p.dates.searchsorted(g["date"].to_numpy())
        st[:pos[0], j] = flag(g["before"].iloc[0])
        for k in range(len(g)):
            end = pos[k + 1] if k + 1 < len(g) else len(p.dates)
            st[pos[k]:end, j] = flag(g["after"].iloc[k])
    return st, covered
