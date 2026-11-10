#!/usr/bin/env python3
"""Target weights of the final basket strategy for the next trading day's open.

  python scripts/download_data.py --update --codes 600066 600406 000895 && python scripts/build_panel.py
  python scripts/basket_signal.py --capital 200000
"""
import argparse, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
from quant import basket as B

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codes", nargs="+", default=["510300", "159915", "600066", "600406", "000895"])
    ap.add_argument("--capital", type=float, default=0)
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    b = B.load_basket(a.codes, a.data_dir, refresh_etf=True)
    px = b.close
    core = B.vol_target(b, B.trend_ensemble(b), 0.20)
    w = B.with_dip_overlay(core, px, 0.10)
    df = pd.DataFrame({"name": b.names, "trend+vol core": core.iloc[-1].round(3).values,
                       "dip overlay": (w.iloc[-1] - core.iloc[-1]).round(3).values,
                       "target weight": w.iloc[-1].round(3).values,
                       "yesterday": w.iloc[-2].round(3).values}, index=b.codes)
    print(f"data as of close {b.dates[-1]:%Y-%m-%d}; target cash {1 - w.iloc[-1].sum():.1%}")
    print(df.to_string())
    if a.capital:
        print("\nCNY per security:", (df["target weight"] * a.capital).round(0).to_dict())
    print("\nOnly trade when a weight differs from what you hold by more than 4 percentage points.")
