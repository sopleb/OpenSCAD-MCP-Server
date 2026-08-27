<metadata>
  related-files: [/src/models/shape_library.py, /src/models/scad_templates/basic_shapes.scad]
  decisions: [/rtfmd/decisions/client-supplied-reasoning.md]
</metadata>

<exploration>
  The generator previously held a Python dict mapping shape names to module names
  and another mapping natural-language parameter names to OpenSCAD ones. Both
  drifted from basic_shapes.scad:

  - torus was mapped to major_radius and minor_radius; the module takes
    outer_radius and inner_radius.
  - cone was mapped to base_radius; the module takes bottom_radius.
  - triangular_prism and custom_shape were mapped to modules that do not exist.

  _map_parameters passed names through without translating them, so those calls
  reached OpenSCAD as arguments no module declared. Nothing detected this,
  because the natural-language path fell back to a 10mm cube on any failure.

  Two fixes were possible: correct the dict, or stop keeping one.
</exploration>

<reasoning>
  Correcting the dict fixes today's drift and invites tomorrow's. Any edit to
  basic_shapes.scad can desynchronise it again, silently, with the failure
  surfacing as a rejected OpenSCAD call.

  shape_library parses module signatures out of the .scad file instead. The
  library is the single source of truth, so a renamed parameter changes the
  validation, the defaults, the error message and the prompt text together.

  generate_code merges caller parameters over the parsed defaults and emits a
  named variable per parameter, keeping the output parametric rather than
  inlining numbers into the module call. Unknown names raise ValueError naming
  the accepted ones, which the calling model reads and corrects.

  The AI branch is gone. It required an ai_service that was never constructed,
  and main.py never passed the description argument that would have reached it,
  so the code was unreachable in two independent ways. A caller that wants
  arbitrary geometry writes SCAD and sends it to create_model_from_scad.
</reasoning>
