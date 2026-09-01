import queue
import socket
import unittest
from pathlib import Path

from memory_profiling.main import LiveMeterServer, MeterHistoryStore


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


if __name__ == "__main__":
    unittest.main()
