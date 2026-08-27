"""Image approval backed by MCP elicitation.

The user decides which captured views feed reconstruction. The server asks
through the connected client rather than through a web page of its own.
"""

import glob
import logging
import os
import shutil
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ApprovalResponse(BaseModel):
    """Elicitation schema. Fields stay primitive; nested models are rejected."""

    approved: bool = Field(description="Use this image for reconstruction?")
    reason: str = Field(default="", description="Optional note about the decision")


class ImageApprovalTool:
    """Stores approved images and asks the user about the rest."""

    def __init__(self, output_dir: str = "output/approved_images"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    async def request_approval(self, ctx, image_path: str, label: str = "") -> dict[str, Any]:
        """Ask the user whether to keep one image.

        Returns a dict carrying `action` (accept, decline, cancel), `approved`,
        and `approved_path` when the image was copied.
        """
        name = label or os.path.basename(image_path)
        result = await ctx.elicit(
            message=f"Use {name} for 3D reconstruction?",
            schema=ApprovalResponse,
        )

        if result.action != "accept":
            logger.info("Approval for %s ended with %s", name, result.action)
            return {"action": result.action, "approved": False, "image_path": image_path}

        if not result.data.approved:
            return {
                "action": "accept",
                "approved": False,
                "image_path": image_path,
                "reason": result.data.reason,
            }

        return {
            "action": "accept",
            "approved": True,
            "image_path": image_path,
            "approved_path": self.accept(image_path),
            "reason": result.data.reason,
        }

    def accept(self, image_path: str) -> str:
        """Copy an image into the approved set and return its new path."""
        approved_path = os.path.join(self.output_dir, os.path.basename(image_path))
        os.makedirs(self.output_dir, exist_ok=True)
        shutil.copy2(image_path, approved_path)
        return approved_path

    def get_approved_images(self, filter_pattern: str | None = None) -> list[str]:
        pattern = os.path.join(self.output_dir, filter_pattern or "*")
        return sorted(glob.glob(pattern))

    def clear(self) -> int:
        """Empty the approved set. Returns how many files were removed."""
        removed = 0
        for path in self.get_approved_images():
            os.remove(path)
            removed += 1
        return removed
