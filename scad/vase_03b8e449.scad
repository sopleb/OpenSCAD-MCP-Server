// Parametric vase
height = 40; wall = 3; base_radius = 18; neck_radius = 11;
difference() {
    hull() { cylinder(r=base_radius,h=1,$fn=64);
             translate([0,0,height-1]) cylinder(r=neck_radius,h=1,$fn=64); }
    translate([0,0,wall]) hull() {
             cylinder(r=base_radius-wall,h=1,$fn=64);
             translate([0,0,height-1-wall]) cylinder(r=neck_radius-wall,h=1,$fn=64); }
}
