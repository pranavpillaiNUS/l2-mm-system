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
- tested 3 lookbacks (12, 24, 48h) x 3 thresholds (1%, 2%, 3%) = 9 combos per strategy, 18 total

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
- Momentum: 24h lookback, 3% threshold -> 40.7% return, 0.36 Sharpe, 119 trades
- Mean Reversion: 12h lookback, 3% threshold -> 36.0% return, 0.39 Sharpe, 47 trades

### vs benchmark
- buy & hold returned +116%
- best Momentum (40.7%) captured ~35% of that
- best MeanRev (36.0%) captured ~31%
- no strategy beats buy & hold - expected in a strong trending year

### Takeaways
1. threshold matters more than lookback. across both strategies, 1% threshold consistently loses money. 3% consistently does best. tighter threshold -> more trades -> more fees -> worse results
2. fees are the silent killer. Momentum(12h, 1%) paid $22k in fees on a $100k account and lost money. Momentum(24h, 3%) paid $5.2k and returned 40.7%. roughly 4x difference in fees between worst and best combos
3. MeanReversion(12h, 3%) has best Sharpe but only 47 trades. 87% win rate looks amazing but 47 trades is thin. standard error of Sharpe is roughly 1/sqrt(47), or 0.15, so true Sharpe could be anywhere from 0.24 to 0.54. Momentum(24h, 3%) with 119 trades is more trustworthy (SE roughly 0.09)
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

Tested 20 period/std combos. Best was BB(24, 2.5 std) with Sharpe 0.20, 20.0% return, 13.0% maxDD. Worst strategy so far. BB(12, 1.0 std) lost 40.8% with 848 trades - tight bands in a bull market is a disaster. BB(12, 3.0 std) only triggered 4 trades total so thats useless.

### Ranking so far (best params for each)

| Strategy | Sharpe | Return | MaxDD | Trades |
|----------|--------|--------|-------|--------|
| SMA(24/72) | 0.39 | 22.8% | 10.4% | 132 |
| MeanRev(12h, 3%) | 0.39 | 36.0% | 10.6% | 47 |
| Momentum(24h, 3%) | 0.36 | 40.7% | 10.6% | 119 |
| Bollinger(24, 2.5 std) | 0.20 | 20.0% | 13.0% | 159 |

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
Walk-forward analysis with anchored expanding windows. Train always starts from January, test is the next unseen month. 9 windows total (train->Mar test Apr, train->Apr test May, ... train->Nov test Dec). For each window, run the full param sweep on training data, pick the best Sharpe, then test those params on the next month. The test month is strictly out-of-sample - the optimizer never saw it.

Used the same param grids from the original sweeps:
- Momentum: lookback [12,24,48] x threshold [1%,2%,3%]
- MeanReversion: same grid
- SMA: fast [12,24,48] x slow [48,72,96,120], skipping fast >= slow
- Bollinger: period [12,24,36,48] x std [1.0,1.5,2.0,2.5,3.0]

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
- Momentum: $100k -> $128k
- MeanReversion: $100k -> $126k
- Bollinger: $100k -> $125k
- SMA: $100k -> $117k

MeanReversion had the smoothest curve - stair-steps up with flat periods, never really dips. Momentum was flat Apr-Sep then ripped Oct-Nov. SMA and Bollinger somewhere in between.

None beat buy & hold ($100k -> ~$148k over same period). Still expected in a trending year.

### Monthly breakdown
Momentum is feast-or-famine: -5.5% in Aug, +16.5% in Nov. Returns concentrated in trending months (Jul, Oct, Nov).
MeanReversion is consistent: never lost money in any month. Worst was 0.0% (flat in Jun and Nov). Best was 7.4% (Sep).
Bollinger similar to MeanReversion but with slightly more variance.
SMA struggled in choppy months and only showed up for the Nov trend.

### Takeaways
1. parameter stability is a better predictor of robustness than raw in-sample Sharpe. SMA had the best in-sample Sharpe (tied) but worst OOS because its "best" params kept changing
2. MeanReversion is genuinely robust. same params won every window, OOS Sharpe actually exceeded in-sample. these aren't overfit
3. Momentum works but is regime-dependent. prints money in trends, bleeds in chop. you'd want some way to detect the regime before allocating to it
4. Bollinger was underrated in-sample. its low Sharpe was partly because early windows hadn't settled on good params. once it locked onto (24, 2.5 std) it was consistently positive
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
About to run the first uncontaminated quote-mechanics sweep with `post_only=True` and the new `fill_rate.py` lens. Grid: half_spread in {1, 2, 3, 5, 8} dollars, requote in {1, 5}s, both strategies, one window 2026-04-16T12-17. Five 1-hour sessions per combo. 100 sessions total.

Writing predictions down now so the post-mortem is honest. The interview value of "I predicted X, found Y, here's why my intuition was off" is much higher than retrospective rationalization.

### The decomposition I'm thinking in
session_pnl approx orders_submitted x P(fill) x E[pnl_per_fill | filled]

Per fill, in bps of notional:
E[pnl_per_fill_bps] approx spread_capture_bps - fee_bps - |markout_bps|

At ~$100k BTC, $1 approx 1 bp. So half_spread in {1, 2, 3, 5, 8} dollars approx {1, 2, 3, 5, 8} bps.

### Predictions

**Fill rate.** Drops monotonically with half_spread. Steeply. At half_spread=1 bp expecting ~5-10% fill rate; at half_spread=8 bps expecting <0.3%. Roughly inverse-quadratic in distance because price has to walk to deeper levels and walks are ~Gaussian over short horizons.

**E[pnl|fill].** Increases monotonically with half_spread, possibly plateauing at the wide end. Reasoning: spread_capture grows linearly with distance (1, 2, 3, 5, 8 bps) but |markout| should grow sublinearly because the Glosten-Milgrom toxicity bias *decreases* with distance - fills at deep levels are more likely uninformed flow that walked there, not informed traders crossing.
- At half_spread=1 bp: expecting markout ~-2.5 bps -> pnl approx 1 - 2 - 2.5 = -3.5 bps. Very negative.
- At half_spread=5 bps: brief showed markout ~-1.5 bps -> pnl approx 5 - 2 - 1.5 = +1.5 bps per fill.
- At half_spread=8 bps: expecting markout ~-1.0 bps -> pnl approx 8 - 2 - 1.0 = +5 bps per fill, but very few fills.

