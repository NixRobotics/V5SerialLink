from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import patch

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard import SerialReader
from telemetry import Telemetry


class SerialReaderTests(unittest.TestCase):
    def test_loopback_serial_stream(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=256)
        stop = threading.Event()
        connection = serial.serial_for_url("loop://", timeout=0.05)
        connection.write(
            b'Hello\r\n{broken}\n'
            b'{"x_mm":12,"y_mm":34,"heading_deg":90,'
            b'"motor_temperatures_c":{"drive":40}}\r\n'
        )
        reader = SerialReader("test-port", 115200, events, stop)
        observed: list[tuple[str, object]] = []
        with patch("serial.Serial", return_value=connection):
            reader.start()
            try:
                while not observed or observed[-1][0] != "telemetry":
                    observed.append(events.get(timeout=2))
            finally:
                stop.set()
                reader.join(timeout=2)
        self.assertFalse(reader.is_alive())
        self.assertFalse(connection.is_open)
        self.assertEqual([kind for kind, _value in observed], ["connected", "console", "warning", "telemetry"])
        payload = observed[-1][1]
        assert isinstance(payload, tuple)
        self.assertIsInstance(payload[0], float)
        self.assertEqual(payload[1], Telemetry(12, 34, 90, {"drive": 40}))

    def test_open_failure_is_reported(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        reader = SerialReader("busy-port", 115200, events, threading.Event())
        with patch("serial.Serial", side_effect=serial.SerialException("Port is busy")):
            reader.run()
        kind, message = events.get_nowait()
        self.assertEqual(kind, "error")
        self.assertIn("Port is busy", str(message))

    def test_stop_does_not_block_when_event_queue_is_full(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        events.put(("console", "existing"))
        stop = threading.Event()
        connection = serial.serial_for_url("loop://", timeout=0.05)
        reader = SerialReader("test-port", 115200, events, stop)
        with patch("serial.Serial", return_value=connection):
            reader.start()
            stop.set()
            reader.join(timeout=2)
        self.assertFalse(reader.is_alive())
        self.assertFalse(connection.is_open)


if __name__ == "__main__":
    unittest.main()
