from __future__ import annotations

from pathlib import Path
from typing import Any


def load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime safeguard
        raise RuntimeError("PyYAML is required to load YAML config. Install with 'pip install pyyaml'.") from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Config YAML must be a mapping (key/value pairs).")
    return data
