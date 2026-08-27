"""OpenSCAD MCP server.

The server holds no API key and calls no model provider. Whatever client is
connected supplies the reasoning: it authors SCAD, chooses shape parameters, and
answers prompts registered here. The server compiles geometry, reconstructs
meshes on this machine, and drives printers.

Run it over stdio:  python -m src.main
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.shared.exceptions import NoBackChannelError
from pydantic import Field

from src.config import (
    CUDA_MVS_PATH,
    DELEGATION_TTL_SECONDS,
    IMAGE_APPROVAL,
    MODELS_DIR,
    MULTI_VIEW,
    OPENSCAD_PATH,
    OUTPUT_DIR,
    REMOTE_CUDA_MVS,
    SCAD_DIR,
    TEMPLATES_DIR,
    ensure_directories,
)
from src.mcp_bridge import DelegationStore, PendingNotFound, register_prompts
from src.models.code_generator import CodeGenerator
from src.models.cuda_mvs import (
    CUDAMultiViewStereo,
    CUDAMVSNotInstalled,
    MissingCameraPoses,
)
from src.models.shape_library import load_library, render_signatures
from src.openscad_wrapper.wrapper import OpenSCADError, OpenSCADNotFound, OpenSCADWrapper
from src.utils.cad_exporter import CADExporter
from src.workflow.image_approval import ImageApprovalTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,  # stdout carries the MCP protocol
)
logger = logging.getLogger(__name__)

ensure_directories()

mcp = MCPServer(
    "openscad",
    title="OpenSCAD",
    instructions=(
        "Parametric 3D modelling with OpenSCAD, photogrammetric reconstruction, "
        "and 3D printing. This server runs no AI model of its own. Write the "
        "OpenSCAD yourself and pass it to create_model_from_scad, or pick a "
        "library shape with create_model_from_shape. Call list_shape_library "
        "first to see what the library offers."
    ),
)

# Registry of models built this session, keyed by model_id.
models: dict[str, dict[str, Any]] = {}
remote_jobs: dict[str, dict[str, Any]] = {}
printers: dict[str, dict[str, Any]] = {}

delegation = DelegationStore(ttl_seconds=DELEGATION_TTL_SECONDS)

code_generator = CodeGenerator(TEMPLATES_DIR, SCAD_DIR)
openscad = OpenSCADWrapper(SCAD_DIR, OUTPUT_DIR, openscad_path=OPENSCAD_PATH)
cad_exporter = CADExporter(openscad_path=OPENSCAD_PATH)
approval_tool = ImageApprovalTool(IMAGE_APPROVAL["APPROVED_IMAGES_DIR"])

_cuda_mvs: CUDAMultiViewStereo | None = None
_printer_interface = None
_remote_manager = None


def get_cuda_mvs() -> CUDAMultiViewStereo:
    """Build the reconstruction wrapper on first use.

    Deferred so a machine without CUDA MVS still serves the OpenSCAD tools.
    """
    global _cuda_mvs
    if _cuda_mvs is None:
        _cuda_mvs = CUDAMultiViewStereo(CUDA_MVS_PATH, MODELS_DIR)
    return _cuda_mvs


def get_printer_interface():
    global _printer_interface
    if _printer_interface is None:
        from src.printer_discovery.printer_discovery import PrinterDiscovery, PrinterInterface

        _printer_interface = PrinterInterface(PrinterDiscovery())
    return _printer_interface


def get_remote_manager():
    """Connect to a LAN CUDA MVS server, or return None when disabled."""
    global _remote_manager
    if not REMOTE_CUDA_MVS["ENABLED"]:
        return None
    if _remote_manager is None:
        from src.remote.connection_manager import CUDAMVSConnectionManager

        _remote_manager = CUDAMVSConnectionManager(
            api_key=REMOTE_CUDA_MVS["API_KEY"],
            discovery_port=REMOTE_CUDA_MVS["DISCOVERY_PORT"],
            use_lan_discovery=REMOTE_CUDA_MVS["USE_LAN_DISCOVERY"],
            server_url=REMOTE_CUDA_MVS["SERVER_URL"] or None,
        )
    return _remote_manager


register_prompts(mcp, render_signatures(TEMPLATES_DIR))


# --------------------------------------------------------------------------
# Authoring
# --------------------------------------------------------------------------

@mcp.tool()
def list_shape_library() -> dict[str, Any]:
    """List the OpenSCAD modules available to create_model_from_shape.

    Read this before choosing a shape so parameter names match the library.
    """
    library = load_library(TEMPLATES_DIR)
    return {
        "include_path": os.path.join(TEMPLATES_DIR, "basic_shapes.scad"),
        "units": "millimetres",
        "shapes": {
            name: {
                "signature": shape.signature(),
                "parameters": shape.defaults(),
            }
            for name, shape in sorted(library.items())
        },
    }


@mcp.tool()
def create_model_from_scad(
    scad_code: Annotated[str, Field(description="Complete OpenSCAD source")],
    name: Annotated[str, Field(description="Short name for the model")] = "",
    parameters: Annotated[
        dict[str, Any] | None,
        Field(description="Values overriding variables in the source, via -D"),
    ] = None,
) -> dict[str, Any]:
    """Compile OpenSCAD source you wrote into a model.

    On a compile error this returns the compiler diagnostics rather than
    raising, so you can fix the reported line and call again.
    """
    model_id = _new_model_id(name)
    scad_file = openscad.generate_scad(scad_code, model_id)

    try:
        warnings = openscad.validate(scad_file, parameters)
    except OpenSCADError as exc:
        return {
            "status": "compile_error",
            "model_id": model_id,
            "scad_file": scad_file,
            "diagnostics": exc.diagnostics,
            "hint": "Fix the reported line and call create_model_from_scad again.",
        }
    except OpenSCADNotFound as exc:
        return {"status": "openscad_missing", "error": str(exc)}

    models[model_id] = {
        "model_id": model_id,
        "scad_file": scad_file,
        "parameters": parameters or {},
        "source": "caller",
    }
    return {
        "status": "ok",
        "model_id": model_id,
        "scad_file": scad_file,
        "warnings": warnings,
    }


@mcp.tool()
def create_model_from_shape(
    shape: Annotated[str, Field(description="Module name from list_shape_library")],
    parameters: Annotated[
        dict[str, Any] | None, Field(description="Module parameters in millimetres")
    ] = None,
    name: Annotated[str, Field(description="Short name for the model")] = "",
) -> dict[str, Any]:
    """Build a model from one library shape.

    Unknown shapes and unknown parameters come back as an error naming the
    valid options.
    """
    try:
        model_id, scad_code = code_generator.generate_code(
            shape, parameters or {}, model_id=_new_model_id(name)
        )
    except ValueError as exc:
        return {"status": "invalid_request", "error": str(exc)}

    return _build(model_id, scad_code, parameters or {}, source=shape)


@mcp.tool()
def combine_shapes(
    operations: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Ordered CSG steps. Each carries shape, parameters, an optional "
                "operation (union, difference, intersection) opening a new block, "
                "and an optional transform such as translate([0,0,5])"
            )
        ),
    ],
    name: Annotated[str, Field(description="Short name for the model")] = "",
) -> dict[str, Any]:
    """Chain library shapes with CSG operators into one model."""
    try:
        model_id, scad_code = code_generator.combine_models(
            operations, model_id=_new_model_id(name)
        )
    except ValueError as exc:
        return {"status": "invalid_request", "error": str(exc)}

    return _build(model_id, scad_code, {}, source="combined")


@mcp.tool()
def render_previews(
    model_id: Annotated[str, Field(description="Model id from a create tool")],
) -> dict[str, Any]:
    """Render front, top, right, and perspective views of a model."""
    model = models.get(model_id)
    if model is None:
        return {"status": "not_found", "error": f"No model {model_id!r} in this session."}

    previews = openscad.generate_multi_angle_previews(
        model["scad_file"], model.get("parameters")
    )
    model["previews"] = previews
    return {"status": "ok", "model_id": model_id, "previews": previews}


@mcp.tool()
def export_model(
    model_id: Annotated[str, Field(description="Model id from a create tool")],
    format: Annotated[
        Literal["stl", "csg", "amf", "3mf", "scad", "off", "dxf", "svg"],
        Field(description="Target format"),
    ] = "stl",
) -> dict[str, Any]:
    """Export a model. csg, scad, amf, and 3mf keep parametric detail; stl does not."""
    model = models.get(model_id)
    if model is None:
        return {"status": "not_found", "error": f"No model {model_id!r} in this session."}

    success, output_file, error = cad_exporter.export_model(
        model["scad_file"], format, model.get("parameters")
    )
    if not success:
        return {"status": "export_failed", "model_id": model_id, "error": error}

    model.setdefault("exports", {})[format] = output_file
    return {
        "status": "ok",
        "model_id": model_id,
        "format": format,
        "file": output_file,
        "size_bytes": os.path.getsize(output_file),
    }


@mcp.tool()
def get_model(
    model_id: Annotated[str, Field(description="Model id from a create tool")],
) -> dict[str, Any]:
    """Return what this session knows about a model, including its SCAD source."""
    model = models.get(model_id)
    if model is None:
        return {"status": "not_found", "error": f"No model {model_id!r} in this session."}

    record = dict(model)
    scad_file = record.get("scad_file")
    if scad_file and os.path.exists(scad_file):
        with open(scad_file) as f:
            record["scad_code"] = f.read()
    return {"status": "ok", **record}


# --------------------------------------------------------------------------
# Reconstruction
# --------------------------------------------------------------------------

@mcp.tool()
async def reconstruct_from_images(
    image_paths: Annotated[list[str], Field(description="Photographs of one object")],
    ctx: Context,
    camera_params: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Explicit camera poses, one per image"),
    ] = None,
    colmap_sparse_dir: Annotated[
        str, Field(description="COLMAP sparse/0 directory holding cameras and images")
    ] = "",
    output_name: Annotated[str, Field(description="Name for the reconstruction")] = "",
    mesh: Annotated[bool, Field(description="Mesh the point cloud into an OBJ")] = True,
) -> dict[str, Any]:
    """Reconstruct a 3D model from photographs using multi-view stereo.

    Runs on this machine as a compiled binary. This is photogrammetry, so it
    needs real photographs taken from different positions, and camera poses with
    a baseline between them. Generated or single-viewpoint images do not
    reconstruct. Call plan_capture_setup for guidance on shooting a usable set.
    """
    output_name = output_name or f"recon_{uuid.uuid4().hex[:8]}"
    num_views = len(image_paths)
    if num_views < MULTI_VIEW["MIN_NUM_VIEWS"]:
        return {
            "status": "invalid_request",
            "error": (
                f"{num_views} images given; reconstruction needs at least "
                f"{MULTI_VIEW['MIN_NUM_VIEWS']}."
            ),
        }

    cuda_mvs = get_cuda_mvs()
    await _progress(ctx, 0.1, f"Reconstructing from {num_views} views")

    try:
        result = cuda_mvs.generate_model_from_images(
            image_paths=image_paths,
            camera_params=camera_params,
            colmap_sparse_dir=colmap_sparse_dir or None,
            output_name=output_name,
        )
    except CUDAMVSNotInstalled as exc:
        return {"status": "cuda_mvs_missing", "error": str(exc)}
    except MissingCameraPoses as exc:
        return {"status": "missing_camera_poses", "error": str(exc)}
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return {"status": "reconstruction_failed", "error": str(exc)}

    await _progress(ctx, 0.7, "Meshing point cloud")

    if mesh:
        try:
            result["mesh_file"] = cuda_mvs.convert_ply_to_obj(result["point_cloud_file"])
        except (RuntimeError, ValueError) as exc:
            result["mesh_error"] = str(exc)

    await _progress(ctx, 1.0, "Done")
    result["status"] = "ok"
    return result


@mcp.tool()
async def approve_images(
    image_paths: Annotated[list[str], Field(description="Images to review")],
    ctx: Context,
) -> dict[str, Any]:
    """Ask the user which images to keep for reconstruction.

    Asks through the connected client, one image at a time. Cancelling stops the
    review and keeps whatever was approved so far.
    """
    approved, rejected = [], []
    for index, path in enumerate(image_paths, start=1):
        try:
            outcome = await approval_tool.request_approval(
                ctx, path, label=f"image {index} of {len(image_paths)} ({os.path.basename(path)})"
            )
        except NoBackChannelError:
            # The transport carries no server-initiated requests, so the server
            # cannot ask. Hand the decision to the caller instead of failing.
            return {
                "status": "elicitation_unavailable",
                "images": image_paths,
                "hint": (
                    "This client accepts no server-initiated prompts. Review the "
                    "images yourself, then pass the ones to keep straight to "
                    "reconstruct_from_images."
                ),
            }
        if outcome["action"] == "cancel":
            return {
                "status": "cancelled",
                "approved": approved,
                "rejected": rejected,
                "reviewed": index - 1,
            }
        if outcome["approved"]:
            approved.append(outcome["approved_path"])
        else:
            rejected.append(path)

    enough = len(approved) >= IMAGE_APPROVAL["MIN_APPROVED_IMAGES"]
    return {
        "status": "ok" if enough else "too_few_approved",
        "approved": approved,
        "rejected": rejected,
        "minimum_required": IMAGE_APPROVAL["MIN_APPROVED_IMAGES"],
    }


# --------------------------------------------------------------------------
# Deferred generation
# --------------------------------------------------------------------------

@mcp.tool()
def submit_model_input(
    request_id: Annotated[str, Field(description="Id from a needs_model_input result")],
    response: Annotated[Any, Field(description="Your answer, matching response_schema")],
) -> dict[str, Any]:
    """Answer a needs_model_input request and resume the waiting job.

    A tool that needs generated content returns the prompt instead of calling a
    model provider. You produce the answer and hand it back here.
    """
    try:
        resumed = delegation.resolve(request_id, response)
    except PendingNotFound:
        return {
            "status": "unknown_request",
            "error": f"No pending request {request_id!r}. It may have expired.",
            "pending": delegation.pending_ids(),
        }
    return {"status": "ok", "request_id": request_id, "result": resumed}


# --------------------------------------------------------------------------
# Remote reconstruction offload
# --------------------------------------------------------------------------

@mcp.tool()
def discover_remote_cuda_mvs_servers() -> dict[str, Any]:
    """Find CUDA MVS servers advertising themselves on the local network."""
    manager = get_remote_manager()
    if manager is None:
        return {
            "status": "disabled",
            "error": "Set REMOTE_CUDA_MVS_ENABLED=True to use LAN offload.",
        }
    servers = manager.discover_servers()
    return {"status": "ok", "servers": servers, "count": len(servers)}


@mcp.tool()
def get_remote_job_status(
    job_id: Annotated[str, Field(description="Remote job id")],
) -> dict[str, Any]:
    """Check a reconstruction job running on a LAN server."""
    manager = get_remote_manager()
    if manager is None:
        return {"status": "disabled", "error": "Remote CUDA MVS is not enabled."}
    job = remote_jobs.get(job_id)
    if job is None:
        return {"status": "not_found", "error": f"No job {job_id!r} in this session."}
    return {"status": "ok", **manager.get_job_status(job["server_id"], job_id)}


# --------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------

@mcp.tool()
def discover_printers() -> dict[str, Any]:
    """Find 3D printers on the local network."""
    interface = get_printer_interface()
    interface.printer_discovery.start_discovery()
    found = interface.printer_discovery.get_printers()
    printers.update(found)
    return {"status": "ok", "printers": found, "count": len(found)}


@mcp.tool()
def connect_to_printer(
    printer_id: Annotated[str, Field(description="Printer id from discover_printers")],
) -> dict[str, Any]:
    """Connect to a discovered printer."""
    connected = get_printer_interface().connect_to_printer(printer_id)
    return {
        "status": "ok" if connected else "connection_failed",
        "printer_id": printer_id,
        "connected": connected,
    }


@mcp.tool()
def print_model(
    model_id: Annotated[str, Field(description="Model id from a create tool")],
    printer_id: Annotated[str, Field(description="Printer id from discover_printers")],
) -> dict[str, Any]:
    """Export a model to STL and send it to a connected printer."""
    model = models.get(model_id)
    if model is None:
        return {"status": "not_found", "error": f"No model {model_id!r} in this session."}

    stl_file = model.get("exports", {}).get("stl")
    if not stl_file or not os.path.exists(stl_file):
        exported = export_model(model_id, "stl")
        if exported["status"] != "ok":
            return exported
        stl_file = exported["file"]

    started = get_printer_interface().print_file(printer_id, stl_file)
    return {
        "status": "ok" if started else "print_failed",
        "model_id": model_id,
        "printer_id": printer_id,
        "file": stl_file,
    }


@mcp.tool()
def get_printer_status(
    printer_id: Annotated[str, Field(description="Printer id from discover_printers")],
) -> dict[str, Any]:
    """Read a printer's current state."""
    return {"status": "ok", **get_printer_interface().get_printer_status(printer_id)}