**Total session P&L.** Likely negative across the entire grid, with the *least negative* (or maybe slightly positive) point at half_spread = 5 or 8 bps. Reasoning: the brief already showed half_spread=5 was net -0.65/session over a 5-hour block. The arithmetic above suggests per-fill economics could be marginally positive at half_spread=5+, but the brief's negative aggregate result must come from a small number of bad fills dominating; the median fill might already be positive at half_spread=5. The fill_rate lens should make this distinction visible for the first time.

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
- If *every* point has negative E[pnl|fill] after fees: passive at this asset/data is fundamentally unprofitable. Story becomes "why" - and that's what fill_rate.py's distance/vol breakdown answers. Steps 3-4 become "rescue" attempts; step 5 still has value (showing strategies fail consistently across windows is itself walk-forward evidence).

> Correction, 2026-06-17: the conversion "$1 approx 1 bp at ~$100k BTC" in the
> decomposition above is wrong. At $100k, $1 is 0.1 bp ($1 / $100,000 x 10,000 =
> 0.1 bp), and at the ~$71.5k price level in this data $1 is about 0.14 bp. So
> half_spread in {1, 2, 3, 5, 8} dollars is about {0.14, 0.28, 0.42, 0.70, 1.12}
> bps here, not {1, 2, 3, 5, 8} bps, and the half_spread=2.00 baseline quotes
> about 0.28 bps from the reference, not 2 bps. This was a prose conversion error
> in the prediction reasoning only. The computed markout, adverse-selection,
> spread-capture, and PnL metrics use move / price x 10,000 (or absolute dollar
> PnL) directly and are unaffected. The original entry is preserved for the audit
> trail.

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

---
## 2026-05-08: Round-Trip PnL And Second-Window Reconciliation

### What changed
Extended `hold_time.py` so FIFO matched lots now allocate fees proportionally:

- `matched_gross_pnl`: completed round-trip PnL before fees.
- `matched_fees`: opening and closing fees allocated by matched quantity.
- `matched_net_pnl`: completed round-trip PnL after fees.
- `residual_inventory_pnl`: remaining session inventory mark-to-market, separate from completed round trips.

This directly answers the question: "Is the strategy making money on closed inventory cycles, or only because open inventory marks favorably at session end?"

### Results

All runs below use:

- Strategy: `microprice`
- Half spread: `2.00`
- Order quantity: `0.001`
- Max position: `0.01`
- Latency: `10ms`
- Jitter: `0`
- Methodology: five independent 1-hour sessions per block

| Window | Requote | Fills | Net PnL | Matched net PnL | Residual inv PnL | Median hold |
|---|---:|---:|---:|---:|---:|---:|
| 12-17 | 5000ms | 157 | +1.8970 | +0.8357 | +1.2276 | 439s |
| 12-17 | 1000ms | 214 | -0.4571 | -0.8411 | +0.5640 | 377s |
| 17-22 | 5000ms | 149 | +1.5244 | +0.5689 | +1.1796 | 542s |
| 17-22 | 1000ms | 206 | +0.0074 | -1.1581 | +1.4324 | 339s |

### Interpretation

This is a big improvement over the previous state of knowledge.

The 5s operating point is not only winning through residual inventory mark-to-market. It has positive completed-round-trip PnL in both tested blocks:

- `+0.8357` from 12-17.
- `+0.5689` from 17-22.

Residual inventory still matters a lot, but the passive baseline is now more credible than it looked after the first reconciliation pass.

The 1s setting is worse despite more fills:

- 12-17: more fills, but matched net PnL is `-0.8411`.
- 17-22: near-flat total net PnL, but matched net PnL is `-1.1581`.

So the 1s strategy appears to rely even more on favorable residual inventory marks, while the 5s strategy has actual completed-cycle edge.

### Queue comparison

| Window | Requote | Orders | Filled orders | Avg initial queue | Avg queue before fill trade | Total trade drain | Total cancel drain |
|---|---:|---:|---:|---:|---:|---:|---:|
| 12-17 | 5000ms | 6,763 | 141 | 0.0441 | 0.0050 | 1.4811 | 17.6832 |
| 12-17 | 1000ms | 17,537 | 205 | 0.0443 | 0.0055 | 2.9438 | 49.7568 |
| 17-22 | 5000ms | 6,538 | 138 | 0.0201 | 0.0016 | 0.8604 | 22.4281 |
| 17-22 | 1000ms | 14,158 | 199 | 0.0228 | 0.0025 | 4.3491 | 41.9586 |

The queue evidence supports the current explanation:

- 1s requote sends far more orders.
- 1s gets more fills, but those fills are lower quality after fees.
- 5s keeps fewer orders resting, but the completed round trips are profitable.
- In every case, filled orders have much lower queue ahead by fill time than at entry.

This supports the queue-position story more than a simple "more attempts is better" story.

### Updated decision

InventorySkewMM is now reasonable to build next, but with a specific goal:

- Not "capture more spread."
- Not "fix an unprofitable baseline."
- Goal: reduce residual inventory dependence while preserving the positive completed-round-trip edge of the 5s passive baseline.

The benchmark for InventorySkewMM should be:

1. Keep matched net PnL positive.
2. Reduce residual inventory PnL dependence.
3. Reduce average and tail absolute inventory.
4. Avoid increasing order churn toward the bad 1s-like regime.

---
## 2026-05-08: Baseline CI Across Six 5-Hour Windows

### Why this was run
Before building `InventorySkewMM`, I wanted to avoid building strategy complexity on top of a fragile baseline. The previous two 5-hour windows from 2026-04-16 looked promising, but that was too little data.

So I ran four more baseline windows on different days:

- `2026-04-13T12` to `2026-04-13T17`
- `2026-04-14T12` to `2026-04-14T17`
- `2026-04-15T12` to `2026-04-15T17`
- `2026-04-17T12` to `2026-04-17T17`

All runs use:

- Strategy: `microprice`
- Half spread: `2.00`
- Requote interval: `5000ms`
- Order quantity: `0.001`
- Max position: `0.01`
- Latency: `10ms`
- Jitter: `0`
- Methodology: five independent 1-hour sessions per 5-hour block

### New tooling
Added:

- `src/analysis/bootstrap.py`
- `scripts/bootstrap_baseline_ci.py`
- `tests/test_bootstrap.py`

The bootstrap report samples at three levels:

