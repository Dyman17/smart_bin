from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Mapping

from src.classifier_mapping import UNKNOWN, build_mapping, resolve_bin_for_class


@dataclass
class SortingDecision:
    class_name: str
    confidence: float
    bin_name: str
    reason: str

    @property
    def valid(self) -> bool:
        return self.bin_name not in {"", UNKNOWN} and self.confidence > 0.0


def evaluate_detection(class_name: str, confidence: float, mapping: Mapping[str, str] | None = None, conf_threshold: float = 0.35) -> SortingDecision:
    normalized_name = str(class_name).strip()
    if confidence < conf_threshold:
        return SortingDecision(
            class_name=normalized_name,
            confidence=float(confidence),
            bin_name=UNKNOWN,
            reason=f"confidence {confidence:.3f} below threshold {conf_threshold:.3f}",
        )

    selected_mapping = build_mapping(mapping) if mapping else None
    bin_name = resolve_bin_for_class(normalized_name, selected_mapping)
    if bin_name == UNKNOWN:
        return SortingDecision(
            class_name=normalized_name,
            confidence=float(confidence),
            bin_name=UNKNOWN,
            reason=f"class '{normalized_name}' is not safely mappable to the three sorting bins",
        )

    return SortingDecision(
        class_name=normalized_name,
        confidence=float(confidence),
        bin_name=bin_name,
        reason=f"class '{normalized_name}' mapped to {bin_name}",
    )


def format_command(bin_name: str) -> str:
    normalized = str(bin_name).strip().upper()
    if normalized not in {"BIN_1", "BIN_2", "BIN_3", UNKNOWN}:
        raise ValueError(f"Unsupported bin name '{bin_name}'.")
    return normalized.strip()


def should_trigger(decision: SortingDecision, last_bin_name: str | None, last_trigger_time: float | None, cooldown_seconds: float = 2.0, now: float | None = None) -> bool:
    if not decision.valid:
        return False
    if decision.bin_name == UNKNOWN:
        return False
    if last_bin_name == decision.bin_name and last_trigger_time is not None:
        if now is None:
            now = monotonic()
        if (now - last_trigger_time) < cooldown_seconds:
            return False
    return True
