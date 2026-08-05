from scripts.demo_event_driven_execution import run_demo


def test_event_driven_demo_exercises_fill_cancel_race():
    result = run_demo()

    assert result["execution_model"] == "event_driven_v2"
    assert result["final_status"] == "filled"
    assert result["fill_count"] == 1
    assert result["fill_quantity"] == "0.001"
    assert result["maker_fee"] == "0.00002"
    assert result["cancel_too_late"] == 1
    assert result["lifecycle"] == [
        "0ms:placed",
        "5ms:arrived",
        "5ms:queued",
        "6ms:cancel_requested",
        "10ms:queue_drain",
        "10ms:filled",
        "11ms:cancel_too_late",
    ]