1. Matched lot level: useful, but optimistic because lots in the same hour are correlated.
2. 1-hour session level: better for estimating session variability.
3. 5-hour window level: most honest for comparing strategy variants across market regimes, though sample size is still small.

### Window results

| Window | Fills | Net PnL | Matched net PnL | Residual inv PnL | Median hold | Orders/fill |
|---|---:|---:|---:|---:|---:|---:|
| 2026-04-13 12-17 | 157 | -11.1816 | -3.2564 | -7.7503 | 475s | 42.5 |
| 2026-04-14 12-17 | 167 | +1.9334 | -1.3870 | +3.5653 | 456s | 41.0 |
| 2026-04-15 12-17 | 156 | +0.9215 | +0.7630 | +0.4210 | 914s | 42.3 |
| 2026-04-16 12-17 | 157 | +1.8970 | +0.8357 | +1.2276 | 439s | 43.1 |
| 2026-04-16 17-22 | 149 | +1.5244 | +0.5689 | +1.1796 | 542s | 43.9 |
| 2026-04-17 12-17 | 155 | -6.7543 | +0.5427 | -7.0576 | 657s | 48.3 |

### Bootstrap CI results

Across all six 5-hour windows:

| Unit | Metric | Mean | 95% CI |
|---|---|---:|---:|
| 5-hour window | Net PnL | -1.9433 | [-6.2409, +1.6844] |
| 5-hour window | Matched net PnL | -0.3222 | [-1.6518, +0.7061] |
| 5-hour window | Residual inventory PnL | -1.4024 | [-4.7769, +1.8566] |
| 1-hour session | Net PnL | -0.3887 | [-1.2522, +0.3253] |
| 1-hour session | Matched net PnL | -0.0644 | [-0.3368, +0.2180] |
| 1-hour session | Residual inventory PnL | -0.2805 | [-1.1041, +0.3336] |
| Matched lot | Net PnL per lot | -0.0027 | [-0.0083, +0.0031] |
| Matched lot | Net PnL per BTC | -29.8368 | [-46.4057, -13.0804] |

### Interpretation

This overturns the previous "build InventorySkewMM next" decision.

The two 2026-04-16 windows were not representative enough. After adding four more windows:

- Total net PnL is negative on average.
- Completed round-trip matched net PnL is negative on average.
- Both window-level and session-level CIs cross zero.
- Residual inventory PnL is large and unstable in both directions.
- The matched-lot per-BTC result is clearly negative, but this unit is correlated and should be treated as a diagnostic rather than the main decision metric.

The baseline is therefore not credible enough yet as a positive-edge passive strategy.

### Updated decision

Do not build `InventorySkewMM` yet.

The next question is no longer:

"Can skew reduce residual inventory dependence while preserving a positive matched edge?"

The corrected question is:

"Why does the same 2-dollar, 5s passive quote have positive matched edge in some windows and negative matched edge in others?"

The likely next diagnostic should compare good and bad windows by:

1. Drift/trend over the 5-hour window.
2. Realized volatility and volatility shape.
3. Fill side imbalance and inventory path.
4. Queue diagnostics for profitable vs unprofitable matched lots.
5. Whether half_spread=2 was only optimal on 2026-04-16 and should be re-swept across the new windows.

This is a better research position than blindly adding inventory skew. It is less flattering, but more defensible.

---
## 2026-05-08: Microprice Predictiveness Test

### Why this was run
After the six-window baseline CI, the next question was whether the premise behind `MicropriceMM` is even true.

The strategy assumes that:

`microprice - mid` predicts future mid drift.

If that premise is stable, then the signal exists and the current quoting strategy may simply harvest it inefficiently. If the premise is not stable, then adding `InventorySkewMM` or `VolAdaptiveMM` on top of microprice is premature.

### Method
Built:

- `src/analysis/microprice_signal.py`
- `scripts/analyze_microprice_signal.py`
- `tests/test_microprice_signal.py`

The script:

1. Replays depth data only, with no strategy and no trade/fill simulation.
2. Samples the book every 1 second using the latest book state at or before the sample time.
3. Computes microprice deviation:
   `(microprice - mid) / mid * 10000`
4. Computes forward mid drift at `1s`, `10s`, `1m`, and `5m`:
   `(mid[t+h] - mid[t]) / mid[t] * 10000`
5. Regresses forward drift on microprice deviation per window and pooled.
6. Uses HAC/Newey-West t-stats so overlapping forward returns do not make the t-stats too optimistic.

Important: the script also reports `beta * x_std`, because a 1 bp microprice deviation is not realistic in this data. The actual standard deviation of microprice deviation is usually only `0.002-0.019 bps`.

### Results

One-second horizon:

| Window | Matched net PnL | Beta | HAC t-stat | R2 | Signal std | Beta * signal std |
|---|---:|---:|---:|---:|---:|---:|
| 2026-04-13 12-17 | -3.2564 | -1.5252 | -0.39 | 0.00019 | 0.00857 | -0.0131 bps |
| 2026-04-14 12-17 | -1.3870 | +4.1882 | +3.75 | 0.00224 | 0.01096 | +0.0459 bps |
| 2026-04-15 12-17 | +0.7630 | +14.7303 | +3.08 | 0.00496 | 0.00357 | +0.0526 bps |
| 2026-04-16 12-17 | +0.8357 | +3.8810 | +2.18 | 0.00152 | 0.00947 | +0.0367 bps |
| 2026-04-16 17-22 | +0.5689 | +48.1848 | +3.44 | 0.01804 | 0.00188 | +0.0908 bps |
| 2026-04-17 12-17 | +0.5427 | -0.0743 | -0.05 | 0.00000 | 0.01898 | -0.0014 bps |
| Pooled | -0.3222 | +1.6210 | +1.12 | 0.00032 | 0.01049 | +0.0170 bps |

Pooled across all windows:

| Horizon | Beta | HAC t-stat | R2 | Beta * signal std |
|---|---:|---:|---:|---:|
| 1s | +1.6210 | +1.12 | 0.00032 | +0.0170 bps |
| 10s | -2.1406 | -0.82 | 0.00005 | -0.0225 bps |
| 1m | -6.3856 | -1.50 | 0.00006 | -0.0671 bps |
| 5m | -8.2580 | -1.47 | 0.00002 | -0.0873 bps |

### Interpretation

Microprice is not a stable standalone signal across these windows.

There is some short-horizon predictiveness in the better windows:

- 2026-04-15
- 2026-04-16 12-17
- 2026-04-16 17-22

