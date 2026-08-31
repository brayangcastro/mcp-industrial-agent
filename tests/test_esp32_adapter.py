"""Esp32Adapter against recorded responses from a real SiloScan module.

The fixtures in ``tests/fixtures/`` were captured from device
``banco-silo3`` (firmware ``agrostar-s3-onewire``): the sensor ones on
2026-08-28 against 0.3.0, the relay ones on 2026-08-30 against 0.4.0.
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
        "/api/probe?i=0": _fixture("esp32_probe_i0_populated.json"),
        "/api/probe?i=8": _fixture("esp32_probe_i8_populated.json"),
    }


def test_faulted_sensor_is_not_averaged_as_zero() -> None:
    """The fault fixture was captured from the device, not constructed.

    Wiggling cable 0's connector produced one bad read in 451 polls: the ROM
    still enumerated and ``present`` stayed true, but the scratchpad failed
    CRC, so the module reported ``estado: fault`` with ``temp: null``. That is
    the shape a loose terminal makes in the field — the device knows the probe
    is there and refuses to give a number for it.
    """
    routes = {
        "/api/config": _fixture("esp32_config.json"),
        "/api/probe?i=0": _fixture("esp32_probe_fault.json"),
        "/api/probe?i=8": _fixture("esp32_probe_i8_populated.json"),
    }
    snapshot = _adapter(routes, cables=[0, 8]).get_silo_thermometry("silo-3")

    # Only cable 8's 15.5 °C is a measurement. Folding the faulted probe in as
    # 0 °C would report 7.75 °C — a silo losing its instrumentation reading as
    # a silo that is merely cold.
    assert snapshot["avg_temp_c"] == 15.5
    assert snapshot["min_temp_c"] == 15.5
    assert snapshot["max_temp_c"] == 15.5
    assert snapshot["sensors_valid"] == 1
    assert snapshot["sensors_total"] == 2

    assert len(snapshot["faulted_sensors"]) == 1
    faulted = snapshot["faulted_sensors"][0]
    assert faulted["estado"] == "fault"
    assert faulted["cable"] == 0
    # The ROM survives into the report, so an operator knows which probe to go
    # look at rather than being told "a sensor somewhere failed".
    assert faulted["rom"] == "2839a47997140315"


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
    hot = _fixture("esp32_probe_i0_populated.json")
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


# ── write path on firmware without a relay ────────────────────────


def test_motor_action_is_refused_when_the_firmware_has_no_relay() -> None:
    """Builds before 0.4.0 have no /api/relay. The 404 has to stay a
    refusal: a write that landed nowhere must never report success."""
    result = _adapter({}).apply_motor_action("fan-1", "start")
    assert result["ok"] is False
    assert "no actuator named" in result["reason"]


def test_no_motors_listed_when_the_firmware_has_no_relay() -> None:
    assert _adapter({}).list_motors("banco-silo3") == []


# ── write path against firmware 0.4.0 ─────────────────────────────
#
# esp32_relay_on/off.json are recorded from device banco-silo3 running
# 0.4.0. The mismatch payload below is the one exception in this suite
# and says so: it is constructed rather than captured, because on 0.4.0
# there was no safe way to make the pin disagree with the command.
# Firmware 0.6.0 adds a feedback input so the fault can be produced by
# pulling a wire; once that is captured, this becomes a fixture.


def _relay_adapter(relay: dict[str, Any]) -> Esp32Adapter:
    """Adapter whose /api/relay answers both GET and POST with ``relay``."""
    routes = {
        "/api/status": _fixture("esp32_status.json"),
        "/api/config": _fixture("esp32_config.json"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/relay":
            return httpx.Response(200, json=relay)
        if request.url.path in routes:
            return httpx.Response(200, json=routes[request.url.path])
        return httpx.Response(404, json={"error": "no route"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://192.168.1.100"
    )
    return Esp32Adapter(host="192.168.1.100", client=client)


def test_relay_off_is_a_stopped_motor() -> None:
    motors = _relay_adapter(_fixture("esp32_relay_off.json")).list_motors("banco-silo3")
    assert len(motors) == 1
    assert motors[0]["id"] == "fan-1"
    assert motors[0]["state"] == "stopped"
    # safety.py needs both of these to weigh a fan against its silo.
    assert motors[0]["plant_id"] == "banco-silo3"
    assert motors[0]["silo_id"] == "silo-3"


def test_relay_on_is_a_running_motor() -> None:
    motors = _relay_adapter(_fixture("esp32_relay_on.json")).list_motors("banco-silo3")
    assert motors[0]["state"] == "running"


def test_starting_the_motor_reports_the_pin_not_the_command() -> None:
    result = _relay_adapter(_fixture("esp32_relay_on.json")).apply_motor_action(
        "fan-1", "start"
    )
    assert result["ok"] is True
    assert result["state"] == "on"


def test_a_pin_that_does_not_follow_the_command_is_a_fault() -> None:
    """The test this endpoint exists for.

    The module was told ``on`` and the pin still reads ``off``. Reporting
    success would tell an operator a fan is running while the grain keeps
    heating — the physical version of averaging a dead sensor as 0 °C.
    """
    stuck = {
        "id": "fan-1",
        "state": "off",       # measured at the pin
        "commanded": "on",    # what it was told
        "pin": 5,
        "mismatch": True,
    }
    adapter = _relay_adapter(stuck)

    result = adapter.apply_motor_action("fan-1", "start")
    assert result["ok"] is False
    assert "did not follow" in result["reason"]

    # And it stays visible as a fault, so safety.py blocks the next action
    # instead of retrying against broken hardware.
    assert adapter.list_motors("banco-silo3")[0]["state"] == "fault"


def test_unknown_action_is_refused_without_touching_the_device() -> None:
    result = _relay_adapter(_fixture("esp32_relay_off.json")).apply_motor_action(
        "fan-1", "explode"
    )
    assert result["ok"] is False
    assert "unknown action" in result["reason"]


def test_unreachable_module_does_not_raise_on_the_write_path() -> None:
    result = _dead_adapter().apply_motor_action("fan-1", "start")
    assert result["ok"] is False
    assert "no response" in result["reason"]


# ── config parsing ────────────────────────────────────────────────


def test_from_env_requires_a_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDUSTRIAL_MCP_ESP32_HOST", raising=False)
    with pytest.raises(ValueError, match="INDUSTRIAL_MCP_ESP32_HOST"):
        Esp32Adapter.from_env()


def test_from_env_parses_cable_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDUSTRIAL_MCP_ESP32_HOST", "192.168.1.71")
    monkeypatch.setenv("INDUSTRIAL_MCP_ESP32_CABLES", "0,3,7")
    assert Esp32Adapter.from_env().cables == [0, 3, 7]
