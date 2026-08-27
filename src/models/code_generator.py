"""OpenSCAD source generation from validated shape calls.

Module names and parameters come from shape_library, which reads the .scad file
directly, so a call this module emits is a call OpenSCAD accepts.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from src.models.shape_library import load_library, validate_call

logger = logging.getLogger(__name__)

CSG_OPERATIONS = ("union", "difference", "intersection")


class CodeGenerator:
    """Emits parametric SCAD source against the shape library."""

    def __init__(self, scad_templates_dir: str, output_dir: str):
        self.scad_templates_dir = scad_templates_dir
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    @property
    def shapes(self) -> dict:
        return load_library(self.scad_templates_dir)

    def generate_code(
        self, shape: str, parameters: dict[str, Any], model_id: str | None = None
    ) -> tuple[str, str]:
        """Build SCAD source for one library shape.

        Returns the model id and the source. Unknown shapes and unknown
        parameters raise ValueError naming the valid options.
        """
        library = self.shapes
        if shape not in library:
            raise ValueError(
                f"Unknown shape {shape!r}. Available: {', '.join(sorted(library))}."
            )
        definition = library[shape]
        validate_call(definition, parameters)

        merged = {**definition.defaults(), **parameters}
        model_id = model_id or str(uuid.uuid4())

        lines = [
            f"// {shape}",
            f"include <{self._library_include()}>;",
            "",
            "// Parameters (millimetres)",
        ]
        lines += [f"{name} = {_literal(value)};" for name, value in merged.items()]
        args = ", ".join(f"{name}={name}" for name in merged)
        lines += ["", "// Model", f"{shape}({args});", ""]

        return model_id, "\n".join(lines)

    def combine_models(self, operations: list[dict[str, Any]], model_id: str | None = None) -> tuple[str, str]:
        """Chain library shapes with CSG operators.

        Each operation carries `shape`, `parameters`, an optional `operation`
        naming the CSG operator that opens a new block, and an optional
        `transform` prefix such as translate([0,0,5]).
        """
        if not operations:
            raise ValueError("combine_models needs at least one operation")

        library = self.shapes
        model_id = model_id or str(uuid.uuid4())
        lines = ["// Combined model", f"include <{self._library_include()}>;", ""]

        current_op = None
        for index, op in enumerate(operations):
            shape = op.get("shape")
            if shape not in library:
                raise ValueError(
                    f"Operation {index}: unknown shape {shape!r}. "
                    f"Available: {', '.join(sorted(library))}."
                )
            definition = library[shape]
            parameters = op.get("parameters", {})
            validate_call(definition, parameters)

            operation = op.get("operation")
            if operation and operation not in CSG_OPERATIONS:
                raise ValueError(
                    f"Operation {index}: {operation!r} is not one of {', '.join(CSG_OPERATIONS)}."
                )

            if operation and operation != current_op:
                if current_op:
                    lines.append("}")
                    lines.append("")
                current_op = operation
                lines.append(f"{operation}() {{")

            merged = {**definition.defaults(), **parameters}
            args = ", ".join(f"{k}={_literal(v)}" for k, v in merged.items())
            transform = op.get("transform")
            call = f"{shape}({args});"
            indent = "    " if current_op else ""
            lines.append(f"{indent}{transform + ' ' if transform else ''}{call}")

        if current_op:
            lines.append("}")
        lines.append("")

        return model_id, "\n".join(lines)

    def _library_include(self) -> str:
        return os.path.join(os.path.abspath(self.scad_templates_dir), "basic_shapes.scad")


def _literal(value: Any) -> str:
    """Render a Python value as OpenSCAD source."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    return str(value)
