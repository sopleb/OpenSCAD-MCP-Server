# OpenSCAD MCP Server

Parametric 3D modelling with OpenSCAD, photogrammetric reconstruction, and 3D
printing, exposed over the Model Context Protocol.

The server runs no AI model and holds no API key. Whatever client connects to it
supplies the reasoning: Claude, Grok, Cursor, or anything else that speaks MCP.
Point a different model at it and the behaviour is identical, with no
configuration change.

## What the calling model does, and what the server does

| The calling model | The server |
|---|---|
| Reads a request and decides on the geometry | Compiles OpenSCAD and returns diagnostics |
| Writes SCAD source, or picks a library shape and its parameters | Validates parameters against the real module signatures |
| Fixes what the compiler rejects and retries | Renders previews, exports STL, CSG, AMF, 3MF |
| Interprets photographs and capture advice | Runs multi-view stereo on this machine |

## Three ways a prompt reaches the caller

**Caller-authored input.** Tools take structured arguments or complete SCAD
source. Turning "a 40mm vase with 3mm walls" into geometry happens in the
client's model, as part of its ordinary tool call. Nothing routes anywhere else.

**Registered prompts.** `design_scad_model`, `parametrize_scad`, and
`plan_capture_setup` are MCP prompts. The server renders the text, the client's
model runs it. In Claude Code they appear as `/mcp__openscad__design_scad_model`.

**Deferred generation.** When a tool needs generated content mid-execution, it
returns the prompt rather than calling out:

```json
{
  "status": "needs_model_input",
  "request_id": "req_a1b2c3d4e5f6",
  "prompt": "...",
  "response_schema": { "...": "..." },
  "resume_with": "submit_model_input"
}
```

The caller answers and calls `submit_model_input`, which resumes the job.

### Why not MCP sampling

`sampling/createMessage` looks like the natural fit, and it is the wrong tool
here. The 2026-07-28 spec deprecated it (SEP-2577), and Claude Code does not
implement it as a client, so a server built on sampling would fail against the
most common caller. The same limitation shows up in elicitation: a transport
without a back-channel cannot carry server-initiated requests at all, which is
why `approve_images` hands the decision back to the caller when that happens.
The three mechanisms above need no back-channel and work on every client today.

## Installing

```bash
git clone https://github.com/sopleb/openscad-mcp-server.git
cd openscad-mcp-server
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Install OpenSCAD:

- Debian and Ubuntu: `sudo apt-get install openscad`
- macOS: `brew install --cask openscad`
- Windows: from [openscad.org](https://openscad.org/downloads.html)

If the binary is not on `PATH`, set `OPENSCAD_PATH` to it. No other
configuration is required, and there is no `.env` to fill in.

## Connecting a client

```bash
claude mcp add openscad -- python -m src.main
```

The server speaks stdio. Any MCP client that launches a subprocess works the
same way.

## Tools

**Modelling**

| Tool | Purpose |
|---|---|
| `list_shape_library` | Module names, parameters, and defaults from the SCAD library |
| `create_model_from_scad` | Compile OpenSCAD source you wrote |
| `create_model_from_shape` | Build one library shape from named parameters |
| `combine_shapes` | Chain shapes with union, difference, intersection |
| `render_previews` | Front, top, right, and perspective renders |
| `export_model` | STL, CSG, AMF, 3MF, SCAD, OFF, DXF, SVG |
| `get_model` | The stored record, including its SCAD source |

`create_model_from_scad` returns `status: "compile_error"` with the OpenSCAD
diagnostics rather than failing, so the calling model can fix the reported line
and call again.

**Reconstruction**

| Tool | Purpose |
|---|---|
| `reconstruct_from_images` | Multi-view stereo over photographs, on this machine |
| `approve_images` | Ask the user which captures to keep |

**Printing**

`discover_printers`, `connect_to_printer`, `print_model`, `get_printer_status`,
`cancel_print_job`.

**Routing**

`submit_model_input` answers a `needs_model_input` request.

## Reconstruction

Reconstruction uses [CUDA Multi-View
Stereo](https://github.com/fixstars/cuda-multi-view-stereo), a compiled
patch-match stereo binary. It runs locally, contacts nothing, and needs no key.

```bash
git clone https://github.com/fixstars/cuda-multi-view-stereo.git
cd cuda-multi-view-stereo && mkdir build && cd build
cmake .. && make
export CUDA_MVS_PATH=/path/to/cuda-multi-view-stereo
```

This is photogrammetry, so it needs real photographs of one rigid object taken
from different positions, along with camera poses that have a baseline between
them. Pass poses directly as `camera_params`, or point `colmap_sparse_dir` at a
COLMAP `sparse/0` directory:

```bash
colmap automatic_reconstructor --workspace_path ./capture --image_path ./capture/images
```

A pose set whose views share a camera centre is rejected, because zero baseline
makes triangulation impossible. `plan_capture_setup` walks a user through
shooting a usable set.

Generated images do not reconstruct. Model-produced views of "the same object"
are not views of the same object, and multi-view stereo has nothing to
triangulate from them. The server no longer offers image generation for this
reason, among others: an MCP server cannot ask Claude or Grok for an image
anyway.

Optional LAN offload to a machine with a GPU is available through
`REMOTE_CUDA_MVS_ENABLED`; see `src/remote/`. That server is your own
infrastructure, and its `REMOTE_CUDA_MVS_API_KEY` authenticates to it, not to a
model provider.

## Configuration

Every setting has a working default. All are optional.

| Variable | Default | Purpose |
|---|---|---|
| `OPENSCAD_PATH` | `openscad` | OpenSCAD executable |
| `CUDA_MVS_PATH` | `./cuda-mvs` | CUDA MVS checkout |
| `REMOTE_CUDA_MVS_ENABLED` | `False` | Offload reconstruction to a LAN server |
| `IMAGE_APPROVAL_MIN_IMAGES` | `3` | Approvals needed before reconstructing |
| `DELEGATION_TTL_SECONDS` | `900` | How long a deferred request stays resumable |

## Tests

```bash
pytest
```

Tests drive the server through a real MCP client session. Cases needing OpenSCAD
or open3d skip when those are absent. `tests/test_no_external_ai.py` greps the
source for provider hosts and API keys, so reintroducing one fails the suite.

## Export formats

CSG, SCAD, AMF, and 3MF keep parametric detail. STL and OFF are meshes. DXF and
SVG are for 2D work.

## License

MIT
