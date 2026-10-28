# ESP32-CAM sketch, pins, and bring-up

The Arduino sketch is in `HerbiBioS/HerbiBioS.ino`. The camera pin map is defined directly in the sketch; `camera_pins.h` is a reference file and is not included by the sketch.

## Pin decisions

This mapping uses the previous working sketch's `CAMERA_MODEL_AI_THINKER` camera wiring and the pinout supplied by the owner. SD is explicitly unused, so its exposed pins are available for other functions. The chosen pins are GPIO12/13/14. GPIO12 is a boot-strapping pin, so the attached servo input must not pull it high during reset. The owner has assembled the wiring; check that the board boots reliably several times with the signal lead attached. If boot fails, disconnect power and move BIN_1 back to GPIO4.

| Function | ESP32-CAM pin |
|---|---|
| BIN_1 MG90S PWM signal | GPIO12 / IO12 |
| BIN_2 MG90S PWM signal | GPIO13 / IO13 |
| BIN_3 MG90S PWM signal | GPIO14 / IO14 |
| USB-UART TXD → board U0R | U0R / UART0 RX |
| Board U0T → USB-UART RXD | U0T / UART0 TX |
| USB-UART GND / servo GND / board GND | Common ground |
| ESP32-CAM power | Board 5V and GND, using the owner's previously working wiring |
| Servo power | 5V rail and common GND, as in the owner's sequential-servo setup |

GPIO4 is left free, so the onboard flash LED remains available.

**Camera pin-map assumption:** `camera_pins.h` contains the common AI-Thinker camera map used by the supplied old sketch. OV3660 is the sensor; it does not alone identify the carrier-board wiring. If this sketch fails camera initialization, do not randomly swap pins—compare the board schematic and previous known-working sketch.

## What the firmware does

- Starts a Wi-Fi access point: SSID `Garbage`, password `12345678`, default AP address `192.168.4.1`.
- Serves `GET /health` and `GET /capture`; `/capture` returns one JPEG frame unless a servo is active, when it returns HTTP 423 `BUSY`.
- Accepts one newline-delimited Serial command at 115200 baud: `BIN_1`, `BIN_2`, or `BIN_3`.
- Replies `ACK BIN_n` when an automatic command starts, then moves only that servo to its saved open angle for 3 seconds, returns it to its saved closed angle and replies `DONE BIN_n`.
- Accepts manual angle commands: `MOVE BIN_n angle` (0-180), which moves to the requested angle and holds it; the firmware replies `ACK MOVE...` and `DONE MOVE...` immediately after issuing the servo position.
- Saves automatic endpoints in ESP32 NVS: `SET BIN_n CLOSED angle` and `SET BIN_n OPEN angle`; retrieves them with `GET_ANGLES`.
- Rejects parallel commands with `BUSY`. `UNKNOWN` and invalid commands do not move a servo.
- Uses GPIO12, GPIO13, GPIO14 for servo signals. Servo angle constants are initially 0° closed and 90° open, copied from the old sketch; verify each door's actual safe positions before attaching the mechanism.
- Leaves servos attached to hold the configured closed position. This can consume holding current; verify whether the mechanism needs torque while closed.
- Reserves LEDC timer 1 for ESP32Servo, leaving LEDC timer 0/channel 0 for camera XCLK.
- Keeps position endpoints across reboot. Starting values are 0° closed/90° open; calibrate them with the door linkage disconnected or unloaded first.

The sketch contains the local AP credentials (`Garbage` / `12345678`); it does not connect to an internet Wi-Fi network. Change the SSID/password in the sketch before use if desired.

## Arduino IDE setup

1. Install the Espressif ESP32 boards platform in Arduino IDE's Boards Manager.
2. Install the `ESP32Servo` library from Library Manager.
3. Open `HerbiBioS/HerbiBioS.ino`.
4. Select board **AI Thinker ESP32-CAM** (the pin map assumes this style), choose the USB-UART COM port and upload speed supported by the adapter.
5. For upload, connect IO0 to GND, reset/power-cycle the board, upload, then disconnect IO0 from GND and reset/power-cycle into normal mode.
6. UART wiring is crossed: adapter TXD → U0R, adapter RXD → U0T, and GND → GND. Do not connect adapter RX/TX by matching labels. For the owner's previously tested power arrangement, use the adapter's 5V/GND outputs and the board's marked 5V/GND pins; do not connect 5V, VCC, and 3V3 together.
7. Connect laptop Wi-Fi to `Garbage` with password `12345678`. Open `http://192.168.4.1/health`; when the camera is ready, the response is `{"status":"ready","busy":false,"camera_ready":true}`.
8. Keep servos mechanically unloaded for first boot. Check that `/capture` returns a JPEG and that the serial monitor shows `READY`.

Power arrangement is based on the owner's previously working 5V/GND USB-UART + breadboard setup and sequential servo operation. Keep the common ground. Verify that the adapter/USB supply and breadboard wiring stay stable with the ESP32-CAM, Wi-Fi, and one moving MG90S; stop if the board resets, image capture fails, or wires/adapter heat up. GPIO is signal only, never servo power. Confirm the USB-UART TX/RX logic level is compatible with ESP32 3.3V before connection.

## Laptop control dashboard

From `laptop_ai`, install dependencies and start the local dashboard:

```powershell
python -m pip install -r requirements.txt
python control_panel.py
```

Open `http://127.0.0.1:5000` in the laptop browser. Connect the laptop to Wi-Fi `Garbage`. The dashboard has:

- **Manual mode:** choose a bin and send a test angle from 0° to 180°; save each bin's open and closed angle to ESP32 NVS.
- **Automatic AI mode:** polls `http://192.168.4.1/capture`, runs the waste and face models, and sends a BIN command only when detection is mapped and confidence is at least 55%.
- Camera check and annotated preview.

The software defaults to simulation. To permit physical movement, edit `laptop_ai/config.yaml` and explicitly set:

```yaml
project:
  actuation_enabled: true
serial:
  enabled: true
  dry_run: false
  port: COMx
```

Replace `COMx` with the USB-UART port. Close Arduino Serial Monitor before starting the dashboard because only one application can own the COM port. Start with manual mode and an unloaded servo. Servo angle values are configurable in the browser and saved by ESP32, so ordinary calibration does not require editing or reflashing the sketch.

The older command-line inference mode is still available with `python predict.py --source 0 --display --device cuda:0`.

In automatic mode, keep one item in view; the laptop rearms after five consecutive empty images. The ESP32 independently blocks parallel motion and rejects commands while busy.

## Known limits

- The sketch assumes AI-Thinker-style camera wiring and SD is not used.
- GPIO12 is a boot-strapping pin; verify repeatable boot with the servo signal attached. GPIO4 is kept free for the flash LED.
- Servo angles 0°/90° are starting values from the previous sketch, not mechanically certified.
- The Arduino sketch has not been compiled against the user's local Arduino core/library versions or tested on the physical board yet.
- The dashboard runs on localhost only; it is not exposed to the Wi-Fi network.
- Do not enable physical actuation until the pinout, servo endpoints, serial link, and camera stream are checked on the real hardware.
