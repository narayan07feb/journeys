"""YAML loading that keeps the line of each mapping (as `__line__`, 1-based) for error messages."""
from __future__ import annotations

from pathlib import Path

import yaml

LINE = "__line__"


class _LineLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        m = super().construct_mapping(node, deep=deep)
        m[LINE] = node.start_mark.line + 1
        return m


def load(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=_LineLoader)


def strip_lines(obj):
    if isinstance(obj, dict):
        return {k: strip_lines(v) for k, v in obj.items() if k != LINE}
    if isinstance(obj, list):
        return [strip_lines(v) for v in obj]
    return obj


def where(path: Path, obj) -> str:
    line = obj.get(LINE) if isinstance(obj, dict) else None
    return f"{path}:{line}" if line else str(path)
