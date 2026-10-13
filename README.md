# V5SerialLink

Live robot telemetry visualization and wire protocol for VEX V5 robots over direct USB, wireless V5 Controller link, VS Code VEX Terminal WebSocket relay, or standard input pipe.

## Desktop Dashboard

Run the dashboard from the `desktop/` directory:

```bash
# Direct Serial / V5 Controller mode
python desktop/dashboard.py

# Direct connection via VS Code VEX Extension WebSocket (simultaneous with VS Code terminal)
python desktop/dashboard.py --ws

# Standard Input (stdin / pipe) mode
python desktop/dashboard.py --stdin
```

### Reading while VS Code is Open (Simultaneous Output)

When the **VEX VS Code Extension** is open and connected to the robot, it can relay data to the dashboard over its built-in WebSocket server:

1. In `.vscode/settings.json`, make sure the VEX WebSocket server is enabled:
   ```json
   {
       "vexrobotics.vexcode.WebsocketServer.Enable": "Enable",
       "vexrobotics.vexcode.WebsocketServer.Port": 7071,
       "vexrobotics.vexcode.WebsocketServer.HostAddress": "127.0.0.1"
   }
   ```
2. In the dashboard's **Serial port** dropdown, select:
   `<VS Code VEX Terminal (ws://127.0.0.1:7071)>`
   and click **Connect** (or run `python desktop/dashboard.py --ws`).

### Wire Protocol

The dashboard supports both compact and verbose JSON packets separated by newlines (`\n` or `\r\n`):

- **Compact format (Recommended for higher refresh rates / wireless):**
  ```json
  {"x": 100.5, "y": -250.0, "h": 90.0, "m": {"left_drive": 35.0, "right_drive": 37.0}}
  ```
- **Verbose format (Full backwards compatibility):**
  ```json
  {"x_mm": 100.5, "y_mm": -250.0, "heading_deg": 90.0, "motor_temperatures_c": {"left_drive": 35.0, "right_drive": 37.0}}
  ```

