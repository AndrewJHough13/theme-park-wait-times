"""Offline tests for the parsing logic in collector.py (no network or database needed).

Run from the repo root:  pip install pytest && python -m pytest
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import collector  # noqa: E402

BUCKET = datetime(2026, 10, 9, 19, 30, tzinfo=timezone.utc)

SAMPLE = {
    "lands": [{"id": 1, "name": "New York", "rides": [
        {"id": 9, "name": "Mummy", "is_open": True, "wait_time": 15,
         "last_updated": "2026-10-09T19:20:32.000Z"}]}],
    "rides": [
        {"id": 10, "name": "Ungrouped", "is_open": False, "wait_time": 0, "last_updated": None}],
}


def test_flatten_handles_lands_and_ungrouped_rides():
    rides, waits = collector.flatten(65, SAMPLE, BUCKET)
    assert rides == [(9, 65, "Mummy", "New York"), (10, 65, "Ungrouped", None)]
    assert waits[0] == (9, BUCKET, 15, True, 9)       # 19:30:00 - 19:20:32 = 9m28s -> floors to 9
    assert waits[1] == (10, BUCKET, 0, False, None)   # no timestamp -> None


def test_stale_minutes_edge_cases():
    assert collector.stale_minutes("garbage", BUCKET) is None
    assert collector.stale_minutes("2030-01-01T00:00:00Z", BUCKET) == 0  # future clamps to 0


def test_window_gate():
    et = collector.ET
    assert collector.in_window(datetime(2026, 10, 9, 7, 0, tzinfo=et))
    assert not collector.in_window(datetime(2026, 10, 9, 23, 0, tzinfo=et))
