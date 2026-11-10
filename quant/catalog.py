"""The strategy line-up: one place that defines every strategy that is backtested / traded."""
from __future__ import annotations

from . import strategies as S
from .backtest import Costs, run_index_strategy, run_stock_strategy

MF = dict(size=1, rev20=1, lowvol=1, lowturn=1)      # small-cap multi-factor
DEF = dict(rev20=1, lowvol=1, lowturn=1)             # defensive factors, no size tilt
TREND_MA, TREND_BUF = 120, 0.02
BUFFER = 2.0                                          # keep a holding while it ranks in the top 2N

STOCK_STRATEGIES = {
    # name: (description, target-builder(panel, fac, start, risk_on))
    "SmallCap-MF": ("50 main-board stocks ranked on small size + 1m reversal + low volatility + low turnover; monthly",
                    lambda p, f, s, on: S.factor_portfolio(p, f, MF, s, n_hold=50, every=20, buffer=BUFFER)),
    "SmallCap-MF+Trend": ("SmallCap-MF, but in cash while the equal-weight market index is below its 120-day MA",
                          lambda p, f, s, on: S.factor_portfolio(p, f, MF, s, n_hold=50, every=20, buffer=BUFFER, risk_on=on)),
    "LargeCap-Defensive": ("50 stocks from the 800 largest, ranked on low volatility + low turnover + 1m reversal; monthly",
                           lambda p, f, s, on: S.factor_portfolio(p, f, DEF, s, n_hold=50, every=20, buffer=BUFFER, top_cap=800)),
    "Momentum-12-1": ("50 stocks from the 800 largest with the best 12-month return (skipping the last month); monthly",
                      lambda p, f, s, on: S.factor_portfolio(p, f, dict(mom=1), s, n_hold=50, every=20, top_cap=800)),
    "MicroCap-50": ("the 50 smallest eligible main-board stocks, nothing else; monthly",
                    lambda p, f, s, on: S.factor_portfolio(p, f, dict(size=1), s, n_hold=50, every=20)),
    "EqualWeight-All": ("every eligible main-board stock, equal weight; monthly (the 'average stock')",
                        lambda p, f, s, on: S.equal_weight_universe(p, f, s, every=20)),
}

INDEX_STRATEGIES = {
    "CSI300 buy&hold": ("CSI 300 index held throughout (ETF proxy, price index)", lambda p: S.index_buy_hold(p, "000300")),
    "CSI300 MA-timing": ("CSI 300 while above its 120-day MA (2% band), else cash", lambda p: S.index_ma_timing(p, "000300", TREND_MA, TREND_BUF)),
    "300/500 rotation": ("each week hold CSI 300 or CSI 500, whichever has the better 20-day return; cash if both negative",
                         lambda p: S.index_rotation(p, ("000300", "000905"), 20, 5)),
}


def risk_on_signal(panel, fac):
    return S.trend_filter(S.market_index(panel, fac), TREND_MA, TREND_BUF)


def run_all(panel, fac, start="2000-01-04", index_start="2006-01-04", costs: Costs | None = None, only=None):
    on = risk_on_signal(panel, fac)
    out = {}
    for name, (_, build) in STOCK_STRATEGIES.items():
        if only and name not in only:
            continue
        out[name] = run_stock_strategy(panel, name, build(panel, fac, start, on), start, costs=costs)
    for name, (_, build) in INDEX_STRATEGIES.items():
        if only and name not in only:
            continue
        out[name] = run_index_strategy(panel, name, build(panel), index_start)
    return out
