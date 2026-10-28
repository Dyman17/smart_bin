from __future__ import annotations

import os
from urllib.request import Request, urlopen
from typing import Optional, Union

import cv2
import numpy as np


class CameraSource:
    def __init__(self, source: Union[int, str] = 0, width: int = 640, height: int = 480, fps: Optional[int] = None):
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps
        self.capture: Optional[cv2.VideoCapture] = None

    def open(self) -> cv2.VideoCapture:
        if isinstance(self.source, str) and os.path.isfile(self.source):
            self.capture = cv2.VideoCapture(self.source)
        else:
            self.capture = cv2.VideoCapture(int(self.source) if str(self.source).isdigit() else str(self.source))

        if not self.capture.isOpened():
            raise RuntimeError(f"Unable to open camera source '{self.source}'.")

        if self.width and self.height:
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
        if self.fps:
            self.capture.set(cv2.CAP_PROP_FPS, float(self.fps))
        return self.capture

    def read_frame(self) -> np.ndarray:
        if isinstance(self.source, str) and self.source.lower().startswith(("http://", "https://")):
            request = Request(self.source, headers={"Cache-Control": "no-cache"})
            try:
                with urlopen(request, timeout=5.0) as response:
                    image_bytes = response.read()
            except OSError as exc:
                raise RuntimeError(f"Unable to fetch a camera image from '{self.source}'.") from exc
            encoded = np.frombuffer(image_bytes, dtype=np.uint8)
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError(f"Camera endpoint '{self.source}' did not return a valid image.")
            return frame

        if self.capture is None or not self.capture.isOpened():
            self.open()
        ok, frame = self.capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Failed to read a frame from camera source '{self.source}'.")
        return frame

    def release(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