But it is not stable enough to be the foundation for the next strategy:

- 2026-04-13 is negative.
- 2026-04-17 is basically zero at 1s and negative at longer horizons.
- The pooled 1s t-stat is only `+1.12`.
- The R2 is tiny in every case.
- The actual economic effect size is tiny because the microprice deviation itself is tiny.

The strongest-looking coefficient is 2026-04-16 17-22, but even there the one-standard-deviation predicted 1s drift is only about `0.09 bps`. That is information, but not enough by itself to pay maker fees or explain a market-making edge.

The signal buckets reinforce this: almost all observations sit in the near-zero bucket `[-0.1, 0.1) bps`. Larger microprice deviations are rare, so a thresholded signal strategy would have very few opportunities unless the threshold is extremely low.

### Updated decision

Do not build `InventorySkewMM` yet.

Do not treat microprice as a proven edge.

The result is best described as:

"Microprice has weak, regime-dependent short-horizon predictiveness in some windows, but it is not stable or economically large enough across the tested sample to justify strategy complexity by itself."

Next research question:

"What distinguishes the windows where microprice has positive 1s predictiveness and positive matched PnL from the windows where the passive baseline fails?"

Likely next diagnostics:

1. Compare good and bad windows by trend, realized volatility, and volatility shape.
2. Compare fill side imbalance and inventory path.
3. Re-run quote-mechanics sweeps across the added windows to see whether `half_spread=2` was only optimal on 2026-04-16.
4. Test whether a microprice signal threshold improves fill quality, but only after confirming the threshold has enough observations.


---

## 2026-05-23 - Tail Diagnostics V1

Built Tail Diagnostics V1:

- `src/analysis/tail_diagnostics.py`
- `scripts/analyze_mm_tail_diagnostics.py`
- `tests/test_tail_diagnostics.py`

Generated:

- `results/tail_diagnostics/btcusdt_microprice_hs2.00_rq5000_anchor6/summary.json`
- `window_summary.csv`
- `fill_tail_rows.csv`
- `matched_lot_tail_rows.csv`
- `cluster_summary.csv`

Inputs:

- corrected six anchor windows
- 921 fill toxicity rows at `30s`
- 696 matched lots

Core result:

Fill toxicity and matched-lot PnL are telling different stories.

Fill-level 30s toxicity:

- mean: `-2.0175 bps`
- median: `-1.6916 bps`
- worst 5% explains `39.6%` of total adverse 30s movement
- worst 10% explains `66.8%`

Matched-lot net PnL per BTC:

- mean: `-28.0814`
- median: `-26.1867`
- worst 5% explains `111.5%` of total matched-lot loss
- worst 10% explains `176.6%`

Interpretation:

- Fill toxicity is broad and somewhat tail-heavy.
- Matched-lot losses are strongly tail-dominated.
- Apr 13 and Apr 14 have negative matched-lot body economics even after removing each window's worst 5%.
- Apr 15, Apr 16 12-17, Apr 16 17-22, and Apr 17 have positive body economics.
- Pooled body metrics can hide this heterogeneity.

This changes the fee question. The right next step is not:

```text
Would a rebate fix the pooled result?
```

The right next step is:

```text
Which windows need what maker fee or rebate, and how stable is that requirement?
```

Matched-lot clustering:

- pooled matched-lot 120s clusters: 14 clusters from 35 tail lots
- pooled matched-lot 300s clusters: 11 clusters from 35 tail lots
- worst 300s cluster: `matched_lot_pooled_pooled_300000_4`
- time: Apr 14, 13:55:18 to 13:58:19 UTC
- contents: 8 matched lots, 4 unique closing fills, 5 unique opening fills

Correct description:

> A short realization episode closed multiple toxic inventory lots.

Do not call it 8 independent bad fills. FIFO matching can split one closing fill
across multiple opening lots.

Session-boundary caution:

- The largest matched-lot cluster is within 30 minutes after US cash open.
- Other large clusters do not show a consistent boundary pattern.
- The anchor windows are not uniformly distributed across the day.
- Boundary proximity is descriptive only until compared with a uniform-time
  baseline.

Small V1 patch:

- Kept `cluster_share_of_total_metric_sum`.
- Added `cluster_share_of_negative_metric_sum`.
- Added `cluster_share_of_tail_metric_sum`.

Reason:

Some windows have positive total metric sums. In those windows, a negative worst
cluster divided by positive total metric sum gives a negative share. That is
mathematically valid but confusing in a writeup. The negative-denominator share
is safer to quote.

Tests:

```text
env PYTHONPATH=. pytest -q tests/test_tail_diagnostics.py
7 passed

env PYTHONPATH=. pytest -q tests --ignore=tests/test_recorder.py
163 passed
```

---
## 2026-06-02: V2 Evidence-Expansion Protocol Frozen

### Why this was done

The six-window V1 result was useful but too small to carry broader claims. V2
expands evidence before adding strategy complexity and treats replay
correctness, clean-data inventory, queue-model dependence, and holdout handling
as explicit protocol items.

### Replay correctness

Added `trade_gap_policy` with legacy `ignore` and strict
`pause_until_snapshot`. Strict mode cancels open orders, pauses unreliable
events, and resumes after the next valid depth snapshot.

Generated:

- `results/replay_correctness/trade_gap_anchor6_delta.json`

Result:

- all six V1 anchors are trade-gap-clean
- every old/new delta is exactly zero
- V1 was not a trade-gap artifact

### Frozen integrity inventory and panels

Generated:

- `results/panels/btcusdt_l2_panel_v2/integrity_manifest.json`
- `results/panels/btcusdt_l2_panel_v2/window_selection_summary.json`

Semantic hashes:

```text
Integrity manifest: a3a99b0a616abe3bc39e0863ed047f075db9ed5d8118ced57c16140b499d8a61
Selected panel:     760c55b7c0929b4a99657f6ca02eb723d930b9f48ebd3786d57bcb1a0f481122
```

Strict capacity:

```text
Frozen hours: 1193
Valid hours:   775
Invalid hours: 418
```

| Range | Valid Hours | Candidate 5h Starts | Non-Overlapping Capacity |
|---|---:|---:|---:|
| Development before `2026-05-20T00:00` | 512 | 377 | 89 |
| Holdout from `2026-05-20T00:00` to `2026-06-01T00:00` | 263 | 185 | 43 |

Frozen selection:

```text
Development: 24 windows
Holdout:     12 windows
```

