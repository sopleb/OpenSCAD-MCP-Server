"""Prompt templates the calling client runs on its own model.

Registering these as MCP prompts is what makes the server model-agnostic: the
text goes to whichever model is connected. In Claude Code they appear as
/mcp__openscad__<name>.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

UNITS_RULE = (
    "All dimensions are millimetres. Convert any other unit before emitting code "
    "and state the conversion in a comment."
)

PRINTABILITY_RULE = (
    "Keep the solid manifold: no zero-thickness walls, no coincident faces on "
    "booleans, and overlap unioned parts by at least 0.01mm."
)


def register_prompts(mcp, shape_library: str) -> None:
    """Attach the prompt set to an MCPServer instance.

    `shape_library` is the rendered signature list from list_shape_library, so
    the caller writes code against modules that exist rather than guessing.
    """

    @mcp.prompt(title="Design a SCAD model")
    def design_scad_model(
        description: Annotated[str, Field(description="What the object should be")],
        constraints: Annotated[
            str, Field(description="Dimensions, tolerances, print limits")
        ] = "",
    ) -> str:
        """Turn a description into parametric OpenSCAD, then build it."""
        return f"""Write OpenSCAD source for: {description}

{("Constraints: " + constraints) if constraints else "No constraints were given; choose sensible defaults and say what you chose."}

{UNITS_RULE}
{PRINTABILITY_RULE}

Declare every dimension as a named variable at the top of the file so the model
stays parametric. Use these library modules where they fit, via
include <basic_shapes.scad>:

{shape_library}

Call create_model_from_scad with the finished source. If it returns compiler
diagnostics, fix the reported line and call it again."""

    @mcp.prompt(title="Parametrize SCAD")
    def parametrize_scad(
        scad_code: Annotated[str, Field(description="OpenSCAD source with literals")],
    ) -> str:
        """Lift hardcoded numbers in existing SCAD into named parameters."""
        return f"""Rewrite this OpenSCAD source so every meaningful dimension is a
named variable declared at the top, with a comment giving its purpose and range.

Leave the rendered geometry identical. Group related variables and keep
derived values as expressions over the parameters rather than as new literals.
{UNITS_RULE}

```openscad
{scad_code}
```

Call create_model_from_scad with the result to confirm it still compiles."""

    @mcp.prompt(title="Plan a photo capture")
    def plan_capture_setup(
        object_description: Annotated[str, Field(description="The object to capture")],
        num_views: Annotated[str, Field(description="How many photographs")] = "12",
    ) -> str:
        """Tell the user what photographs the reconstruction needs."""
        return f"""Explain how to photograph this object for multi-view stereo
reconstruction: {object_description}

Cover {num_views} views. The reconstruction is photogrammetry, so it needs real
parallax between shots and recovers nothing from synthetic or single-viewpoint
images. Address:

- Camera path and spacing, with enough overlap between neighbouring shots
- Lighting that avoids specular highlights and hard shadows
- Fixed focal length and focus across the whole set
- Background and turntable choices, and why a textureless object fails
- How to recover camera poses with COLMAP, since reconstruct_from_images needs
  either a sparse/ reconstruction directory or explicit poses

Then tell the user to call reconstruct_from_images with the photo directory."""
