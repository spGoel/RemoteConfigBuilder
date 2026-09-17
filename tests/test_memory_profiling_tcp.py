import queue
import socket
import unittest
from datetime import datetime, timezone
from pathlib import Path

from memory_profiling.main import LiveMeterServer, MeterHistoryStore, _parse_robot_time


class MemoryProfilingTcpTests(unittest.TestCase):
    def test_robot_csv_stream_is_received_and_saved(self):
        events = queue.Queue()
        server = LiveMeterServer("127.0.0.1", 0, events)
        server.start()
        kind, _message = events.get(timeout=2)
        self.assertEqual(kind, "status")

        try:
            with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
                client.sendall(
                    b"Time,Info,Free-Memory,Games-Played\n"
                    b"2026-09-01 12:00:00,Game-Idle,4096,12\n"
                )
            received = None
            while received is None:
                kind, payload = events.get(timeout=2)
                if kind == "row":
                    received = payload

            store = MeterHistoryStore(Path(":memory:"))
            source = Path("live.stream")
            self.assertEqual(store.insert_sample(source, received), 2)
            series = store.load_series(source, 0, 2_000_000_000, ["Free-Memory"])
            self.assertEqual(series["Free-Memory"][0][1], 4096)
            store.close()
        finally:
            server.stop()


class ParseRobotTimeTests(unittest.TestCase):
    """Robot logs Time as naive UTC; the host's local timezone must not matter.

    Regression coverage for a bug where a naive-datetime .timestamp() call
    assumed the *profiler host's* local timezone, so samples imported on a
    non-UTC machine (e.g. Windows in IST) landed hours away from time.time()
    and silently disappeared from the live/history chart window.
    """

    def test_boost_ptime_format_is_interpreted_as_utc(self):
        expected = datetime(2026, 9, 17, 8, 46, 24, 641094, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(
            _parse_robot_time("2026-Sep-17 08:46:24.641094"), expected, places=5,
        )

    def test_iso_format_without_tz_is_interpreted_as_utc(self):
        expected = datetime(2026, 9, 17, 8, 46, 24, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(
            _parse_robot_time("2026-09-17T08:46:24"), expected, places=5,
        )

    def test_iso_format_with_explicit_offset_is_respected(self):
        expected = datetime(2026, 9, 17, 8, 16, 24, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(
            _parse_robot_time("2026-09-17T13:46:24+05:30"), expected, places=5,
        )


class LatestBeforeTests(unittest.TestCase):
    """Backs the chart's gap-continuity fix: a meter's line should hold its
    last known value across a reporting gap (e.g. slower state-change dumps,
    or the machine being powered off) instead of dangling mid-canvas."""

    def test_returns_the_most_recent_sample_at_or_before_the_timestamp(self):
        store = MeterHistoryStore(Path(":memory:"))
        source = Path("egm.source")
        store.insert_sample(source, {"Time": "2026-09-01 12:00:00", "Free-Memory": "4096"})
        store.insert_sample(source, {"Time": "2026-09-01 18:00:00", "Free-Memory": "2048"})

        # A long gap follows (e.g. the machine was off); querying a moment
        # inside that gap should still surface the last known value.
        query_time = _parse_robot_time("2026-09-01 18:00:00") + 3600
        anchor = store.latest_before(source, "Free-Memory", query_time)
        self.assertIsNotNone(anchor)
        self.assertEqual(anchor[1], 2048)
        store.close()

    def test_returns_none_when_meter_has_no_history_yet(self):
        store = MeterHistoryStore(Path(":memory:"))
        source = Path("egm.source")
        store.insert_sample(source, {"Time": "2026-09-01 12:00:00", "Free-Memory": "4096"})
        before = _parse_robot_time("2026-09-01 12:00:00") - 3600
        self.assertIsNone(store.latest_before(source, "Free-Memory", before))
        store.close()


if __name__ == "__main__":
    unittest.main()
