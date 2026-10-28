from __future__ import annotations

from unittest.mock import patch

import cv2
import numpy as np
import pytest

from src.camera import CameraSource
from src.serial_controller import SerialController


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self) -> bytes:
        return self.body


class FakeSerialConnection:
    is_open = True

    def __init__(self, responses: list[bytes]):
        self.responses = iter(responses)
        self.written = bytearray()
        self.flushed = False

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def flush(self) -> None:
        self.flushed = True

    def readline(self) -> bytes:
        return next(self.responses, b"")


def test_camera_source_decodes_http_jpeg():
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok

    with patch("src.camera.urlopen", return_value=FakeResponse(encoded.tobytes())):
        frame = CameraSource("http://192.168.4.1/capture").read_frame()

    assert frame.shape == (24, 32, 3)


def test_serial_command_waits_for_matching_done():
    controller = SerialController(
        dry_run=False,
        enabled=True,
        completion_timeout=1.0,
    )
    connection = FakeSerialConnection([b"ACK BIN_2\n", b"DONE BIN_2\n"])
    controller.connection = connection

    assert controller.send_command("BIN_2") == "DONE BIN_2"
    assert bytes(connection.written) == b"BIN_2\n"
    assert connection.flushed


def test_serial_busy_response_is_not_reported_as_success():
    controller = SerialController(
        dry_run=False,
        enabled=True,
        completion_timeout=1.0,
    )
    controller.connection = FakeSerialConnection([b"BUSY\n"])

    with pytest.raises(RuntimeError, match="rejected command"):
        controller.send_command("BIN_1")
