#!/usr/bin/env python3
"""Print the target portfolio for the NEXT trading day's open, from the latest data in the panel.

  python scripts/download_data.py --update && python scripts/build_panel.py
  python scripts/generate_signals.py --strategy SmallCap-MF+Trend --capital 200000
"""
import argparse, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant import strategies as S
from quant.catalog import BUFFER, MF, DEF, risk_on_signal
from quant.panel import load_panel

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", default="SmallCap-MF+Trend",
                    choices=["SmallCap-MF", "SmallCap-MF+Trend", "LargeCap-Defensive"])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--capital", type=float, default=0, help="CNY; if given, prints 100-share lots")
    ap.add_argument("--holdings", nargs="*", default=[],
                    help="codes you hold now; they are kept while still ranked in the top 2N (turnover buffer)")
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()

    p = load_panel(a.data_dir)
    fac = S.Factors(p)
    t = len(p.dates) - 1
    names = pd.read_csv(Path(a.data_dir) / "meta" / "stocks.csv", dtype=str).set_index("code")["name"]
    on = bool(risk_on_signal(p, fac).iloc[-1])
    print(f"data as of close {p.dates[t]:%Y-%m-%d}; market trend filter: {'RISK-ON' if on else 'RISK-OFF'}")
    if a.strategy.endswith("+Trend") and not on:
        print("-> target: 100% cash (money-market fund / reverse repo)")
        sys.exit()
    w = DEF if a.strategy == "LargeCap-Defensive" else MF
    m = fac.universe(t, top_cap=800 if a.strategy == "LargeCap-Defensive" else None)
    score = sum(v * np.nan_to_num(S._rank(getattr(fac, k)[t], m), nan=0.5) for k, v in w.items()) / sum(w.values())
    order = np.argsort(-np.where(m, score, -np.inf))
    held = np.flatnonzero(np.isin(p.codes, a.holdings))
    keep = held[np.isin(held, order[:int(BUFFER * a.n)])]
    pick = np.concatenate([keep, order[~np.isin(order, keep)][:a.n - keep.size]]).astype(int)
    if a.holdings:
        print(f"keep {keep.size}, sell {sorted(set(a.holdings) - set(p.codes[keep]))}, "
              f"buy {sorted(set(p.codes[pick]) - set(a.holdings))}")
    df = pd.DataFrame({"code": p.codes[pick], "name": names.reindex(p.codes[pick]).values,
                       "close": p.close[t, pick], "float_cap_bn": p.float_cap[t, pick] / 1e9,
                       "score": score[pick], "weight": 1 / a.n})
    if a.capital:
        df["lots"] = np.floor(a.capital / a.n / df["close"] / 100).astype(int)
    print(df.round(3).to_string(index=False))
