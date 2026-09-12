"""
Tests for fill-rate analysis.

Run with: python tests/test_fill_rate.py
"""
from decimal import Decimal

from src.analysis.fill_rate import (
    compute_order_contexts,
    fill_rate_by_distance_bps,
    fill_rate_by_quote_age_ms,
    fill_rate_by_volatility_bps,
    format_fill_rate_summary,
    summarize_fill_rate,
    summarize_pooled_contexts,
)
from src.analysis.markout import Markout
from src.execution.order import Fill, OrderEvent, OrderSide
from src.replay.engine import BookSample, ReplayResult, ReplayStats


def make_sample(t_ms, mid):
    mid = Decimal(mid)
    return BookSample(
        timestamp_ms=t_ms,
        best_bid=mid - Decimal("0.50"),
        best_ask=mid + Decimal("0.50"),
        mid=mid,
        microprice=mid,
        spread=Decimal("1.00"),
    )


def make_placed(order_id, t_ms, side, price, qty="0.001"):
    return OrderEvent(
        order_id=order_id,
        timestamp_ms=t_ms,
        event_type="placed",
        detail={
            "side": side,
            "type": "limit",
            "price": str(price),
            "qty": qty,
            "arrival_ms": t_ms + 10,
        },
    )


def make_event(order_id, t_ms, event_type, detail=None):
    return OrderEvent(
        order_id=order_id,
        timestamp_ms=t_ms,
        event_type=event_type,
        detail=detail or {},
    )


def make_fill(fill_id, order_id, t_ms, side, price, qty="0.001"):
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        side=side,
        price=Decimal(price),
        quantity=Decimal(qty),
        is_maker=True,
        timestamp_ms=t_ms,
        fee=Decimal("0"),
    )


def make_markout(fill_id, order_id, side, fill_price, markout_bps, horizon="30s"):
    return Markout(
        fill_id=fill_id,
        order_id=order_id,
        side=side,
        fill_timestamp_ms=0,
        fill_price=Decimal(fill_price),
        quantity=Decimal("0.001"),
        horizon=horizon,
        horizon_ms=30_000,
        sample_timestamp_ms=0,
        future_mid=Decimal(fill_price),
        markout=Decimal("0"),
        markout_bps=Decimal(str(markout_bps)),
    )


def make_result(fills, events, samples):
    return ReplayResult(
        fills=fills,
        events=events,
        stats=ReplayStats(),
        checkpoints=[],
        book_samples=samples,
    )


def test_filled_order_has_correct_context():
    # Bid placed at 99.95 when mid is 100.00 -> 5 bps below mid
    samples = [make_sample(t, "100.00") for t in range(0, 60_000, 1000)]
    events = [
        make_placed("o1", 30_000, "buy", "99.95"),
        make_event("o1", 30_010, "queued", {"price": "99.95", "queue_ahead": "0"}),
        make_event("o1", 31_000, "filled", {}),
    ]
    fills = [make_fill("f1", "o1", 31_000, OrderSide.BUY, "99.95")]
    markouts = [make_markout("f1", "o1", OrderSide.BUY, "99.95", -2.5)]

    contexts = compute_order_contexts(make_result(fills, events, samples), markouts)

    assert len(contexts) == 1
    ctx = contexts[0]
    assert ctx.final_status == "filled"
    assert ctx.filled is True
    assert ctx.distance_from_mid_bps == Decimal("5.0000")
    assert ctx.quote_age_ms == 990
    assert ctx.fill_markout_bps == Decimal("-2.5")
    print("PASS: filled order has correct context")


def test_post_only_rejected_classified():
    samples = [make_sample(0, "100.00"), make_sample(1000, "100.00")]
    events = [
        make_placed("o1", 100, "buy", "100.50"),  # crossing
        make_event("o1", 110, "cancelled", {"reason": "post_only_would_cross"}),
    ]

    contexts = compute_order_contexts(make_result([], events, samples), [])

    assert len(contexts) == 1
    assert contexts[0].final_status == "post_only_rejected"
    assert contexts[0].filled is False
    assert contexts[0].quote_age_ms is None  # never queued
    print("PASS: post-only rejected classified correctly")


def test_cancelled_unfilled_classified():
    samples = [make_sample(t, "100.00") for t in range(0, 5000, 500)]
    events = [
        make_placed("o1", 1000, "sell", "100.10"),
        make_event("o1", 1010, "queued", {"price": "100.10", "queue_ahead": "0"}),
        make_event("o1", 3000, "cancelled", {}),
    ]

    contexts = compute_order_contexts(make_result([], events, samples), [])

    assert contexts[0].final_status == "cancelled_unfilled"
    assert contexts[0].quote_age_ms == 1990
    assert contexts[0].fill_markout_bps is None
    print("PASS: cancelled-unfilled classified correctly")


