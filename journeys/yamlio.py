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


def load_map(path: Path, errors: list[str]) -> dict:
    """A pack file whose top level is a map. A syntax error or another shape is an error line
    (file:line when YAML gives one) and an empty map, never a traceback."""
    try:
        raw = load(path)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        errors.append(f"{path}:{mark.line + 1 if mark else 1}: not valid YAML: {getattr(e, 'problem', None) or e}")
        return {}
    except (OSError, UnicodeDecodeError, ValueError) as e:  # ValueError: a value YAML cannot build (2026-02-30)
        errors.append(f"{path}: cannot read: {e}")
        return {}
    if raw is not None and not isinstance(raw, dict):
        errors.append(f"{path}:1: the file is a map (key: value), not a {type(raw).__name__}")
        return {}
    return raw or {}


def strip_lines(obj):
    if isinstance(obj, dict):
        return {k: strip_lines(v) for k, v in obj.items() if k != LINE}
    if isinstance(obj, list):
        return [strip_lines(v) for v in obj]
    return obj


def where(path: Path, obj) -> str:
    line = obj.get(LINE) if isinstance(obj, dict) else None
    return f"{path}:{line}" if line else str(path)
