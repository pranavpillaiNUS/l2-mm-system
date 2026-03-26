"""
Parameter sweep for SMA crossover strategy.
Test different fast/slow period combos, check if its a fluke.
"""
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from src.backtester.config import BacktestConfig
from src.backtester.engine import BacktestEngine
from src.backtester.strategy import SMACrossoverStrategy

def sweep_sma():
    output_dir = Path("results/sma_sweep")
    output_dir.mkdir(parents=True, exist_ok=True)

    config = BacktestConfig(
        data_path=Path("data/bars"),
        symbol="BTCUSDT",
        initial_cash=100_000,
    )

    engine = BacktestEngine(config)

    fast_values = [6, 12, 24]
    slow_values = [24, 48, 72, 96]

    results = []
    combos = [(f, s) for f in fast_values for s in slow_values if f < s]
    
    for i, (fast, slow) in enumerate(combos, 1):
        print(f"[{i}/{len(combos)}] SMA({fast}/{slow})...", end="")
        strategy = SMACrossoverStrategy(fast_period=fast, slow_period=slow)
        result = engine.run(strategy, verbose=False)
        m = result.metrics
        print(f" Sharpe={m.sharpe_ratio:.2f}  Return={m.total_return_pct:.1f}%  Trades={m.num_trades}")
        
        results.append({
            "fast": fast,
            "slow": slow,
            "label": f"SMA({fast}/{slow})",
            "return_pct": m.total_return_pct,
            "sharpe": m.sharpe_ratio,
            "sortino": m.sortino_ratio,
            "max_dd": m.max_drawdown * 100,
            "win_rate": m.win_rate * 100,
            "trades": m.num_trades,
            "fees": m.total_fees,
        })

    df = pd.DataFrame(results)

    # save CSV
    df.to_csv(output_dir / "sma_sweep.csv", index=False)

    # print results table
    print(f"\n{'Combo':<15} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>10} {'WinRate':>10}")
    print("-" * 65)
    for _, r in df.iterrows():
        print(f"SMA({int(r['fast'])}/{int(r['slow'])}){'':<5} {r['return_pct']:>9.1f}% {r['sharpe']:>10.2f} {r['max_dd']:>9.1f}% {int(r['trades']):>10} {r['win_rate']:>9.1f}%")

    best = df.loc[df["sharpe"].idxmax()]
    print(f"\nBest: SMA({int(best['fast'])}/{int(best['slow'])}) — Sharpe {best['sharpe']:.2f}, Return {best['return_pct']:.1f}%, MaxDD {best['max_dd']:.1f}%")

    # heatmap - pivot fast vs slow
    import seaborn as sns
    pivot = df.pivot_table(index="fast", columns="slow", values="sharpe")
    fig, ax = plt.subplots(figsize=(8, 4))
    sns.heatmap(pivot, annot=True, fmt=".2f", cmap="RdYlGn", center=0, ax=ax)
    ax.set_title("SMA Crossover — Sharpe by fast/slow period")
    ax.set_xlabel("Slow period (hours)")
    ax.set_ylabel("Fast period (hours)")
    plt.tight_layout()
    plt.savefig(output_dir / "sma_sharpe_heatmap.png", dpi=150)
    plt.close()
    print(f"\nSaved heatmap to {output_dir}/sma_sharpe_heatmap.png")

    # equity curves for top 3
    fig, ax = plt.subplots(figsize=(12, 6))
    top3 = df.nlargest(3, "sharpe")
    for _, r in top3.iterrows():
        strategy = SMACrossoverStrategy(fast_period=int(r["fast"]), slow_period=int(r["slow"]))
        result = engine.run(strategy, verbose=False)
        ax.plot(result.equity_series.index, result.equity_series.values,
                label=r["label"], linewidth=1)

    # benchmark
    from src.backtester.data_loader import DataLoader
    loader = DataLoader(Path("data/bars"))
    bars = loader.load("BTCUSDT")
    benchmark = bars["close"] / bars["close"].iloc[0] * 100_000
    ax.plot(benchmark.index, benchmark.values,
            label="Buy & Hold", linewidth=2, linestyle="--", color="black")

    ax.set_title("Top 3 SMA Combos vs Buy & Hold")
    ax.set_ylabel("Equity ($)")
    ax.set_xlabel("Date")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_dir / "sma_equity_curves.png", dpi=150)
    plt.close()
    print(f"Saved equity curves to {output_dir}/sma_equity_curves.png")

if __name__ == "__main__":
    sweep_sma()