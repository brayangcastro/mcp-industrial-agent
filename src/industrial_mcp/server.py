"""Entrypoint — wire tools into an MCP stdio server.

Run with::

    uvx industrial-mcp@latest

or, from a checkout::

    uv run industrial-mcp
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from industrial_mcp.adapters.mock import MockAdapter
from industrial_mcp.audit import AuditLog
from industrial_mcp.config import Config
from industrial_mcp.tools import Tools


def build_server() -> FastMCP:
    config = Config.from_env()
    adapter = MockAdapter()  # only adapter shipped in this scaffold
    audit = AuditLog(config.audit_log_path)
    tools = Tools(adapter=adapter, config=config, audit=audit)

    mcp = FastMCP("industrial-mcp")

    mcp.tool()(tools.list_plants)
    mcp.tool()(tools.list_silos)
    mcp.tool()(tools.get_silo_thermometry)
    mcp.tool()(tools.list_motors)
    mcp.tool()(tools.get_active_alerts)
    mcp.tool()(tools.trigger_motor_action)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
