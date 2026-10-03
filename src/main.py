#vex:disable=repl

from vex import *
import math

brain = Brain()

SEND_INTERVAL_MS = 250


def read_telemetry(elapsed):
    # Hardware hook: replace these simulated values with GPS/odometry readings
    # in mm, clockwise heading from +Y, and motor.temperature(CELSIUS).
    angle = elapsed * 0.35
    x_mm = 1000 * math.cos(angle)
    y_mm = 1000 * math.sin(angle)
    heading_deg = (-angle * 180 / math.pi) % 360
    left_c = 35 + 5 * math.sin(angle)
    right_c = 37 + 4 * math.cos(angle)
    return x_mm, y_mm, heading_deg, left_c, right_c


def serial_monitor():
    try:
        serial_port = open('/dev/serial1', 'wb')
    except OSError as error:
        brain.screen.print_at("Serial port unavailable", x=5, y=20)
        raise RuntimeError("serial port not available") from error

    started = brain.timer.time(SECONDS)
    try:
        while True:
            elapsed = brain.timer.time(SECONDS) - started
            x_mm, y_mm, heading_deg, left_c, right_c = read_telemetry(elapsed)
            # One complete JSON object per line; no JSON library needed on the brain.
            packet = (
                '{"x_mm":%.1f,"y_mm":%.1f,"heading_deg":%.1f,'
                '"motor_temperatures_c":{"left_drive":%.1f,"right_drive":%.1f}}\r\n'
                % (x_mm, y_mm, heading_deg, left_c, right_c)
            )
            serial_port.write(packet.encode('utf-8'))
            brain.screen.print_at("DEMO telemetry %.1fs" % elapsed, x=5, y=20)
            # ~4 Hz (~460 B/s) fits a V5 Controller wireless link (~560 B/s measured).
            # Faster rates overflow the brain's output buffer and corrupt lines.
            # A direct USB cable to the brain can go much faster.
            wait(SEND_INTERVAL_MS, MSEC)
    finally:
        serial_port.close()


t1 = Thread(serial_monitor)
