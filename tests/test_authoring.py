"""The caller writes the geometry; the server compiles and reports back."""

import os

import pytest
from conftest import needs_openscad, unwrap

from mcp import Client

VASE = """// Parametric vase
height = 40;
wall = 3;
base_radius = 18;
neck_radius = 11;

difference() {
    hull() {
        cylinder(r=base_radius, h=1, $fn=64);
        translate([0, 0, height - 1]) cylinder(r=neck_radius, h=1, $fn=64);
    }
    translate([0, 0, wall])
    hull() {
        cylinder(r=base_radius - wall, h=1, $fn=64);
        translate([0, 0, height - 1 - wall]) cylinder(r=neck_radius - wall, h=1, $fn=64);
    }
}
"""


async def test_tools_and_prompts_are_registered(server):
    async with Client(server.mcp) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        prompts = {p.name for p in (await client.list_prompts()).prompts}

    assert {"create_model_from_scad", "create_model_from_shape", "list_shape_library",
            "reconstruct_from_images", "submit_model_input"} <= tools
    assert {"design_scad_model", "parametrize_scad", "plan_capture_setup"} == prompts


async def test_shape_library_reports_real_signatures(server):
    async with Client(server.mcp) as client:
        library = unwrap(await client.call_tool("list_shape_library", {}))

    assert library["units"] == "millimetres"
    # torus takes outer_radius and inner_radius, which the old hand-written map
    # got wrong as major_radius and minor_radius.
    assert set(library["shapes"]["torus"]["parameters"]) == {
        "outer_radius", "inner_radius", "segments"
    }
    assert "bottom_radius" in library["shapes"]["cone"]["parameters"]


@needs_openscad
async def test_caller_authored_scad_compiles_and_exports(server):
    async with Client(server.mcp) as client:
        created = unwrap(
            await client.call_tool(
                "create_model_from_scad", {"scad_code": VASE, "name": "vase"}
            )
        )
        assert created["status"] == "ok"

        exported = unwrap(
            await client.call_tool(
                "export_model", {"model_id": created["model_id"], "format": "stl"}
            )
        )

    assert exported["status"] == "ok"
    assert exported["size_bytes"] > 1000
    assert os.path.exists(exported["file"])


@needs_openscad
async def test_broken_scad_returns_diagnostics_not_an_error(server):
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "create_model_from_scad", {"scad_code": "cube([1,2,3);", "name": "bad"}
        )

    assert result.is_error is False
    payload = unwrap(result)
    assert payload["status"] == "compile_error"
    assert "syntax error" in payload["diagnostics"]
    assert payload["hint"]


@needs_openscad
async def test_library_shape_builds(server):
    async with Client(server.mcp) as client:
        created = unwrap(
            await client.call_tool(
                "create_model_from_shape",
                {"shape": "rounded_box",
                 "parameters": {"width": 40, "depth": 25, "height": 12, "radius": 4}},
            )
        )
    assert created["status"] == "ok"
    assert "rounded_box(" in created["scad_code"]


async def test_unknown_parameter_names_the_real_ones(server):
    async with Client(server.mcp) as client:
        result = unwrap(
            await client.call_tool(
                "create_model_from_shape",
                {"shape": "torus", "parameters": {"major_radius": 20}},
            )
        )
    assert result["status"] == "invalid_request"
    assert "outer_radius" in result["error"]


@needs_openscad
async def test_csg_chain_compiles(server):
    async with Client(server.mcp) as client:
        created = unwrap(
            await client.call_tool(
                "combine_shapes",
                {"operations": [
                    {"shape": "parametric_cube",
                     "parameters": {"width": 30, "depth": 30, "height": 10},
                     "operation": "difference"},
                    {"shape": "parametric_cylinder",
                     "parameters": {"radius": 5, "height": 30},
                     "transform": "translate([15,15,-10])"},
                ]},
            )
        )
    assert created["status"] == "ok"
    assert "difference()" in created["scad_code"]


async def test_unknown_model_id_is_reported(server):
    async with Client(server.mcp) as client:
        result = unwrap(await client.call_tool("export_model", {"model_id": "nope"}))
    assert result["status"] == "not_found"


async def test_prompt_renders_against_the_real_library(server):
    async with Client(server.mcp) as client:
        prompt = await client.get_prompt(
            "design_scad_model", {"description": "a vase", "constraints": "3mm walls"}
        )
    text = prompt.messages[0].content.text
    assert "millimetres" in text
    assert "parametric_cylinder(" in text
    assert "create_model_from_scad" in text