### Holdout interpretation

The pre-strategy screen labels the holdout `regime-shifted`: late-May realized
volatility and jump-count descriptors are lower than development. This is one
correlated context screen, not several independent confirmations.

The protocol now pre-commits to reporting that label alongside the eventual
verdict. A `pass` is encouraging but may reflect easier conditions. A `fail` or
`mixed` result is consistent with an untested mechanism rather than a broken
one. Do not over-update in either direction.

### Implemented V2 path

- Phase A baseline remeasurement at queue credits `{0.0, 1.0}`
- endpoint verdict ladder plus explicit queue-model-dependent headline
- Phase B OFI tri-state conditional-power handling
- Phase C queue-credit and latency stress
- narrow `OFIGatedMM` candidate
- paired per-window matched-net-PnL-per-BTC strategy gate
- locked one-shot holdout gate

### Verification

```text
env PYTHONPATH=. pytest -q tests --ignore=tests/test_recorder.py
215 passed in 9.49s
```

Remote CI remains pending authenticated verification. No green remote run is
claimed.

### Next action

Run Phase A on the frozen 24-window development panel. Keep the candidate
holdout sealed until `notebooks/holdout_protocol.md` is filled, locked, and
committed.

---
## 2026-06-02: CI Bar-Fixture Hardening

### Problem

The GitHub deterministic suite runs on a clean checkout without local
`data/bars`. Module-level `BacktestEngine` construction in
`tests/test_strategies.py` had already been removed, but the bar-loader tests
still read `data/bars` directly and the strategy smoke test skipped when local
bars were absent.

### Fix

- Added a shared deterministic synthetic OHLCV fixture in `tests/conftest.py`.
- Changed `tests/test_data_loader.py` to load and assert against the synthetic
  CSV instead of untracked local market data.
- Changed `tests/test_strategies.py` to run its smoke comparison against the
  fixture, so CI exercises all four original bar strategies instead of
  skipping them.
- Kept the manual `python tests/test_strategies.py` entry point tied to local
  bars for exploratory use.

### Verification

Affected tests run from `/tmp`, outside the repository and without visibility
into local `data/bars`:

```text
5 passed in 0.29s
```

Full deterministic suite from the repository:

```text
env PYTHONPATH=. pytest -q tests --ignore=tests/test_recorder.py
216 passed in 5.14s
```

Full deterministic suite from `/tmp`, without visibility into the repository
`data/` tree:

```text
216 passed in 1.26s
```

---
## 2026-06-09: Phase A 24-Window Baseline Remeasurement

### Question
Does the V1 negative passive-microprice conclusion survive the frozen 24-window
development panel at queue-credit endpoints `{0.0, 1.0}`?

### Hypothesis
Pre-registered. No directional prediction beyond the V1 prior that the passive
baseline shows no stable positive edge. The classification ladder
(`Overturns` / `Strengthens` / `Weakens` / `Confirms`) and the queue-disagreement
rule were fixed in `research_writeup_v2.md` before the run.

### Data window and config
24 manifest-clean non-overlapping 5-hour development windows, `2026-04-12T09` to
`2026-05-09T13`, frozen panel SHA `760c55b7...`. microprice, `half_spread=2.00`,
`requote=5000ms`, `order_qty=0.001`, `max_position=0.01`, `latency=10ms`,
`jitter=0`, `maker_bps=2`, `taker_bps=5`. Queue credits `{0.0, 1.0}`, 120
sessions per endpoint. Deterministic suite `216 passed` immediately before the run.

### Command
```text
env PYTHONPATH=. python scripts/run_l2_panel.py --phase a
```
59 resumable steps, completed exit 0.

### Artifact paths
```text
results/panels/btcusdt_l2_panel_v2/phase_a_verdict.json
results/panels/btcusdt_l2_panel_v2/baseline_ci/{...,_qc0}.json
results/panels/btcusdt_l2_panel_v2/{tail_diagnostics,fee_break_even,microprice_signal,microprice_fill_toxicity,same_ms_audit}/...
```

### Headline result
`Conditional V2: queue-model-dependent result`. Conservative passive-edge
verdict: `Strengthens V1`.

| Endpoint | Mean net PnL | 95% CI | Verdict |
|---|---:|---|---|
| `1.0` proportional | -1.0431 | [-2.3316, -0.0323] | Strengthens V1 |
| `0.0` none | -0.6858 | [-1.8833, +0.2896] | Weakens V1 |

- 18 of 24 windows are net-negative at each endpoint. The worst windows remain
  Apr 13 and Apr 17, the two worst V1 anchors.
- Matched net PnL crosses zero at both endpoints (`-0.3577` [-0.7990, +0.0602]
  proportional; `-0.1074` [-0.4503, +0.2526] none).
- Microprice pooled 1s `beta * signal_std` `+0.0190 bps`, HAC `t +1.74`,
  positive 1s beta in 19 of 24 windows. Fails the `|t| >= 2` and `0.05 bps` bars.
- Fill toxicity broad: worst-5% fill share `40.6%` of total adverse 30s move
  (proportional), comparable to V1's `39.6%`.
- Fee break-even: full strategy needs a maker rebate at both endpoints
  (`1.3515 bps` proportional, `0.7693 bps` none); tail-excluded matched lots flip
  positive at both.

### What changed from previous result
V1 (six windows, proportional) had mean net PnL `-2.4388` with CI
`[-6.7121, +1.2887]` that crossed zero. On 24 windows the proportional CI is
`[-2.3316, -0.0323]`, excluding zero. The point estimate is less negative (the
two extreme V1 windows are diluted by many mildly negative windows) but the CI
tightened enough to establish the negative at the 95% level. The conclusion
strengthened even though the mean rose.

### What could be artifact
- Full-strategy net PnL is endpoint-sensitive (residual inventory marked at
  window close). Matched-lot economics are cleaner and still cross zero, so the
  firmer negative is partly residual-inventory driven.
- The proportional endpoint is the more generous queue assumption; `0.0`
  (no credit) `Weakens` rather than `Strengthens`. The headline is explicitly
  queue-model conditional.
- Development panel only. The holdout stays sealed and is `regime-shifted`; that
  label applies to any future holdout verdict.

### What this proves
The passive microprice-only baseline shows no stable positive edge on the
broader, cleaner, pre-selected panel, and under the realistic queue model the
negative is now statistically reliable (CI excludes zero). The V1 result was not
a six-window small-sample fluke.

