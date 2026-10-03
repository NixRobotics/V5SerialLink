from pathlib import Path
import queue
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vex_cdc import (
    ACK,
    USER_CDC,
    USER_READ,
    Cdc2ReplyParser,
    build_user_read_request,
    crc16_xmodem,
)


def make_reply(ack: int, payload: bytes, ecmd: int = USER_READ) -> bytes:
    body = bytes([ecmd, ack]) + payload
    length = len(body) + 2
    size = bytes([length]) if length <= 0x7F else bytes([(length >> 8) | 0x80, length & 0xFF])
    packet = b"\xaa\x55" + bytes([USER_CDC]) + size + body
    return packet + crc16_xmodem(packet).to_bytes(2, "big")


class VexCdcTests(unittest.TestCase):
    def test_crc_standard_check_value(self) -> None:
        self.assertEqual(crc16_xmodem(b"123456789"), 0x31C3)

    def test_user_read_request_bytes(self) -> None:
        request = build_user_read_request()
        self.assertEqual(request[:9], bytes.fromhex("c936b847562702 0140".replace(" ", "")))
        self.assertEqual(len(request), 11)
        self.assertEqual(crc16_xmodem(request), 0)

    def test_reply_parsing_split_padding_and_noise(self) -> None:
        parser = Cdc2ReplyParser()
        reply = make_reply(ACK, b"\x01hello\n\x00\x00\x00")
        self.assertEqual(parser.feed(b"\x00junk\xaa" + reply[:5]), [])
        replies = parser.feed(reply[5:])
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].user_data, b"hello\n")

    def test_wide_length_reply(self) -> None:
        data = b"x" * 200
        replies = Cdc2ReplyParser().feed(make_reply(ACK, b"\x01" + data))
        self.assertEqual(replies[0].user_data, data)

    def test_corrupted_reply_is_skipped_and_parser_resyncs(self) -> None:
        bad = bytearray(make_reply(ACK, b"\x01bad"))
        bad[-1] ^= 0xFF
        good = make_reply(ACK, b"\x01good")
        replies = Cdc2ReplyParser().feed(bytes(bad) + good)
        self.assertEqual([item.user_data for item in replies], [b"good"])

    def test_nack_has_no_user_data(self) -> None:
        reply = Cdc2ReplyParser().feed(make_reply(0xFF, b""))[0]
        self.assertEqual(reply.user_data, b"")
        self.assertEqual(reply.nack_message, "general NACK")


class FakeController:
    """Answers each USER_READ request with the next chunk of robot output."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.pending = bytearray()
        self.requests: list[bytes] = []
        self.is_open = True

    def __enter__(self) -> "FakeController":
        return self

    def __exit__(self, *_args: object) -> None:
        self.is_open = False

    @property
    def in_waiting(self) -> int:
        return len(self.pending)

    def reset_input_buffer(self) -> None:
        self.pending.clear()

    def write(self, data: bytes) -> int:
        self.requests.append(data)
        chunk = self.chunks.pop(0) if self.chunks else b""
        self.pending += make_reply(ACK, b"\x01" + chunk + b"\x00")
        return len(data)

    def read(self, size: int) -> bytes:
        if not self.pending:
            time.sleep(0.01)
            return b""
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data


class ControllerReaderTests(unittest.TestCase):
    def test_controller_mode_polls_and_reassembles_telemetry(self) -> None:
        from dashboard import SerialReader

        line = b'{"x_mm":1,"y_mm":2,"heading_deg":3,"motor_temperatures_c":{"m":40}}\r\n'
        fake = FakeController([b"Hello\r\n", line[:30], b"", line[30:]])
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        stop = threading.Event()
        reader = SerialReader("ctrl", 115200, events, stop, controller=True)
        observed: list[str] = []
        with patch("serial.Serial", return_value=fake):
            reader.start()
            try:
                while "telemetry" not in observed:
                    observed.append(events.get(timeout=2)[0])
            finally:
                stop.set()
                reader.join(timeout=2)
        self.assertEqual(observed, ["connected", "console", "telemetry"])
        self.assertTrue(all(request == build_user_read_request() for request in fake.requests))
        self.assertFalse(reader.is_alive())

    def test_busy_port_error_mentions_vs_code(self) -> None:
        import serial
        from dashboard import SerialReader

        events: queue.Queue[tuple[str, object]] = queue.Queue()
        reader = SerialReader("ctrl", 115200, events, threading.Event(), controller=True)
        with patch("serial.Serial", side_effect=serial.SerialException("[Errno 16] Resource busy")):
            reader.run()
        kind, message = events.get_nowait()
        self.assertEqual(kind, "error")
        self.assertIn("VS Code", str(message))


if __name__ == "__main__":
    unittest.main()
