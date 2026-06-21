"""Unit tests for the balance-dispatch throttle (pure logic)."""

from datetime import datetime, timedelta, timezone

from src.balance_throttle import MIN_INTERVAL_HOURS, merge_pending, window_open


def test_merge_pending_unions_and_sorts_and_drops_empty():
    state = {"pending_skus": ["ITBISA-B", "ITBISA-A"]}
    assert merge_pending(state, ["ITBISA-C", "ITBISA-A", "", None]) == [
        "ITBISA-A",
        "ITBISA-B",
        "ITBISA-C",
    ]


def test_merge_pending_from_empty_state():
    assert merge_pending({}, ["ITBISA-X"]) == ["ITBISA-X"]
    assert merge_pending({"pending_skus": []}, []) == []


def test_window_open_when_never_dispatched():
    assert window_open({"last_dispatch_at": None}) is True
    assert window_open({}) is True


def test_window_open_after_interval_elapsed():
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=MIN_INTERVAL_HOURS, minutes=1)).isoformat()
    assert window_open({"last_dispatch_at": old}, now=now) is True


def test_window_closed_within_interval():
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=MIN_INTERVAL_HOURS, minutes=-1)).isoformat()
    assert window_open({"last_dispatch_at": recent}, now=now) is False


def test_window_open_on_corrupt_timestamp():
    assert window_open({"last_dispatch_at": "not-a-date"}) is True
