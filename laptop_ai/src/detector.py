from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from ultralytics import YOLO


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    is_face: bool = False


def _as_float_list(values: Any) -> List[float]:
    if isinstance(values, (list, tuple)):
        return [float(item) for item in values]
    return [float(values)]


def detection_overlaps_face(detection: Detection, face: Detection, minimum_detection_overlap: float = 0.25) -> bool:
    intersection_width = max(0.0, min(detection.x2, face.x2) - max(detection.x1, face.x1))
    intersection_height = max(0.0, min(detection.y2, face.y2) - max(detection.y1, face.y1))
    intersection_area = intersection_width * intersection_height
    detection_area = max(0.0, detection.x2 - detection.x1) * max(0.0, detection.y2 - detection.y1)
    if detection_area == 0.0:
        return False
    return intersection_area / detection_area >= minimum_detection_overlap


class WasteDetector:
    def __init__(
        self,
        model_path: str = "yolov8s.pt",
        device: str = "auto",
        conf_threshold: float = 0.35,
        imgsz: int = 640,
        iou_threshold: float = 0.45,
        max_det: int = 10,
        face_model_path: Optional[str] = None,
        face_conf_threshold: float = 0.35,
        face_overlap_threshold: float = 0.25,
    ):
        self.model_path = model_path
        self.device = device
        self.conf_threshold = conf_threshold
        self.imgsz = imgsz
        self.iou_threshold = iou_threshold
        self.max_det = max_det
        self.model = YOLO(model_path)
        self.model.to(self.device)
        self.face_model_path = face_model_path
        self.face_conf_threshold = face_conf_threshold
        self.face_overlap_threshold = face_overlap_threshold
        self.face_model = None
        if face_model_path:
            face_path = Path(face_model_path)
            if not face_path.is_file():
                raise FileNotFoundError(
                    f"Face detector weights not found at '{face_path}'. "
                    "Download yolov8n-face.pt into the configured path or disable face filtering."
                )
            self.face_model = YOLO(str(face_path))
            self.face_model.to(self.device)

    def predict_image(self, image: np.ndarray | str | Path, conf_threshold: Optional[float] = None) -> List[Detection]:
        threshold = self.conf_threshold if conf_threshold is None else conf_threshold
        if isinstance(image, (str, Path)):
            image_array = cv2.imread(str(image))
            if image_array is None:
                raise FileNotFoundError(f"Could not read image '{image}'.")
        else:
            image_array = image

        results = self.model(
            image_array,
            imgsz=self.imgsz,
            conf=threshold,
            iou=self.iou_threshold,
            max_det=self.max_det,
            verbose=False,
            device=self.device,
        )
        detections: List[Detection] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                xyxy = box.xyxy[0].tolist()
                cls_id = int(box.cls[0])
                confidence = float(box.conf[0])
                class_name = self.model.names.get(cls_id, str(cls_id))
                detections.append(
                    Detection(
                        class_id=cls_id,
                        class_name=class_name,
                        confidence=confidence,
                        x1=float(xyxy[0]),
                        y1=float(xyxy[1]),
                        x2=float(xyxy[2]),
                        y2=float(xyxy[3]),
                    )
                )
        if self.face_model is not None:
            face_results = self.face_model(
                image_array,
                imgsz=self.imgsz,
                conf=self.face_conf_threshold,
                iou=self.iou_threshold,
                max_det=self.max_det,
                verbose=False,
                device=self.device,
            )
            faces: List[Detection] = []
            for result in face_results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    xyxy = box.xyxy[0].tolist()
                    faces.append(
                        Detection(
                            class_id=int(box.cls[0]),
                            class_name="face (ignored)",
                            confidence=float(box.conf[0]),
                            x1=float(xyxy[0]),
                            y1=float(xyxy[1]),
                            x2=float(xyxy[2]),
                            y2=float(xyxy[3]),
                            is_face=True,
                        )
                    )
            detections = [
                detection
                for detection in detections
                if not any(
                    detection_overlaps_face(detection, face, self.face_overlap_threshold)
                    for face in faces
                )
            ]
            detections.extend(faces)
        return sorted(detections, key=lambda item: item.confidence, reverse=True)

    def predict_frame(self, frame: np.ndarray, conf_threshold: Optional[float] = None) -> List[Detection]:
        return self.predict_image(frame, conf_threshold=conf_threshold)

    def draw_detections(self, frame: np.ndarray, detections: List[Detection]) -> np.ndarray:
        annotated = frame.copy()
        for detection in detections:
            color = (255, 0, 255) if detection.is_face else (0, 255, 0)
            cv2.rectangle(
                annotated,
                (int(detection.x1), int(detection.y1)),
                (int(detection.x2), int(detection.y2)),
                color,
                2,
            )
            cv2.putText(
                annotated,
                f"{detection.class_name} {detection.confidence:.2f}",
                (int(detection.x1), max(20, int(detection.y1) - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )
        return annotated
