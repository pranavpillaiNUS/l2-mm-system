import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

plt.style.use('seaborn-v0_8-whitegrid')


def plot_backtest_results(
    equity_series: pd.Series,
    fills_df: Optional[pd.DataFrame] = None,
    benchmark: Optional[pd.Series] = None,
    title: str = "Backtest Results",
    save_path: Optional[Path] = None,
) -> None:

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[3, 1], sharex=True)
    
    # === Equity Curve ===
    ax1 = axes[0]
    ax1.plot(equity_series.index, equity_series.values, 
             label="Strategy", linewidth=1.5, color='#2E86AB')
    
    if benchmark is not None:
        norm_bench = benchmark / benchmark.iloc[0] * equity_series.iloc[0]
        ax1.plot(benchmark.index, norm_bench.values,
                 label="Benchmark", linewidth=1, color='#A23B72', 
                 alpha=0.7, linestyle='--')
    
    # Mark trades
    if fills_df is not None and not fills_df.empty:
        buys = fills_df[fills_df['side'] == 'buy']
        sells = fills_df[fills_df['side'] == 'sell']
        
        for _, trade in buys.iterrows():
            ax1.axvline(trade['timestamp'], color='green', alpha=0.3, linewidth=0.5)
        for _, trade in sells.iterrows():
            ax1.axvline(trade['timestamp'], color='red', alpha=0.3, linewidth=0.5)
    
    ax1.set_ylabel("Equity ($)")
    ax1.set_title(title, fontsize=14, fontweight='bold')
    ax1.legend(loc='upper left')
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    
    ax2 = axes[1]
    cummax = equity_series.cummax()
    drawdown = (equity_series - cummax) / cummax * 100
    
    ax2.fill_between(drawdown.index, drawdown.values, 0, alpha=0.5, color='#E94F37')
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    
    plt.tight_layout()
    
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Saved plot to {save_path}")
        plt.close()
    else:
        plt.show()


def plot_returns_distribution(
    equity_series: pd.Series,
    title: str = "Returns Distribution",
    save_path: Optional[Path] = None,
) -> None:
    returns = equity_series.pct_change().dropna() * 100
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    ax.hist(returns, bins=50, edgecolor='black', alpha=0.7, color='#2E86AB')
    ax.axvline(returns.mean(), color='red', linestyle='--', 
               label=f'Mean: {returns.mean():.2f}%')
    ax.axvline(0, color='black', linewidth=0.5)
    
    ax.set_xlabel("Daily Return (%)")
    ax.set_ylabel("Frequency")
    ax.set_title(title)
    ax.legend()
    
    plt.tight_layout()
    
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Saved plot to {save_path}")
        plt.close()
    else:
        plt.show()


def plot_metrics_table(
    metrics_dict: dict,
    title: str = "Performance Metrics",
    save_path: Optional[Path] = None,
) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.axis('off')
    
    cell_text = [[k, v] for k, v in metrics_dict.items()]
    
    table = ax.table(
        cellText=cell_text,
        colLabels=['Metric', 'Value'],
        loc='center',
        cellLoc='left',
        colWidths=[0.6, 0.3],
    )
    
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.5)
    
    for j in range(2):
        table[(0, j)].set_facecolor('#2E86AB')
        table[(0, j)].set_text_props(color='white', fontweight='bold')
    
    # Alternate row colors
    for i in range(1, len(cell_text) + 1):
        for j in range(2):
            if i % 2 == 0:
                table[(i, j)].set_facecolor('#f0f0f0')
    
    plt.title(title, fontsize=14, fontweight='bold', pad=20)
    plt.tight_layout()
    
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Saved plot to {save_path}")
        plt.close()
    else:
        plt.show()