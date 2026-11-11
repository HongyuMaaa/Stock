# High-quality A-share stock pool: six-criteria screen

Screened all 5,224 companies on the Shanghai/Shenzhen main boards, ChiNext and STAR using annual reports
2016–2025, the Q3 2026 report, dividend records, and prices as of 2026-11-11.
**Seven companies meet all six criteria; 24 more meet five.** Full tables: [`results.md`](results.md),
every company's scores: `tables/all_scores.csv`. Reproduce with `python scripts/screen_quality.py`.

Two of the six criteria cannot be settled by numbers:

- **Governance (5)** is scored on proxies only. A pass means "no red flag in the data", not "well governed".
- **Industry outlook (6)** is scored on the last five years of revenue growth, which says where the
  industry has been, not where it is going.

For both I add a judgement column, which is my own view from general knowledge and may be out of date.
Treat it as a prompt for your own reading, not as a finding.

## How each criterion is measured

| Criterion | Fully met when | Partly met when |
|---|---|---|
| 1 Industry leadership | Top 3 by revenue or net profit in its industry (128 Eastmoney industries), 10-year ROE at or above the industry median, market value ≥ 20bn CNY | Top 5 by revenue/profit or top 3 by market value, ≥ 10bn |
| 2 Stable performance | 10 annual reports, no loss year, average ROE ≥ 12% (financials ≥ 9%), no year with ROE < 6% or profit down more than 30%, operating cash flow ≥ 80% of profit over 5 years (not applied to financials), 9-month 2026 profit not down more than 20% | 8+ reports, no loss year, average ROE ≥ 8%, no profit drop over 50%, cash conversion ≥ 60% |
| 3 High dividends | FY2025 cash dividends ≥ 3% of today's price, paid in each of the last 10 years, payout 25–90% of earnings, not cut by more than 20% | Yield ≥ 2%, 5+ consecutive years, payout 15–110% |
| 4 Reasonable valuation | P/E (trailing) ≤ 20 and P/B ≤ 5 | P/E ≤ 28 and P/B ≤ 8 |
| 5 Governance (proxy) | Not ST, pledged shares ≤ 15%, debt ratio ≤ 70% (non-financial), 10 years listed with no loss, 10 straight years of dividends, cash conversion ≥ 80% | Not ST, pledges ≤ 30%, at most one loss year, 5+ years of dividends |
| 6 Industry growth (proxy) | Industry revenue and company revenue both grew ≥ 5% a year over 2020–2025, and 9-month 2026 revenue is not down more than 5% | Both non-negative, 2026 revenue not down more than 15% |

The thresholds are plain round numbers set before looking at which companies pass. Moving them changes
the names at the margin; the tables show each company's raw figures so you can apply your own cut-offs.

## The funnel

| Step | Companies |
|---|---:|
| Scored | 5,224 |
| Leader | 249 |
| Stable | 236 |
| Dividend | 213 |
| Valuation | 831 |
| Governance proxy | 708 |
| Growth proxy | 1,847 |
| **All six** | **7** |
| Five of six (market value ≥ 20bn) | 24 |

Leadership, stability and dividends are each met by under 5% of the market; a high yield that is also
sustainable is the rarest of all.

## Tier 1: meet all six

| Code | Company | Industry | Mkt cap (bn CNY) | P/E | P/B | Yield | Payout | ROE 10y avg | Profit 9M 2026 | Revenue 5y/yr |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 000333 | Midea Group 美的集团 | White goods | 669 | 14.1 | 3.0 | 4.9% | 74% | 23.9% | +9% | +10% |
| 002415 | Hikvision 海康威视 | Security equipment | 314 | 17.6 | 3.7 | 3.4% | 74% | 26.3% | +39% | +8% |
| 600660 | Fuyao Glass 福耀玻璃 | Auto parts | 145 | 17.6 | 3.9 | 3.8% | 59% | 18.1% | -15% | +18% |
| 002001 | NHU (Zhejiang NHU) 新和成 | Chemicals | 80 | 10.9 | 2.2 | 3.9% | 45% | 18.1% | +10% | +17% |
| 000708 | CITIC Special Steel 中信特钢 | Special steel | 63 | 10.9 | 1.4 | 5.2% | 55% | 17.2% | -4% | +7% |
| 600741 | Huayu Automotive 华域汽车 | Auto parts | 52 | 7.6 | 0.8 | 6.1% | 44% | 13.8% | -8% | +7% |
| 603899 | M&G Stationery 晨光股份 | Stationery | 21 | 15.7 | 2.3 | 4.4% | 70% | 22.6% | +0% | +14% |

