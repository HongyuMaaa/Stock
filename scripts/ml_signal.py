#!/usr/bin/env python3
"""Fit the model on all data whose outcome is already known and list the top-ranked stocks
for the next trading day's open (takes a few minutes).

  python scripts/download_data.py --update && python scripts/build_panel.py
  python scripts/ml_signal.py --n 50 --capital 500000 --holdings 600123 000456 ...
"""
import argparse, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
from quant import ml, strategies as S
from quant.catalog import BUFFER
from quant.panel import load_panel

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--capital", type=float, default=0)
    ap.add_argument("--holdings", nargs="*", default=[], help="codes held now; kept while still in the top 2N")
    ap.add_argument("--vol-target", type=float, default=0.20, help="0 = always fully invested")
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    p = load_panel(a.data_dir); fac = S.Factors(p)
    last = len(p.dates) - 1
    start = p.dates[last - ((last - (p.loc("2000-01-04") - 1)) // ml.STEP) * ml.STEP + 1]   # grid ends on the last bar
    ds = ml.build_dataset(p, fac, start=start)
    assert ds.rows[-1] == last
    score = ml.fit_latest(p, ds)
    full = np.full(len(p.codes), np.nan); full[ds.cols] = score
    names = pd.read_csv(Path(a.data_dir) / "meta" / "stocks.csv", dtype=str).set_index("code")["name"]
    risky = np.array([("ST" in str(names.get(c, "")).upper()) or ("退" in str(names.get(c, ""))) for c in p.codes])
    m = fac.universe(last) & np.isfinite(full) & ~risky          # drop ST / delisting names by today's name
    order = np.argsort(-np.where(m, full, -np.inf))
    held = np.flatnonzero(np.isin(p.codes, a.holdings))
    keep = held[np.isin(held, order[:int(BUFFER * a.n)])]
    pick = np.concatenate([keep, order[~np.isin(order, keep)][:a.n - keep.size]]).astype(int)
    expo = float(S.vol_exposure(S.market_index(p, fac), a.vol_target)[-1]) if a.vol_target else 1.0
    df = pd.DataFrame({"code": p.codes[pick], "name": names.reindex(p.codes[pick]).values, "close": p.close[last, pick],
                       "float_cap_bn": p.float_cap[last, pick] / 1e9, "score": full[pick], "weight": expo / a.n})
    if a.capital:
        df["lots"] = np.floor(a.capital * df["weight"] / df["close"] / 100).astype(int)
    print(f"data as of close {p.dates[last]:%Y-%m-%d}; exposure from volatility target: {expo:.0%}")
    if a.holdings:
        print(f"keep {keep.size}, sell {sorted(set(a.holdings) - set(p.codes[keep]))}")
    print(df.round(3).to_string(index=False))
