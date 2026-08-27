<metadata>
  related-files: [/src/mcp_bridge/prompts.py, /src/mcp_bridge/delegation.py, /src/models/code_generator.py, /src/models/cuda_mvs.py]
  decisions: [/rtfmd/decisions/client-supplied-reasoning.md]
</metadata>

<exploration>
  The server layer was rewritten because the previous one could not run. It
  imported MCPServer, MCPTool, MCPToolCall and MCPToolCallResult from `mcp`, an
  API the Python SDK has never exported, and reached a bespoke FastAPI
  POST /tool_call endpoint rather than an MCP transport. Import failed before
  any of that mattered: the module pulled in a file moved to old/, used five
  names it never imported, and built CUDAMultiViewStereo at module scope, whose
  constructor raised FileNotFoundError wherever CUDA MVS was not compiled.

  Three server shapes were considered:

  1. Repair the FastAPI shim in place. Rejected: it is not MCP, so no client
     could reach it without a custom adapter.
  2. Keep FastAPI for a web UI and add MCP alongside. Rejected as scope; the web
     interface remains in src/visualization/ and is not registered.
  3. Rewrite on mcp.server.MCPServer over stdio. Chosen.
</exploration>

<reasoning>
  SDK v2 renamed FastMCP to MCPServer and moved it to mcp.server; Context comes
  from mcp.server.mcpserver. Tools declare their arguments through type hints and
  Annotated Field descriptions, so the schema the client sees is the signature.

  Components initialise through get_cuda_mvs, get_printer_interface and
  get_remote_manager rather than at import. A machine with no CUDA MVS build, no
  printers on the network and no .env still serves every OpenSCAD tool. This was
  the specific defect that made the old server unstartable.

  Tools return status values instead of raising. A caller that receives
  compile_error with OpenSCAD's diagnostics can fix the reported line and call
  again, which is the loop the whole design depends on: the model on the other
  end is doing the authoring, so it needs the compiler's answer, not a stack
  trace. missing_camera_poses and cuda_mvs_missing work the same way.

  Two paths need the client to accept a server-initiated request, and neither
  may assume it. _progress swallows delivery failures. approve_images catches
  NoBackChannelError and returns elicitation_unavailable with the image list, so
  the caller decides instead. That is the same constraint that rules out
  sampling, met the same way: fall back to the tool call already in flight.

  Logging goes to stderr because stdout carries the protocol.
</reasoning>
