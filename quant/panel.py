"""Build / load the wide (dates x stocks) panel used by all strategies and the backtester."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FIELDS = ["open", "high", "low", "close", "amount", "turnover", "adj_factor"]


@dataclass
class Panel:
    dates: pd.DatetimeIndex
    codes: np.ndarray          # str codes
    board: np.ndarray          # main | chinext | star | bse
    open: np.ndarray           # raw prices, NaN when not traded (suspended / not listed)
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    amount: np.ndarray         # traded value, CNY
    turnover: np.ndarray       # % of free float traded
    adj: np.ndarray            # backward adjustment factor (forward filled)
    index: pd.DataFrame        # benchmark index closes
    index_open: pd.DataFrame

    # ---- derived (lazily cached) ----
    def __post_init__(self):
        self._cache = {}

    def _c(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    @property
    def traded(self):          # bar exists
        return self._c("traded", lambda: np.isfinite(self.close) & (self.amount > 0))

    @property
    def adj_close(self):
        return self._c("adj_close", lambda: self.close * self.adj)

    @property
    def adj_open(self):
        return self._c("adj_open", lambda: self.open * self.adj)

    @property
    def adj_close_ff(self):
        """Adjusted close, forward filled through suspensions (NaN before listing)."""
        return self._c("adj_close_ff", lambda: _ffill(self.adj_close))

    @property
    def prev_close(self):
        """Previous traded close expressed in today's raw price terms (ex-rights adjusted)."""
        def f():
            pc = np.full_like(self.close, np.nan)
            pc[1:] = self.adj_close_ff[:-1]
            return pc / self.adj
        return self._c("prev_close", f)

    @property
    def ret(self):
        """Close-to-close total return; NaN on days without a bar (resumption day spans the gap)."""
        def f():
            r = np.full_like(self.close, np.nan)
            r[1:] = self.adj_close[1:] / self.adj_close_ff[:-1] - 1
            return r
        return self._c("ret", f)

    @property
    def days_listed(self):
        """Number of bars the stock has printed so far (0 before listing)."""
        return self._c("days_listed", lambda: np.cumsum(self.traded, axis=0))

    @property
    def float_cap(self):
        """Free-float market cap (CNY) = amount / turnover, 20-day median, forward filled."""
        def f():
            with np.errstate(divide="ignore", invalid="ignore"):
                cap = np.where(self.turnover > 0, self.amount / (self.turnover / 100.0), np.nan)
            df = pd.DataFrame(cap).rolling(20, min_periods=3).median().ffill(limit=250)
            out = np.array(df.to_numpy(dtype=np.float32))
            out[~np.isfinite(self.adj_close_ff)] = np.nan
            return out
        return self._c("float_cap", f)

    @property
    def limit_pct(self):
        """Daily price-limit by board / date (ST handled separately via `st_like`)."""
        def f():
            lim = np.full(self.close.shape, 0.10, dtype=np.float32)
            d = self.dates.values
            lim[:, self.board == "star"] = 0.20
            lim[np.ix_(d >= np.datetime64("2020-08-24"), self.board == "chinext")] = 0.20
            lim[:, self.board == "bse"] = 0.30
            return lim
        return self._c("limit_pct", f)

    @property
    def st_like(self):
        """Heuristic ST flag (no point-in-time ST list is freely available).

        A main-board stock is treated as ST when, over the trailing 40 bars, it never moved
        more than 5.3% in a day yet closed locked at a ~5% limit at least once. Stocks trading
        under 2 CNY are excluded separately by the universe filter.
        """
        def f():
            with np.errstate(invalid="ignore", divide="ignore"):
                r = self.close / self.prev_close - 1
                hit = (np.abs(np.abs(r) - 0.05) < 0.0035) & ((self.close == self.high) | (self.close == self.low))
            absr = pd.DataFrame(np.abs(r))
            mx = absr.rolling(40, min_periods=20).max().to_numpy()
            nh = pd.DataFrame(hit.astype(np.float32)).rolling(40, min_periods=1).sum().to_numpy()
            st = (mx < 0.053) & (nh >= 1) & (self.board == "main")[None, :]
            return st
        return self._c("st_like", f)

    @property
    def cant_buy_open(self):
        """Opens at (or within a tick of) the upper price limit, or no bar."""
        def f():
            with np.errstate(invalid="ignore", divide="ignore"):
                r = self.open / self.prev_close - 1
            lim = np.where(self.st_like, 0.05, self.limit_pct)
            first_days = self.days_listed <= 1
            return (~self.traded) | ((r >= lim - 0.003) & ~first_days) | (first_days & (self.high == self.low))
        return self._c("cant_buy_open", f)

    @property
    def cant_sell_open(self):
        def f():
            with np.errstate(invalid="ignore", divide="ignore"):
                r = self.open / self.prev_close - 1
            lim = np.where(self.st_like, 0.05, self.limit_pct)
            return (~self.traded) | (r <= -lim + 0.003)
        return self._c("cant_sell_open", f)

    def loc(self, date) -> int:
        return int(self.dates.searchsorted(pd.Timestamp(date)))


