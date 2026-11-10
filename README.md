# A-share daily quant research kit

Download the full history of every China A-share stock, build daily-bar strategies, and backtest
them through 26 years of bull and bear markets with realistic trading frictions.

**Start with [`reports/REPORT.md`](reports/REPORT.md)** for the findings. This page is the how-to.

## Quick start

```bash
pip install -r requirements.txt
python scripts/download_data.py        # ~20 min, ~700 MB: all listed + delisted stocks, 10 indices
python scripts/build_panel.py          # per-stock files -> data/panel.npz
python scripts/run_backtests.py        # all strategies + robustness -> reports/
python scripts/generate_signals.py --strategy SmallCap-MF --capital 200000   # tomorrow's target portfolio
```

To refresh later: `python scripts/download_data.py --update && python scripts/build_panel.py`.
`data/` is git-ignored; everything in `reports/` is reproducible from the scripts.

## What is in the box

| Path | Purpose |
|---|---|
| `scripts/download_data.py` | Resumable, multi-threaded downloader. Unadjusted OHLCV + amount + turnover from Eastmoney, adjustment factors from Sina, delisted lists from SSE/SZSE. |
| `quant/panel.py` | Wide date × stock panel; derives total returns, free-float market cap, price-limit and suspension flags, an ST heuristic. |
| `quant/strategies.py` | Factor library, universe filter, portfolio construction, trend filter, index strategies. |
| `quant/backtest.py` | Daily simulator: next-open execution, limit-up/limit-down and suspension blocking, retried exits, delisting write-offs, commissions, slippage, historical stamp duty. |
| `quant/metrics.py` | CAGR, Sharpe, drawdown, alpha/beta, and mechanical bull/bear phase detection. |
| `quant/catalog.py` | The strategy line-up. Add your own strategy here. |
| `scripts/run_backtests.py` | Runs everything and writes `reports/results.md`, CSV tables and figures. |
| `scripts/generate_signals.py` | Target holdings for the next open from the latest data. |

## Data

- 5,922 stocks (main boards, ChiNext, STAR, Beijing), of which about 350 are delisted or no longer
  trading, so the backtests are not flattered by survivorship. 18.3 million daily rows, 1990 → today.
- Prices are stored **unadjusted** with a separate multiplicative `adj_factor`. Returns use adjusted
  prices; price-limit checks use raw prices.
- Free-float market cap is derived as `amount / turnover` (20-day median), which is point-in-time.
- 3 codes returned no data (`data/meta/failures.csv`). Both sources throttle; the script backs off and
  can simply be re-run.

## Backtest rules

- Signal from data up to the close of day *t*; trade at the **open of day *t+1***. No intraday timing.
- Cannot buy a stock that opens at limit-up, cannot sell one that opens at limit-down or is suspended.
  Blocked sells are retried every following open.
- Costs per side: 0.03% commission + 0.10% slippage, plus the stamp duty in force on that date
  (0.4% both sides in 2000, 0.05% sell-only today).
- Default universe: Shanghai/Shenzhen **main boards only**, listed ≥ 250 trading days, price ≥ 2 CNY,
  not ST-like, not in the least liquid 10%. ChiNext and STAR need extra account permissions
  (trading experience and minimum assets; check with your broker) and can be switched on with
  `boards=ALL_BOARDS`.

## Writing your own strategy

```python
from quant.panel import load_panel
from quant import strategies as S
from quant.backtest import run_stock_strategy
from quant.metrics import perf_stats

p = load_panel(); fac = S.Factors(p)
targets = S.factor_portfolio(p, fac, dict(lowvol=1, rev20=1), "2010-01-04", n_hold=30, every=20, buffer=2)
res = run_stock_strategy(p, "my-strategy", targets, "2010-01-04")
print(perf_stats(res.equity))
```

A strategy is just a dict `{signal_day_index: (stock_indices, weights)}`; anything that can be computed
from the panel at the close of day *t* fits.

## Disclaimer

Research code and historical simulation, not investment advice. Backtested returns are not a forecast;
see the limitations section of the report before putting money behind any of this.
