# Research Log

---
## 2026-02-28: Initial Data Exploration

### Question
What does BTCUSDT 2024 hourly data actually look like fr?

### Findings
- 8,761 bars from 2023-12-31 to 2024-12-30
- price ranged from $38,555 to $108,353
- year return: +116.3%
- annualized volatility: 52.5%
- mean hourly volume: 1,474 BTC
- skewness: -0.13 (slight left tail)
- kurtosis: 7.63 (fat tails)

### What this means
1. high volatility (52%) - lots of opportunity but need to be careful with position sizing
2. fat tails (kurtosis 7.6) - normal distribution assumptions don't hold. extreme moves happen ~3x more often than expected. stop losses can get blown through
3. slight negative skew - crashes are a bit sharper than rallies
4. strong uptrend (+116%) - buy and hold was very profitable in 2024. any strategy needs to beat this benchmark

---
## 2026-03-XX: First Backtest - Simple Momentum

### Strategy
- buy 0.5 BTC when price up >2% over last 24 hours
- sell all when price down >2% over last 24 hours

### Results
- return: 17.7%
- sharpe: 0.18
- max drawdown: 15.9%
- trades: 203
- win rate: 38%
- total costs: $13,325 (fees + slippage)

### vs benchmark
buy & hold returned +116%. strategy significantly underperformed.

### Lessons
1. simple momentum doesn't work well in strong uptrends - constantly getting out and missing gains
2. transaction costs ($13k) are significant - need fewer, higher quality trades
3. 38% win rate can still be profitable if winners are bigger than losers

### Ideas for next
- try mean reversion?
- longer holding periods to reduce costs?
- only trade when volatility is high?

---
## 2026-03-11: Strategy Comparison - Momentum vs Mean Reversion

### Question
How do Momentum and Mean Reversion compare across different parameters? Which params matter most?

### Setup
- BTCUSDT 2024 hourly bars (8,761 bars)
- $100,000 capital
- 10 bps taker fee (Binance spot)
- 5 bps half-spread slippage
- tested 3 lookbacks (12, 24, 48h) × 3 thresholds (1%, 2%, 3%) = 9 combos per strategy, 18 total

### Momentum results

| Lookback | Threshold | Return (%) | Sharpe | Max DD (%) | Trades | Win Rate (%) | Fees ($) |
|----------|-----------|------------|--------|------------|--------|--------------|----------|
| 12h      | 1%        | -2.0       | 0.01   | 19.9       | 508    | 33.4         | 22,053   |
| 12h      | 2%        | 27.5       | 0.26   | 15.1       | 235    | 40.3         | 10,344   |
| 12h      | 3%        | 15.4       | 0.15   | 23.1       | 125    | 42.7         | 5,451    |
| 24h      | 1%        | -0.5       | 0.02   | 25.7       | 388    | 35.8         | 16,577   |
| 24h      | 2%        | 13.4       | 0.14   | 19.4       | 207    | 38.0         | 9,044    |
| **24h**  | **3%**    | **40.7**   | **0.36**| **10.6**   | **119**| **43.6**     | **5,240**|
| 48h      | 1%        | 25.8       | 0.23   | 23.5       | 248    | 36.6         | 10,458   |
| 48h      | 2%        | 39.8       | 0.35   | 14.5       | 146    | 44.8         | 6,382    |
| 48h      | 3%        | 33.6       | 0.30   | 12.7       | 113    | 43.2         | 4,997    |

### Mean Reversion results

| Lookback | Threshold | Return (%) | Sharpe | Max DD (%) | Trades | Win Rate (%) | Fees ($) |
|----------|-----------|------------|--------|------------|--------|--------------|----------|
| 12h      | 1%        | -6.6       | -0.03  | 23.1       | 321    | 61.4         | 13,946   |
| 12h      | 2%        | 12.8       | 0.15   | 14.9       | 116    | 64.9         | 4,905    |
| **12h**  | **3%**    | **36.0**   | **0.39**| **10.6**   | **47** | **86.7**     | **1,894**|
| 24h      | 1%        | -11.1      | -0.07  | 23.8       | 293    | 60.3         | 12,418   |
| 24h      | 2%        | 1.1        | 0.03   | 22.0       | 136    | 60.9         | 5,820    |
| 24h      | 3%        | 12.9       | 0.14   | 13.1       | 62     | 65.0         | 2,626    |
| 48h      | 1%        | -5.3       | -0.02  | 17.6       | 210    | 62.5         | 8,910    |
| 48h      | 2%        | 0.4        | 0.03   | 19.3       | 119    | 63.6         | 5,153    |
| 48h      | 3%        | 8.6        | 0.10   | 21.8       | 64     | 57.1         | 2,761    |

