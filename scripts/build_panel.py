#!/usr/bin/env python3
"""Assemble data/daily/*.parquet into the wide panel (data/panel.npz) used by the backtests."""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.panel import build_panel

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--start", default="1998-01-01")
    a = ap.parse_args()
    build_panel(a.data_dir, a.start)