| Company | What the data shows | Judgement on moat, governance, outlook (mine; verify) |
|---|---|---|
| **Midea** | Largest by revenue and profit in white goods; ROE never below 19.7% in ten years; profit has not fallen in any of them; 4.9% yield on a 74% payout. The cleanest pass in the market. | Diversified into HVAC, robotics and components; professional management with a long buyback and dividend record. Appliance demand at home is mature, so growth depends on exports and commercial lines. |
| **Hikvision** | Largest profit in its industry, ROE averaging 26%, 9-month 2026 profit up 39%. | Global leader in video surveillance. On the US Entity List since 2019, which constrains components and overseas sales; state-controlled via CETC. The export-control risk is the main thing the numbers do not show. |
| **Fuyao Glass** | 18% average ROE, 37% gross margin, revenue up 18% a year. 9-month profit down 15%. | Dominant auto-glass maker in China with a large global share; content per car rises with EVs. Founder-led, and overseas plants add tariff and labour exposure. |
| **NHU** | 18% ROE, revenue up 17% a year, P/E 10.9. Ranks 5th by revenue and 2nd by profit in a 171-company industry. | A leading vitamin and amino-acid producer. Earnings follow vitamin prices; the low P/E may reflect peak-cycle profit rather than cheapness. |
| **CITIC Special Steel** | A third of its industry's revenue; ROE 17%; 5.1% yield. | China's largest special-steel maker, backed by CITIC. Still a steel business: cyclical, capital-heavy, tied to machinery and auto demand. |
| **Huayu Automotive** | P/E 7.6, P/B 0.76, 6.1% yield, profit flat for four years (6.7–7.2bn). 12% gross margin. | Largest domestic parts group, but a subsidiary of SAIC with heavy dependence on SAIC's joint ventures, which have been losing share. Cheap for a reason; the weakest moat in this tier. |
| **M&G Stationery** | 42% of its industry's revenue, ROE 23%, 10 years of dividends. Gross margin is down to 18% as low-margin office supply grows. | Strongest stationery brand in China. Falling births shrink the core student market, which the 5-year revenue growth conceals. |

All seven pass on numbers. On judgement I would rank **Midea** clearly first, then **Fuyao** and
**Hikvision** (subject to the sanctions risk), and treat Huayu, CITIC Special Steel, NHU and M&G as
passes with a visible weakness in moat, cyclicality or outlook.

## Tier 2: meet five of six

