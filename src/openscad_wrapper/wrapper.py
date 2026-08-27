"""Driver for the OpenSCAD command line.

Writes SCAD source, compiles it, and renders previews. Shape templates live in
src/models/scad_templates/basic_shapes.scad rather than being duplicated here.
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from typing import Any

logger = logging.getLogger(__name__)

CAMERA_VIEWS = {
    "front": "0,0,0,0,0,0,50",
    "top": "0,0,0,90,0,0,50",
    "right": "0,0,0,0,90,0,50",
    "perspective": "40,30,30,55,0,25,100",
}

DEFAULT_IMAGE_SIZE = "800,600"


class OpenSCADNotFound(RuntimeError):
    """The openscad binary is missing from PATH and from OPENSCAD_PATH."""


class OpenSCADError(RuntimeError):
    """OpenSCAD rejected the source. Carries the compiler diagnostics."""

    def __init__(self, message: str, diagnostics: str = ""):
        super().__init__(message)
        self.diagnostics = diagnostics


class OpenSCADWrapper:
    """Runs OpenSCAD over SCAD files and collects its output."""

    def __init__(self, scad_dir: str, output_dir: str, openscad_path: str = "openscad"):
        self.scad_dir = scad_dir
        self.output_dir = output_dir
        self.openscad_path = openscad_path
        self.stl_dir = os.path.join(output_dir, "stl")
        self.preview_dir = os.path.join(output_dir, "preview")

        for directory in (self.scad_dir, self.stl_dir, self.preview_dir):
            os.makedirs(directory, exist_ok=True)

    def generate_scad(self, scad_code: str, model_id: str | None = None) -> str:
        """Write SCAD source to disk and return its path."""
        model_id = model_id or str(uuid.uuid4())
        scad_file = os.path.join(self.scad_dir, f"{model_id}.scad")
        with open(scad_file, "w") as f:
            f.write(scad_code)
        logger.info("Wrote %s", scad_file)
        return scad_file

    def validate(self, scad_file: str, parameters: dict[str, Any] | None = None) -> str:
        """Compile without keeping the output, to surface syntax and geometry errors.

        Returns the compiler's stderr, which holds warnings even on success.
        """
        null_output = os.path.join(self.output_dir, "validate.stl")
        result = self._run(["-o", null_output], scad_file, parameters, check=False)
        if os.path.exists(null_output):
            os.remove(null_output)
        if result.returncode != 0:
            raise OpenSCADError(
                f"OpenSCAD rejected {os.path.basename(scad_file)}",
                diagnostics=result.stderr.strip(),
            )
        return result.stderr.strip()

    def generate_stl(self, scad_file: str, parameters: dict[str, Any] | None = None) -> str:
        model_id = os.path.splitext(os.path.basename(scad_file))[0]
        stl_file = os.path.join(self.stl_dir, f"{model_id}.stl")
        self._run(["-o", stl_file], scad_file, parameters)
        logger.info("Wrote %s", stl_file)
        return stl_file

    def generate_preview(
        self,
        scad_file: str,
        parameters: dict[str, Any] | None = None,
        camera_position: str = CAMERA_VIEWS["front"],
        image_size: str = DEFAULT_IMAGE_SIZE,
        suffix: str = "",
    ) -> str:
        model_id = os.path.splitext(os.path.basename(scad_file))[0]
        preview_file = os.path.join(self.preview_dir, f"{model_id}{suffix}.png")
        args = ["--camera", camera_position, "--imgsize", image_size, "-o", preview_file]
        result = self._run(args, scad_file, parameters, check=False)
        if result.returncode != 0 or not os.path.exists(preview_file):
            logger.warning(
                "Preview render failed for %s: %s", scad_file, result.stderr.strip()
            )
            return self._placeholder(preview_file, image_size)
        return preview_file

    def generate_multi_angle_previews(
        self, scad_file: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Render the four standard camera angles."""
        return {
            view: self.generate_preview(
                scad_file, parameters, camera_position=camera, suffix=f"_{view}"
            )
            for view, camera in CAMERA_VIEWS.items()
        }

    def _run(
        self,
        args: list[str],
        scad_file: str,
        parameters: dict[str, Any] | None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        cmd = [self.openscad_path, *args]
        for key, value in (parameters or {}).items():
            cmd.extend(["-D", f"{key}={_scad_literal(value)}"])
        cmd.append(scad_file)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise OpenSCADNotFound(
                f"Could not run {self.openscad_path!r}. Install OpenSCAD or set "
                f"OPENSCAD_PATH to the executable."
            ) from exc

        if check and result.returncode != 0:
            raise OpenSCADError(
                f"OpenSCAD exited with {result.returncode}",
                diagnostics=result.stderr.strip(),
            )
        return result

    def _placeholder(self, output_path: str, image_size: str) -> str:
        """Stand in for a render the headless environment could not produce."""
        try:
            from PIL import Image, ImageDraw

            width, height = (int(v) for v in image_size.split(","))
            img = Image.new("RGB", (width, height), color=(240, 240, 240))
            ImageDraw.Draw(img).text(
                (width // 2 - 60, height // 2), "Preview not available", fill=(0, 0, 0)
            )
            img.save(output_path)
        except Exception as exc:
            logger.error("Could not write placeholder image: %s", exc)
        return output_path


def _scad_literal(value: Any) -> str:
    """Render a Python value as an OpenSCAD -D literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_scad_literal(v) for v in value) + "]"
    return str(value)