def test_partial_then_cancelled_classified():
    samples = [make_sample(t, "100.00") for t in range(0, 5000, 500)]
    events = [
        make_placed("o1", 1000, "buy", "99.90", qty="0.002"),
        make_event("o1", 1010, "queued", {}),
        make_event("o1", 1500, "partial_fill", {}),
        make_event("o1", 2000, "cancelled", {}),
    ]
    fills = [make_fill("f1", "o1", 1500, OrderSide.BUY, "99.90", qty="0.001")]
    markouts = [make_markout("f1", "o1", OrderSide.BUY, "99.90", 1.0)]

    contexts = compute_order_contexts(make_result(fills, events, samples), markouts)

    assert contexts[0].final_status == "partial_then_cancelled"
    assert contexts[0].filled is True   # we got *some* fill
    assert contexts[0].fill_markout_bps == Decimal("1.0")
    print("PASS: partial-then-cancelled classified correctly")


def test_distance_binning_and_markout_conditional():
    # Three orders at distances 1, 6, 6 bps. The 1-bps fills with bad markout,
    # the 6-bps fills with good markout, and one 6-bps doesn't fill.
    samples = [make_sample(t, "100.00") for t in range(0, 10_000, 200)]
    events = [
        make_placed("o1", 1000, "buy", "99.99"),  # 1 bps
        make_event("o1", 1010, "queued", {}),
        make_event("o1", 1500, "filled", {}),
        make_placed("o2", 2000, "buy", "99.94"),  # 6 bps
        make_event("o2", 2010, "queued", {}),
        make_event("o2", 2500, "filled", {}),
        make_placed("o3", 3000, "buy", "99.94"),  # 6 bps
        make_event("o3", 3010, "queued", {}),
        make_event("o3", 4000, "cancelled", {}),
    ]
    fills = [
        make_fill("f1", "o1", 1500, OrderSide.BUY, "99.99"),
        make_fill("f2", "o2", 2500, OrderSide.BUY, "99.94"),
    ]
    markouts = [
        make_markout("f1", "o1", OrderSide.BUY, "99.99", -3.0),
        make_markout("f2", "o2", OrderSide.BUY, "99.94", 0.5),
    ]

    contexts = compute_order_contexts(make_result(fills, events, samples), markouts)
    bins = fill_rate_by_distance_bps(contexts, edges_bps=[0, 5, 10])

    # Bin 0 = [0,5) bps: o1 only, filled
    assert bins[0].n_orders == 1
    assert bins[0].n_filled == 1
    assert bins[0].fill_rate == 1.0
    assert bins[0].avg_markout_bps_given_filled == Decimal("-3.0")

    # Bin 1 = [5,10) bps: o2 + o3, one fills
    assert bins[1].n_orders == 2
    assert bins[1].n_filled == 1
    assert bins[1].fill_rate == 0.5
    assert bins[1].avg_markout_bps_given_filled == Decimal("0.5")

    print("PASS: distance binning + markout-given-fill works")


def test_quote_age_binning():
    samples = [make_sample(t, "100.00") for t in range(0, 60_000, 1000)]
    events = [
        # Short-lived: filled in 100ms
        make_placed("o1", 1000, "buy", "99.95"),
        make_event("o1", 1010, "queued", {}),
        make_event("o1", 1110, "filled", {}),
        # Long-lived: cancelled after 5s
        make_placed("o2", 2000, "buy", "99.95"),
        make_event("o2", 2010, "queued", {}),
        make_event("o2", 7010, "cancelled", {}),
    ]
    fills = [make_fill("f1", "o1", 1110, OrderSide.BUY, "99.95")]
    markouts = [make_markout("f1", "o1", OrderSide.BUY, "99.95", 1.0)]

    contexts = compute_order_contexts(make_result(fills, events, samples), markouts)
    bins = fill_rate_by_quote_age_ms(contexts, edges_ms=[0, 1000, 10_000])

    # Bin [0, 1000): the filled 100ms order
    assert bins[0].n_orders == 1
    assert bins[0].n_filled == 1
    # Bin [1000, 10_000): the cancelled 5s order
    assert bins[1].n_orders == 1
    assert bins[1].n_filled == 0
    print("PASS: quote-age binning works")


