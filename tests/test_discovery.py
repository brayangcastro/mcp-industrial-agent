"""Finding a module that moved, and refusing to find the wrong one.

The bench device took three DHCP leases in three reboots, which is what
this exists for. The test that matters is
``test_never_adopts_a_module_with_a_different_id``: repointing thermometry
tools at whatever else answered the sweep would be worse than staying
broken, because the reading would look fine and be from another silo.
"""

from __future__ import annotations

from typing import Any

import pytest

from industrial_mcp.adapters import discovery
from industrial_mcp.adapters.esp32 import Esp32Adapter

# ── scan boundaries ───────────────────────────────────────────────


def test_public_ranges_are_refused() -> None:
    with pytest.raises(ValueError, match="not a private network"):
        discovery.scan_subnet("8.8.8.0/24")


def test_oversized_ranges_are_refused() -> None:
    """A /8 sweep is 16 million probes. Refuse rather than hang."""
    with pytest.raises(ValueError, match="addresses"):
        discovery.scan_subnet("10.0.0.0/8")


def test_scan_tool_reports_a_refusal_instead_of_raising() -> None:
    """A refused range is an answer the model can act on, not a crash."""
    from industrial_mcp.audit import AuditLog
    from industrial_mcp.config import Config
    from industrial_mcp.tools import Tools

    tools = Tools(
        adapter=Esp32Adapter(host="192.0.2.1", client=_never_answers()),
        config=Config(adapter="esp32", audit_log_path="t.jsonl", allow_writes=False),
        audit=AuditLog("t.jsonl"),
    )
    result = tools.scan_devices(subnet="8.8.8.0/24")
    assert result["ok"] is False
    assert result["devices"] == []
    assert "private" in result["reason"]


# ── identity, not availability ────────────────────────────────────


def _never_answers() -> Any:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("moved")

    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://192.0.2.1"
    )


def test_never_adopts_a_module_with_a_different_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sweep that finds *a* module must not adopt the *wrong* module.

    Silo 3's tools pointing at silo 7's sensors would report plausible
    temperatures from the wrong bin — a failure no downstream check can
    catch, because nothing about the reading looks wrong.
    """
    monkeypatch.setattr(discovery, "resolve_by_name", lambda device_id: None)
    monkeypatch.setattr(
        discovery,
        "scan_subnet",
        lambda subnet=None: [{"host": "192.168.1.55", "device_id": "otro-silo7"}],
    )
    assert discovery.find_device("banco-silo3") is None


def test_finds_the_module_by_name_before_sweeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mDNS first: a name lookup costs milliseconds, a sweep costs seconds."""
    monkeypatch.setattr(
        discovery,
        "resolve_by_name",
        lambda device_id: {"host": f"{device_id}.local", "device_id": device_id},
    )

    def _must_not_run(subnet: str | None = None) -> list[dict[str, Any]]:
        raise AssertionError("swept the subnet when the name already resolved")

    monkeypatch.setattr(discovery, "scan_subnet", _must_not_run)
    assert discovery.find_device("banco-silo3") == "banco-silo3.local"


# ── adapter self-healing ──────────────────────────────────────────


def test_adapter_without_a_device_id_does_not_go_looking() -> None:
    """Discovery is opt-in. With no id, a sweep cannot verify identity."""
    adapter = Esp32Adapter(host="192.0.2.1", client=_never_answers())
    assert adapter._relocate() is False
    snapshot = adapter.get_silo_thermometry("silo-3")
    assert snapshot["error"] == "device_unreachable"


def test_supplied_client_is_never_swapped_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests inject a transport; relocating would yank it mid-run."""
    monkeypatch.setattr(discovery, "find_device", lambda d, s=None: "192.168.1.99")
    adapter = Esp32Adapter(
        host="192.0.2.1", client=_never_answers(), device_id="banco-silo3"
    )
    assert adapter._relocate() is False
    assert adapter.host == "192.0.2.1"


def test_relocates_and_remembers_where_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery, "find_device", lambda d, s=None: "banco-silo3.local")
    adapter = Esp32Adapter(host="192.168.1.100", device_id="banco-silo3")

    assert adapter._relocate() is True
    assert adapter.host == "banco-silo3.local"
    # Kept so an operator can see the address moved rather than wondering
    # why the configured one stopped being used.
    assert adapter.relocated_from == "192.168.1.100"


def test_from_env_reads_the_device_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDUSTRIAL_MCP_ESP32_HOST", "banco-silo3.local")
    monkeypatch.setenv("INDUSTRIAL_MCP_ESP32_DEVICE_ID", "banco-silo3")
    assert Esp32Adapter.from_env().device_id == "banco-silo3"
