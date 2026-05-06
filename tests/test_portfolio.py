from datetime import datetime, timedelta
from src.backtester.portfolio import Portfolio, Fill, Side


def test_basic_trading():
    portfolio = Portfolio(initial_cash=100_000)
    now = datetime(2024, 1, 1, 10, 0, 0)

    buy_fill = Fill(
        timestamp=now,
        side=Side.BUY,
        price=50_000,
        quantity=1.0,
        fee=50.0,
        slippage_cost=25.0,
        reason="test_buy",
    )
    portfolio.execute(buy_fill)

    assert portfolio.cash == 49_925.0
    assert portfolio.position == 1.0
    assert portfolio.avg_entry_price == 50_000

    snapshot = portfolio.mark_to_market(now + timedelta(hours=1), price=51_000)
    assert snapshot.equity == 100_925.0
    assert snapshot.unrealized_pnl == 1_000.0

    sell_fill = Fill(
        timestamp=now + timedelta(hours=2),
        side=Side.SELL,
        price=51_000,
        quantity=1.0,
        fee=51.0,
        slippage_cost=25.5,
        reason="test_sell",
    )
    portfolio.execute(sell_fill)

    assert portfolio.cash == 100_848.5
    assert portfolio.position == 0
    assert portfolio.realized_pnl == 1_000.0
    assert portfolio.total_fees == 101.0
    assert portfolio.total_slippage == 50.5


def test_short_selling():
    portfolio = Portfolio(initial_cash=100_000)
    now = datetime(2024, 1, 1)

    short_fill = Fill(
        timestamp=now,
        side=Side.SELL,
        price=50_000,
        quantity=1.0,
        fee=50,
        slippage_cost=25,
    )
    portfolio.execute(short_fill)

    assert portfolio.position == -1.0
    assert portfolio.cash == 149_925.0
    assert portfolio.avg_entry_price == 50_000

    cover_fill = Fill(
        timestamp=now + timedelta(hours=1),
        side=Side.BUY,
        price=48_000,
        quantity=1.0,
        fee=48,
        slippage_cost=24,
    )
    portfolio.execute(cover_fill)

    assert portfolio.position == 0
    assert portfolio.cash == 101_853.0
    assert portfolio.realized_pnl == 2_000.0


def test_multiple_trades():
    portfolio = Portfolio(initial_cash=100_000)
    now = datetime(2024, 1, 1)

    portfolio.execute(Fill(
        timestamp=now,
        side=Side.BUY,
        price=50_000,
        quantity=1.0,
        fee=50,
        slippage_cost=25,
    ))
    assert portfolio.position == 1.0
    assert portfolio.avg_entry_price == 50_000

    portfolio.execute(Fill(
        timestamp=now + timedelta(hours=1),
        side=Side.BUY,
        price=52_000,
        quantity=1.0,
        fee=52,
        slippage_cost=26,
    ))
    assert portfolio.position == 2.0
    assert portfolio.avg_entry_price == 51_000

    portfolio.execute(Fill(
        timestamp=now + timedelta(hours=2),
        side=Side.SELL,
        price=53_000,
        quantity=1.0,
        fee=53,
        slippage_cost=26.5,
    ))
    assert portfolio.position == 1.0
    assert portfolio.avg_entry_price == 51_000
    assert portfolio.realized_pnl == 2_000.0


if __name__ == "__main__":
    print("\nPORTFOLIO MODULE TESTS")
    
    test_basic_trading()
    test_short_selling()
    test_multiple_trades()
    
    print("\nAll portfolio tests passed.")