### What this does not prove
Nothing about OFI, inventory-aware, or vol-adaptive quoting; nothing about perp
or other venues; nothing about whether an observable filter could avoid the
adverse tail. Microprice retains directional 1s consistency (19/24) that is not
economically usable on its own.

### Next action
Phase B OFI diagnostics as a premise test (unconditional drift plus
conditional-on-fill toxicity), gated by the pre-registered support criteria. No
strategy or holdout work until OFI clears its gate. Then Phase C queue and
latency stress.

---
## 2026-06-14: Phase B OFI Diagnostics (Premise Test)

### Question
Does order-flow imbalance (OFI) give the passive microprice maker a usable edge?
Two pre-registered sub-questions: unconditional (does 1s OFI predict forward mid
drift?) and conditional-on-fill (given a passive fill, does prior OFI separate
toxic from benign fills?).

### Hypothesis
Pre-registered support gate, fixed in `research_writeup_v2.md` and
`current_stage_brief.md` before the run: same-sign 1s beta in at least 75 percent
of development windows, pooled HAC `|t| >= 2`, pooled `|beta * signal_std| >=
0.05 bps`, and a conditional 30s toxicity result that is not a clear signal
failure. Conditional buckets below 30 samples are `inconclusive_power`, not
failure. No directional prediction beyond the V1 / Phase A prior.

### Data window and config
Same frozen 24-window development panel as Phase A (`2026-04-12T09` to
`2026-05-09T13`, panel SHA `760c55b7...`). microprice, `half_spread=2.00`,
`requote=5000ms`, `order_qty=0.001`, `max_position=0.01`, `latency=10ms`,
`jitter=0`, `maker_bps=2`, `taker_bps=5`. Queue credits `{0.0, 1.0}`. OFI sampled
every 1s over the prior 1s interval; forward drift at 1s/10s/1m/5m; conditional
toxicity at 30s. Deterministic suite `223 passed` immediately before the run.

### Leakage hardening (done before the locked re-run)
The conditional fill-toxicity computation was tightened to strictly-pre-fill
samples: the reference book and the OFI window now use the most recent sample
STRICTLY before the fill (`bisect_left`, `end_inclusive=False`), so a book sample
stamped at the fill millisecond (which can encode the fill-causing move) cannot
leak into the OFI or the reference mid. A leakage tripwire was added to
`tests/test_ofi_signal.py`: a same-ms move guard, plus a random-walk / shuffle
test that requires `|t| ~ 0` when the OFI-to-drift pairing is destroyed (observed
clean `t=+1.0`, shuffled `t=-0.2`, versus the `|t| >> 10` a window-overlap bug
would produce). The unconditional path was unchanged, so its cached samples
remain valid.

### Command
```text
env PYTHONPATH=. python scripts/run_l2_panel.py --phase b
```
Re-run after the hardening with the `ofi_signal` step statuses invalidated to
force recomputation. Unconditional samples were reused from the cache;
conditional fill toxicity was re-replayed over 120 sessions per credit.

### Artifact paths
```text
results/panels/btcusdt_l2_panel_v2/ofi_signal/btcusdt_microprice_ofi_20260412_09_to_20260509_13_24blocks_1000ms{,_qc0}/
  summary.json, regressions.csv, signal_buckets.csv, fill_toxicity_rows.csv, fill_buckets.csv
```

### Headline result
Verdict: `blocked`. The unconditional OFI signal passes every gate strongly, but
the conditional-on-fill test fails. Its predictive content did not survive
conditioning on this maker's baseline passive fills.

Unconditional, pooled, queue-independent (`qc0 == qc1`):

| Horizon | n | beta | HAC t | R2 | beta * signal_std |
|---|---:|---:|---:|---:|---:|
| 1s | 430,278 | +0.1192 | +64.63 | 0.0366 | +0.1233 bps |
| 10s | 430,062 | +0.2424 | +35.83 | 0.0128 | +0.2508 bps |
| 1m | 428,862 | +0.3157 | +18.06 | 0.0032 | +0.3268 bps |
| 5m | 423,102 | +0.2958 | +7.48 | 0.0006 | +0.3069 bps |

- Per-window 1s: 24 of 24 windows have positive beta (100 percent same-sign),
  `|t|` from 4.48 to 26.90 (every window individually significant), beta from
  0.0646 to 0.1431.
- 1s bucket dose-response is clean and monotone: average forward drift rises from
  `-0.276 bps` (OFI `< -1.0`) through zero to `+0.284 bps` (OFI `>= 1.0`).
- Unconditional gate: same-sign 100 percent (bar 75), `|t| 64.63` (bar 2),
  effect `0.1233 bps` (bar 0.05). All pass.

Conditional-on-fill, 30s side-aligned separation (favorable minus adverse largest
bucket):

| Queue credit | Separation | Min bucket n | Status |
|---|---:|---:|---|
| 1.0 proportional | +0.1272 bps | 201 | fail_signal |
| 0.0 none | -0.3756 bps | 154 | fail_signal |

Both are far below the `1.0 bps` separation bar, sign-inconsistent across queue
models, and well powered (`n >= 30`). Conditional status `fail_signal`, overall
verdict `blocked`.

### Interpretation (centerpiece)
OFI is a real, strong, monotone predictor of forward mid drift at the population
level (1s HAC `t=64.63`, 24 of 24 windows same-sign, clean bucket dose-response).
But conditional on receiving a passive fill, prior OFI does not separate toxic
from benign fills: favorable-OFI fills move about as adversely as adverse-OFI
fills (`+0.13 bps` proportional, `-0.38 bps` none). The fills a passive maker
receives are the adversely-selected subsample, so a population-strong signal is
not usable by the maker. Adverse selection stated precisely: signal existence
does not imply harvestable edge under passive execution.

### What changed from the pre-hardening run
The strictly-pre-fill hardening moved the conditional separation only marginally
(`qc1 +0.1264 -> +0.1272 bps`; `qc0 -0.3788 -> -0.3756 bps`) and changed no
status. Same-ms leakage was therefore empirically negligible in this dense
top-of-book data, so the conditional-fail result was never a same-ms artifact.
The hardening makes the claim robust without altering it.

### What could be artifact
- Conditional toxicity still depends on the queue/fill model that produced the
  fills. The unconditional result is queue-independent (`qc0 == qc1`) and clean
  (OFI over `[t-1s, t]`, forward over `[t, t+h]`, no overlap), so it does not rely
  on the simulator's fill assumptions.
