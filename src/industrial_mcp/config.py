"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the MCP server.

    All values come from environment variables — no secrets in code.
    """

    adapter: str
    audit_log_path: str
    allow_writes: bool

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            adapter=os.environ.get("INDUSTRIAL_MCP_ADAPTER", "mock"),
            audit_log_path=os.environ.get(
                "INDUSTRIAL_MCP_AUDIT_LOG", "audit_industrial.jsonl"
            ),
            allow_writes=os.environ.get("INDUSTRIAL_MCP_ALLOW_WRITES", "false").lower()
            == "true",
        )
