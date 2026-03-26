"""
Parameter sweep for Bollinger band strategy.
"""
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import BollingerBandStrategy

output_dir = Path("results/bollinger_sweep")
output_dir.mkdir(parents=True, exist_ok=True)

config = BacktestConfig(data_path=Path("data/bars"), symbol="BTCUSDT", initial_cash=100_000)
engine = BacktestEngine(config)

period_values = [12, 24, 48, 72]
std_values = [1.0, 1.5, 2.0, 2.5, 3.0]
combos = [(p, s) for p in period_values for s in std_values]

results = []
for i, (period, num_std) in enumerate(combos, 1):
    strategy = BollingerBandStrategy(period=period, num_std=num_std)
    result = engine.run(strategy, verbose=False)
    m = result.metrics
    print(f"[{i}/{len(combos)}] BB({period}, {num_std}σ) sharpe={m.sharpe_ratio:.2f} return={m.total_return_pct:.1f}% trades={m.num_trades}")

    results.append({
        "period": period,
        "num_std": num_std,
        "label": f"BB({period}, {num_std}σ)",
        "return_pct": m.total_return_pct,
        "sharpe": m.sharpe_ratio,
        "sortino": m.sortino_ratio,
        "max_dd": m.max_drawdown * 100,
        "win_rate": m.win_rate * 100,
        "trades": m.num_trades,
        "fees": m.total_fees,
    })

df = pd.DataFrame(results)
df.to_csv(output_dir / "bollinger_sweep.csv", index=False)

# print table
print(f"\n{'Combo':<20} {'Return':>8} {'Sharpe':>8} {'MaxDD':>8} {'Trades':>8} {'WinRate':>8}")
for _, r in df.iterrows():
    print(f"BB({int(r['period'])}, {r['num_std']}σ){'':>7} {r['return_pct']:>7.1f}% {r['sharpe']:>8.2f} {r['max_dd']:>7.1f}% {int(r['trades']):>8} {r['win_rate']:>7.1f}%")

best = df.loc[df["sharpe"].idxmax()]
print(f"\nbest: BB({int(best['period'])}, {best['num_std']}σ) — sharpe {best['sharpe']:.2f}, ret {best['return_pct']:.1f}%, maxdd {best['max_dd']:.1f}%")

# heatmap
pivot = df.pivot_table(index="period", columns="num_std", values="sharpe")
fig, ax = plt.subplots(figsize=(8, 4))
sns.heatmap(pivot, annot=True, fmt=".2f", cmap="RdYlGn", center=0, ax=ax)
ax.set_title("Bollinger Bands — Sharpe by period/num_std")
ax.set_xlabel("Num std deviations")
ax.set_ylabel("Period (hours)")
plt.tight_layout()
plt.savefig(output_dir / "bollinger_sharpe_heatmap.png", dpi=150)
plt.close()
print(f"\nsaved heatmap to {output_dir}/bollinger_sharpe_heatmap.png")

# equity curves for top 3
fig, ax = plt.subplots(figsize=(12, 6))
top3 = df.nlargest(3, "sharpe")
for _, r in top3.iterrows():
    strategy = BollingerBandStrategy(period=int(r["period"]), num_std=r["num_std"])
    result = engine.run(strategy, verbose=False)
    ax.plot(result.equity_series.index, result.equity_series.values, label=r["label"], linewidth=1)

from src.backtester.data_loader import DataLoader
loader = DataLoader(Path("data/bars"))
bars = loader.load("BTCUSDT")
benchmark = bars["close"] / bars["close"].iloc[0] * 100_000
ax.plot(benchmark.index, benchmark.values, label="Buy & Hold", linewidth=2, linestyle="--", color="black")
ax.set_title("Top 3 Bollinger Combos vs Buy & Hold")
ax.set_ylabel("Equity ($)")
ax.set_xlabel("Date")
ax.legend()
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(output_dir / "bollinger_equity_curves.png", dpi=150)
plt.close()
print(f"saved equity curves to {output_dir}/bollinger_equity_curves.png")