| Code | Company | Industry | Mkt cap (bn CNY) | P/E | P/B | Yield | Payout | ROE 10y avg | Profit 9M 2026 | Revenue 5y/yr | Not fully met |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 601939 | Construction Bank 建设银行 | Banks | 3,118 | 8.8 | 0.8 | 3.3% | 30% | 12.7% | +7% | +0% | Growth |
| 601398 | ICBC 工商银行 | Banks | 3,040 | 7.9 | 0.8 | 3.6% | 31% | 12.2% | +6% | -1% | Growth (fails) |
| 601288 | Agricultural Bank 农业银行 | Banks | 2,541 | 8.3 | 0.9 | 3.4% | 32% | 12.2% | +7% | +2% | Growth |
| 601857 | PetroChina 中国石油 | Oil & gas | 2,182 | 11.8 | 1.3 | 3.9% | 55% | 6.4% | +22% | +8% | Stable (fails) |
| 600519 | Kweichow Moutai 贵州茅台 | Baijiu | 1,753 | 21.3 | 6.5 | 3.7% | 79% | 31.9% | -0% | +12% | Valuation |
| 601088 | China Shenhua 中国神华 | Coal | 1,063 | 19.5 | 2.4 | 4.1% | 76% | 13.2% | +4% | +5% | Growth |
| 601899 | Zijin Mining 紫金矿业 | Metals & mining | 741 | 10.4 | 3.5 | 2.1% | 31% | 18.3% | +52% | +15% | Dividend |
| 600900 | Yangtze Power 长江电力 | Power | 721 | 22.1 | 3.2 | 3.4% | 71% | 15.1% | -7% | +8% | Valuation |
| 600690 | Haier Smart Home 海尔智家 | White goods | 196 | 11.4 | 1.6 | 5.5% | 55% | 18.7% | -14% | +8% | Growth |
| 600887 | Yili 伊利股份 | Dairy | 194 | 19.5 | 3.4 | 4.5% | 75% | 22.9% | -16% | +4% | Growth |
| 600018 | Shanghai Intl Port 上港集团 | Ports | 132 | 8.6 | 0.9 | 3.4% | 33% | 13.0% | +16% | +9% | Growth (fails) |
| 600233 | YTO Express 圆通速递 | Logistics | 62 | 10.3 | 1.6 | 1.4% | 20% | 15.5% | +61% | +17% | Dividend (fails) |
| 000963 | Huadong Medicine 华东医药 | Pharma | 47 | 13.6 | 1.8 | 3.5% | 48% | 18.9% | +1% | +5% | Growth |
| 603369 | Jinshiyuan 今世缘 | Baijiu | 41 | 15.9 | 2.3 | 3.7% | 57% | 21.1% | -2% | +15% | Leader (fails) |
| 603198 | Yingjia Gongjiu 迎驾贡酒 | Baijiu | 35 | 17.0 | 3.2 | 3.4% | 60% | 22.0% | +4% | +12% | Leader (fails) |
| 600298 | Angel Yeast 安琪酵母 | Condiments | 32 | 20.0 | 2.5 | 1.5% | 31% | 18.2% | +5% | +13% | Dividend (fails) |
| 603699 | Neway Valve 纽威股份 | Machinery | 31 | 19.4 | 6.6 | 4.0% | 77% | 17.6% | +0% | +16% | Valuation |
| 603345 | Anjoy Foods 安井食品 | Food processing | 29 | 18.4 | 1.8 | 3.3% | 65% | 14.1% | +20% | +18% | Growth (fails) |
| 002056 | Hengdian DMEGC 横店东磁 | Solar equipment | 28 | 16.3 | 2.6 | 5.7% | 84% | 17.2% | -8% | +23% | Leader |
| 600583 | Offshore Oil Engineering 海油工程 | Oil services | 27 | 12.7 | 0.9 | 3.2% | 42% | 4.1% | +5% | +9% | Stable (fails) |
| 603444 | G-bits 吉比特 | Games | 25 | 11.6 | 4.3 | 5.6% | 79% | 34.3% | +31% | +18% | Leader |
| 600323 | Grandblue Environment 瀚蓝环境 | Environmental | 25 | 11.3 | 1.6 | 3.4% | 43% | 13.3% | +15% | +13% | Growth |
| 600511 | China National Medicines 国药股份 | Drug distribution | 21 | 10.8 | 1.1 | 2.8% | 30% | 14.6% | -1% | +5% | Dividend |
| 600970 | Sinoma International 中材国际 | Engineering | 21 | 9.9 | 0.9 | 6.1% | 44% | 14.2% | -38% | +10% | Stable |

Grouped by what is missing:

- **Missing only the growth proxy, otherwise strong:** Agricultural Bank, Construction Bank, China
  Shenhua, Haier, Yili, Huadong Medicine, Grandblue. These are income holdings: dependable cash, little
  growth. Coal faces long-run decline under decarbonisation, and bank margins are under pressure.
- **Missing only valuation:** Moutai (P/E 21.3, P/B 6.5), Yangtze Power (P/E 22.1), Neway (P/B 6.6).
  The first two are among the highest-quality businesses in the market and sit just outside the
  valuation line; they qualify on a modest price decline.
- **Missing only dividend:** Zijin Mining (2.1%), China National Medicines (2.8%).
- **Not leaders, otherwise pass:** Hengdian DMEGC, G-bits. Smaller companies with high returns; G-bits
  depends on a few game titles.
