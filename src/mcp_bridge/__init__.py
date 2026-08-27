"""Routing between this server and whatever model is calling it.

The server holds no API key and contacts no model provider. Work that needs
generation goes to the connected client: as a registered MCP prompt the client
runs itself (`prompts`), or as a deferred request the client answers and hands
back (`delegation`).
"""

from src.mcp_bridge.delegation import DelegationStore, PendingExpired, PendingNotFound
from src.mcp_bridge.prompts import register_prompts

__all__ = [
    "DelegationStore",
    "PendingExpired",
    "PendingNotFound",
    "register_prompts",
]
