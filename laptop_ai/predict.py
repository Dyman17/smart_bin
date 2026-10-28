from __future__ import annotations

import argparse
import os
from pathlib import Path

import cv2

from src.detector import WasteDetector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run object detection on an image, webcam stream or video source.")
    parser.add_argument("--source", type=str, default="0", help="Image path, a video file or webcam index (0, 1, ...). For ESP32-CAM, set the stream URL here.")
    parser.add_argument("--weights", type=str, default="../models/waste_yolov8s_best.pt", help="Model weights path.")
    parser.add_argument("--conf", type=float, default=0.55, help="Minimum confidence to show a detection (default: 0.55).")
    parser.add_argument("--imgsz", type=int, default=640, help="Input image size for inference.")
    parser.add_argument("--device", type=str, default="auto", help="Device name, e.g. 'cuda:0' or 'cpu'.")
    parser.add_argument("--face-weights", type=str, default="../models/yolov8n-face.pt", help="Optional face detector weights used to suppress waste boxes overlapping a face.")
    parser.add_argument("--no-face-filter", action="store_true", help="Disable the secondary face detector.")
    parser.add_argument("--display", action="store_true", help="Display annotated frames in a window.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    detector = WasteDetector(
        model_path=args.weights,
        device=args.device,
        conf_threshold=args.conf,
        imgsz=args.imgsz,
        face_model_path=None if args.no_face_filter else args.face_weights,
    )
    source = args.source

    if os.path.isfile(source):
        image = cv2.imread(source)
        if image is None:
            raise FileNotFoundError(f"Could not read image '{source}'.")
        detections = detector.predict_image(image, conf_threshold=args.conf)
        annotated = detector.draw_detections(image, detections)
        output_path = Path("predictions") / "output.jpg"
        output_path.parent.mkdir(exist_ok=True, parents=True)
        cv2.imwrite(str(output_path), annotated)
        print(f"Saved annotated image to {output_path}")
        for detection in detections:
            print(f"{detection.class_name}: {detection.confidence:.3f} @ [{detection.x1}, {detection.y1}, {detection.x2}, {detection.y2}]")
        if args.display:
            cv2.imshow("Waste detection", annotated)
            cv2.waitKey(0)
        return

    capture = cv2.VideoCapture(int(source) if str(source).isdigit() else str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open source '{source}'.")

    while True:
        ok, frame = capture.read()
        if not ok:
            break
        detections = detector.predict_frame(frame, conf_threshold=args.conf)
        annotated = detector.draw_detections(frame, detections)
        if args.display:
            cv2.imshow("Waste detection", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        for detection in detections[:5]:
            print(f"{detection.class_name}: {detection.confidence:.3f}")
    capture.release()
    if args.display:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