- **Outright failure on one criterion (excluded from the pool):** PetroChina and Offshore Oil
  Engineering (ROE too low and erratic), ICBC and Shanghai Port (no growth), YTO and Angel Yeast (yield
  under 1.5%), Jinshiyuan and Yingjia (regional baijiu, not leaders), Anjoy (its industry shrank).
- **Sinoma International** now misses stability: 9-month 2026 profit is down 38%.

## Well-known names that do not qualify

| Code | Company | Industry | Mkt cap (bn CNY) | P/E | P/B | Yield | Payout | ROE 10y avg | Profit 9M 2026 | Revenue 5y/yr | Not fully met |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 600519 | Kweichow Moutai 贵州茅台 | Baijiu | 1,753 | 21.3 | 6.5 | 3.7% | 79% | 31.9% | -0% | +12% | Valuation |
| 000858 | Wuliangye 五粮液 | Baijiu | 281 | 23.1 | 2.4 | 7.1% | 224% | 21.3% | +50% | -7% | Stable (fails), Dividend (fails), Valuation, Growth (fails) |
| 600036 | China Merchants Bank 招商银行 | Banks | 1,073 | 7.0 | 0.9 | 4.7% | 35% | 16.0% | +2% | +3% | Leader, Growth |
| 601318 | Ping An 中国平安 | Insurance | 967 | 9.8 | 0.9 | 5.1% | 35% | 16.7% | -27% | -3% | Stable, Growth (fails) |
| 300750 | CATL 宁德时代 | Batteries | 1,141 | 13.0 | 3.0 | 3.2% | 49% | 24.4% | +32% | +53% | Dividend (fails), Governance (fails) |
| 002594 | BYD 比亚迪 | Cars | 748 | 23.6 | 3.0 | 0.4% | 10% | 12.1% | -4% | +39% | Stable, Dividend (fails), Valuation, Governance |
| 600276 | Hengrui 恒瑞医药 | Pharma | 320 | 36.0 | 4.8 | 0.4% | 17% | 18.1% | +20% | +3% | Dividend (fails), Valuation (fails), Growth |
| 000651 | Gree 格力电器 | White goods | 217 | 8.7 | 1.4 | 7.8% | 58% | 26.4% | -20% | +0% | Dividend, Governance, Growth |
| 600309 | Wanhua Chemical 万华化学 | Chemicals | 219 | 11.5 | 1.9 | 1.8% | 31% | 32.5% | +71% | +23% | Stable, Dividend (fails) |
| 603288 | Haitian 海天味业 | Condiments | 202 | 28.5 | 5.1 | 3.1% | 86% | 28.4% | +1% | +5% | Valuation (fails), Growth |
| 600436 | Pien Tze Huang 片仔癀 | TCM | 79 | 59.7 | 5.5 | 2.3% | 83% | 21.9% | -40% | +7% | Leader, Stable, Dividend, Valuation (fails), Growth |
| 300760 | Mindray 迈瑞医疗 | Medical devices | 214 | 27.6 | 5.4 | 2.5% | 65% | 33.0% | -5% | +10% | Stable, Dividend, Valuation, Governance, Growth |
| 601888 | China Tourism Duty Free 中国中免 | Travel retail | 108 | 25.8 | 1.9 | 1.3% | 40% | 18.8% | +20% | +0% | Stable, Dividend (fails), Valuation, Growth (fails) |
| 000002 | Vanke 万科A | Property | 38 | loss | 0.4 | 0.0% |  | 5.5% | +15% | -11% | Stable (fails), Dividend (fails), Valuation (fails), Governance (fails), Growth (fails) |
| 601012 | LONGi 隆基绿能 | Solar equipment | 89 | loss | 1.9 | 0.0% |  | 16.0% | -38% | +5% | Stable (fails), Dividend (fails), Valuation (fails), Governance (fails), Growth |
| 002714 | Muyuan 牧原股份 | Hog farming | 255 | loss | 3.3 | 3.1% | 47% | 26.5% | -163% | +21% | Stable (fails), Dividend (fails), Valuation (fails), Governance (fails), Growth (fails) |
| 600585 | Conch Cement 海螺水泥 | Cement | 95 | 21.2 | 0.5 | 4.7% | 55% | 15.3% | -57% | -14% | Stable (fails), Valuation, Growth (fails) |
| 000895 | Shuanghui 双汇发展 | Food processing | 84 | 17.5 | 3.8 | 6.0% | 98% | 29.2% | -8% | -4% | Dividend, Growth (fails) |
| 600066 | Yutong Bus 宇通客车 | Commercial vehicles | 67 | 12.4 | 4.7 | 8.2% | 100% | 17.3% | -3% | +14% | Stable (fails), Dividend, Growth (fails) |
| 600406 | NARI Technology 国电南瑞 | Grid equipment | 186 | 22.3 | 3.5 | 2.7% | 60% | 15.9% | +1% | +11% | Dividend, Valuation |

