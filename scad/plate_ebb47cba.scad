// Combined model
include </home/user/OpenSCAD-MCP-Server/src/models/scad_templates/basic_shapes.scad>;

difference() {
    parametric_cube(width=30, depth=30, height=10, center=false);
    translate([15,15,-10]) parametric_cylinder(radius=5, height=30, center=false, segments=32);
}
