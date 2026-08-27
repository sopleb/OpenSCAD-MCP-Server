"""Shared fixtures.

Tests drive the server through a real MCP client session rather than calling
tool functions directly, so tool registration, schema validation, and result
encoding are all exercised.
"""

import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def unwrap(result):
    """Pull the payload out of a CallToolResult."""
    structured = getattr(result, "structured_content", None)
    if structured:
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A server instance writing into a temporary output tree."""
    monkeypatch.setenv("OPENSCAD_PATH", os.getenv("OPENSCAD_PATH", "openscad"))

    import src.config as config
    import src.main as main

    monkeypatch.setattr(main.openscad, "scad_dir", str(tmp_path / "scad"))
    monkeypatch.setattr(main.openscad, "output_dir", str(tmp_path))
    monkeypatch.setattr(main.openscad, "stl_dir", str(tmp_path / "stl"))
    monkeypatch.setattr(main.openscad, "preview_dir", str(tmp_path / "preview"))
    for directory in ("scad", "stl", "preview"):
        (tmp_path / directory).mkdir(exist_ok=True)

    main.models.clear()
    return main


@pytest.fixture
def images(tmp_path):
    """Three files standing in for photographs."""
    paths = []
    for name in ("front.png", "side.png", "back.png"):
        path = tmp_path / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        paths.append(str(path))
    return paths


def has_openscad():
    from shutil import which

    return which(os.getenv("OPENSCAD_PATH", "openscad")) is not None


needs_openscad = pytest.mark.skipif(
    not has_openscad(), reason="OpenSCAD is not installed"
)


needs_open3d = pytest.mark.skipif(
    importlib.util.find_spec("open3d") is None, reason="open3d is not installed"
)
