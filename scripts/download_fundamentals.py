#!/usr/bin/env python3
"""Download market-wide fundamentals from Eastmoney (via akshare) into data/fundamentals/.

  reports_<YYYYMMDD>.parquet   earnings summary of every company for that report date
                               (EPS, revenue, net profit, ROE, BVPS, operating cash flow / share, gross margin, industry)
  dividends_<YYYYMMDD>.parquet distribution plans attached to that report date (cash per 10 shares, ex-date, status)
  balance_<YYYYMMDD>.parquet   balance-sheet summary (debt ratio etc.) for the latest report date
  pledge.parquet               share-pledge ratio per company (latest)

Resumable: existing files are skipped unless --force.
"""
import argparse, sys, time, warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")


def fetch(fn, tries=8, **kw):
    import akshare as ak
    err = None
    for i in range(tries):
        try:
            df = getattr(ak, fn)(**kw)
            if df is not None and len(df):
                return df
            err = "empty"
        except Exception as e:          # throttling / dropped connections are common
            err = repr(e)[:120]
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"{fn}{kw}: {err}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--first-year", type=int, default=2014)
    ap.add_argument("--latest", default="20260930", help="most recent quarterly report date")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = Path(a.data_dir) / "fundamentals"; out.mkdir(parents=True, exist_ok=True)
    ly = int(a.latest[:4])
    reports = [f"{y}1231" for y in range(a.first_year, ly)] + [a.latest, f"{ly - 1}{a.latest[4:]}"]
    divs = [f"{y}{md}" for y in range(a.first_year, ly + 1) for md in ("0331", "0630", "0930", "1231") if f"{y}{md}" <= a.latest]
    jobs = [("reports", "stock_yjbb_em", d) for d in reports] + [("dividends", "stock_fhps_em", d) for d in divs] + \
           [("balance", "stock_zcfz_em", a.latest)]
    fails = []

    def job(kind, fn, d):
        f = out / f"{kind}_{d}.parquet"
        if f.exists() and not a.force:
            return
        try:   # few companies distribute at Q1 / Q3, so an empty answer there is normal: do not retry for long
            df = fetch(fn, tries=3 if (kind == "dividends" and d[4:] in ("0331", "0930")) else 8, date=d)
            for c in df.columns:
                if df[c].dtype == object:
                    df[c] = df[c].astype(str)
            df.to_parquet(f, index=False)
            print(f"{kind} {d}: {len(df)} rows", flush=True)
        except Exception as e:
            if kind == "dividends" and d[4:] in ("0331", "0930"):
                print(f"{kind} {d}: none", flush=True)
            else:
                fails.append((kind, d, str(e)[:150])); print(f"FAILED {kind} {d}: {e}", flush=True)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(lambda j: job(*j), jobs))
    f = out / "pledge.parquet"
    if not f.exists() or a.force:
        try:
            import akshare as ak
            cal = pd.bdate_range(end=pd.Timestamp.today(), periods=30)
            for d in reversed([x for x in cal if x.weekday() == 4]):       # pledge data is published for Fridays
                try:
                    df = ak.stock_gpzy_pledge_ratio_em(date=d.strftime("%Y%m%d"))
                    if len(df):
                        df.astype({c: str for c in df.columns if df[c].dtype == object}).to_parquet(f, index=False)
                        print(f"pledge {d.date()}: {len(df)} rows", flush=True); break
                except Exception:
                    time.sleep(3)
        except Exception as e:
            print("pledge failed", e)
    print("done; failures:", fails)


if __name__ == "__main__":
    main()