@mcp.tool()
def cancel_print_job(
    printer_id: Annotated[str, Field(description="Printer id from discover_printers")],
) -> dict[str, Any]:
    """Cancel the print running on a printer."""
    cancelled = get_printer_interface().cancel_print(printer_id)
    return {
        "status": "ok" if cancelled else "cancel_failed",
        "printer_id": printer_id,
        "cancelled": cancelled,
    }


# --------------------------------------------------------------------------

async def _progress(ctx: Context, fraction: float, message: str) -> None:
    """Report progress where the client accepts it.

    A client that sends no progress token, or none at all, must not fail the
    reconstruction it was watching.
    """
    try:
        await ctx.report_progress(fraction, 1.0, message)
    except (ValueError, AttributeError) as exc:
        logger.debug("Progress not delivered: %s", exc)


def _new_model_id(name: str) -> str:
    """Build a filesystem-safe id, keeping the caller's name for recognisability."""
    suffix = uuid.uuid4().hex[:8]
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")
    return f"{slug}_{suffix}" if slug else suffix


def _build(
    model_id: str, scad_code: str, parameters: dict[str, Any], source: str
) -> dict[str, Any]:
    """Write and compile generated source, sharing the caller-authored path's result shape."""
    scad_file = openscad.generate_scad(scad_code, model_id)
    try:
        warnings = openscad.validate(scad_file)
    except OpenSCADError as exc:
        return {
            "status": "compile_error",
            "model_id": model_id,
            "scad_file": scad_file,
            "diagnostics": exc.diagnostics,
        }
    except OpenSCADNotFound as exc:
        return {"status": "openscad_missing", "error": str(exc)}

    models[model_id] = {
        "model_id": model_id,
        "scad_file": scad_file,
        "parameters": parameters,
        "source": source,
    }
    return {
        "status": "ok",
        "model_id": model_id,
        "scad_file": scad_file,
        "scad_code": scad_code,
        "warnings": warnings,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
