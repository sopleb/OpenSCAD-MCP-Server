"""Introspection of the OpenSCAD shape library.

Module signatures are read from basic_shapes.scad rather than mirrored in a
Python dict. A hand-maintained copy drifted from the .scad file: torus was
listed with major_radius and minor_radius while the module takes outer_radius
and inner_radius, and cone was listed with base_radius against the module's
bottom_radius. Both produced calls OpenSCAD rejects.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODULE_PATTERN = re.compile(r"^module\s+(\w+)\s*\((.*?)\)\s*\{", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class ShapeParameter:
    name: str
    default: Any


@dataclass(frozen=True)
class Shape:
    name: str
    parameters: tuple[ShapeParameter, ...]

    @property
    def parameter_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.parameters)

    def signature(self) -> str:
        args = ", ".join(f"{p.name}={_render(p.default)}" for p in self.parameters)
        return f"{self.name}({args})"

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.parameters}


@functools.lru_cache(maxsize=8)
def load_library(templates_dir: str) -> dict[str, Shape]:
    """Parse every module in basic_shapes.scad, keyed by module name."""
    source = Path(templates_dir, "basic_shapes.scad").read_text()
    shapes: dict[str, Shape] = {}
    for name, raw_args in MODULE_PATTERN.findall(source):
        shapes[name] = Shape(name=name, parameters=tuple(_parse_args(raw_args)))
    return shapes


def render_signatures(templates_dir: str) -> str:
    """A signature list suitable for dropping into a prompt."""
    shapes = load_library(templates_dir)
    return "\n".join(f"  {shapes[name].signature()}" for name in sorted(shapes))


def validate_call(shape: Shape, parameters: dict[str, Any]) -> None:
    """Reject parameters the module does not declare, naming the valid ones."""
    unknown = sorted(set(parameters) - set(shape.parameter_names))
    if unknown:
        raise ValueError(
            f"{shape.name} has no parameter {', '.join(unknown)}. "
            f"It accepts: {', '.join(shape.parameter_names)}."
        )


def _parse_args(raw: str) -> list[ShapeParameter]:
    params = []
    for chunk in _split_args(raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, default = chunk.partition("=")
        params.append(ShapeParameter(name.strip(), _coerce(default.strip())))
    return params


def _split_args(raw: str) -> list[str]:
    """Split on commas that sit outside brackets and quotes."""
    parts, depth, quoted, current = [], 0, False, []
    for char in raw:
        if char == '"':
            quoted = not quoted
        elif not quoted and char in "[(":
            depth += 1
        elif not quoted and char in "])":
            depth -= 1
        if char == "," and depth == 0 and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _coerce(token: str) -> Any:
    if not token:
        return None
    if token in ("true", "false"):
        return token == "true"
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)
