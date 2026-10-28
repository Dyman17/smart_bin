from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import yaml

from src.camera import CameraSource
from src.classifier_mapping import build_mapping
from src.detector import WasteDetector
from src.serial_controller import SerialController
from src.sorting_logic import evaluate_detection, format_command, should_trigger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the waste-sorting robot application.")
    parser.add_argument("--config", type=str, default="config.yaml", help="Project configuration file.")
    parser.add_argument("--mode", choices=["test", "robot"], default=None, help="Application mode. Default is test mode unless config says otherwise.")
    return parser.parse_args()


def load_config(config_path: Path):
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    config = load_config(config_path)

    project_cfg = config.get("project", {})
    mode = args.mode or project_cfg.get("mode", "test")
    logging.basicConfig(level=getattr(logging, str(project_cfg.get("log_level", "INFO")).upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

    actuation_enabled = bool(project_cfg.get("actuation_enabled", False))
    if mode == "robot" and not actuation_enabled:
        logging.warning("Robot mode was requested but actuation is disabled in config. The system will run in TEST mode only.")
        mode = "test"

    model_cfg = config.get("model", {})
    face_cfg = config.get("face_filter", {})
    camera_cfg = config.get("camera", {})
    mapping_cfg = config.get("mapping", {}).get("classes", {})
    serial_cfg = config.get("serial", {})

    detector = WasteDetector(
        model_path=str(model_cfg.get("path", "yolov8s.pt")),
        device=str(model_cfg.get("device", "auto")),
        conf_threshold=float(model_cfg.get("conf_threshold", 0.55)),
        imgsz=int(model_cfg.get("imgsz", 640)),
        iou_threshold=float(model_cfg.get("iou_threshold", 0.45)),
        max_det=int(model_cfg.get("max_det", 10)),
        face_model_path=str(face_cfg.get("model_path")) if face_cfg.get("enabled", False) else None,
        face_conf_threshold=float(face_cfg.get("confidence_threshold", 0.55)),
        face_overlap_threshold=float(face_cfg.get("overlap_threshold", 0.25)),
    )
    camera = CameraSource(
        source=camera_cfg.get("source", 0),
        width=int(camera_cfg.get("width", 640)),
        height=int(camera_cfg.get("height", 480)),
        fps=camera_cfg.get("fps"),
    )

    controller = SerialController(
        port=str(serial_cfg.get("port", "COM3")),
        baudrate=int(serial_cfg.get("baudrate", 115200)),
        timeout=float(serial_cfg.get("timeout", 1.0)),
        dry_run=bool(serial_cfg.get("dry_run", True)),
        enabled=bool(serial_cfg.get("enabled", False)) and mode == "robot",
    )

    mapping = build_mapping(mapping_cfg)
    last_bin_name = None
    last_trigger_time = None
    cooldown_seconds = float(project_cfg.get("cooldown_seconds", 2.0))
    empty_frames_to_rearm = int(project_cfg.get("empty_frames_to_rearm", 5))
    poll_interval_seconds = max(0.1, float(camera_cfg.get("poll_interval_seconds", 2.0)))
    empty_frames = 0
    item_latched = False

    try:
        while True:
            frame = camera.read_frame()
            detections = detector.predict_frame(frame)
            waste_detections = [detection for detection in detections if not detection.is_face]
            if not waste_detections:
                empty_frames += 1
                if item_latched and empty_frames >= empty_frames_to_rearm:
                    logging.info("Scene clear for %d frames; re-arming the sorter.", empty_frames)
                    item_latched = False
                    last_bin_name = None
                    last_trigger_time = None
                time.sleep(poll_interval_seconds)
                continue
            empty_frames = 0

            best = waste_detections[0]
            decision = evaluate_detection(best.class_name, best.confidence, mapping=mapping, conf_threshold=model_cfg.get("conf_threshold", 0.55))
            logging.info("Best detection: %s (%.3f) -> %s | reason: %s", best.class_name, best.confidence, decision.bin_name, decision.reason)

            if decision.bin_name == "UNKNOWN":
                logging.info("No safe sorting decision for class '%s'; no command will be sent.", best.class_name)
                time.sleep(poll_interval_seconds)
                continue
            if item_latched:
                logging.debug("An item was already sorted; waiting for the scene to clear.")
                time.sleep(poll_interval_seconds)
                continue

            if not should_trigger(decision, last_bin_name, last_trigger_time, cooldown_seconds=cooldown_seconds, now=time.monotonic()):
                logging.info("Cooldown active or duplicate command prevented for %s.", decision.bin_name)
                time.sleep(poll_interval_seconds)
                continue

            command = format_command(decision.bin_name)
            logging.info("Prepared message: %s", command)
            if mode == "robot" and actuation_enabled:
                controller.send_command(decision.bin_name)
            else:
                logging.info("TEST MODE: no physical command sent. Serial controller dry-run would transmit '%s'.", command)
            last_bin_name = decision.bin_name
            last_trigger_time = time.monotonic()
            item_latched = True
            time.sleep(poll_interval_seconds)
    finally:
        camera.release()
        controller.disconnect()


if __name__ == "__main__":
    main()
