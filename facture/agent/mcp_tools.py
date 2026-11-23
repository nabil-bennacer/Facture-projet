"""Client MCP de l'agent : lance notre serveur MCP (stdio) et lui délègue les appels.

L'agent n'a AUCUN autre accès aux données : tout passe par ces outils.
"""
from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client, StdioServerParameters

from facture.agent.config import ROOT
from facture.agent.providers.base import ToolSpec


class McpTools:
    def __init__(self, command: list[str] | None = None):
        self.command = command or [sys.executable, "-m", "facture.mcp_server"]
        self.specs: list[ToolSpec] = []
        self._stack = AsyncExitStack()
        self._client: Client | None = None

    async def __aenter__(self) -> "McpTools":
        params = StdioServerParameters(command=self.command[0], args=self.command[1:],
                                       env=dict(os.environ), cwd=str(ROOT))
        self._client = await self._stack.enter_async_context(Client(params))
        listing = await self._client.list_tools()
        self.specs = [ToolSpec(t.name, t.description or "", t.input_schema) for t in listing.tools]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()

    @property
    def names(self) -> set[str]:
        return {s.name for s in self.specs}

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        assert self._client is not None
        res = await self._client.call_tool(name, arguments)
        text = "\n".join(getattr(c, "text", "") for c in res.content)
        return text, bool(res.is_error)
