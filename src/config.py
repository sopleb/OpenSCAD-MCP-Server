"""Configuration for the OpenSCAD MCP server.

No API keys. The server calls no external AI service; the connected MCP client
supplies any model reasoning.
"""

import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")
MULTI_VIEW_DIR = os.path.join(OUTPUT_DIR, "multi_view")
APPROVED_IMAGES_DIR = os.path.join(OUTPUT_DIR, "approved_images")
MODELS_DIR = os.path.join(OUTPUT_DIR, "models")
SCAD_DIR = os.path.join(BASE_DIR, "scad")
TEMPLATES_DIR = os.path.join(BASE_DIR, "src", "models", "scad_templates")

# OpenSCAD binary. Override on Windows and on macOS app installs, where the
# executable is not on PATH.
OPENSCAD_PATH = os.getenv("OPENSCAD_PATH", "openscad")

# CUDA Multi-View Stereo runs on this machine as a compiled binary. It is
# photogrammetry, not a model, and needs no key.
CUDA_MVS_PATH = os.getenv("CUDA_MVS_PATH", os.path.join(BASE_DIR, "cuda-mvs"))

REMOTE_CUDA_MVS = {
    "ENABLED": os.getenv("REMOTE_CUDA_MVS_ENABLED", "False").lower() == "true",
    "USE_LAN_DISCOVERY": os.getenv("REMOTE_CUDA_MVS_USE_LAN_DISCOVERY", "True").lower() == "true",

    "SERVER_URL": os.getenv("REMOTE_CUDA_MVS_SERVER_URL", ""),  # empty means LAN discovery
    "API_KEY": os.getenv("REMOTE_CUDA_MVS_API_KEY", ""),
    "DISCOVERY_PORT": int(os.getenv("REMOTE_CUDA_MVS_DISCOVERY_PORT", "8765")),

    "CONNECTION_TIMEOUT": int(os.getenv("REMOTE_CUDA_MVS_CONNECTION_TIMEOUT", "10")),
    "UPLOAD_CHUNK_SIZE": int(os.getenv("REMOTE_CUDA_MVS_UPLOAD_CHUNK_SIZE", "1048576")),
    "DOWNLOAD_CHUNK_SIZE": int(os.getenv("REMOTE_CUDA_MVS_DOWNLOAD_CHUNK_SIZE", "1048576")),

    "MAX_RETRIES": int(os.getenv("REMOTE_CUDA_MVS_MAX_RETRIES", "3")),
    "BASE_RETRY_DELAY": float(os.getenv("REMOTE_CUDA_MVS_BASE_RETRY_DELAY", "1.0")),
    "MAX_RETRY_DELAY": float(os.getenv("REMOTE_CUDA_MVS_MAX_RETRY_DELAY", "60.0")),
    "JITTER_FACTOR": float(os.getenv("REMOTE_CUDA_MVS_JITTER_FACTOR", "0.1")),

    "HEALTH_CHECK_INTERVAL": int(os.getenv("REMOTE_CUDA_MVS_HEALTH_CHECK_INTERVAL", "60")),
    "CIRCUIT_BREAKER_THRESHOLD": int(os.getenv("REMOTE_CUDA_MVS_CIRCUIT_BREAKER_THRESHOLD", "5")),
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": float(
        os.getenv("REMOTE_CUDA_MVS_CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30.0")
    ),

    "DEFAULT_RECONSTRUCTION_QUALITY": os.getenv("REMOTE_CUDA_MVS_DEFAULT_QUALITY", "normal"),
    "DEFAULT_OUTPUT_FORMAT": os.getenv("REMOTE_CUDA_MVS_DEFAULT_FORMAT", "obj"),
    "POLL_INTERVAL": int(os.getenv("REMOTE_CUDA_MVS_POLL_INTERVAL", "5")),
    "POLL_TIMEOUT": int(os.getenv("REMOTE_CUDA_MVS_POLL_TIMEOUT", "1800")),

    "OUTPUT_DIR": MODELS_DIR,
    "IMAGES_DIR": IMAGES_DIR,
    "MULTI_VIEW_DIR": MULTI_VIEW_DIR,
    "APPROVED_IMAGES_DIR": APPROVED_IMAGES_DIR,
}

IMAGE_APPROVAL = {
    "ENABLED": os.getenv("IMAGE_APPROVAL_ENABLED", "True").lower() == "true",
    "AUTO_APPROVE": os.getenv("IMAGE_APPROVAL_AUTO_APPROVE", "False").lower() == "true",
    "MIN_APPROVED_IMAGES": int(os.getenv("IMAGE_APPROVAL_MIN_IMAGES", "3")),
    "APPROVED_IMAGES_DIR": APPROVED_IMAGES_DIR,
}

MULTI_VIEW = {
    "DEFAULT_NUM_VIEWS": int(os.getenv("MULTI_VIEW_DEFAULT_NUM_VIEWS", "4")),
    "MIN_NUM_VIEWS": int(os.getenv("MULTI_VIEW_MIN_NUM_VIEWS", "3")),
    "MAX_NUM_VIEWS": int(os.getenv("MULTI_VIEW_MAX_NUM_VIEWS", "16")),
    "OUTPUT_DIR": MULTI_VIEW_DIR,
}

# How long a deferred-generation request stays resumable.
DELEGATION_TTL_SECONDS = int(os.getenv("DELEGATION_TTL_SECONDS", "900"))


def ensure_directories() -> None:
    """Create the output tree. Called at server start, not at import."""
    for directory in (
        OUTPUT_DIR, IMAGES_DIR, MULTI_VIEW_DIR, APPROVED_IMAGES_DIR, MODELS_DIR, SCAD_DIR
    ):
        os.makedirs(directory, exist_ok=True)
