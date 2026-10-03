"""Windows/macOS robot telemetry viewer. Run directly with Python 3.10+."""

from collections import deque
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from telemetry import LineFramer, Telemetry, TelemetryError, parse_telemetry
from vex_cdc import (
    ACK,
    USER_READ,
    V5_CONTROLLER_PRODUCT_ID,
    VEX_VENDOR_ID,
    Cdc2ReplyParser,
    build_user_read_request,
)

POLL_MS = 50
STALE_SECONDS = 2.0
FIELD_MM = 3600.0
LINK_AUTO = "Auto"
LINK_DIRECT = "Direct USB (brain user port)"
LINK_CONTROLLER = "V5 Controller (wireless)"
REPLY_TIMEOUT_SECONDS = 1.5
PORT_BUSY_HINT = (
    "If VS Code is open, the VEX extension may own this port. Close VS Code "
    "(or disconnect the device in the VEX extension), then try again."
)


class SerialReader(threading.Thread):
    def __init__(
        self,
        port: str,
        baud: int,
        events: queue.Queue[tuple[str, object]],
        stop: threading.Event,
        controller: bool = False,
    ) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.events = events
        self.stop = stop
        self.controller = controller
        self.framer = LineFramer()

    def publish(self, kind: str, value: object) -> None:
        while not self.stop.is_set():
            try:
                self.events.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                continue

    def run(self) -> None:
        try:
            import serial
        except ImportError:
            self.publish("error", "Install desktop/requirements.txt to use serial connections.")
            return
        try:
            with serial.Serial(self.port, self.baud, timeout=0.2) as connection:
                link = "V5 Controller" if self.controller else "direct"
                self.publish("connected", f"{self.port} ({link})")
                if self.controller:
                    self.read_controller(connection)
                else:
                    while not self.stop.is_set():
                        self.handle_bytes(connection.read(min(connection.in_waiting or 1, 4096)))
        except (serial.SerialException, OSError, ValueError) as error:
            message = f"Serial connection failed: {error}"
            if any(word in str(error).lower() for word in ("busy", "denied", "in use", "exclusive")):
                message += f"\n\n{PORT_BUSY_HINT}"
            self.publish("error", message)

    def read_controller(self, connection) -> None:
        parser = Cdc2ReplyParser()
        request = build_user_read_request()
        connection.reset_input_buffer()
        last_warning = 0.0
        while not self.stop.is_set():
            connection.write(request)
            deadline = time.monotonic() + REPLY_TIMEOUT_SECONDS
            reply = None
            while reply is None and not self.stop.is_set() and time.monotonic() < deadline:
                chunk = connection.read(min(connection.in_waiting or 1, 4096))
                reply = next((item for item in parser.feed(chunk) if item.ecmd == USER_READ), None)
            if self.stop.is_set():
                return
            if reply is None or reply.ack != ACK:
                if time.monotonic() - last_warning > 5:
                    last_warning = time.monotonic()
                    reason = "no reply" if reply is None else reply.nack_message
                    self.publish(
                        "warning",
                        f"Controller {reason} to user-data request. Check that the controller is "
                        "linked to the brain and the program is running.",
                    )
                self.stop.wait(0.1)
                continue
            data = reply.user_data
            if data:
                self.handle_bytes(data)
            else:
                self.stop.wait(0.02)

    def handle_bytes(self, data: bytes) -> None:
        for frame in self.framer.feed(data):
            if isinstance(frame, TelemetryError):
                self.publish("warning", str(frame))
                continue
            try:
                telemetry = parse_telemetry(frame)
            except TelemetryError as error:
                self.publish("warning", str(error))
            else:
                if telemetry is not None:
                    self.publish("telemetry", (time.monotonic(), telemetry))
                elif frame.strip():
                    self.publish("console", frame.decode("utf-8", "replace").strip())


