from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import yaml
from flask import Flask, jsonify, render_template, request

from src.camera import CameraSource
from src.classifier_mapping import build_mapping
from src.detector import Detection, WasteDetector
from src.serial_controller import SerialController
from src.sorting_logic import evaluate_detection


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
BIN_NAMES = ("BIN_1", "BIN_2", "BIN_3")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class ControlPanel:
    def __init__(self) -> None:
        self.config = load_config()
        self.project_config = self.config.get("project", {})
        self.camera_config = self.config.get("camera", {})
        self.model_config = self.config.get("model", {})
        self.face_config = self.config.get("face_filter", {})
        self.serial_config = self.config.get("serial", {})
        self.mapping = build_mapping(self.config.get("mapping", {}).get("classes", {}))
        self.hardware_enabled = (
            bool(self.project_config.get("actuation_enabled", False))
            and bool(self.serial_config.get("enabled", False))
            and not bool(self.serial_config.get("dry_run", True))
        )
        self.serial = SerialController(
            port=str(self.serial_config.get("port", "COM3")),
            baudrate=int(self.serial_config.get("baudrate", 115200)),
            timeout=float(self.serial_config.get("timeout", 1.0)),
            completion_timeout=float(self.serial_config.get("completion_timeout", 6.0)),
            dry_run=not self.hardware_enabled,
            enabled=self.hardware_enabled,
        )
        self.detector: WasteDetector | None = None
        self.camera = CameraSource(
            source=self.camera_config.get("source", "http://192.168.4.1/capture"),
            width=int(self.camera_config.get("width", 640)),
            height=int(self.camera_config.get("height", 480)),
            fps=self.camera_config.get("fps"),
        )
        self.lock = threading.RLock()
        self.camera_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.mode = "manual"
        self.auto_thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_detection: dict[str, Any] | None = None
        self.last_frame: bytes | None = None
        self.last_command: str | None = None
        self.last_command_at: float | None = None
        self.servo_angles = {
            name: {"closed": 0, "open": 90}
            for name in BIN_NAMES
        }
        self.logger = logging.getLogger("herbibios.control_panel")

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "mode": self.mode,
                "hardware_enabled": self.hardware_enabled,
                "camera_url": str(self.camera_config.get("source", "")),
                "last_error": self.last_error,
                "last_detection": self.last_detection,
                "last_command": self.last_command,
                "last_command_at": self.last_command_at,
                "servo_angles": self.servo_angles,
                "frame_ready": self.last_frame is not None,
            }

    def capture(self) -> Any:
        with self.camera_lock:
            return self.camera.read_frame()

    @staticmethod
    def resolve_model_path(value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        return str(path)

    def get_detector(self) -> WasteDetector:
        if self.detector is None:
            self.detector = WasteDetector(
                model_path=self.resolve_model_path(str(self.model_config["path"])),
                device=str(self.model_config.get("device", "auto")),
                conf_threshold=float(self.model_config.get("conf_threshold", 0.55)),
                imgsz=int(self.model_config.get("imgsz", 640)),
                iou_threshold=float(self.model_config.get("iou_threshold", 0.45)),
                max_det=int(self.model_config.get("max_det", 10)),
                face_model_path=(
                    self.resolve_model_path(str(self.face_config.get("model_path")))
                    if self.face_config.get("enabled", False)
                    else None
                ),
                face_conf_threshold=float(self.face_config.get("confidence_threshold", 0.55)),
                face_overlap_threshold=float(self.face_config.get("overlap_threshold", 0.25)),
            )
        return self.detector

    def start_mode(self, mode: str) -> None:
        if mode not in {"manual", "automatic"}:
            raise ValueError("Mode must be 'manual' or 'automatic'.")
        with self.lock:
            self.stop_event.set()
            self.mode = mode
            self.last_error = None
            if mode == "automatic":
                self.stop_event = threading.Event()
                self.auto_thread = threading.Thread(
                    target=self._automatic_loop,
                    args=(self.stop_event,),
                    name="HerbiBioSAutoMode",
                    daemon=True,
                )
                self.auto_thread.start()
            self.logger.info("Control panel switched to %s mode.", mode)

    def _automatic_loop(self, stop_event: threading.Event) -> None:
        empty_frames = 0
        item_latched = False
        poll_interval = max(
            0.5,
            float(self.camera_config.get("poll_interval_seconds", 2.0)),
        )
        try:
            detector = self.get_detector()
            while not stop_event.is_set():
                frame = self.capture()
                detections = detector.predict_frame(frame)
                waste = [item for item in detections if not item.is_face]
                annotated = detector.draw_detections(frame, detections)
                with self.lock:
                    ok, encoded = cv2.imencode(".jpg", annotated)
                    if ok:
                        self.last_frame = encoded.tobytes()

                if not waste:
                    empty_frames += 1
                    if item_latched and empty_frames >= int(
                        self.project_config.get("empty_frames_to_rearm", 5)
                    ):
                        item_latched = False
                        self.logger.info("Scene cleared; automatic mode is re-armed.")
                    stop_event.wait(poll_interval)
                    continue

                empty_frames = 0
                best: Detection = max(waste, key=lambda item: item.confidence)
                decision = evaluate_detection(
                    best.class_name,
                    best.confidence,
                    mapping=self.mapping,
                    conf_threshold=float(self.model_config.get("conf_threshold", 0.55)),
                )
                with self.lock:
                    self.last_detection = {
                        "class_name": best.class_name,
                        "confidence": best.confidence,
                        "bin": decision.bin_name,
                        "reason": decision.reason,
                    }

                if decision.bin_name == "UNKNOWN" or item_latched:
                    stop_event.wait(poll_interval)
                    continue
                if stop_event.is_set():
                    break

                self.logger.info(
                    "Automatic decision %s (%.3f) -> %s",
                    best.class_name,
                    best.confidence,
                    decision.bin_name,
                )
                response = self.serial.send_command(decision.bin_name)
                with self.lock:
                    self.last_command = response
                    self.last_command_at = time.time()
                item_latched = True
                stop_event.wait(poll_interval)
        except Exception as exc:
            self.logger.exception("Automatic mode stopped after an error.")
            with self.lock:
                self.last_error = str(exc)
                self.mode = "manual"
        finally:
            if stop_event is self.stop_event:
                stop_event.set()

    def manual_move(self, bin_name: str, angle: int) -> str:
        with self.lock:
            if self.mode != "manual":
                raise RuntimeError("Switch to manual mode before moving a servo.")
        response = self.serial.move_servo(bin_name, angle)
        if self.hardware_enabled:
            with self.lock:
                self.last_command = response
                self.last_command_at = time.time()
        return response

    def set_position(self, bin_name: str, position: str, angle: int) -> str:
        normalized_bin = bin_name.strip().upper()
        normalized_position = position.strip().lower()
        if normalized_bin not in BIN_NAMES:
            raise ValueError("Invalid bin.")
        if normalized_position not in {"open", "closed"}:
            raise ValueError("Position must be open or closed.")
        if not 0 <= angle <= 180:
            raise ValueError("Angle must be between 0 and 180 degrees.")
        with self.lock:
            if self.mode != "manual":
                raise RuntimeError("Switch to manual mode before changing servo positions.")

        response = self.serial.set_servo_position(
            normalized_bin,
            normalized_position,
            angle,
        )
        with self.lock:
            self.servo_angles[normalized_bin][normalized_position] = angle
            if self.hardware_enabled:
                self.last_command = response
                self.last_command_at = time.time()
        return response

    def refresh_servo_positions(self) -> dict[str, dict[str, int]]:
        if self.hardware_enabled:
            response = self.serial.get_servo_positions()
            parts = response.split()
            if len(parts) != 7 or parts[0] != "ANGLES":
                raise RuntimeError(f"Unexpected servo angle response: {response}")
            values = [int(value) for value in parts[1:]]
            with self.lock:
                for index, bin_name in enumerate(BIN_NAMES):
                    self.servo_angles[bin_name] = {
                        "closed": values[index * 2],
                        "open": values[index * 2 + 1],
                    }
        with self.lock:
            return {key: value.copy() for key, value in self.servo_angles.items()}

    @property
    def positions_read_from_device(self) -> bool:
        return self.hardware_enabled


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
app = Flask(__name__)
panel = ControlPanel()


@app.get("/")
def index():
    return render_template("control_panel.html")


@app.get("/api/state")
def get_state():
    return jsonify(panel.state())


@app.get("/api/frame.jpg")
def get_frame():
    with panel.lock:
        frame = panel.last_frame
    if frame is None:
        return ("No camera frame available. Start automatic mode first.", 404)
    return app.response_class(frame, mimetype="image/jpeg")


@app.get("/api/camera-check")
def camera_check():
    try:
        frame = panel.capture()
        with panel.lock:
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                raise RuntimeError("Could not encode captured camera frame.")
            panel.last_frame = encoded.tobytes()
        return jsonify({"ok": True, "width": int(frame.shape[1]), "height": int(frame.shape[0])})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.post("/api/mode")
def set_mode():
    payload = request.get_json(silent=True) or {}
    try:
        panel.start_mode(str(payload.get("mode", "")))
        return jsonify(panel.state())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/servo-angles")
def get_servo_angles():
    try:
        angles = panel.refresh_servo_positions()
        return jsonify(
            {
                "angles": angles,
                "from_device": panel.positions_read_from_device,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/servo/move")
def move_servo():
    payload = request.get_json(silent=True) or {}
    try:
        bin_name = str(payload.get("bin", ""))
        angle = int(payload.get("angle"))
        response = panel.manual_move(bin_name, angle)
        return jsonify(
            {
                "ok": True,
                "response": response,
                "physical": panel.hardware_enabled,
            }
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        panel.logger.exception("Manual servo command failed.")
        return jsonify({"error": str(exc)}), 502


@app.post("/api/servo/position")
def set_servo_position():
    payload = request.get_json(silent=True) or {}
    try:
        bin_name = str(payload.get("bin", ""))
        position = str(payload.get("position", ""))
        angle = int(payload.get("angle"))
        response = panel.set_position(bin_name, position, angle)
        return jsonify(
            {
                "ok": True,
                "response": response,
                "persisted": panel.hardware_enabled,
            }
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        panel.logger.exception("Saving servo position failed.")
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)
