// Parametric vase
height = 40;      // mm
wall = 3;         // mm
base_radius = 18; // mm
neck_radius = 11; // mm

difference() {
    hull() {
        cylinder(r=base_radius, h=1, $fn=64);
        translate([0,0,height-1]) cylinder(r=neck_radius, h=1, $fn=64);
    }
    translate([0,0,wall])
    hull() {
        cylinder(r=base_radius-wall, h=1, $fn=64);
        translate([0,0,height-1-wall]) cylinder(r=neck_radius-wall, h=1, $fn=64);
    }
}