def test_volatility_computed_when_window_has_samples():
    # Mids vary 99.5-100.5 in the last 60s before placement.
    samples = (
        [make_sample(t, "99.50") for t in range(0, 30_000, 1000)] +
        [make_sample(t, "100.50") for t in range(30_000, 60_000, 1000)]
    )
    events = [
        make_placed("o1", 60_000, "buy", "100.00"),
        make_event("o1", 60_010, "queued", {}),
    ]

    contexts = compute_order_contexts(
        make_result([], events, samples), [], vol_window_ms=60_000
    )

    assert contexts[0].volatility_bps is not None
    assert contexts[0].volatility_bps > Decimal("0")
    print("PASS: volatility computed when window has samples")


def test_summarize_returns_full_structure():
    samples = [make_sample(t, "100.00") for t in range(0, 60_000, 1000)]
    events = [
        make_placed("o1", 30_000, "buy", "99.95"),
        make_event("o1", 30_010, "queued", {}),
        make_event("o1", 31_000, "filled", {}),
    ]
    fills = [make_fill("f1", "o1", 31_000, OrderSide.BUY, "99.95")]
    markouts = [make_markout("f1", "o1", OrderSide.BUY, "99.95", -1.0)]

    summary = summarize_fill_rate(make_result(fills, events, samples), markouts)

    assert "overall" in summary
    assert "by_distance_bps" in summary
    assert "by_quote_age_ms" in summary
    assert "by_volatility_bps" in summary
    assert "contexts" in summary
    assert summary["overall"].n_orders == 1
    assert summary["overall"].n_filled == 1
    assert summary["overall"].fill_rate == 1.0

    text = format_fill_rate_summary(summary)
    assert "Fill-rate breakdown" in text
    assert "overall" in text
    print("PASS: summarize returns full structure and formats")


def test_session_id_is_stamped_on_contexts():
    samples = [make_sample(t, "100.00") for t in range(0, 5_000, 1000)]
    events = [
        make_placed("o1", 1000, "buy", "99.95"),
        make_event("o1", 1010, "queued", {}),
        make_event("o1", 2000, "filled", {}),
    ]
    fills = [make_fill("f1", "o1", 2000, OrderSide.BUY, "99.95")]

    contexts = compute_order_contexts(
        make_result(fills, events, samples), [], session_id="2026-04-16T12"
    )
    assert contexts[0].session_id == "2026-04-16T12"
    print("PASS: session_id is stamped on contexts")


def test_summarize_pooled_contexts_combines_sessions():
    # Build two synthetic sessions, pool them, and confirm overall counts add up.
    samples_a = [make_sample(t, "100.00") for t in range(0, 5_000, 1000)]
    events_a = [
        make_placed("o1", 1000, "buy", "99.95"),
        make_event("o1", 1010, "queued", {}),
        make_event("o1", 2000, "filled", {}),
    ]
    fills_a = [make_fill("f1", "o1", 2000, OrderSide.BUY, "99.95")]
    markouts_a = [make_markout("f1", "o1", OrderSide.BUY, "99.95", -2.0)]

    samples_b = [make_sample(t, "100.00") for t in range(0, 5_000, 1000)]
    events_b = [
        make_placed("o2", 1000, "sell", "100.05"),
        make_event("o2", 1010, "queued", {}),
        make_event("o2", 3000, "cancelled", {}),
    ]

    ctx_a = compute_order_contexts(make_result(fills_a, events_a, samples_a),
                                    markouts_a, session_id="A")
    ctx_b = compute_order_contexts(make_result([], events_b, samples_b),
                                    [], session_id="B")
    pooled = ctx_a + ctx_b

    summary = summarize_pooled_contexts(pooled)
    assert summary["overall"].n_orders == 2
    assert summary["overall"].n_filled == 1
    assert summary["overall"].fill_rate == 0.5
    print("PASS: summarize_pooled_contexts combines sessions")


def test_market_orders_are_skipped():
    # Market orders shouldn't appear in fill-rate analysis (they don't rest)
    events = [
        OrderEvent(
            order_id="m1",
            timestamp_ms=1000,
            event_type="placed",
            detail={"side": "buy", "type": "market", "price": "None", "qty": "0.001",
                    "arrival_ms": 1010},
        ),
        make_event("m1", 1010, "filled", {}),
    ]
    contexts = compute_order_contexts(make_result([], events, []), [])
    assert contexts == []
    print("PASS: market orders are skipped")


if __name__ == "__main__":
    test_filled_order_has_correct_context()
    test_post_only_rejected_classified()
    test_cancelled_unfilled_classified()
    test_partial_then_cancelled_classified()
    test_distance_binning_and_markout_conditional()
    test_quote_age_binning()
    test_volatility_computed_when_window_has_samples()
    test_summarize_returns_full_structure()
    test_session_id_is_stamped_on_contexts()
    test_summarize_pooled_contexts_combines_sessions()
    test_market_orders_are_skipped()
    print("\nAll tests passed.")