- Development panel only. The holdout stays sealed and `regime-shifted`.

### What this proves
The pre-registered OFI premise test is decided: unconditional support is strong
but the conditional-on-fill gate fails, so OFI is `blocked` as a passive maker
edge. `OFIGatedMM` does not advance to a development gate.

### What this does not prove
Nothing about whether OFI is usable by a faster or taker-capable participant,
nothing about inventory-aware or vol-adaptive quoting, nothing about perp or
other venues. The unconditional signal is genuinely informative, but the
conditional result failed the frozen premise gate. That blocked `OFIGatedMM`
from advancing; it did not establish how the unrun candidate's altered fill set
would perform.

### Next action
Phase C queue-credit and latency stress (credits `{0,0.25,0.5,0.75,1.0}` by
latency `{0,10,50}ms`) for model-risk closure. `OFIGatedMM` does not advance (gate
blocked); the holdout stays sealed; tail-aware is not an automatic fallback.

### Session note (infrastructure)
Long-lead perp recording started this session. `src/recorder/simple_recorder.py`
and `trade_recorder.py` gained `--market {spot,perp}`, writing to
`data/raw/btcusdt_perp/` and `data/raw/btcusdt_perp_trades/` (spot paths and
defaults unchanged). This environment's USD-M futures feed does not populate
`@aggTrade`, so perp trades use the raw `@trade` stream (more granular than spot's
aggTrade); depth uses `@depth@100ms` with `U/u/pu` futures bridging fields
captured raw. Recording and reconnect only; no perp analysis until the spot arc
completes.

---
## 2026-06-14: Phase C Queue-Credit and Latency Stress (Model-Risk Closure)

### Question
Is the negative passive-baseline conclusion robust across the
queue-cancellation-credit and latency assumptions, or is it an artifact of the
proportional (credit `1.0`) queue model or the `10ms` latency used in Phase A?

### Data window and config
Frozen 24-window development panel. microprice, `half_spread=2.00`,
`requote=5000ms`, `maker_bps=2`, `taker_bps=5`. Grid: queue credits
`{0.0, 0.25, 0.5, 0.75, 1.0}` at latency `10ms`, plus endpoint credits
`{0.0, 1.0}` at latencies `{0, 50}ms`. The latency-10 endpoints reuse the Phase A
reconciliation runs. 168 new window replays, all completed (168/168).

### Command
```text
env PYTHONPATH=. python scripts/sweep_queue_credit.py
env PYTHONPATH=. python scripts/summarize_queue_credit_sweep.py \
  --runs-csv results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/queue_credit_sweep_runs.csv \
  --output-root results/panels/btcusdt_l2_panel_v2/queue_credit_sweep/summary
```
One fix to `scripts/summarize_queue_credit_sweep.py`: the latency-10 endpoints
share the Phase A reconciliation root, which holds both credits. The summarizer
now selects only the matching-credit summaries from a shared root (instead of
raising on the other credit) and adds a post-load count check.

### Result (pooled, 24 windows)

| Credit | Latency | Fills | Orders/fill | Matched net/BTC | Matched net | Full net | Matched break-even fee |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.0 | 0/10/50 | 1595 | 70.8 | -9.75 | -2.58 | -16.46 | +1.35 |
| 0.25 | 10 | 1796 | 62.9 | -15.08 | -4.38 | -17.64 | +1.00 |
| 0.5 | 10 | 1802 | 62.6 | -16.08 | -4.97 | -20.88 | +0.93 |
| 0.75 | 10 | 1831 | 61.6 | -17.51 | -5.69 | -21.33 | +0.84 |
| 1.0 | 0/10/50 | 2041 | 55.4 | -23.65 | -8.59 | -25.03 | +0.43 |

### Findings
- Latency-invariant. For each credit, latencies `{0, 10, 50}ms` give identical
  matched and full-strategy PnL (only orders-per-fill changes marginally). At a
  `2.00` half-spread, a 0 to 50ms latency difference does not change the fill set.
- Monotonic in queue credit. As cancellation credit rises `0.0 -> 1.0`, fills
  rise (1595 -> 2041), orders-per-fill falls (70.8 -> 55.4), and both matched and
  full-strategy PnL worsen (matched per BTC `-9.75 -> -23.65`; full net `-16.46
  -> -25.03`). More credit yields more, more-toxic fills.
- The matched-lot sign flip does not persist. On the 24-window panel matched net
  is negative across the entire grid, most favorable at credit `0.0` (pooled
  `-2.58`, i.e. `-0.107` per window, the near-flat CI-crossing-zero Phase A
  no-credit value) and most negative at credit `1.0` (`-8.59` pooled, `-0.358`
  per window, exactly the Phase A proportional value). The V1 six-anchor
  "positive matched under no credit" does not generalize to the broader panel.

### Interpretation
The negative passive-baseline conclusion is robust to the two main model-risk
levers. Queue-cancellation credit changes the magnitude of the loss but never the
sign; latency in `[0, 50]ms` is immaterial at this spread. The proportional
(credit `1.0`) assumption that Phase A headlined is the least favorable endpoint,
so the Phase A proportional negative is conservative; the no-credit endpoint is
the most favorable but still negative and near-flat. This closes the model-risk
question for the passive microprice baseline.

### What this proves / does not prove
Proves: the passive baseline's negative result does not depend on the queue model
or on a particular sub-50ms latency. Does not prove anything about other spreads,
strategies, signals, or venues.

### Status of the research arc
Phases A, B, and C are complete. The passive microprice baseline shows no stable
edge (`Strengthens V1`, robust across queue credit and latency); OFI is a strong
population signal but `blocked` for a passive maker by adverse selection on fills.
`OFIGatedMM` does not advance; the holdout stays sealed; tail-aware is not an
automatic fallback. The disciplined outcome is to publish the expanded negative.

---
## 2026-06-16: Phase 2 Closure And Publication Hygiene

### Question
Are the public-facing notes and reproducibility hooks aligned with the completed
Phase A/B/C development arc before moving to the C++ port?

### Actions
- Added `notebooks/phase2_artifact_index.md` as the compact map of canonical
  writeups, frozen hashes, Phase A/B/C artifacts, holdout status, and perp
  capture QA.
- Added `scripts/verify_v2_artifacts.py` as a lightweight guard for frozen panel
  provenance, Phase A/B/C verdict shape, and the sealed-holdout boundary.
