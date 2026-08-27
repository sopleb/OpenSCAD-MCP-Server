"""Reconstruction runs on this machine and needs poses that can triangulate."""

import json
import os
import struct

import pytest
from conftest import needs_open3d, unwrap

from mcp import Client

from src.models.cuda_mvs import (
    CUDAMultiViewStereo,
    CUDAMVSNotInstalled,
    MissingCameraPoses,
    _quaternion_to_matrix,
    read_colmap_poses,
    validate_poses,
)


def poses_at(*centres):
    return [
        {"image_name": f"v{i}.png", "camera": {"translation": list(c)}}
        for i, c in enumerate(centres)
    ]


def test_shared_camera_centre_is_rejected():
    """The previous generator wrote zero translation for every view."""
    with pytest.raises(MissingCameraPoses, match="baseline"):
        validate_poses(poses_at((0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0)))


def test_real_baseline_is_accepted():
    validate_poses(poses_at((0, 0, 0), (10, 0, 0), (20, 0, 5)))


def test_missing_translation_is_rejected():
    with pytest.raises(MissingCameraPoses):
        validate_poses([{"image_name": "v0.png", "camera": {}}])


def test_identity_quaternion_gives_identity_rotation():
    assert _quaternion_to_matrix((1, 0, 0, 0)) == [1, 0, 0, 0, 1, 0, 0, 0, 1]


def test_quarter_turn_about_z():
    matrix = _quaternion_to_matrix((0.7071067811865476, 0, 0, 0.7071067811865476))
    assert matrix[0] == pytest.approx(0, abs=1e-9)
    assert matrix[1] == pytest.approx(-1, abs=1e-9)
    assert matrix[3] == pytest.approx(1, abs=1e-9)


def test_unnormalised_quaternion_is_normalised():
    assert _quaternion_to_matrix((2, 0, 0, 0)) == [1, 0, 0, 0, 1, 0, 0, 0, 1]


def test_colmap_text_model_is_read(tmp_path):
    (tmp_path / "cameras.txt").write_text(
        "# camera list\n1 PINHOLE 1920 1080 1500.0 1500.0 960.0 540.0\n"
    )
    (tmp_path / "images.txt").write_text(
        "# image list\n"
        "1 1 0 0 0 0 0 0 1 front.png\n"
        "0 0\n"
        "2 1 0 0 0 12.5 0 0 1 side.png\n"
        "0 0\n"
    )

    poses = read_colmap_poses(str(tmp_path))

    assert [p["image_name"] for p in poses] == ["front.png", "side.png"]
    assert poses[0]["width"] == 1920
    assert poses[0]["camera"]["focal_length"] == 1500.0
    assert poses[1]["camera"]["translation"] == [12.5, 0.0, 0.0]
    # Two distinct centres, so this set can triangulate.
    validate_poses(poses)


def test_missing_colmap_model_is_reported(tmp_path):
    with pytest.raises(MissingCameraPoses, match="No COLMAP reconstruction"):
        read_colmap_poses(str(tmp_path))


def test_uninstalled_cuda_mvs_is_reported(tmp_path):
    mvs = CUDAMultiViewStereo(str(tmp_path / "absent"), str(tmp_path))
    assert mvs.is_installed() is False
    with pytest.raises(CUDAMVSNotInstalled, match="CUDA_MVS_PATH"):
        mvs.ensure_installed()


def test_built_checkout_without_binary_is_reported(tmp_path):
    checkout = tmp_path / "cuda-mvs"
    (checkout / "build").mkdir(parents=True)
    mvs = CUDAMultiViewStereo(str(checkout), str(tmp_path))
    with pytest.raises(CUDAMVSNotInstalled, match="cmake"):
        mvs.ensure_installed()


async def test_reconstruct_reports_missing_cuda_mvs(server, images, monkeypatch, tmp_path):
    monkeypatch.setattr(
        server, "_cuda_mvs", CUDAMultiViewStereo(str(tmp_path / "absent"), str(tmp_path))
    )
    async with Client(server.mcp) as client:
        result = await client.call_tool("reconstruct_from_images", {"image_paths": images})

    assert result.is_error is False
    assert unwrap(result)["status"] == "cuda_mvs_missing"


async def test_reconstruct_needs_enough_views(server, images):
    async with Client(server.mcp) as client:
        result = unwrap(
            await client.call_tool("reconstruct_from_images", {"image_paths": images[:2]})
        )
    assert result["status"] == "invalid_request"
    assert "at least 3" in result["error"]


async def test_reconstruct_reports_missing_poses(server, images, monkeypatch, tmp_path):
    """With the binary present but no poses, the tool explains rather than guessing."""
    checkout = tmp_path / "cuda-mvs" / "build"
    checkout.mkdir(parents=True)
    (checkout / "app_patch_match_mvs").write_text("#!/bin/sh\nexit 0\n")

    monkeypatch.setattr(
        server,
        "_cuda_mvs",
        CUDAMultiViewStereo(str(tmp_path / "cuda-mvs"), str(tmp_path / "models")),
    )
    async with Client(server.mcp) as client:
        result = unwrap(
            await client.call_tool("reconstruct_from_images", {"image_paths": images})
        )

    assert result["status"] == "missing_camera_poses"
    assert "COLMAP" in result["error"]


def sphere_point_cloud(path, count=4000):
    """A dense sphere of points, the shape Poisson reconstruction handles well."""
    import numpy as np
    import open3d as o3d

    rng = np.random.default_rng(7)
    directions = rng.normal(size=(count, 3))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)

    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(directions * 20.0)
    cloud.normals = o3d.utility.Vector3dVector(directions)
    o3d.io.write_point_cloud(str(path), cloud)
    return str(path)


@needs_open3d
def test_point_cloud_is_meshed_into_a_real_surface(tmp_path):
    """The old convert_ply_to_obj wrote a fixed three-vertex triangle."""
    import open3d as o3d

    ply = sphere_point_cloud(tmp_path / "cloud.ply")
    mvs = CUDAMultiViewStereo(str(tmp_path), str(tmp_path))

    obj = mvs.convert_ply_to_obj(ply, str(tmp_path))

    mesh = o3d.io.read_triangle_mesh(obj)
    assert len(mesh.vertices) > 3
    assert len(mesh.triangles) > 100

    # The surface should sit near the sampled radius, not at some default.
    import numpy as np

    radii = np.linalg.norm(np.asarray(mesh.vertices), axis=1)
    assert radii.mean() == pytest.approx(20.0, rel=0.1)


@needs_open3d
def test_empty_point_cloud_is_reported(tmp_path):
    import open3d as o3d

    empty = tmp_path / "empty.ply"
    o3d.io.write_point_cloud(str(empty), o3d.geometry.PointCloud())
    mvs = CUDAMultiViewStereo(str(tmp_path), str(tmp_path))

    with pytest.raises(ValueError, match="no points"):
        mvs.convert_ply_to_obj(str(empty), str(tmp_path))