- **Expensive or paying almost nothing:** Hengrui (P/E 36, 0.4% yield), Haitian (P/E 28.5), Pien Tze
  Huang (P/E 60), Mindray, BYD.
- **Earnings not stable:** Wuliangye (2025 profit down 72%, dividend more than twice earnings), Vanke,
  LONGi and Muyuan (losses), Conch Cement (profit down 53% in its worst year and falling again), Yutong
  Bus (ROE as low as 3.1%), Wanhua.
- **Short or broken dividend record:** CATL (4 years), Gree (skipped 2017, and 16% of shares pledged).
- **Close:** NARI (2.7% yield, P/E 22), China Merchants Bank (ranks 5th–6th among banks by size, so
  not a "leader" by this rule, and no growth), Shuanghui (pays out 98% of earnings, revenue shrinking).

Two of the stocks in your earlier five-security basket appear here: Yutong Bus fails stability and
Shuanghui fails growth and dividend sustainability; NARI is close but not in.

## The pool

Rule: all Tier 1 names, plus Tier 2 names with no outright failure, at most two per industry, market
value ≥ 20bn CNY. That gives 22 companies in 19 industries.

| Code | Company | Industry | Mkt cap (bn CNY) | P/E | P/B | Yield | Payout | ROE 10y avg | Profit 9M 2026 | Revenue 5y/yr | Not fully met |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 600741 | Huayu Automotive 华域汽车 | Auto parts | 52 | 7.6 | 0.8 | 6.1% | 44% | 13.8% | -8% | +7% | – |
| 000708 | CITIC Special Steel 中信特钢 | Special steel | 63 | 10.9 | 1.4 | 5.2% | 55% | 17.2% | -4% | +7% | – |
| 000333 | Midea Group 美的集团 | White goods | 669 | 14.1 | 3.0 | 4.9% | 74% | 23.9% | +9% | +10% | – |
| 603899 | M&G Stationery 晨光股份 | Stationery | 21 | 15.7 | 2.3 | 4.4% | 70% | 22.6% | +0% | +14% | – |
| 002001 | NHU (Zhejiang NHU) 新和成 | Chemicals | 80 | 10.9 | 2.2 | 3.9% | 45% | 18.1% | +10% | +17% | – |
| 600660 | Fuyao Glass 福耀玻璃 | Auto parts | 145 | 17.6 | 3.9 | 3.8% | 59% | 18.1% | -15% | +18% | – |
| 002415 | Hikvision 海康威视 | Security equipment | 314 | 17.6 | 3.7 | 3.4% | 74% | 26.3% | +39% | +8% | – |
| 600970 | Sinoma International 中材国际 | Engineering | 21 | 9.9 | 0.9 | 6.1% | 44% | 14.2% | -38% | +10% | Stable |
| 002056 | Hengdian DMEGC 横店东磁 | Solar equipment | 28 | 16.3 | 2.6 | 5.7% | 84% | 17.2% | -8% | +23% | Leader |
| 603444 | G-bits 吉比特 | Games | 25 | 11.6 | 4.3 | 5.6% | 79% | 34.3% | +31% | +18% | Leader |
| 600690 | Haier Smart Home 海尔智家 | White goods | 196 | 11.4 | 1.6 | 5.5% | 55% | 18.7% | -14% | +8% | Growth |
| 600887 | Yili 伊利股份 | Dairy | 194 | 19.5 | 3.4 | 4.5% | 75% | 22.9% | -16% | +4% | Growth |
| 601088 | China Shenhua 中国神华 | Coal | 1,063 | 19.5 | 2.4 | 4.1% | 76% | 13.2% | +4% | +5% | Growth |
| 603699 | Neway Valve 纽威股份 | Machinery | 31 | 19.4 | 6.6 | 4.0% | 77% | 17.6% | +0% | +16% | Valuation |
| 600519 | Kweichow Moutai 贵州茅台 | Baijiu | 1,753 | 21.3 | 6.5 | 3.7% | 79% | 31.9% | -0% | +12% | Valuation |
| 000963 | Huadong Medicine 华东医药 | Pharma | 47 | 13.6 | 1.8 | 3.5% | 48% | 18.9% | +1% | +5% | Growth |
| 601288 | Agricultural Bank 农业银行 | Banks | 2,541 | 8.3 | 0.9 | 3.4% | 32% | 12.2% | +7% | +2% | Growth |
| 600323 | Grandblue Environment 瀚蓝环境 | Environmental | 25 | 11.3 | 1.6 | 3.4% | 43% | 13.3% | +15% | +13% | Growth |
| 600900 | Yangtze Power 长江电力 | Power | 721 | 22.1 | 3.2 | 3.4% | 71% | 15.1% | -7% | +8% | Valuation |
| 601939 | Construction Bank 建设银行 | Banks | 3,118 | 8.8 | 0.8 | 3.3% | 30% | 12.7% | +7% | +0% | Growth |
| 600511 | China National Medicines 国药股份 | Drug distribution | 21 | 10.8 | 1.1 | 2.8% | 30% | 14.6% | -1% | +5% | Dividend |
| 601899 | Zijin Mining 紫金矿业 | Metals & mining | 741 | 10.4 | 3.5 | 2.1% | 31% | 18.3% | +52% | +15% | Dividend |

