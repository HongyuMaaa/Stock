#!/usr/bin/env python3
"""Download full daily history for every A-share stock (listed AND delisted).

Sources
-------
* Eastmoney kline API  - unadjusted daily OHLCV, amount, turnover (works for delisted codes too)
* Sina hfq.js          - multiplicative backward-adjustment factors (dividends / splits / rights)
* SSE / SZSE (akshare) - lists of delisted companies, so the data set is survivorship-bias free
* Eastmoney            - benchmark indices

Output (default ./data)
-----------------------
  data/meta/stocks.csv        code, name, exchange, board, status(listed|delisted)
  data/daily/<code>.parquet   date, open, high, low, close, volume, amount, turnover, adj_factor
  data/index/<code>.parquet   date, open, high, low, close, volume, amount
  data/meta/failures.csv      codes that could not be downloaded (re-run to retry)

The script is resumable: files already on disk are skipped unless --force / --update is given.

  python scripts/download_data.py                 # full download (30-60 min)
  python scripts/download_data.py --update        # refresh all currently listed stocks
  python scripts/download_data.py --codes 600519 000001
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
SINA_HFQ_URL = "https://finance.sina.com.cn/realstock/company/{sym}/hfq.js"

INDICES = {
    "000001": ("1.000001", "SSE Composite"),
    "000300": ("1.000300", "CSI 300"),
    "000905": ("1.000905", "CSI 500"),
    "000852": ("1.000852", "CSI 1000"),
    "932000": ("2.932000", "CSI 2000"),
    "000985": ("1.000985", "CSI All Share"),
    "399001": ("0.399001", "SZSE Component"),
    "399006": ("0.399006", "ChiNext"),
    "000688": ("1.000688", "STAR 50"),
    "000016": ("1.000016", "SSE 50"),
}

_local = threading.local()


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.headers["User-Agent"] = UA
        _local.s = s
    return _local.s


def get(url: str, params=None, retries: int = 6, timeout: int = 30) -> requests.Response:
    """GET with exponential backoff (Eastmoney / Sina throttle aggressive clients)."""
    err = None
    for i in range(retries):
        try:
            r = session().get(url, params=params, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r
            err = RuntimeError(f"HTTP {r.status_code}")
        except requests.RequestException as e:  # connection reset == throttled
            err = e
            _local.__dict__.pop("s", None)
        time.sleep(min(60, 1.5 * 2 ** i) + random.random())
    raise RuntimeError(f"GET failed after {retries} tries: {url} ({err})")


# --------------------------------------------------------------------------- universe
def classify(code: str):
    """-> (exchange, board) or None if the code is not an A-share common stock."""
    if re.match(r"60[0135]\d{3}", code):
        return "SH", "main"
    if code.startswith(("688", "689")):
        return "SH", "star"
    if re.match(r"00[0-4]\d{3}", code):
        return "SZ", "main"
    if re.match(r"30[0-2]\d{3}", code):
        return "SZ", "chinext"
    if re.match(r"(8[3-9]|4[3]|92)\d{4}", code):
        return "BJ", "bse"
    return None


def secid(code: str) -> str:
    return ("1." if classify(code)[0] == "SH" else "0.") + code


def sina_symbol(code: str) -> str:
    return classify(code)[0].lower() + code


def _eastmoney_list() -> list:
    """Eastmoney board list. NOTE: it also contains most delisted codes."""
    rows, pn = [], 1
    fs = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
    while True:
        r = get(CLIST_URL, params=dict(pn=pn, pz=100, po=0, np=1, fltt=2, invt=2, fid="f12",
                                       fs=fs, fields="f12,f14"), retries=3)
        d = r.json()["data"]
        if not d or not d.get("diff"):
            break
        rows += [(x["f12"], x["f14"]) for x in d["diff"]]
        if len(rows) >= d["total"]:
            break
        pn += 1
        time.sleep(0.3)
    return rows


def _sina_list() -> list:
    """Sina list of currently traded A shares (SH + SZ + BJ)."""
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    rows, page = [], 1
    while True:
        r = get(url, params=dict(page=page, num=100, sort="symbol", asc=1, node="hs_a", symbol="", _s_r_a="page"))
        d = r.json()
        if not d:
            break
        rows += [(x["code"], x["name"]) for x in d]
        page += 1
        time.sleep(0.2)
    return rows


def _delisted_list() -> list:
    """Official SSE / SZSE lists of companies whose listing was terminated."""
    import akshare as ak
    rows = []
    for fn, kw, c_code, c_name in (
        (ak.stock_info_sh_delist, {}, "公司代码", "公司简称"),
        (ak.stock_info_sz_delist, {"symbol": "终止上市公司"}, "证券代码", "证券简称"),
    ):
        for attempt in range(6):
            try:
                df = fn(**kw)
                rows += list(zip(df[c_code].astype(str).str.zfill(6), df[c_name]))
                break
            except Exception as e:
                print(f"[warn] {fn.__name__} attempt {attempt + 1} failed: {e!r:.120}", file=sys.stderr)
                time.sleep(5)
        else:
            raise RuntimeError(f"could not fetch delisted list via {fn.__name__}")
    return rows


def build_universe(meta_dir: Path, refresh: bool = True) -> pd.DataFrame:
    """Union of every source that answers (the sources throttle independently).

    `status` here is only informational; the panel builder decides listed/delisted
    from the data itself (date of the last bar).
    """
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / "stocks.csv"
    names, delisted = {}, set()
    if path.exists():
        old = pd.read_csv(path, dtype=str)
        names.update(zip(old["code"], old["name"]))
        delisted |= set(old.loc[old["status"] == "delisted", "code"])
        if not refresh:
            return old
    ok = 0
    for src in (_sina_list, _eastmoney_list, _delisted_list):
        try:
            rows = src()
            ok += 1
        except Exception as e:
            print(f"[warn] {src.__name__} unavailable: {str(e)[:120]}", file=sys.stderr)
            continue
        print(f"  {src.__name__}: {len(rows)} codes")
        for code, name in rows:
            names.setdefault(code, name)
        if src is _delisted_list:
            delisted |= {c for c, _ in rows}
    if not names:
        raise RuntimeError("no stock list source reachable")
    df = pd.DataFrame(sorted(names.items()), columns=["code", "name"])
    cls = df["code"].map(classify)
    df = df[cls.notna()].copy()
    df["exchange"] = [classify(c)[0] for c in df["code"]]
    df["board"] = [classify(c)[1] for c in df["code"]]
    df["status"] = ["delisted" if c in delisted else "listed" for c in df["code"]]
    df.to_csv(path, index=False)
    return df


# --------------------------------------------------------------------------- downloads
KCOLS = ["date", "open", "close", "high", "low", "volume", "amount", "amplitude", "pct", "chg", "turnover"]


def fetch_kline(sec: str) -> pd.DataFrame:
    r = get(KLINE_URL, params=dict(secid=sec, fields1="f1,f2,f3,f4,f5,f6",
                                   fields2="f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                                   klt=101, fqt=0, beg="19900101", end="20500101", lmt=1000000))
    d = r.json().get("data")
    if not d or not d.get("klines"):
        return pd.DataFrame(columns=KCOLS)
    df = pd.DataFrame([k.split(",") for k in d["klines"]], columns=KCOLS)
    df["date"] = pd.to_datetime(df["date"])
    for c in KCOLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fetch_adj_factor(code: str) -> pd.DataFrame:
    """Sina backward-adjustment factor: factor f applies to all dates >= d."""
    r = get(SINA_HFQ_URL.format(sym=sina_symbol(code)))
    m = re.search(r"=\s*(\{.*\})", r.text, re.S)
    if not m:
        raise RuntimeError(f"unparseable Sina factor payload for {code}")
    data = json.loads(m.group(1)).get("data") or []
    df = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame({"date": [pd.Timestamp("1990-01-01")], "adj_factor": [1.0]})
    df["d"] = df["d"].replace("1900-01-01", "1990-01-01")
    out = pd.DataFrame({"date": pd.to_datetime(df["d"]), "adj_factor": df["f"].astype(float)})
    return out.sort_values("date").reset_index(drop=True)


def download_stock(code: str, out_dir: Path) -> int:
    k = fetch_kline(secid(code))
    if k.empty:
        return 0
    f = fetch_adj_factor(code)
    k = pd.merge_asof(k.sort_values("date"), f, on="date", direction="backward")
    k["adj_factor"] = k["adj_factor"].bfill().fillna(1.0)
    k = k[["date", "open", "high", "low", "close", "volume", "amount", "turnover", "adj_factor"]]
    tmp = out_dir / f"{code}.parquet.tmp"
    k.to_parquet(tmp, index=False)
    tmp.rename(out_dir / f"{code}.parquet")
    return len(k)


def download_indices(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for code, (sec, name) in INDICES.items():
        k = fetch_kline(sec)[["date", "open", "high", "low", "close", "volume", "amount"]]
        k.to_parquet(out_dir / f"{code}.parquet", index=False)
        print(f"index {code} {name}: {len(k)} rows from {k['date'].min():%Y-%m-%d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="re-download everything")
    ap.add_argument("--update", action="store_true", help="re-download currently listed stocks")
    ap.add_argument("--no-refresh-universe", action="store_true", help="reuse data/meta/stocks.csv")
    ap.add_argument("--codes", nargs="*", help="only these codes")
    args = ap.parse_args()

    root = Path(args.data_dir)
    daily = root / "daily"
    daily.mkdir(parents=True, exist_ok=True)

    uni = build_universe(root / "meta", refresh=not args.no_refresh_universe)
    print(f"universe: {len(uni)} stocks "
          f"({(uni.status == 'listed').sum()} listed, {(uni.status == 'delisted').sum()} delisted)")
    download_indices(root / "index")

    todo = []
    for code, status in zip(uni["code"], uni["status"]):
        if args.codes and code not in args.codes:
            continue
        exists = (daily / f"{code}.parquet").exists()
        if args.force or not exists or (args.update and status == "listed"):
            todo.append(code)
    print(f"to download: {len(todo)}")

    failures, done, rows, t0 = [], 0, 0, time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(download_stock, c, daily): c for c in todo}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                n = fut.result()
                rows += n
                if n == 0:
                    failures.append((code, "no data"))
            except Exception as e:
                failures.append((code, str(e)[:200]))
            done += 1
            if done % 200 == 0 or done == len(todo):
                el = time.time() - t0
                print(f"  {done}/{len(todo)}  rows={rows:,}  failures={len(failures)}  "
                      f"elapsed={el / 60:.1f}m  eta={(len(todo) - done) * el / done / 60:.1f}m", flush=True)
    pd.DataFrame(failures, columns=["code", "error"]).to_csv(root / "meta" / "failures.csv", index=False)
    print(f"done. failures: {len(failures)} (see {root / 'meta' / 'failures.csv'}; re-run to retry)")


if __name__ == "__main__":
    main()
