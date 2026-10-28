from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import serial

from src.classifier_mapping import BIN_1, BIN_2, BIN_3, UNKNOWN

VALID_COMMANDS = {BIN_1, BIN_2, BIN_3, UNKNOWN}


class SerialController:
    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 115200,
        timeout: float = 1.0,
        completion_timeout: float = 6.0,
        dry_run: bool = True,
        enabled: bool = False,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.completion_timeout = completion_timeout
        self.dry_run = dry_run
        self.enabled = enabled
        self.connection: Optional[serial.Serial] = None
        self.logger = logging.getLogger(__name__)
        self._lock = threading.Lock()

    def connect(self) -> None:
        if self.dry_run or not self.enabled:
            self.logger.info("Serial controller is disabled or in dry-run mode; no serial connection opened.")
            return
        try:
            self.connection = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            self.connection.reset_input_buffer()
            self.logger.info("Connected to serial controller on %s at %s baud.", self.port, self.baudrate)
        except serial.SerialException as exc:
            raise RuntimeError(f"Unable to open serial port '{self.port}'.") from exc

    def disconnect(self) -> None:
        if self.connection is not None and self.connection.is_open:
            self.connection.close()
        self.connection = None

    def format_command(self, bin_name: str) -> str:
        normalized = str(bin_name).strip().upper()
        if normalized not in VALID_COMMANDS:
            raise ValueError(f"Unsupported command '{bin_name}'. Expected one of {sorted(VALID_COMMANDS)}.")
        return f"{normalized}\n"

    def send_command(self, bin_name: str) -> str:
        command = self.format_command(bin_name).strip()
        return self.transact(command, {f"DONE {command}"}, self.completion_timeout)

    def move_servo(self, bin_name: str, angle: int) -> str:
        normalized = str(bin_name).strip().upper()
        if normalized not in {BIN_1, BIN_2, BIN_3}:
            raise ValueError(f"Unsupported bin '{bin_name}'.")
        if not 0 <= angle <= 180:
            raise ValueError("Servo angle must be between 0 and 180 degrees.")
        command = f"MOVE {normalized} {angle}"
        return self.transact(command, {f"DONE {command}"}, self.completion_timeout)

    def set_servo_position(self, bin_name: str, position: str, angle: int) -> str:
        normalized = str(bin_name).strip().upper()
        normalized_position = str(position).strip().upper()
        if normalized not in {BIN_1, BIN_2, BIN_3}:
            raise ValueError(f"Unsupported bin '{bin_name}'.")
        if normalized_position not in {"OPEN", "CLOSED"}:
            raise ValueError("Servo position must be OPEN or CLOSED.")
        if not 0 <= angle <= 180:
            raise ValueError("Servo angle must be between 0 and 180 degrees.")
        command = f"SET {normalized} {normalized_position} {angle}"
        return self.transact(command, {f"OK {command}"}, self.timeout + 1.0)

    def get_servo_positions(self) -> str:
        return self.transact("GET_ANGLES", {"ANGLES"}, self.timeout + 1.0)

    def transact(self, command: str, expected_responses: set[str], timeout: float) -> str:
        line = f"{command.strip()}\n"
        if self.dry_run or not self.enabled:
            self.logger.info("DRY_RUN: would send %s", command.strip())
            return f"DRY_RUN {command.strip()}"

        with self._lock:
            if self.connection is None:
                self.connect()

            if self.connection is None:
                raise RuntimeError("Serial connection is not available.")

            try:
                self.connection.write(line.encode("utf-8"))
                self.connection.flush()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    response = self.connection.readline().decode("utf-8", errors="replace").strip()
                    if not response:
                        continue
                    self.logger.info("Controller response: %s", response)
                    if response in expected_responses or any(
                        response.startswith(f"{expected} ")
                        for expected in expected_responses
                    ):
                        return response
                    if response == "BUSY" or response.startswith("ERR "):
                        raise RuntimeError(f"ESP32 rejected command: {response}")

                raise TimeoutError(
                    f"Timed out waiting for ESP32 response to '{command.strip()}' within {timeout:.1f} seconds."
                )
            except serial.SerialTimeoutException:
                self.logger.warning("Serial write timed out on port %s.", self.port)
                raise
            except serial.SerialException as exc:
                self.logger.exception("Serial error while sending command.")
                raise RuntimeError("Serial communication failed while sending a command.") from exc
