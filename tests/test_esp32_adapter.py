"""Esp32Adapter against recorded responses from a real SiloScan module.

The fixtures in ``tests/fixtures/`` were captured from device
``banco-silo3`` (firmware ``agrostar-s3-onewire`` 0.3.0) on 2026-08-28.
See that folder's README for which are recorded and which are derived.

The test that matters most is ``test_faulted_sensor_is_not_averaged_as_zero``:
a probe that cannot be read must never contribute a number to an average.
A dead thermocouple counted as 0 °C turns a hot-grain alarm into a
reassuring one, and that is the failure this whole repo exists to stop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from industrial_mcp.adapters.base import PlantAdapter
from industrial_mcp.adapters.esp32 import Esp32Adapter

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _adapter(routes: dict[str, Any], cables: list[int] | None = None) -> Esp32Adapter:
    """Build an adapter whose HTTP calls are served from ``routes``."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        if request.url.query:
            key = f"{key}?{request.url.query.decode()}"
        if key not in routes:
            return httpx.Response(404, json={"error": "no route"})
        return httpx.Response(200, json=routes[key])

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://192.168.1.71"
    )
    return Esp32Adapter(host="192.168.1.71", cables=cables, client=client)


def _dead_adapter() -> Esp32Adapter:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("device is asleep or unplugged")

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://192.168.1.71"
    )
    return Esp32Adapter(host="192.168.1.71", client=client)


# ── contract ──────────────────────────────────────────────────────


def test_satisfies_the_adapter_protocol() -> None:
    assert isinstance(_adapter({}), PlantAdapter)


# ── read path with sensors attached ───────────────────────────────


def _populated_routes() -> dict[str, Any]:
    return {
        "/api/status": _fixture("esp32_status.json"),
        "/api/config": _fixture("esp32_config.json"),
        "/api/probe?i=0": _fixture("esp32_probe_i0_populated.derived.json"),
    }


def test_maps_min_max_avg_from_valid_sensors_only() -> None:
    snapshot = _adapter(_populated_routes()).get_silo_thermometry("silo-3")

    # Valid readings in the fixture: 24.31, 25.06, 26.75
    assert snapshot["min_temp_c"] == 24.31
    assert snapshot["max_temp_c"] == 26.75
    assert snapshot["avg_temp_c"] == 25.37
    assert snapshot["sensors_valid"] == 3
    assert snapshot["sensors_total"] == 4


def test_faulted_sensor_is_not_averaged_as_zero() -> None:
    snapshot = _adapter(_populated_routes()).get_silo_thermometry("silo-3")

    # Had the unreadable channel been folded in as 0 °C, the average would
    # drop to ~19 and the max would still look fine — a silo that is losing
    # its instrumentation would read as a silo that is cool.
    assert snapshot["avg_temp_c"] > 25.0
    faulted = {f["punto"]: f["estado"] for f in snapshot["faulted_sensors"]}
    assert faulted == {"P3": "fault"}


def test_aggregates_two_real_cables_recorded_from_the_device() -> None:
    """Recorded from banco-silo3 with two DS18B20 cables on mux channels 0 and 8.

    Channel 0 was warmed by hand (31.5 °C) while channel 8 was chilled in a
    glass (15.5 °C) — a 16-degree spread that the average alone reports as an
    unremarkable 23.5 °C. Both probes read valid; nothing was faulty. That is
    why the snapshot carries min and max, not just avg: a real hot spot next to
    a cold one averages out to a number that looks like neither.
    """
    routes = {
        "/api/config": _fixture("esp32_config.json"),
        "/api/probe?i=0": _fixture("esp32_probe_i0_populated.json"),
        "/api/probe?i=8": _fixture("esp32_probe_i8_populated.json"),
    }
    snapshot = _adapter(routes, cables=[0, 8]).get_silo_thermometry("silo-3")

    assert snapshot["min_temp_c"] == 15.5
    assert snapshot["max_temp_c"] == 31.5
    assert snapshot["avg_temp_c"] == 23.5
    assert snapshot["cable_count"] == 2
    assert snapshot["sensors_valid"] == 2
    assert snapshot["faulted_sensors"] == []


def test_capacity_and_fill_are_none_not_zero() -> None:
    snapshot = _adapter(_populated_routes()).get_silo_thermometry("silo-3")
    # The module measures grain temperature and nothing else. Zero would
    # read as "the silo is empty", which is a claim it cannot make.
    assert snapshot["capacity_t"] is None
    assert snapshot["fill_pct"] is None


# ── read path with nothing attached (recorded state) ──────────────


def test_module_with_no_cable_reports_no_reading_not_zero() -> None:
    routes = {
        "/api/config": _fixture("esp32_config.json"),
        "/api/probe?i=0": _fixture("esp32_probe_i0.json"),  # {"sensores": [], "present": false}
    }
    snapshot = _adapter(routes).get_silo_thermometry("silo-3")

    assert snapshot["error"] == "no_valid_readings"
    assert snapshot["max_temp_c"] is None
    assert snapshot["avg_temp_c"] is None
    assert snapshot["cable_count"] == 0


def test_blind_module_raises_a_sensor_health_alert_not_silence() -> None:
    """No temperature is not the same as no problem."""
    routes = {
        "/api/config": _fixture("esp32_config.json"),
        "/api/probe?i=0": _fixture("esp32_probe_i0.json"),
    }
    alerts = _adapter(routes).get_active_alerts("banco-silo3")

    assert [a["kind"] for a in alerts] == ["sensor_health"]
    assert alerts[0]["severity"] == "warning"


def test_hot_silo_alerts_against_the_configured_threshold() -> None:
    hot = _fixture("esp32_probe_i0_populated.derived.json")
    hot["sensores"][0]["temp"] = 34.2  # umbral in the recorded config is 30
    routes = {**_populated_routes(), "/api/probe?i=0": hot}

    alerts = _adapter(routes).get_active_alerts("banco-silo3")
    assert [a["kind"] for a in alerts] == ["temperature"]
    assert alerts[0]["severity"] == "critical"


# ── device unreachable ────────────────────────────────────────────


def test_unreachable_device_returns_structured_error_without_raising() -> None:
    snapshot = _dead_adapter().get_silo_thermometry("silo-3")
    assert snapshot["error"] == "device_unreachable"
    assert "192.168.1.71" in snapshot["detail"]


def test_unreachable_device_lists_nothing_rather_than_guessing() -> None:
    adapter = _dead_adapter()
    assert adapter.list_plants() == []
    assert adapter.list_silos("banco-silo3") == []


# ── write path is honestly absent ─────────────────────────────────


def test_motor_action_is_refused_because_the_firmware_has_no_relay() -> None:
    result = _adapter({}).apply_motor_action("fan-1", "start")
    assert result["ok"] is False
    assert "no motor or relay endpoint" in result["reason"]


# ── config parsing ────────────────────────────────────────────────


def test_from_env_requires_a_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDUSTRIAL_MCP_ESP32_HOST", raising=False)
    with pytest.raises(ValueError, match="INDUSTRIAL_MCP_ESP32_HOST"):
        Esp32Adapter.from_env()


def test_from_env_parses_cable_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDUSTRIAL_MCP_ESP32_HOST", "192.168.1.71")
    monkeypatch.setenv("INDUSTRIAL_MCP_ESP32_CABLES", "0,3,7")
    assert Esp32Adapter.from_env().cables == [0, 3, 7]
