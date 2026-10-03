from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telemetry import parse_telemetry


class StopSample(Exception):
    pass


class RobotSenderTests(unittest.TestCase):
    def test_robot_packets_match_desktop_protocol(self) -> None:
        brain = SimpleNamespace(
            timer=SimpleNamespace(time=Mock(side_effect=[0, 0.1, 0.2])),
            screen=SimpleNamespace(print_at=Mock()),
        )
        vex = ModuleType("vex")
        vex.__dict__.update(
            Brain=lambda: brain,
            Thread=lambda target: target,
            SECONDS="seconds",
            MSEC="milliseconds",
            wait=Mock(side_effect=[None, StopSample()]),
        )
        serial_port = Mock()
        sender = Path(__file__).resolve().parents[2] / "src" / "main.py"
        with patch.dict(sys.modules, {"vex": vex}), patch("builtins.open", return_value=serial_port):
            program = runpy.run_path(str(sender))
            with self.assertRaises(StopSample):
                program["t1"]()
        self.assertEqual(serial_port.write.call_count, 2)
        for call in serial_port.write.call_args_list:
            frame = call.args[0]
            self.assertTrue(frame.endswith(b"\r\n"))
            self.assertEqual(frame.count(b"\n"), 1)
            telemetry = parse_telemetry(frame)
            assert telemetry is not None
            self.assertEqual(set(telemetry.motor_temperatures_c), {"left_drive", "right_drive"})
            self.assertTrue(0 <= telemetry.heading_deg < 360)
        serial_port.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
