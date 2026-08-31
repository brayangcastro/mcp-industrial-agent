"""Entrypoint — wire tools into an MCP stdio server.

Run with::

    uvx industrial-mcp@latest

or, from a checkout::

    uv run industrial-mcp
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from industrial_mcp.adapters.base import PlantAdapter
from industrial_mcp.adapters.mock import MockAdapter
from industrial_mcp.audit import AuditLog
from industrial_mcp.config import Config
from industrial_mcp.tools import Tools


def build_adapter(config: Config) -> PlantAdapter:
    """Select the adapter named by ``INDUSTRIAL_MCP_ADAPTER``.

    Unknown names raise instead of falling back to the mock: a server
    that quietly serves fake data when you asked for the plant is worse
    than one that refuses to start.
    """
    if config.adapter == "mock":
        return MockAdapter()
    if config.adapter == "esp32":
        # Imported inside the branch so the mock path never needs httpx.
        try:
            from industrial_mcp.adapters.esp32 import Esp32Adapter
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on checkout
            raise ValueError(
                "adapter 'esp32' is declared but not implemented in this checkout"
            ) from exc

        return Esp32Adapter.from_env()
    raise ValueError(f"unknown adapter {config.adapter!r} (known: mock, esp32)")


def build_server() -> FastMCP:
    config = Config.from_env()
    adapter = build_adapter(config)
    audit = AuditLog(config.audit_log_path)
    tools = Tools(adapter=adapter, config=config, audit=audit)

    mcp = FastMCP("industrial-mcp")

    mcp.tool()(tools.list_plants)
    mcp.tool()(tools.list_silos)
    mcp.tool()(tools.get_silo_thermometry)
    mcp.tool()(tools.list_motors)
    mcp.tool()(tools.get_active_alerts)
    mcp.tool()(tools.scan_devices)
    mcp.tool()(tools.trigger_motor_action)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
