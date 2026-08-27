// rounded_box
include </home/user/OpenSCAD-MCP-Server/src/models/scad_templates/basic_shapes.scad>;

// Parameters (millimetres)
width = 40;
depth = 25;
height = 12;
radius = 4;
segments = 32;

// Model
rounded_box(width=width, depth=depth, height=height, radius=radius, segments=segments);
