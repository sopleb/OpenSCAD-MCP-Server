"""The regression test for this whole change.

The server must reach no model provider and require no API key.
"""

import os
import re
import subprocess

import pytest

# Hosts and SDKs the server used to depend on, plus the ones it must not grow.
FORBIDDEN = [
    "generativelanguage.googleapis.com",
    "api.venice.ai",
    "api.openai.com",
    "api.anthropic.com",
    "api.x.ai",
    "google.generativeai",
    "import openai",
    "import anthropic",
    "GEMINI_API_KEY",
    "VENICE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]


def test_no_ai_provider_references_in_source(repo_root):
    src = os.path.join(repo_root, "src")
    offenders = []
    for needle in FORBIDDEN:
        result = subprocess.run(
            ["grep", "-rn", "-F", needle, src], capture_output=True, text=True
        )
        if result.returncode == 0:
            offenders.append(f"{needle}: {result.stdout.strip()}")
    assert not offenders, "Server source reaches an AI provider:\n" + "\n".join(offenders)


def test_config_declares_no_api_keys(repo_root):
    config = open(os.path.join(repo_root, "src", "config.py")).read()
    keys = re.findall(r'os\.getenv\(\s*"(\w*API_KEY\w*)"', config)
    # The one remaining key authenticates a self-hosted LAN reconstruction
    # server, not a model provider.
    assert keys == ["REMOTE_CUDA_MVS_API_KEY"], keys


def test_requirements_carry_no_provider_sdks(repo_root):
    requirements = open(os.path.join(repo_root, "requirements.txt")).read().lower()
    for package in ("google-generativeai", "openai", "anthropic", "cohere", "mistralai"):
        assert package not in requirements, f"{package} is back in requirements"


def test_server_imports_without_env_or_cuda_mvs(repo_root):
    """The original blocker: the server refused to start without a key or CUDA MVS."""
    result = subprocess.run(
        ["python", "-c", "import src.main; print(len(src.main.models))"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env={**os.environ, "CUDA_MVS_PATH": "/nonexistent", "PYTHONPATH": repo_root},
    )
    assert result.returncode == 0, result.stderr
