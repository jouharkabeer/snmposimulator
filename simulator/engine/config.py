"""Load YAML configuration files used by the simulator engine."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings


@lru_cache(maxsize=1)
def load_simulator_config() -> dict[str, Any]:
    path: Path = settings.SIMULATOR_CONFIG_FILE
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


@lru_cache(maxsize=1)
def load_device_types() -> dict[str, Any]:
    path: Path = settings.SIMULATOR_DEVICE_TYPES_FILE
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not data:
        raise ValueError(f"No device types defined in {path}")
    return data


def get_device_type(name: str) -> dict[str, Any]:
    types = load_device_types()
    key = name.lower().strip()
    if key not in types:
        known = ", ".join(sorted(types))
        raise KeyError(f"Unknown device type '{name}'. Known types: {known}")
    spec = dict(types[key])
    spec["key"] = key
    return spec


def available_device_types() -> list[str]:
    return sorted(load_device_types().keys())
