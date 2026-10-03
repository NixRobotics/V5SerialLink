"""Wire protocol shared by the desktop dashboard and its tests."""

from dataclasses import dataclass
import json
import math

MAX_FRAME_BYTES = 16384


class TelemetryError(ValueError):
    """A telemetry packet does not match the wire protocol."""


@dataclass(frozen=True)
class Telemetry:
    x_mm: float
    y_mm: float
    heading_deg: float
    motor_temperatures_c: dict[str, float]


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryError(f"{field} must be a number")
    try:
        result = float(value)
    except OverflowError as error:
        raise TelemetryError(f"{field} must be finite") from error
    if not math.isfinite(result):
        raise TelemetryError(f"{field} must be finite")
    return result


def parse_telemetry(line: bytes) -> Telemetry | None:
    """Ignore ordinary console text; validate JSON telemetry strictly."""
    try:
        text = line.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise TelemetryError("Serial frame is not UTF-8 text") from error
    if not text or not text.startswith("{"):
        return None
    try:
        packet = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise TelemetryError(f"Invalid telemetry JSON: {error}") from error
    if not isinstance(packet, dict):
        raise TelemetryError("Telemetry must be a JSON object")
    for field in ("x_mm", "y_mm", "heading_deg", "motor_temperatures_c"):
        if field not in packet:
            raise TelemetryError(f"Missing telemetry field: {field}")
    temperatures = packet["motor_temperatures_c"]
    if not isinstance(temperatures, dict):
        raise TelemetryError("motor_temperatures_c must be an object")
    if len(temperatures) > 21:
        raise TelemetryError("At most 21 motor temperatures are supported")
    validated_temperatures = {}
    for name, value in temperatures.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise TelemetryError("Motor names must contain 1-64 characters")
        validated_temperatures[name] = _number(value, f"Temperature for {name}")
    return Telemetry(
        _number(packet["x_mm"], "x_mm"),
        _number(packet["y_mm"], "y_mm"),
        _number(packet["heading_deg"], "heading_deg") % 360,
        validated_temperatures,
    )


class LineFramer:
    """Reassemble partial serial reads without unbounded memory growth."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data: bytes) -> list[bytes | TelemetryError]:
        frames: list[bytes | TelemetryError] = []
        for byte in data:
            if byte == 10:
                if not self.discarding:
                    frames.append(bytes(self.buffer))
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                if len(self.buffer) >= MAX_FRAME_BYTES:
                    self.buffer.clear()
                    self.discarding = True
                    frames.append(TelemetryError("Serial frame exceeds 16 KiB; discarded"))
                else:
                    self.buffer.append(byte)
        return frames
