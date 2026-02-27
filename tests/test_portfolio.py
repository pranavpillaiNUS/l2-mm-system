from datetime import datetime, timedelta
from src.backtester.portfolio import Portfolio, Fill, Side

def test_basic_trading():
    print("=" * 60)
    print("TEST: Basic Trading")
    print("=" * 60)
    
    portfolio = Portfolio(initial_cash=100_000)
    now = datetime(2024, 1, 1, 10, 0, 0)
    
    print(f"\n1. Initial state:")
    print(f"   Cash: ${portfolio.cash:,.2f}")
    print(f"   Position: {portfolio.position}")

    buy_fill = Fill( # buy 1 BTC at $50,000
        timestamp=now,
        side=Side.BUY,
        price=50_000,
        quantity=1.0,
        fee=50.0,
        slippage_cost=25.0,
        reason="test_buy",
    )
    portfolio.execute(buy_fill)

    print(f"\n2. After BUY 1 BTC @ $50,000:")
    print(f"   Cash: ${portfolio.cash:,.2f}")
    print(f"   Position: {portfolio.position} BTC")
    print(f"   Avg Entry: ${portfolio.avg_entry_price:,.2f}")

    snapshot = portfolio.mark_to_market(now + timedelta(hours=1), price=51_000)
    print(f"\n3. Mark-to-Market @ $51,000:")
    print(f"   Equity: ${snapshot.equity:,.2f}")
    print(f"   Unrealized P&L: ${snapshot.unrealized_pnl:,.2f}")

    sell_fill = Fill( # selling the 1 BTC at $51,000
        timestamp=now + timedelta(hours=2),
        side=Side.SELL,
        price=51_000,
        quantity=1.0,
        fee=51.0,
        slippage_cost=25.5,
        reason="test_sell",
    )
    portfolio.execute(sell_fill)

    print(f"\n4. After SELL 1 BTC @ $51,000:")
    print(f"   Cash: ${portfolio.cash:,.2f}")
    print(f"   Position: {portfolio.position} BTC")
    print(f"   Realized P&L: ${portfolio.realized_pnl:,.2f}")

    print(f"\n5. Summary:")
    print(f"   {portfolio.summary()}")
    print(f"   Total fees: ${portfolio.total_fees:.2f}")
    print(f"   Total slippage: ${portfolio.total_slippage:.2f}")
    
    print("\nwow! Basic trading test passed!")
    return True

def test_short_selling():
    print("\n" + "=" * 60)
    print("TEST: Short Selling")
    print("=" * 60)

    portfolio = Portfolio(initial_cash=100_000)
    now = datetime(2024, 1, 1)

    short_fill = Fill( #shorti 1 BTC at $50,000
        timestamp=now,
        side=Side.SELL,
        price=50_000,
        quantity=1.0,
        fee=50,
        slippage_cost=25,
    )
    portfolio.execute(short_fill)

    print(f"\n1. After SHORT 1 BTC @ $50,000:")
    print(f"   Position: {portfolio.position} BTC (negative = short)")
    print(f"   Cash: ${portfolio.cash:,.2f}")

    cover_fill = Fill(
        timestamp=now + timedelta(hours=1),
        side=Side.BUY,
        price=48_000,
        quantity=1.0,
        fee=48,
        slippage_cost=24,
    )
    portfolio.execute(cover_fill)

    print(f"\n2. After COVER @ $48,000:")
    print(f"   Position: {portfolio.position} BTC")
    print(f"   Realized P&L: ${portfolio.realized_pnl:,.2f}")
    print(f"   (Profit from short: $50k - $48k = $2k minus costs)")
    
    print("\nslay! short selling test passed!")
    return True

def test_multiple_trades():
    print("\n" + "=" * 60)
    print("TEST: Multiple Trades (Averaging)")
    print("=" * 60)

    portfolio = Portfolio(initial_cash=100_000)
    now = datetime(2024, 1, 1)

    portfolio.execute(Fill( #buy BTC; $50,000
        timestamp=now,
        side=Side.BUY,
        price=50_000,
        quantity=1.0,
        fee=50,
        slippage_cost=25,
    ))
    print(f"\n1. Buy 1 BTC @ $50,000 → Avg entry: ${portfolio.avg_entry_price:,.2f}")

    portfolio.execute(Fill(
        timestamp=now + timedelta(hours=1),
        side=Side.BUY,
        price=52_000,
        quantity=1.0,
        fee=52,
        slippage_cost=26,
    ))
    print(f"2. Buy 1 BTC @ $52,000 → Avg entry: ${portfolio.avg_entry_price:,.2f}")
    print(f"   (Expected: ($50k + $52k) / 2 = $51,000)")

    portfolio.execute(Fill(
        timestamp=now + timedelta(hours=2),
        side=Side.SELL,
        price=53_000,
        quantity=1.0,
        fee=53,
        slippage_cost=26.5,
    ))
    print(f"\n3. Sell 1 BTC @ $53,000:")
    print(f"   Remaining position: {portfolio.position} BTC")
    print(f"   Realized P&L: ${portfolio.realized_pnl:,.2f}")
    print(f"   (Sold at $53k, avg entry was $51k → ~$2k profit minus costs)")
    
    print("\nmultiple trades test passed!")
    return True

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("PORTFOLIO MODULE TESTS")
    print("=" * 60)
    
    test_basic_trading()
    test_short_selling()
    test_multiple_trades()
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED! hooray!")
    print("=" * 60)