### Best per strategy
- Momentum: 24h lookback, 3% threshold → 40.7% return, 0.36 Sharpe, 119 trades
- Mean Reversion: 12h lookback, 3% threshold → 36.0% return, 0.39 Sharpe, 47 trades

### vs benchmark
- buy & hold returned +116%
- best Momentum (40.7%) captured ~35% of that
- best MeanRev (36.0%) captured ~31%
- no strategy beats buy & hold - expected in a strong trending year

### Takeaways
1. threshold matters more than lookback. across both strategies, 1% threshold consistently loses money. 3% consistently does best. tighter threshold → more trades → more fees → worse results
2. fees are the silent killer. Momentum(12h, 1%) paid $22k in fees on a $100k account and lost money. Momentum(24h, 3%) paid $5.2k and returned 40.7%. roughly 4x difference in fees between worst and best combos
3. MeanReversion(12h, 3%) has best Sharpe but only 47 trades. 87% win rate looks amazing but 47 trades is thin. standard error of Sharpe ≈ 1/√47 ≈ 0.15, so true Sharpe could be anywhere from 0.24 to 0.54. Momentum(24h, 3%) with 119 trades is more trustworthy (SE ≈ 0.09)
4. mean reversion has higher win rates but lower returns. MeanRev: 57-87% win rate. Momentum: 33-45%. but Momentum's best return (40.7%) beats MeanRev's best (36.0%). MeanRev wins many small trades but misses the big trending moves
5. both strategies struggle in trends. BTC 2024 was a +116% year. Momentum keeps exiting and re-entering. Mean Reversion actively fights the trend

### Next
- add SMA crossover and Bollinger band strategies
- walk-forward testing to check if best params are overfit
- would these results hold in a sideways or bearish market?

---
## 2026-03-26: SMA Crossover + Bollinger Bands

### Question
Do SMA crossover and Bollinger bands do better than Momentum and Mean Reversion?

### SMA Crossover
Uses two moving averages - a fast one and a slow one. When the fast crosses above the slow, buy. When it crosses below, sell. Idea is it should stay in trades longer than raw Momentum.

Tested 11 fast/slow combos. Best was SMA(24/72) with Sharpe 0.39, 22.8% return, 10.4% maxDD, 132 trades. SMA(24/96) was close behind with the lowest drawdown I've seen so far - 7.7%.

Main finding: the wider the gap between fast and slow periods, the better. Small gaps like 6/24 or 12/24 just trade on noise and lose money.

### Bollinger Bands
SMA plus/minus N standard deviations. Bands get wider when volatility is high and tighter when its low. Buy when price drops below lower band, sell when it goes above upper band. Basically adaptive mean reversion.

Tested 20 period/std combos. Best was BB(24, 2.5σ) with Sharpe 0.20, 20.0% return, 13.0% maxDD. Worst strategy so far. BB(12, 1.0σ) lost 40.8% with 848 trades - tight bands in a bull market is a disaster. BB(12, 3.0σ) only triggered 4 trades total so thats useless.

### Ranking so far (best params for each)

| Strategy | Sharpe | Return | MaxDD | Trades |
|----------|--------|--------|-------|--------|
| SMA(24/72) | 0.39 | 22.8% | 10.4% | 132 |
| MeanRev(12h, 3%) | 0.39 | 36.0% | 10.6% | 47 |
| Momentum(24h, 3%) | 0.36 | 40.7% | 10.6% | 119 |
| Bollinger(24, 2.5σ) | 0.20 | 20.0% | 13.0% | 159 |

### Takeaways
- trend following (SMA, Momentum) beats mean reversion (Bollinger, MeanRev) in a bull market. makes sense - mean reversion keeps selling into rallies
- SMA crossover has the smoothest equity curves. best risk profile of everything tested
- Bollinger bands didnt really improve on basic MeanReversion despite being "adaptive". same core problem - fighting the trend
- still nothing beats buy & hold at 116%
- same pattern everywhere: wider filters, fewer trades, less fees = better results

