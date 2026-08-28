"""The adapter is chosen by config, and an unknown name fails loudly.

A server that silently falls back to the mock when you ask for real
hardware is the exact failure this repo exists to prevent: it would
answer questions about a plant it is not connected to.
"""

from __future__ import annotations

import pytest

from industrial_mcp.adapters.base import PlantAdapter
from industrial_mcp.adapters.mock import MockAdapter
from industrial_mcp.config import Config
from industrial_mcp.server import build_adapter


def _cfg(adapter: str) -> Config:
    return Config(adapter=adapter, audit_log_path="test_audit.jsonl", allow_writes=False)


def test_mock_adapter_satisfies_protocol() -> None:
    assert isinstance(MockAdapter(), PlantAdapter)


def test_build_adapter_returns_mock_by_name() -> None:
    assert build_adapter(_cfg("mock")).name == "mock"


def test_unknown_adapter_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter(_cfg("nonexistent"))


def test_declared_but_unimplemented_adapter_says_so() -> None:
    """Asking for esp32 before it exists must not surface a raw ImportError."""
    try:
        import industrial_mcp.adapters.esp32  # noqa: F401
    except ModuleNotFoundError:
        with pytest.raises(ValueError, match="not implemented in this checkout"):
            build_adapter(_cfg("esp32"))
    else:
        pytest.skip("esp32 adapter is implemented; nothing to assert here")
