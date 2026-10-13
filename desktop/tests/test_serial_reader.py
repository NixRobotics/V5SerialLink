from pathlib import Path
import io
import json
import queue
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard import SerialReader, StdinReader, WebSocketReader
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

    def test_stdin_reader_processes_stream_and_handles_eof(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=256)
        stop = threading.Event()
        stream = io.BytesIO(
            b'Greeting\r\n'
            b'{"x_mm":50,"y_mm":-75,"heading_deg":180,'
            b'"motor_temperatures_c":{"drive":33.5}}\n'
        )
        reader = StdinReader(events, stop, stream=stream)
        reader.start()
        reader.join(timeout=2)
        self.assertFalse(reader.is_alive())

        observed = []
        while not events.empty():
            observed.append(events.get_nowait())

        kinds = [kind for kind, _val in observed]
        self.assertIn("connected", kinds)
        self.assertIn("console", kinds)
        self.assertIn("telemetry", kinds)

        telemetry_item = next(val for kind, val in observed if kind == "telemetry")
        assert isinstance(telemetry_item, tuple)
        self.assertIsInstance(telemetry_item[0], float)
        self.assertEqual(telemetry_item[1], Telemetry(50, -75, 180, {"drive": 33.5}))

    def test_websocket_reader_queries_device_and_receives_telemetry(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        port = server.getsockname()[1]

        def handle_server():
            # First connection: /query
            client, _ = server.accept()
            req = client.recv(1024)
            client.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n"
            )
            # read query request frame
            client.recv(1024)
            # send reply
            reply = json.dumps({"command": "Selected Device", "result": {"device": "Brain", "instanceId": "123"}}).encode()
            client.sendall(bytes([0x81, len(reply)]) + reply)
            client.close()

            # Second connection: /device
            client2, _ = server.accept()
            req2 = client2.recv(1024)
            client2.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n"
            )
            # send a telemetry frame
            line = b'{"x_mm":20,"y_mm":30,"heading_deg":45,"motor_temperatures_c":{"m1":25}}\r\n'
            client2.sendall(bytes([0x81, len(line)]) + line)
            time.sleep(0.05)
            # send close frame
            client2.sendall(bytes([0x88, 0x00]))
            client2.close()
            server.close()

        server_thread = threading.Thread(target=handle_server, daemon=True)
        server_thread.start()

        events: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=256)
        stop = threading.Event()
        reader = WebSocketReader(events, stop, host="127.0.0.1", port=port)
        reader.start()
        reader.join(timeout=3)
        self.assertFalse(reader.is_alive())

        observed = []
        while not events.empty():
            observed.append(events.get_nowait())

        kinds = [kind for kind, _ in observed]
        self.assertIn("connected", kinds)
        self.assertIn("telemetry", kinds)
        telemetry_item = next(val for kind, val in observed if kind == "telemetry")
        assert isinstance(telemetry_item, tuple)
        self.assertEqual(telemetry_item[1], Telemetry(20, 30, 45, {"m1": 25}))


if __name__ == "__main__":
    unittest.main()