### Next
- walk-forward testing - are these best params overfit to 2024?
- would mean reversion win in a flat or bearish year?
---

## 2026-04-03: Walk-Forward Testing

### Question
Are the "best" params from the sweeps actually good, or did they just get lucky on 2024 data?

### Method
Walk-forward analysis with anchored expanding windows. Train always starts from January, test is the next unseen month. 9 windows total (train→Mar test Apr, train→Apr test May, ... train→Nov test Dec). For each window, run the full param sweep on training data, pick the best Sharpe, then test those params on the next month. The test month is strictly out-of-sample - the optimizer never saw it.

Used the same param grids from the original sweeps:
- Momentum: lookback [12,24,48] × threshold [1%,2%,3%]
- MeanReversion: same grid
- SMA: fast [12,24,48] × slow [48,72,96,120], skipping fast >= slow
- Bollinger: period [12,24,36,48] × std [1.0,1.5,2.0,2.5,3.0]

### Results

| Strategy | In-Sample Sharpe | OOS Sharpe | Overfit Ratio | Win Rate | Param Stability |
|----------|-----------------|------------|---------------|----------|-----------------|
| MeanReversion | 0.39 | 0.51 | 1.31 | 78% | 1 unique (perfect) |
| Bollinger | 0.20 | 0.33 | 1.63 | 89% | 3 unique |
| Momentum | 0.36 | 0.24 | 0.67 | 56% | 2 unique |
| SMA | 0.39 | 0.22 | 0.56 | 56% | 4 unique |

Overfit ratio = OOS Sharpe / in-sample Sharpe. 1.0 = no overfit, <0.5 = heavily overfit.

### The ranking flipped
In-sample ranking: MeanRev = SMA > Momentum > Bollinger
Out-of-sample ranking: MeanRev > Bollinger > Momentum > SMA

Bollinger jumped from last to second. SMA dropped from tied-first to last. This is exactly why you do walk-forward - in-sample numbers are misleading.

### Parameter stability
- MeanReversion picked {lookback: 12, threshold: 0.03} in all 9 windows. perfect stability
- Momentum picked {lookback: 24, threshold: 0.03} in 8/9 windows. very stable
- Bollinger settled on {period: 24, num_std: 2.5} from June onward (7/9). good once it settled
- SMA jumped between 4 different combos. optimizer chasing noise

### Equity curves
All four strategies ended profitable OOS (Apr-Dec):
- Momentum: $100k → $128k
- MeanReversion: $100k → $126k
- Bollinger: $100k → $125k
- SMA: $100k → $117k

MeanReversion had the smoothest curve - stair-steps up with flat periods, never really dips. Momentum was flat Apr-Sep then ripped Oct-Nov. SMA and Bollinger somewhere in between.

None beat buy & hold ($100k → ~$148k over same period). Still expected in a trending year.

### Monthly breakdown
Momentum is feast-or-famine: -5.5% in Aug, +16.5% in Nov. Returns concentrated in trending months (Jul, Oct, Nov).
MeanReversion is consistent: never lost money in any month. Worst was 0.0% (flat in Jun and Nov). Best was 7.4% (Sep).
Bollinger similar to MeanReversion but with slightly more variance.
SMA struggled in choppy months and only showed up for the Nov trend.

### Takeaways
1. parameter stability is a better predictor of robustness than raw in-sample Sharpe. SMA had the best in-sample Sharpe (tied) but worst OOS because its "best" params kept changing
2. MeanReversion is genuinely robust. same params won every window, OOS Sharpe actually exceeded in-sample. these aren't overfit
3. Momentum works but is regime-dependent. prints money in trends, bleeds in chop. you'd want some way to detect the regime before allocating to it
4. Bollinger was underrated in-sample. its low Sharpe was partly because early windows hadn't settled on good params. once it locked onto (24, 2.5σ) it was consistently positive
5. a practical allocation would blend MeanReversion (consistent base) with Momentum (trend capture). diversification across strategy types, not just assets

### Next
- error analysis document
- lessons learned document
- README improvements
- then exams (weeks 13-15), then summer L2 work

---
## 2026-05-06: Post-Only Fix and First Clean MM Baseline

### Question
Why was the maker fill rate only 34% with a $5 half-spread and 5-second requote interval? A passive market maker should be nearly 100% maker.

### Investigation
Wrote a diagnostic script that intercepted every order activation in the simulator and logged whether it was classified as aggressive (crossing the spread at arrival) or resting.