- Updated the README and notebooks so Phase C, the same-ms V2 audit, and
  publication checks are recorded as complete.
- Kept `notebooks/research_writeup.md` as the frozen V1 reference and kept
  `notebooks/holdout_protocol.md` unlocked because no candidate advanced.

### Verification
The artifact verifier passes:

```text
OK: panel selection
OK: Phase A verdict
OK: Phase B OFI gate
OK: Phase C queue stress
OK: same-ms audit
OK: sealed holdout
OK: V2 Phase 2 artifacts verified
```

The deterministic suite remains green:

```text
223 passed in 5.37s
```

`tests/test_recorder.py` remains excluded because it is a live
network/environment test.

### Interpretation
Phase 2 is closed locally in both the research and reproducibility sense. The
scientific result is still the expanded negative / conditional result: no passive
candidate advances, OFI is blocked by conditional-on-fill adverse selection, and
the holdout remains sealed.

### Next action
Publish the cleaned branch, then begin Phase 3: C++17 hot-path parity against
the Python reference. Benchmark claims wait until bit-identical state-hash parity
passes.

---
## 2026-07-02: Public Claim Scope Hardening

### Question
Does the public wording distinguish the observed conditional-fill result from
the unrun `OFIGatedMM` counterfactual?

### Change
- Retitled the public note to index the claim to this passive maker.
- Replaced categorical harvestability wording with the tested result: OFI's
  predictive content did not survive conditioning on the baseline passive
  fills.
- Stated the decision rule explicitly: the failed premise gate meant the
  candidate was not justified to advance.
- Stated the counterfactual boundary explicitly: suppressing one quote side
  would change the fill set, so the result does not prove the unrun candidate
  would lose.
- Added a compact related-work section covering adverse selection, OFI price
  impact, and inventory-aware market making.

### Research status
No data, code path, artifact, gate, or verdict changed. This is a claim-scope
correction only. Phase 2 remains complete, no candidate advances, and the
holdout remains untouched.

Verification after the documentation change:

```text
223 passed in 5.40s
```

### Next action
Rebase the local `cpp-orderbook-parity` branch onto current `main`, run the full
suite, and publish that branch. Then establish operation-level parity before
implementing bindings or reporting benchmarks.

---
## 2026-07-14: Phase 2.5 Execution-Model Provenance Boundary

### Question
Can the Python execution reference be corrected without making current results
look interchangeable with the already published V2 artifacts?

### Provenance finding
The committed artifacts under `results/panels/btcusdt_l2_panel_v2` were
generated at research commit `1066950` under
`legacy_book_update_v1`. A modeled order arrival became eligible only on the
next depth update, and a cancellation request took effect immediately. Those
semantics omit intervening-trade eligibility and cancel/fill races. Historical
own-order handling also needed an explicit volume-conservation rule when
delayed cancels leave overlapping orders at one price.

The V2 numerical claims remain the record of the experiment that was actually
run. They are not relabeled as current-engine results, and the frozen root must
not be overwritten. Raw-file integrity, selected development and holdout
windows, book-state hashes, and unconditional book-state signal results are not
invalidated by the timing correction. Fills, PnL, conditional-on-fill studies,
queue diagnostics, and latency conclusions require a new development rerun.

### Phase 2.5 boundary

- Current model identifier: `event_driven_v2`
- Equal-time policy: recorded market data before private actions
- New result namespace:
  `results/panels/btcusdt_l2_panel_v3_event_driven`
- Model and deterministic acceptance contract:
  `notebooks/execution_model_v2.md`
- Research status: Python implementation acceptance is complete locally; no
  V3 execution-derived result or verdict is validated yet
- Holdout status: sealed and unavailable for model development

This stage must finish the event scheduler, delayed-cancel lifecycle,
same-millisecond attribution, own-order FIFO/volume conservation, provenance
guards, and deterministic acceptance cases before any V3 claim is made.

The V3 panel runner must not call the frozen V2/V1 verdict classifier. Its
Phase A endpoint output is a descriptive, provenance-checked execution-model
comparison over the identical development-window set. A mean delta is not a
paired confidence interval and is not an automatic advancement verdict.

### C++ decision
The C++ performance port is intentionally paused until the project owner has a
fundamental understanding of modern C++. When work resumes, the completed
Python event-driven model will be the parity reference; bindings and benchmark
claims still come only after parity. This pause is a sequencing decision, not a
performance result.

---
## 2026-07-14: Event-Driven Python Reference Acceptance

### Implemented boundary

- Exact scheduled order and cancellation arrivals share the replay clock with
  recorded market data.
- The conservative equal-time rule, delayed cancel/fill races, gap-group
  censoring, replay-end expiry, own-order FIFO, and recorded-volume
  conservation are explicit and tested.
- Every execution-derived artifact carries model, timing, gap-policy, latency,
  post-only, and queue-credit provenance.
- Current writers cannot target the frozen V2 result root. The V3 runner is
  pinned to the frozen development panel and verifies the integrity manifest
  plus every selected raw depth/trade hash before resuming work.
- Bootstrap inputs use an exact run/start allowlist with containment checks.
  Resume markers bind commands, source, input contents, the current expected
  output set, and output contents.
- Cross-model reporting is descriptive only and requires the canonical
  five-hour experiment; the historical verdict classifier is not reused.

### Acceptance evidence

```text
369 passed in 5.56s
126 focused simulator/engine/merger/strategy tests passed in 2.26s
all frozen V2 artifact checks passed
```

A real 2026-04-12 09:00 UTC one-hour BTCUSDT smoke replay processed 48,257
events and terminated with eight maker fills, zero taker fills, two explicit
replay-end expirations, and zero pending orders. This is a mechanics smoke test,
not a strategy result.

The final acceptance pass also made `max_position` a hard working-exposure
envelope: pending entries and cancels in flight count against the limit, and a
delayed cancel/replace cannot add a quote whose worst-case fill would breach
it. The official full-panel runner now writes a tracked artifact manifest with
source, panel, raw-input, derivation, and output hashes; a separate V3 verifier
checks that manifest without relying on ignored status files.

### Research status and next action

The Python implementation boundary is accepted locally. No V3 economic result
has been generated, published, or validated, and the holdout remains sealed.
The next action is the isolated 24-window development rerun into
`results/panels/btcusdt_l2_panel_v3_event_driven`, followed by review of the
descriptive execution-model sensitivity report. C++ remains paused until modern
C++ fundamentals are in place.
