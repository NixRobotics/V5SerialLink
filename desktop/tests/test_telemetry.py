import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telemetry import MAX_FRAME_BYTES, LineFramer, TelemetryError, parse_telemetry


class TelemetryTests(unittest.TestCase):
    def packet(self, **changes: object) -> bytes:
        packet = {
            "x_mm": 100,
            "y_mm": -250.5,
            "heading_deg": 370,
            "motor_temperatures_c": {"left_drive": 42.5, "right_drive": 39},
        }
        packet.update(changes)
        return json.dumps(packet).encode()

    def test_valid_packet_and_heading_normalization(self) -> None:
        result = parse_telemetry(self.packet() + b"\r\n")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual((result.x_mm, result.y_mm, result.heading_deg), (100, -250.5, 10))
        self.assertEqual(result.motor_temperatures_c, {"left_drive": 42.5, "right_drive": 39})
        negative = parse_telemetry(self.packet(heading_deg=-90))
        assert negative is not None
        self.assertEqual(negative.heading_deg, 270)

    def test_console_text_is_not_telemetry(self) -> None:
        for frame in (b"Hello\r\n", b"", b"Robot ready"):
            with self.subTest(frame=frame):
                self.assertIsNone(parse_telemetry(frame))

    def test_malformed_frames_report_errors(self) -> None:
        for frame in (b"\xff", b"{bad json", b"{}", b'{"x_mm":'):
            with self.subTest(frame=frame):
                with self.assertRaises(TelemetryError):
                    parse_telemetry(frame)

    def test_coordinates_require_finite_numbers(self) -> None:
        for field in ("x_mm", "y_mm", "heading_deg"):
            for value in (True, "12", None, float("nan"), float("inf"), 10**400):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(TelemetryError):
                        parse_telemetry(self.packet(**{field: value}))

    def test_motor_temperatures_are_validated(self) -> None:
        for value in ([], {"": 20}, {"motor": True}, {"motor": float("inf")}, {"x" * 65: 20},
                      {str(index): 20 for index in range(22)}):
            with self.subTest(value=value):
                with self.assertRaises(TelemetryError):
                    parse_telemetry(self.packet(motor_temperatures_c=value))
        result = parse_telemetry(self.packet(motor_temperatures_c={}))
        assert result is not None
        self.assertEqual(result.motor_temperatures_c, {})

    def test_partial_reads_and_multiple_packets(self) -> None:
        framer = LineFramer()
        packet = self.packet()
        self.assertEqual(framer.feed(packet[:20]), [])
        frames = framer.feed(packet[20:] + b"\r\nHello\n" + packet + b"\n")
        self.assertEqual(frames, [packet + b"\r", b"Hello", packet])
        self.assertEqual(framer.buffer, b"")

    def test_frame_limit_and_recovery(self) -> None:
        framer = LineFramer()
        self.assertEqual(framer.feed(b"x" * MAX_FRAME_BYTES), [])
        frames = framer.feed(b"x" * 100)
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], TelemetryError)
        self.assertEqual(framer.buffer, b"")
        self.assertEqual(framer.feed(b"discarded\nHello\n"), [b"Hello"])
        self.assertEqual(framer.feed(b"x" * MAX_FRAME_BYTES + b"\n"), [b"x" * MAX_FRAME_BYTES])


if __name__ == "__main__":
    unittest.main()