def _ffill(a: np.ndarray) -> np.ndarray:
    mask = np.isfinite(a)
    idx = np.where(mask, np.arange(a.shape[0])[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    out = a[idx, np.arange(a.shape[1])[None, :]]
    out[~np.maximum.accumulate(mask, axis=0)] = np.nan
    return out


def build_panel(data_dir="data", start="1999-01-01") -> Path:
    """Read every per-stock parquet and write data/panel.npz."""
    root = Path(data_dir)
    meta = pd.read_csv(root / "meta" / "stocks.csv", dtype=str).set_index("code")
    files = sorted((root / "daily").glob("*.parquet"))
    frames = {}
    for f in files:
        df = pd.read_parquet(f)
        df = df[df["date"] >= start]
        df = df[(df["close"] > 0) & (df["open"] > 0)]
        if len(df):
            frames[f.stem] = df.set_index("date")
    idx_frames = {f.stem: pd.read_parquet(f).set_index("date") for f in sorted((root / "index").glob("*.parquet"))}
    dates = idx_frames["000001"].index
    dates = dates[dates >= start]
    codes = np.array(sorted(frames))
    arr = {k: np.full((len(dates), len(codes)), np.nan, dtype=np.float32) for k in FIELDS}
    for j, c in enumerate(codes):
        df = frames[c]
        pos = dates.get_indexer(df.index)
        ok = pos >= 0
        for k in FIELDS:
            arr[k][pos[ok], j] = df[k].to_numpy(dtype=np.float32)[ok]
    arr["adj_factor"] = _ffill(arr["adj_factor"])
    arr["adj_factor"] = pd.DataFrame(arr["adj_factor"]).bfill().to_numpy(dtype=np.float32)
    index_close = pd.DataFrame({k: v["close"] for k, v in idx_frames.items()}).reindex(dates)
    index_open = pd.DataFrame({k: v["open"] for k, v in idx_frames.items()}).reindex(dates)
    out = root / "panel.npz"
    np.savez(out, dates=dates.values.astype("datetime64[D]"), codes=codes,
             board=meta.reindex(codes)["board"].to_numpy(dtype=str),
             index_cols=np.array(index_close.columns, dtype=str), index_close=index_close.to_numpy(),
             index_open=index_open.to_numpy(), **arr)
    print(f"panel: {len(dates)} days x {len(codes)} stocks -> {out}")
    return out


def load_panel(data_dir="data") -> Panel:
    z = np.load(Path(data_dir) / "panel.npz", allow_pickle=False)
    dates = pd.DatetimeIndex(z["dates"])
    cols = [str(c) for c in z["index_cols"]]
    return Panel(dates=dates, codes=z["codes"].astype(str), board=z["board"].astype(str),
                 open=z["open"], high=z["high"], low=z["low"], close=z["close"],
                 amount=z["amount"], turnover=z["turnover"], adj=z["adj_factor"],
                 index=pd.DataFrame(z["index_close"], index=dates, columns=cols),
                 index_open=pd.DataFrame(z["index_open"], index=dates, columns=cols))