Result: 831 orders activated as resting, 17 as aggressive. Only 2% of orders were aggressive. But those 17 aggressive orders produced 17 taker fills, while 831 resting orders produced only 7 maker fills. Taker fills are certain (immediate execution); maker fills are rare (need a trade to reach your deep-in-book level and drain the queue). So taker fills dominated by count even though aggressive orders were a tiny fraction.

Root cause: orders are computed against the current book but only activate at the next depth event (~100ms later). In that window, the mid can move $5+ during volatile moments. A bid at `mid - 5` submitted during a calm moment arrives after a crash and is now above the best ask. The simulator was executing these as taker fills.

### Fix
Added `post_only=True` to `SimConfig`. When a limit order arrives and would cross the spread, the simulator now cancels it instead of executing as taker. This mimics real exchange post-only (maker-only) order behavior. After the fix: 100% maker fills.

### Clean Baseline Results
Re-ran the 5-hour comparison (2026-04-16 12:00-17:00) with `half_spread=5.00`, `requote_interval_ms=5000`, `post_only=True`:

| Strategy | Fills | Maker % | Net PnL | Fees | Spread bps | Avg markout bps | AdvSel bps |
|---|---|---|---|---|---|---|---|
| symmetric | 78 | 100% | -3.22 | 0.46 | 0.45 | -1.52 | 1.47 |
| microprice | 79 | 100% | -3.57 | 0.47 | 0.45 | -1.50 | 1.34 |

Both strategies lose money. The previous +$1.56 result was an artifact of favorable taker fills.

### What this means

1. **Adverse selection is the dominant cost.** Average markout is -1.5 bps - the mid moves against you after each fill. Spread capture is only 0.45 bps. You're losing about 1 bps per fill to informed flow. Fees ($0.46 total) are negligible by comparison.

2. **The fills that happen are the worst fills.** When your bid $5 below mid gets hit, it's because someone is selling aggressively through that level. The trade that fills you is informed - price keeps going. This is the fundamental adverse selection problem in market making.

3. **Microprice doesn't help at this spread width.** At $5 from mid, the microprice vs arithmetic mid difference (usually a few cents) rounds to the same tick. In 3 of 5 sessions they produced identical fills and P&L.

4. **The old profitability was fake.** Any conclusion drawn from the pre-fix results (34% maker rate) was contaminated by taker fills that happened to go in the right direction. The post-only fix makes the simulation honest.

### Next
- Try narrower spreads to increase fill count and spread capture, but expect more adverse selection
- Investigate whether fills cluster at specific times (maybe all the losses come from a few fast moves)
- Inventory skew might help - but only after establishing whether any spread width breaks even on a pure passive basis

---
## 2026-05-08: Prediction Before the Post-Only Quote-Mechanics Sweep

### Setup
About to run the first uncontaminated quote-mechanics sweep with `post_only=True` and the new `fill_rate.py` lens. Grid: half_spread ∈ {1, 2, 3, 5, 8} dollars, requote ∈ {1, 5}s, both strategies, one window 2026-04-16T12–17. Five 1-hour sessions per combo. 100 sessions total.

Writing predictions down now so the post-mortem is honest. The interview value of "I predicted X, found Y, here's why my intuition was off" is much higher than retrospective rationalization.

### The decomposition I'm thinking in
session_pnl ≈ orders_submitted × P(fill) × E[pnl_per_fill | filled]

Per fill, in bps of notional:
E[pnl_per_fill_bps] ≈ spread_capture_bps − fee_bps − |markout_bps|

At ~$100k BTC, $1 ≈ 1 bp. So half_spread ∈ {1, 2, 3, 5, 8} dollars ≈ {1, 2, 3, 5, 8} bps.

### Predictions

**Fill rate.** Drops monotonically with half_spread. Steeply. At half_spread=1 bp expecting ~5–10% fill rate; at half_spread=8 bps expecting <0.3%. Roughly inverse-quadratic in distance because price has to walk to deeper levels and walks are ~Gaussian over short horizons.