How I would use it:

| Group | Companies | Role |
|---|---|---|
| Core quality | Midea, Hikvision, Fuyao | Meet all six and hold up on judgement |
| Quality at a low price, with a caveat | Huayu, CITIC Special Steel, NHU, M&G | Meet all six on numbers; cyclical or structurally challenged |
| Income | Agricultural Bank, Construction Bank, Shenhua, Haier, Yili, Huadong Medicine, Grandblue | 3.3–5.5% yield, little growth |
| Quality, wait for price | Moutai, Yangtze Power, Neway | Buy below roughly 20× earnings |
| Growth at a fair price | Zijin Mining, China National Medicines | Lower yield |
| Smaller / watch | Hengdian DMEGC, G-bits, Sinoma International | Not leaders, or earnings currently falling |

- **Concentration.** Consumer names (Midea, Haier, Yili, Moutai, M&G) are about a quarter of the pool and cyclicals (steel, chemicals,
  mining, auto parts, solar, engineering) about a third. Technology and healthcare are
  thin, because high-quality companies there rarely pay 3% or trade under 20× earnings.
- **Sizing.** Equal weights with no more than 15% in any one sector is a reasonable starting point; I
  have not optimised weights.

## Limits

- **Not backtested.** This is a snapshot at one date. I have not shown that this screen would have
  beaten the market historically; doing that properly needs reports back to about 2005 and historical
  valuations, which I did not download.
- **Low P/E can mean peak earnings.** Zijin, NHU, CITIC Special Steel and Hengdian DMEGC earn cyclical
  profits; a trailing P/E flatters them near the top of a cycle.
- **Governance is not measured.** Related-party dealings, accounting quality, audit opinions,
  regulatory penalties and how minority shareholders have been treated are not in this data. State
  ownership, common in the pool, usually means stable dividends but also policy obligations.
- **"Leader" depends on the industry map.** Eastmoney's 128 industries are broad in places (auto parts
  has 244 companies) and narrow in others (special steel has 12), which makes leadership easier to
  claim in small groups.
- **Data.** Reports and dividends are from Eastmoney via akshare, quotes from Tencent. I spot-checked
  dividend and earnings records for several companies but did not reconcile against annual reports.
- **Not investment advice.** Read the latest annual report of anything you intend to buy.

## Refreshing

```bash
python scripts/download_fundamentals.py          # new reports and dividends
python scripts/screen_quality.py --refresh-quotes
```
