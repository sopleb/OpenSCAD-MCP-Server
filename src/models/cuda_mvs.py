"""CUDA Multi-View Stereo wrapper.

Reconstruction runs on this machine as a compiled binary from
fixstars/cuda-multi-view-stereo. Patch-match stereo is classical photogrammetry,
so nothing here contacts a model or needs an API key.

Triangulation requires camera poses with real baselines between them. Supply
them directly, or point at a COLMAP sparse reconstruction. Fabricated poses
produce a point cloud that looks plausible and encodes nothing.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXECUTABLE = "app_patch_match_mvs"

POSE_HELP = (
    "Multi-view stereo needs camera poses with a real baseline between views. "
    "Pass camera_params directly, or run COLMAP over the images and pass "
    "colmap_sparse_dir pointing at the sparse/0 directory containing "
    "cameras.bin and images.bin."
)


class CUDAMVSNotInstalled(RuntimeError):
    """The CUDA MVS binary is missing or was never built."""


class MissingCameraPoses(ValueError):
    """Reconstruction was asked for without usable camera poses."""


class CUDAMultiViewStereo:
    """Runs the local CUDA MVS binary over a set of posed images."""

    def __init__(self, cuda_mvs_path: str, output_dir: str = "output/models"):
        self.cuda_mvs_path = cuda_mvs_path
        self.output_dir = output_dir
        self._validated = False

    @property
    def executable_path(self) -> str:
        return os.path.join(self.cuda_mvs_path, "build", EXECUTABLE)

    def is_installed(self) -> bool:
        return os.path.exists(self.executable_path)

    def ensure_installed(self) -> None:
        """Check for the binary on first use.

        Deferred so the OpenSCAD tools still run on a machine without CUDA MVS.
        """
        if self._validated:
            return
        if not os.path.exists(self.cuda_mvs_path):
            raise CUDAMVSNotInstalled(
                f"CUDA MVS not found at {self.cuda_mvs_path}. Clone and build "
                f"fixstars/cuda-multi-view-stereo, then set CUDA_MVS_PATH."
            )
        if not self.is_installed():
            raise CUDAMVSNotInstalled(
                f"{EXECUTABLE} not found at {self.executable_path}. Run cmake and "
                f"make in the CUDA MVS checkout."
            )
        self._validated = True

    def generate_model_from_images(
        self,
        image_paths: list[str],
        camera_params: list[dict[str, Any]] | None = None,
        colmap_sparse_dir: str | None = None,
        output_name: str = "model",
    ) -> dict[str, Any]:
        """Reconstruct a point cloud from posed images.

        Raises MissingCameraPoses when neither camera_params nor a readable
        COLMAP reconstruction is available.
        """
        self.ensure_installed()

        if len(image_paths) < 2:
            raise ValueError("Reconstruction needs at least two images")
        missing = [p for p in image_paths if not os.path.exists(p)]
        if missing:
            raise FileNotFoundError(f"Input images not found: {missing}")

        model_dir = os.path.join(self.output_dir, output_name)
        os.makedirs(model_dir, exist_ok=True)

        poses = camera_params
        if poses is None and colmap_sparse_dir:
            poses = read_colmap_poses(colmap_sparse_dir)
        if not poses:
            raise MissingCameraPoses(POSE_HELP)

        validate_poses(poses)

        params_file = os.path.join(model_dir, "camera_params.json")
        with open(params_file, "w") as f:
            json.dump(poses, f, indent=2)

        point_cloud_file = os.path.join(model_dir, f"{output_name}.ply")
        cmd = [
            self.executable_path,
            "--image_dir", os.path.dirname(os.path.abspath(image_paths[0])),
            "--camera_params", params_file,
            "--output_file", point_cloud_file,
        ]
        logger.info("Running CUDA MVS: %s", " ".join(cmd))

        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise RuntimeError(
                f"CUDA MVS exited with {process.returncode}: {process.stderr.strip()}"
            )
        if not os.path.exists(point_cloud_file):
            raise FileNotFoundError(
                f"CUDA MVS reported success but wrote no point cloud to {point_cloud_file}"
            )

        return {
            "model_id": output_name,
            "output_dir": model_dir,
            "point_cloud_file": point_cloud_file,
            "camera_params_file": params_file,
            "input_images": image_paths,
            "num_views": len(image_paths),
        }

    def convert_ply_to_obj(
        self, ply_file: str, output_dir: str | None = None, depth: int = 9
    ) -> str:
        """Mesh a point cloud into an OBJ with Poisson surface reconstruction."""
        try:
            import open3d as o3d
        except ImportError as exc:
            raise RuntimeError(
                "open3d is required to mesh point clouds. Install it with "
                "pip install open3d."
            ) from exc

        if output_dir is None:
            output_dir = os.path.dirname(ply_file)
        os.makedirs(output_dir, exist_ok=True)
        obj_file = os.path.join(output_dir, f"{Path(ply_file).stem}.obj")

        cloud = o3d.io.read_point_cloud(ply_file)
        if len(cloud.points) == 0:
            raise ValueError(f"{ply_file} holds no points")

        if not cloud.has_normals():
            cloud.estimate_normals()
            cloud.orient_normals_consistent_tangent_plane(k=15)

        mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
            cloud, depth=depth
        )

        # Poisson extrapolates a closed surface past the samples. Drop the
        # lowest-support eighth so the mesh stops at the observed geometry.
        import numpy as np

        threshold = np.quantile(np.asarray(densities), 0.125)
        mesh.remove_vertices_by_mask(np.asarray(densities) < threshold)
        mesh.remove_degenerate_triangles()
        mesh.remove_duplicated_vertices()

        if len(mesh.triangles) == 0:
            raise ValueError(
                f"Poisson reconstruction produced no faces from {ply_file}. The "
                f"point cloud is likely too sparse or the poses were wrong."
            )

        o3d.io.write_triangle_mesh(obj_file, mesh)
        logger.info(
            "Meshed %s into %s (%d vertices, %d faces)",
            ply_file, obj_file, len(mesh.vertices), len(mesh.triangles),
        )
        return obj_file


def validate_poses(poses: list[dict[str, Any]]) -> None:
    """Reject pose sets that cannot triangulate.

    Every view sharing one camera centre gives zero baseline, which is what the
    previous placeholder generator emitted for every image.
    """
    centres = set()
    for pose in poses:
        camera = pose.get("camera", pose)
        translation = camera.get("translation")
        if translation is None:
            raise MissingCameraPoses(f"Pose for {pose.get('image_name')} has no translation. {POSE_HELP}")
        centres.add(tuple(round(float(v), 9) for v in translation))

    if len(centres) < 2:
        raise MissingCameraPoses(
            f"All {len(poses)} views share one camera centre, so there is no "
            f"baseline to triangulate from. {POSE_HELP}"
        )


def read_colmap_poses(sparse_dir: str) -> list[dict[str, Any]]:
    """Read camera intrinsics and extrinsics from a COLMAP sparse model.

    Handles the binary format COLMAP writes by default; falls back to the text
    format when cameras.txt and images.txt are present instead.
    """
    sparse = Path(sparse_dir)
    if (sparse / "images.bin").exists():
        cameras = _read_colmap_cameras_bin(sparse / "cameras.bin")
        return _read_colmap_images_bin(sparse / "images.bin", cameras)
    if (sparse / "images.txt").exists():
        cameras = _read_colmap_cameras_txt(sparse / "cameras.txt")
        return _read_colmap_images_txt(sparse / "images.txt", cameras)
    raise MissingCameraPoses(
        f"No COLMAP reconstruction in {sparse_dir}. Expected cameras.bin and "
        f"images.bin, or their .txt equivalents."
    )


def _read_next_bytes(fid, num_bytes: int, format_char_sequence: str):
    return struct.unpack("<" + format_char_sequence, fid.read(num_bytes))


def _read_colmap_cameras_bin(path: Path) -> dict[int, dict[str, Any]]:
    cameras: dict[int, dict[str, Any]] = {}
    with open(path, "rb") as fid:
        num_cameras = _read_next_bytes(fid, 8, "Q")[0]
        for _ in range(num_cameras):
            camera_id, model_id, width, height = _read_next_bytes(fid, 24, "iiQQ")
            num_params = _COLMAP_MODEL_PARAMS.get(model_id, 4)
            params = _read_next_bytes(fid, 8 * num_params, "d" * num_params)
            cameras[camera_id] = {
                "width": width, "height": height, "params": list(params),
            }
    return cameras


def _read_colmap_images_bin(path: Path, cameras: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    poses = []
    with open(path, "rb") as fid:
        num_images = _read_next_bytes(fid, 8, "Q")[0]
        for _ in range(num_images):
            image_id, qw, qx, qy, qz, tx, ty, tz, camera_id = _read_next_bytes(
                fid, 64, "idddddddi"
            )
            name = b""
            while True:
                char = fid.read(1)
                if char == b"\x00":
                    break
                name += char
            num_points = _read_next_bytes(fid, 8, "Q")[0]
            fid.read(24 * num_points)
            poses.append(
                _build_pose(image_id, name.decode(), (qw, qx, qy, qz), (tx, ty, tz),
                            cameras.get(camera_id, {}))
            )
    return poses


def _read_colmap_cameras_txt(path: Path) -> dict[int, dict[str, Any]]:
    cameras: dict[int, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        cameras[int(parts[0])] = {
            "width": int(parts[2]),
            "height": int(parts[3]),
            "params": [float(p) for p in parts[4:]],
        }
    return cameras


def _read_colmap_images_txt(path: Path, cameras: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    poses = []
    lines = [ln for ln in path.read_text().splitlines() if ln and not ln.startswith("#")]
    # COLMAP writes two lines per image; the second holds 2D points.
    for line in lines[::2]:
        parts = line.split()
        image_id = int(parts[0])
        quaternion = tuple(float(p) for p in parts[1:5])
        translation = tuple(float(p) for p in parts[5:8])
        camera_id = int(parts[8])
        poses.append(
            _build_pose(image_id, parts[9], quaternion, translation, cameras.get(camera_id, {}))
        )
    return poses


def _build_pose(image_id, name, quaternion, translation, camera) -> dict[str, Any]:
    params = camera.get("params", [])
    width = camera.get("width", 0)
    height = camera.get("height", 0)
    focal = params[0] if params else min(width, height)
    principal = params[1:3] if len(params) >= 3 else [width / 2, height / 2]
    return {
        "image_id": image_id,
        "image_name": name,
        "width": width,
        "height": height,
        "camera": {
            "model": "PINHOLE",
            "focal_length": focal,
            "principal_point": list(principal),
            "rotation": _quaternion_to_matrix(quaternion),
            "translation": list(translation),
        },
    }


def _quaternion_to_matrix(q) -> list[float]:
    """COLMAP stores rotation as a wxyz quaternion; CUDA MVS wants a row-major 3x3."""
    w, x, y, z = q
    norm = (w * w + x * x + y * y + z * z) ** 0.5
    if norm == 0:
        raise ValueError("Zero-length rotation quaternion")
    w, x, y, z = (v / norm for v in (w, x, y, z))
    return [
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
        2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
        2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
    ]


# Parameter counts per COLMAP camera model id.
_COLMAP_MODEL_PARAMS = {
    0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12,
}