class Dashboard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("V5 Robot Telemetry")
        root.geometry("1050x760")
        root.minsize(800, 600)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=256)
        self.worker: SerialReader | None = None
        self.stop = threading.Event()
        self.demo = False
        self.demo_started = 0.0
        self.last_received: float | None = None
        self.latest: Telemetry | None = None
        self.packet_count = 0
        self.trail: deque[tuple[float, float]] = deque(maxlen=600)
        self.devices: dict[str, str] = {}
        self.controller_ports: set[str] = set()
        self.port = tk.StringVar()
        self.baud = tk.StringVar(value="115200")
        self.link = tk.StringVar(value=LINK_AUTO)
        self.status = tk.StringVar(value="Disconnected")
        self.age = tk.StringVar(value="No telemetry received")
        self.values = {key: tk.StringVar(value="--") for key in ("X", "Y", "Heading")}
        self._build()
        self.refresh_ports()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(POLL_MS, self.poll)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        controls = ttk.Frame(outer)
        controls.pack(fill="x")
        ttk.Label(controls, text="Serial port").pack(side="left")
        self.port_box = ttk.Combobox(controls, textvariable=self.port, width=32)
        self.port_box.pack(side="left", padx=8)
        self.refresh_button = ttk.Button(controls, text="Refresh", command=self.refresh_ports)
        self.refresh_button.pack(side="left")
        ttk.Label(controls, text="Baud").pack(side="left", padx=(12, 4))
        self.baud_box = ttk.Combobox(
            controls, textvariable=self.baud, values=("115200", "57600", "38400", "9600"), width=8
        )
        self.baud_box.pack(side="left")
        ttk.Label(controls, text="Link").pack(side="left", padx=(12, 4))
        self.link_box = ttk.Combobox(
            controls, textvariable=self.link, values=(LINK_AUTO, LINK_DIRECT, LINK_CONTROLLER),
            width=24, state="readonly",
        )
        self.link_box.pack(side="left")
        self.connect_button = ttk.Button(controls, text="Connect", command=self.connect)
        self.connect_button.pack(side="left", padx=8)
        self.demo_button = ttk.Button(controls, text="Start demo", command=self.start_demo)
        self.demo_button.pack(side="left")

        status_row = ttk.Frame(outer)
        status_row.pack(fill="x", pady=(12, 16))
        ttk.Label(status_row, textvariable=self.status, font=("", 12, "bold")).pack(side="left")
        ttk.Label(status_row, textvariable=self.age).pack(side="right")

        cards = ttk.Frame(outer)
        cards.pack(fill="x")
        for index, (name, unit) in enumerate((("X", "mm"), ("Y", "mm"), ("Heading", "deg"))):
            card = ttk.LabelFrame(cards, text=f"{name} ({unit})", padding=10)
            card.grid(row=0, column=index, sticky="ew", padx=4)
            cards.columnconfigure(index, weight=1)
            ttk.Label(card, textvariable=self.values[name], font=("", 24, "bold")).pack()

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=16)
        plot = ttk.LabelFrame(body, text="Position - mm / heading clockwise from +Y", padding=8)
        plot.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(plot, background="#111827", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw_plot())
        ttk.Button(plot, text="Clear trail", command=self.clear_trail).pack(pady=(8, 0))
        temperatures = ttk.LabelFrame(body, text="Motor temperatures (C)", padding=8)
        temperatures.pack(side="right", fill="both", padx=(12, 0))
        self.motors = ttk.Treeview(
            temperatures, columns=("motor", "temperature"), show="headings", height=12
        )
        self.motors.heading("motor", text="Motor")
        self.motors.heading("temperature", text="C")
        self.motors.column("motor", width=150)
        self.motors.column("temperature", width=70, anchor="center")
        self.motors.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(temperatures, orient="vertical", command=self.motors.yview)
        scrollbar.pack(side="right", fill="y")
        self.motors.configure(yscrollcommand=scrollbar.set)
        logs = ttk.LabelFrame(outer, text="Serial console / errors", padding=8)
        logs.pack(fill="x")
        self.log = tk.Text(logs, height=5, state="disabled", wrap="word")
        self.log.pack(fill="x")

    def log_message(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 200:
            self.log.delete("1.0", "2.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports
        except ImportError:
            self.log_message("Serial support unavailable. Install desktop/requirements.txt; demo still works.")
            return
        try:
            ports = sorted(list_ports.comports(), key=lambda item: item.device)
        except OSError as error:
            self.log_message(f"Cannot list serial ports: {error}")
            return
        current = self.devices.get(self.port.get(), self.port.get())
        self.devices = {f"{item.device} - {item.description}": item.device for item in ports}
        self.controller_ports = {
            item.device for item in ports
            if item.vid == VEX_VENDOR_ID and item.pid == V5_CONTROLLER_PRODUCT_ID
        }
        self.port_box["values"] = tuple(self.devices)
        selected = next((label for label, device in self.devices.items() if device == current), None)
        if selected:
            self.port.set(selected)
        elif not current and self.devices:
            self.port.set(next(iter(self.devices)))
        if not ports:
            self.log_message("No serial ports found. Connect the robot by USB, then click Refresh.")

    def set_active(self, active: bool) -> None:
        self.connect_button.configure(text="Disconnect" if active else "Connect")
        state = "disabled" if active else "normal"
        for widget in (self.port_box, self.baud_box, self.refresh_button, self.demo_button):
            widget.configure(state=state)
        self.link_box.configure(state="disabled" if active else "readonly")

    def reset_telemetry(self) -> None:
        self.last_received = None
        self.latest = None
        self.packet_count = 0
        self.trail.clear()
        for value in self.values.values():
            value.set("--")
        self.motors.delete(*self.motors.get_children())
        self.draw_plot()

    def disconnect(self) -> None:
        self.demo = False
        self.stop.set()
        # The reader uses a short serial timeout; never block the GUI while it exits.
        if self.worker is not None:
            self.status.set("Disconnecting...")
        else:
            self.finish_disconnect()

    def finish_disconnect(self) -> None:
        self.worker = None
        self.events = queue.Queue(maxsize=256)
        self.set_active(False)
        self.status.set("Disconnected")

    def connect(self) -> None:
        if self.worker is not None or self.demo:
            self.disconnect()
            return
        port = self.devices.get(self.port.get(), self.port.get()).strip()
        if not port:
            messagebox.showerror("Serial port required", "Select or enter a serial port.", parent=self.root)
            return
        try:
            baud = int(self.baud.get())
            if baud <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid baud", "Baud must be a positive integer.", parent=self.root)
            return
        self.reset_telemetry()
        self.stop = threading.Event()
        link = self.link.get()
        controller = link == LINK_CONTROLLER or (link == LINK_AUTO and port in self.controller_ports)
        self.worker = SerialReader(port, baud, self.events, self.stop, controller)
        self.set_active(True)
        self.status.set("Connecting...")
        self.worker.start()

    def start_demo(self) -> None:
        self.reset_telemetry()
        self.demo = True
        self.demo_started = time.monotonic()
        self.set_active(True)
        self.status.set("DEMO - simulated telemetry (no robot)")
        self.log_message("Demo started. All values are simulated.")

    def accept(self, received: float, telemetry: Telemetry) -> None:
        self.last_received = received
        self.latest = telemetry
        self.packet_count += 1
        self.trail.append((telemetry.x_mm, telemetry.y_mm))

    def render_telemetry(self) -> None:
        if self.latest is None:
            return
        for name, value in (
            ("X", self.latest.x_mm),
            ("Y", self.latest.y_mm),
            ("Heading", self.latest.heading_deg),
        ):
            self.values[name].set(f"{value:.1f}")
        self.motors.delete(*self.motors.get_children())
        for name, temperature in sorted(self.latest.motor_temperatures_c.items()):
            self.motors.insert("", "end", values=(name, f"{temperature:.1f}"))
        self.draw_plot()

    def poll(self) -> None:
        now = time.monotonic()
        updated = False
        for _ in range(256):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if self.stop.is_set():
                continue
            if kind == "telemetry":
                if isinstance(payload, tuple):
                    received, telemetry = payload
                    if isinstance(received, float) and isinstance(telemetry, Telemetry):
                        self.accept(received, telemetry)
                        updated = True
            elif kind == "connected":
                self.status.set(f"Connected: {payload} - waiting for telemetry")
                self.log_message(f"Connected to {payload}")
            elif kind == "error":
                self.log_message(str(payload))
                self.disconnect()
                messagebox.showerror("Serial connection error", str(payload), parent=self.root)
            else:
                self.log_message(str(payload))
        if self.worker is not None and not self.worker.is_alive() and self.events.empty():
            if not self.stop.is_set():
                self.log_message("Serial reader stopped.")
            self.finish_disconnect()
        if self.demo:
            angle = (now - self.demo_started) * 0.35
            self.accept(
                now,
                Telemetry(
                    1000 * math.cos(angle),
                    1000 * math.sin(angle),
                    (-math.degrees(angle)) % 360,
                    {"left_drive": 35 + 5 * math.sin(angle), "right_drive": 37 + 4 * math.cos(angle)},
                ),
            )
            updated = True
        if updated:
            self.render_telemetry()
        if self.last_received is not None:
            elapsed = max(0.0, now - self.last_received)
            stale = elapsed > STALE_SECONDS
            self.age.set(
                f"{'STALE - ' if stale else ''}{elapsed:.1f}s since telemetry | {self.packet_count} packets"
            )
            if self.worker is not None and not self.stop.is_set():
                self.status.set("STALE - no recent telemetry" if stale else "Connected - live telemetry")
        else:
            self.age.set("No telemetry received")
        self.root.after(POLL_MS, self.poll)

    def clear_trail(self) -> None:
        self.trail.clear()
        self.draw_plot()

    def draw_plot(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 100:
            return
        extent = max(FIELD_MM / 2, *(max(abs(x), abs(y)) for x, y in self.trail), 1)
        if self.latest is not None:
            extent = max(extent, abs(self.latest.x_mm), abs(self.latest.y_mm))
        # Normalize first so even unusually large, finite coordinates remain drawable.
        radius = max(1, min(width, height) / 2 - 40)
        center_x, center_y = width / 2, height / 2

        def point(x: float, y: float) -> tuple[float, float]:
            return center_x + (x / extent) * radius, center_y - (y / extent) * radius

        for fraction in (-1, -0.5, 0, 0.5, 1):
            offset = fraction * radius
            color = "#64748b" if fraction == 0 else "#334155"
            canvas.create_line(center_x + offset, center_y - radius, center_x + offset, center_y + radius, fill=color)
            canvas.create_line(center_x - radius, center_y + offset, center_x + radius, center_y + offset, fill=color)
        canvas.create_text(center_x + radius, center_y + 14, text="+X", fill="white")
        canvas.create_text(center_x + 14, center_y - radius, text="+Y", fill="white")
        canvas.create_text(8, 12, anchor="w", text=f"Axes: +/- {extent:.0f} mm (auto-scaled)", fill="#cbd5e1")
        if len(self.trail) > 1:
            coordinates = [coordinate for x, y in self.trail for coordinate in point(x, y)]
            canvas.create_line(*coordinates, fill="#38bdf8", width=2)
        if self.latest is not None:
            x, y = point(self.latest.x_mm, self.latest.y_mm)
            angle = math.radians(self.latest.heading_deg)
            canvas.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#4ade80", outline="")
            canvas.create_line(
                x, y, x + 30 * math.sin(angle), y - 30 * math.cos(angle),
                fill="#4ade80", width=3, arrow="last",
            )

    def close(self) -> None:
        self.stop.set()
        if self.worker is not None and self.worker.is_alive():
            self.root.after(POLL_MS, self.close)
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
