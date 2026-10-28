from __future__ import annotations

from typing import Dict, Iterable, Mapping

BIN_1 = "BIN_1"
BIN_2 = "BIN_2"
BIN_3 = "BIN_3"
UNKNOWN = "UNKNOWN"
VALID_BINS = {BIN_1, BIN_2, BIN_3, UNKNOWN}

DEFAULT_CLASS_TO_BIN: Dict[str, str] = {
    "plastic bottle": BIN_1,
    "plastic bag": BIN_1,
    "drink carton": BIN_1,
    "plastic bottle cap": BIN_1,
    "can": BIN_2,
    "pop tab": BIN_2,
    "cardboard": BIN_3,
    "paper": BIN_3,
    "battery": UNKNOWN,
    "glass bottle": UNKNOWN,
}


def normalize_class_name(name: str) -> str:
    if name is None:
        return ""
    return str(name).strip().lower()


def build_mapping(raw_mapping: Mapping[str, str] | None = None) -> Dict[str, str]:
    mapping = dict(DEFAULT_CLASS_TO_BIN)
    if raw_mapping:
        for raw_key, raw_value in raw_mapping.items():
            key = normalize_class_name(raw_key)
            value = str(raw_value).strip().upper()
            if value not in VALID_BINS:
                raise ValueError(f"Unsupported bin mapping '{raw_value}' for class '{raw_key}'.")
            mapping[key] = value
    return mapping


def resolve_bin_for_class(class_name: str, mapping: Mapping[str, str] | None = None) -> str:
    key = normalize_class_name(class_name)
    selected = (mapping or DEFAULT_CLASS_TO_BIN).get(key)
    if selected is None:
        return UNKNOWN
    return str(selected).upper()


def class_names_to_bins(class_names: Iterable[str], mapping: Mapping[str, str] | None = None) -> Dict[str, str]:
    selected = build_mapping(mapping) if mapping else DEFAULT_CLASS_TO_BIN
    return {name: resolve_bin_for_class(name, selected) for name in class_names}