**E[pnl|fill].** Increases monotonically with half_spread, possibly plateauing at the wide end. Reasoning: spread_capture grows linearly with distance (1, 2, 3, 5, 8 bps) but |markout| should grow sublinearly because the Glosten-Milgrom toxicity bias *decreases* with distance - fills at deep levels are more likely uninformed flow that walked there, not informed traders crossing.
- At half_spread=1 bp: expecting markout ~−2.5 bps → pnl ≈ 1 − 2 − 2.5 = −3.5 bps. Very negative.
- At half_spread=5 bps: brief showed markout ~−1.5 bps → pnl ≈ 5 − 2 − 1.5 = +1.5 bps per fill.
- At half_spread=8 bps: expecting markout ~−1.0 bps → pnl ≈ 8 − 2 − 1.0 = +5 bps per fill, but very few fills.

**Total session P&L.** Likely negative across the entire grid, with the *least negative* (or maybe slightly positive) point at half_spread = 5 or 8 bps. Reasoning: the brief already showed half_spread=5 was net −0.65/session over a 5-hour block. The arithmetic above suggests per-fill economics could be marginally positive at half_spread=5+, but the brief's negative aggregate result must come from a small number of bad fills dominating; the median fill might already be positive at half_spread=5. The fill_rate lens should make this distinction visible for the first time.

**Microprice vs symmetric.** Approximately tied at wide spreads (rounding makes their quotes identical). Microprice may show a small advantage at narrow spreads where the few-cents shift actually changes the rounded tick. If neither is profitable, microprice's advantage is in *less negative*, not in *positive*.

**Requote interval.** 1s vs 5s probably similar net P&L. 1s has more attempts but higher cancel-replace churn so some quotes never get to fill. 5s has older quotes that may be staler at fill (worse markout) but more chances to get hit. Weak prior: 5s slightly better.

**Fill_rate breakdown (the lens itself):**
- By distance bucket: most orders cluster in one bucket per combo (since strategy quotes at fixed half_spread). Comparing across combos gives the surface.
- By volatility bucket: expect markout-given-fill to be *more negative* at higher volatility regimes - textbook adverse-selection-scales-with-vol prediction, and the case for VolAdaptiveMM.
- By quote age bucket: muddier prior. Survivor bias confounds the simple read.

### Where I could be wrong (most informative scenarios)

1. **Total P&L is positive at half_spread=8.** Would mean toxicity drop with distance is steeper than modelled. Implication: spread width alone solves the problem; inventory skew is a refinement, not a rescue.
2. **Total P&L is *more* negative at the wide end.** Fill count drops faster than per-fill economics improve, OR wide-spread fills are *equally* toxic (i.e., toxicity isn't really distance-dependent at this timescale, contradicting Glosten-Milgrom intuition). That'd be a real finding.
3. **Markout-given-fill is *not* monotonic in distance.** Would suggest the toxicity story is wrong - maybe fills at moderate distances are the worst because that's where retail-aggressive flow lives.
4. **Volatility regime doesn't matter for markout-given-fill.** Would undercut VolAdaptiveMM before it gets built. Best possible outcome from the "save effort" perspective - better to know now.

### Decision rule
- If any combo has positive net P&L: that's the operating point. Build InventorySkewMM on top.
- If no combo is positive but the *least negative* point has clearly positive E[pnl|fill] and the loss is just "too few fills": fill rate is the binding constraint, not edge per fill. Implication: distance is right, need queue-priority or a smarter quote-placement timing.
- If *every* point has negative E[pnl|fill] after fees: passive at this asset/data is fundamentally unprofitable. Story becomes "why" - and that's what fill_rate.py's distance/vol breakdown answers. Steps 3–4 become "rescue" attempts; step 5 still has value (showing strategies fail consistently across windows is itself walk-forward evidence).

---
## 2026-05-08: Post-Only Sweep Results - Prediction Mostly Wrong

### Headline
Found a profitable operating point: **half_spread=2.00, requote=5000ms, both strategies profitable.**

| Strategy | Half spread | Requote | Fills | Net PnL | Spread bps | AdvSel bps |
|---|---:|---:|---:|---:|---:|---:|
| microprice | 2.00 | 5000ms | 157 | **+1.90** | 0.17 | 1.96 |
| symmetric | 2.00 | 5000ms | 156 | **+1.83** | 0.17 | 1.99 |

Surface (5s requote, microprice, by half_spread):
- 1.00: -1.21
- 2.00: **+1.90**, peak
- 3.00: -1.57
- 5.00: -3.57
- 8.00: -1.74

Inverted-U shape with the peak at half_spread=2. Wider spreads do worse, not better.

### Predictions, graded

| Prediction | Result | Verdict |
|---|---|---|
| Total P&L negative across the entire grid | Half_spread=2 profitable | Wrong |
| Least-negative at half_spread = 5 or 8 bps | 5 and 8 are *among the worst* | Wrong |
| Fill rate drops monotonically with half_spread | Fill rate peaks at half_spread=2 | Wrong |
| E[pnl|fill] increases monotonically with distance | Non-monotone; worst markout at half_spread=5 | Wrong |
| Microprice approximately equals symmetric | Tied within $0.10 across all combos | Correct |
| 5s requote slightly better than 1s | 5s dominates everywhere, often by $2-4 | Correct direction, understated magnitude |

Score: 2/6. The "shape" model in my head was almost completely wrong.

### Why I was wrong

**1. Underestimated post-only filtering at narrow distances.** At half_spread=1.00, ~18,000 orders submitted but only 110 fills (0.6% rate). Many quotes get rejected at arrival because the book moved. The post-only filter is doing useful protective work at narrow spreads, but it also eats most attempts. Net: too few fills.

**2. Overestimated toxicity-decay with distance.** I expected wider spreads to attract uninformed flow that "walked there." Empirically, fills at half_spread=5-8 are rare and happen during big directional moves that *continue*. Markouts get *worse* at the wide end (5.00/1000ms had adverse_selection of 3.6 bps; 8.00/1000ms had 4.1 bps).

**3. The 30s markout may be misleading, but I have not proved the mechanism yet.** At the operating point, markout-given-fill is -1.85 bps while net P&L is positive. My first explanation was that inventory turns over before 30s, but that is only a hypothesis until I measure inventory hold times and reconcile markouts against actual matched lots.

**4. Cancel/replace churn matters more than I weighted.** 1s requote produces 2.5-3x more orders submitted but only about 30% more fills. The unfilled-and-replaced orders incur opportunity cost: you cancel one that was about to fill and place a new one with fresh queue position. 5s requote keeps quotes in book long enough for queue to drain.

### Mechanism: why half_spread=2 wins

- **Far enough that post-only does not reject most attempts** (fills at 2.00/5000 are 2.07% of orders, vs 1.10% at 1.00/5000, almost 2x higher fill rate, plus more orders).
- **Close enough to fill in normal flow, not just on big informed moves.** Bulk of fills happen at 2-5 bps rolling vol regime, which is the middle of the market-state distribution, not the extremes.
- **Spread capture (~0.17 bps) is much smaller than adverse selection markout (~1.96 bps).** This needs reconciliation before claiming the strategy has real edge.

### Volatility breakdown at the operating point (microprice 2.00/5000)

| Vol regime | n_fills | Fill rate | Markout|filled |
|---|---:|---:|---:|
| 0-1 bps | 16 | 2.12% | -1.20 bps |
| 1-2 bps | 35 | 1.89% | **-2.60 bps** |
| 2-5 bps | 73 | 2.15% | -1.83 bps |
| 5-10 bps | 14 | 2.31% | -1.27 bps |
| 10+ bps | 3 | 1.97% | -0.47 bps (n=3) |

**Toxicity is U-shaped in vol, not monotone.** Worst toxicity at mid-vol (1-2 bps), better at extremes. This is the opposite of the textbook Avellaneda-Stoikov assumption "adverse cost scales linearly with vol."

Possible mechanism: at low vol there's no informed flow to fear; at high vol other MMs widen and the few crossings that happen are noise/chaos that mean-reverts; mid-vol is where directional informed flow is most active.

### Implications for next steps

- **Do the markout reconciliation before InventorySkewMM.** The operating point is profitable, but the explanation is not settled.
- **VolAdaptiveMM is not yet justified.** The textbook formulation (widen when vol high) may not work here. Empirics suggest narrowing at extreme vols and widening at mid-vol, but this needs more windows before becoming a design.
- **Walk-forward remains important.** My one-window predictions failed, so I need to know whether the surface shape is stable across windows. The 17-22 window from the same day is the obvious second test.

### Caveats

- One window only. The 17-22 window from the same day, and other days, may show different surfaces.
- Sample sizes per fill_rate bucket are small (n=14 at vol 5-10 bps). The U-shape in toxicity is suggestive but not statistically robust.
- 30s markout horizon is one choice. The PnL and markout divergence needs a dedicated reconciliation investigation.

---
## 2026-05-08: Markout Reconciliation Investigation

### Why I ran this
The sweep result looked internally contradictory:

- `half_spread=2.00`, `requote=5000ms`, microprice made **+1.90** across five independent 1-hour sessions.
- Spread capture was only **0.17 bps**.
- 30s adverse-selection cost was **1.96 bps**.

My first explanation was: "30s markout overstates realized adverse cost because inventory turns over before 30s." That sounded plausible, but it was not proven. So I built `src/analysis/hold_time.py` and `scripts/analyze_markout_reconciliation.py` to FIFO-match fills, measure hold times, compute short-horizon markouts, decompose pre-fill drift, and inspect queue position.

### Result
The original reconciliation story is **wrong as stated**.

Operating point:

- Strategy: microprice
- Half spread: `2.00`
- Requote interval: `5000ms`
- Window: `2026-04-16T12` to `2026-04-16T17`
- Methodology: five independent 1-hour sessions, matching the sweep

Key outputs:

| Metric | Value |
|---|---:|
| Fills | 157 |
| Net PnL | +1.8969557759 |
| Spread capture | +0.0918925065 |
| Fees | 1.1062469251 |
| Actual inventory PnL | +2.9113101945 |
| Matched inventory PnL | +1.6837024569 |
| Residual inventory PnL | +1.2276077376 |
| Median hold time | 439,223 ms (7.3 min) |
| p25 hold time | 216,980 ms |
| p75 hold time | 832,993 ms |
| p90 hold time | 1,610,636 ms |

This is the crucial correction: **30s is not too long. It is much shorter than median realized hold time.**

### Horizon reconciliation

The markout proxy does not reconcile realized PnL at any tested horizon.

| Horizon | Proxy inventory PnL | Proxy net PnL | Error vs net PnL |
|---|---:|---:|---:|
| 1s | -0.2932 | -1.3075 | -3.2045 |
| 5s | -0.3171 | -1.3315 | -3.2284 |
| 10s | -0.3163 | -1.3306 | -3.2276 |
| 30s | -0.3872 | -1.4016 | -3.2986 |

The "best" horizon by PnL error is 1s, but it is still very far from realized net PnL. This means fixed-horizon markout is not measuring the same object as the realized accounting PnL for this strategy.

### Pre-fill drift

Average fill-level decomposition:

- Avg quoted distance: **0.27 bps**
- Avg edge at fill: **0.19 bps**
- Avg pre-fill mid move: **-0.08 bps**

This supports the idea that some adverse selection happens before the fill, but it is not enough by itself to explain the PnL gap.

### Queue diagnostics

Queue diagnostics show that filled orders are usually close to the front of queue by the time they fill.

- Orders: 6,763
- Filled orders: 141
- Avg initial queue ahead for filled orders: 0.0441 BTC
- Avg queue ahead before trade at first fill: 0.0050 BTC
- Avg queue ahead before fill: 0
- Total trade queue drained: 1.4811 BTC
- Total cancellation queue drained: 17.6832 BTC

This leans toward the queue-position explanation for 5s requote dominance: leaving orders resting lets queue ahead drain, especially through the simulator's proportional-cancellation model. That is a useful but important footnote because this profitability depends partly on a queue model assumption.

### Book spread check

At fill time, average book spread is basically one tick (`0.01`, about `0.0013 bps`). The high-vol bucket is not explained by the market spread widening above the $2 quote distance. Quotes are usually at best or behind best, not inside a wide spread.

### Updated interpretation

The profitable `2.00/5000ms` result is more fragile than it looked.

The old story:

- "30s markout overstates adverse cost because inventory closes before 30s."

The updated story:

- "Fixed-horizon markouts do not reconcile realized PnL here."
- "A large part of the positive PnL comes from inventory PnL, including residual open inventory at session marks."
- "5s requote likely works partly because it preserves queue position."
- "Before InventorySkewMM, I need to check whether this survives another window and whether PnL remains positive after controlling for residual inventory mark-to-market."

### Next

Do not build InventorySkewMM yet. Next investigation:

1. Run the same reconciliation on `2026-04-16T17` to `2026-04-16T22`.
2. Add a metric that separates completed round-trip PnL from residual inventory mark-to-market.
3. Compare `1s` vs `5s` requote through queue diagnostics.
4. Only build InventorySkewMM if the baseline remains credible after this reconciliation pass.
