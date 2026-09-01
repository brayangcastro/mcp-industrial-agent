"""Adapter for a live SiloScan ESP32-S3 module over HTTP.

Talks to the device's own web server on the plant LAN — not to the cloud
backend. The device is the only place where a sensor's failure state
exists: the round it uploads carries temperatures, but the per-channel
classification (``valid`` / ``open`` / ``fault``) is computed on the
module and is what keeps a dead probe from being read as a cold one.

Firmware: ``agrostar-s3-onewire`` — DS18B20 chains behind a 16-way mux,
one OneWire rail. Endpoints used:

``GET /api/status``     device health, firmware, silo
``GET /api/config``     configured silo and alert threshold
``GET /api/probe?i=N``  live read of one cable: per-sensor estado + temp
``GET /api/relay``      actuator level read back off the pin (0.4.0+)
``POST /api/relay``     drive the actuator (0.4.0+)

Builds before 0.4.0 have no relay endpoint. The write path handles that
by staying a refusal — ``list_motors`` comes back empty and
``apply_motor_action`` reports the 404 rather than claiming success.

The firmware already refuses to lie: ``temp`` is ``null`` unless the
channel classified as ``CH_OK``, and an 85.00 °C power-on-reset reading
(which passes CRC) is classified out of range rather than reported. This
adapter's job is to not undo that on the way to the model.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from industrial_mcp.adapters import discovery

# Channel vocabulary emitted by the firmware (payload.h:31).
_VALID = "valid"

_DEFAULT_TIMEOUT_S = 5.0


class Esp32Adapter:
    """Read path against one SiloScan module."""

    name = "esp32"

    def __init__(
        self,
        host: str,
        cables: list[int] | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
        device_id: str | None = None,
    ) -> None:
        self.host = host
        # Which mux channels to read. One instrumented cable is the
        # default: probing all 16 costs a OneWire conversion each.
        self.cables = cables if cables is not None else [0]
        self._timeout_s = timeout_s
        # Set: the client was supplied (tests), so relocating would swap out
        # the transport under the caller's feet. Discovery stays off.
        self._client_is_external = client is not None
        self._client = client or httpx.Client(
            base_url=f"http://{host}", timeout=timeout_s
        )
        # Which module this adapter is for. Without it a sweep cannot tell
        # "the device moved" from "some other module answered", so discovery
        # only runs when this is known.
        self.device_id = device_id
        self.relocated_from: str | None = None

    @classmethod
    def from_env(cls) -> Esp32Adapter:
        host = os.environ.get("INDUSTRIAL_MCP_ESP32_HOST")
        if not host:
            raise ValueError(
                "adapter 'esp32' requires INDUSTRIAL_MCP_ESP32_HOST (device IP or hostname)"
            )
        raw = os.environ.get("INDUSTRIAL_MCP_ESP32_CABLES", "0")
        cables = [int(part) for part in raw.split(",") if part.strip()]
        return cls(
            host=host,
            cables=cables,
            device_id=os.environ.get("INDUSTRIAL_MCP_ESP32_DEVICE_ID"),
        )

    # ── transport ─────────────────────────────────────────────────

    def _request(self, path: str) -> dict[str, Any] | list[Any] | None:
        try:
            response = self._client.get(path)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _relocate(self) -> bool:
        """The configured host went quiet. Find the module by identity.

        Returns True if a new host was adopted. This repoints the *running*
        session, which is the part a config file cannot do: that file is read
        once at host startup, so rewriting it would fix things one restart
        too late.

        Only ever adopts a host that identifies itself as ``device_id``.
        Pointing thermometry tools at whatever else happens to answer on the
        subnet would be worse than staying broken.
        """
        if self.device_id is None or self._client_is_external:
            return False
        found = discovery.find_device(self.device_id)
        if found is None or found == self.host:
            return False
        self.relocated_from = self.host
        self.host = found
        self._client.close()
        self._client = httpx.Client(base_url=f"http://{found}", timeout=self._timeout_s)
        return True

    def _get(self, path: str) -> dict[str, Any] | list[Any] | None:
        """GET and parse JSON. Returns None on any failure — never raises.

        A tool that raises turns an unreachable module into a broken
        server; a tool that returns None lets the caller say "I could not
        reach the device", which is the true answer.

        One retry, and only after the module has been re-identified at a new
        address — not a blind retry, which would just double the wait on a
        device that is genuinely off.
        """
        result = self._request(path)
        if result is None and self._relocate():
            result = self._request(path)
        return result

    def _unreachable(self, what: str) -> dict[str, Any]:
        return {
            "error": "device_unreachable",
            "detail": f"no response from SiloScan module at {self.host} while reading {what}",
        }

    # ── read paths ────────────────────────────────────────────────

    def list_plants(self) -> list[dict[str, Any]]:
        status = self._get("/api/status")
        if not isinstance(status, dict):
            return []
        return [
            {
                "id": status.get("device_id"),
                "label": f"SiloScan module {status.get('device_id')} (fw {status.get('fw')})",
                "silo_count": 1,
                "motor_count": 0,
                "uptime_s": status.get("uptime_s"),
                "queued_rounds": status.get("cola"),
            }
        ]

    def list_silos(self, plant_id: str) -> list[dict[str, Any]]:
        config = self._get("/api/config")
        if not isinstance(config, dict) or config.get("device_id") != plant_id:
            return []
        return [
            {
                "id": f"silo-{config.get('silo')}",
                # The module measures grain temperature; it has no idea how
                # much grain is in the bin. None, not a plausible number.
                "capacity_t": None,
            }
        ]

    def get_silo_thermometry(self, silo_id: str) -> dict[str, Any]:
        readings: list[float] = []
        cables_present = 0
        sensors_total = 0
        faults: list[dict[str, Any]] = []

        for index in self.cables:
            cable = self._get(f"/api/probe?i={index}")
            if not isinstance(cable, dict):
                return self._unreachable(f"cable {index}")
            if cable.get("present"):
                cables_present += 1
            for sensor in cable.get("sensores") or []:
                sensors_total += 1
                estado = sensor.get("estado")
                temp = sensor.get("temp")
                # Both conditions matter. estado gates on the firmware's
                # classification; the None check catches a 'valid' channel
                # whose temperature still came back absent. Averaging a
                # missing reading as 0 °C is how a hot silo reads cool.
                if estado == _VALID and temp is not None:
                    readings.append(float(temp))
                else:
                    faults.append(
                        {
                            "cable": index,
                            "punto": sensor.get("punto"),
                            "estado": estado,
                            "rom": sensor.get("rom"),
                        }
                    )

        snapshot: dict[str, Any] = {
            "silo_id": silo_id,
            "capacity_t": None,
            "fill_pct": None,
            "cable_count": cables_present,
            "sensors_total": sensors_total,
            "sensors_valid": len(readings),
            "faulted_sensors": faults,
            # The module's clock is only right after an NTP sync, so the
            # honest timestamp is the one from the machine doing the read.
            "sampled_at": int(time.time()),
        }

        if not readings:
            snapshot.update(
                {
                    "error": "no_valid_readings",
                    "detail": (
                        f"{sensors_total} sensor(s) seen on cable(s) {self.cables}, "
                        "none classified valid by the module"
                    ),
                    "min_temp_c": None,
                    "max_temp_c": None,
                    "avg_temp_c": None,
                }
            )
            return snapshot

        snapshot.update(
            {
                "min_temp_c": round(min(readings), 2),
                "max_temp_c": round(max(readings), 2),
                "avg_temp_c": round(sum(readings) / len(readings), 2),
            }
        )
        return snapshot

    def get_plant_context(self, plant_id: str) -> dict[str, Any]:
        silos = {s["id"]: self.get_silo_thermometry(s["id"]) for s in self.list_silos(plant_id)}
        return {
            "plant_id": plant_id,
            "silos": silos,
            # No weather sensor on this module and no forecast wired in.
            # An empty dict reads as "unknown"; inventing 'not raining'
            # would silently satisfy a safety check that never ran.
            "weather": {},
        }

    def get_active_alerts(self, plant_id: str, min_severity: str = "info") -> list[dict[str, Any]]:
        config = self._get("/api/config")
        if not isinstance(config, dict):
            return []
        threshold = config.get("umbral")
        if threshold is None:
            return []

        severities = {"info": 0, "warning": 1, "critical": 2}
        floor = severities.get(min_severity, 0)

        alerts: list[dict[str, Any]] = []
        for silo_id, snapshot in self.get_plant_context(plant_id)["silos"].items():
            max_temp = snapshot.get("max_temp_c")
            if max_temp is None:
                # Not "no alert" — the module could not measure. Say so,
                # because silence here looks identical to a cool silo.
                if severities["warning"] >= floor:
                    alerts.append(
                        {
                            "id": f"alert-{silo_id}-blind",
                            "target": silo_id,
                            "kind": "sensor_health",
                            "severity": "warning",
                            "detail": snapshot.get("detail") or snapshot.get("error", "no reading"),
                        }
                    )
                continue
            if max_temp >= threshold:
                severity = "critical" if max_temp >= threshold + 2 else "warning"
                if severities[severity] >= floor:
                    alerts.append(
                        {
                            "id": f"alert-{silo_id}-temp",
                            "target": silo_id,
                            "kind": "temperature",
                            "severity": severity,
                            "detail": f"max temp {max_temp}°C (threshold {threshold}°C)",
                        }
                    )
        return alerts

    # ── write paths ───────────────────────────────────────────────
    #
    # /api/relay publishes three things, and keeping them apart is the
    # whole design: ``commanded`` is the last order, ``drive`` is the level
    # on the output pin, and ``state`` is the actuator's own feedback line
    # (firmware 0.6.0+, a second GPIO).
    #
    # Reading back only the output pin — which is what 0.4.0 did — detects
    # a dead GPIO driver and nothing else. The failures that matter are the
    # relay not closing, the coil going open, the contactor not pulling in,
    # and in every one of those the output still reads "on" while the fan
    # sits still. So ``state`` comes from the feedback, and ``drive`` is
    # kept alongside it because together they say *which* half broke.

    def _relay(self) -> dict[str, Any] | None:
        relay = self._get("/api/relay")
        return relay if isinstance(relay, dict) else None

    @staticmethod
    def _mismatch_reason(relay: dict[str, Any]) -> str:
        """Say which half failed, not just that something did.

        ``drive`` on with feedback off is the actuator: wiring, coil,
        contacts. ``drive`` off when it was commanded on is the GPIO
        itself. An operator sent to the wrong half wastes the outage.
        """
        commanded = relay.get("commanded")
        if relay.get("drive") == commanded:
            return (
                f"commanded {commanded} and pin {relay.get('pin')} is driving it, "
                f"but feedback on pin {relay.get('pin_fb')} reads "
                f"{relay.get('state')} — the actuator did not follow"
            )
        return (
            f"commanded {commanded} but pin {relay.get('pin')} is not driving "
            f"({relay.get('drive')}) — the output stage did not follow"
        )

    def _motor_from_relay(self, relay: dict[str, Any], plant_id: str) -> dict[str, Any]:
        """Shape one relay as the motor record the tool layer expects.

        ``state`` is what the actuator reports about itself, not what it
        was told. When the two disagree the motor is ``fault``, which
        safety.py already refuses to act on — that refusal is the whole
        point of the module publishing both.
        """
        measured_on = relay.get("state") == "on"
        if relay.get("mismatch"):
            state = "fault"
        else:
            state = "running" if measured_on else "stopped"
        return {
            "id": relay.get("id"),
            "plant_id": plant_id,
            "silo_id": self._silo_id(),
            "kind": "fan",
            "drive": relay.get("drive"),
            "state": state,
            "commanded": relay.get("commanded"),
            "pin": relay.get("pin"),
        }

    def _plant_id(self) -> str | None:
        status = self._get("/api/status")
        return status.get("device_id") if isinstance(status, dict) else None

    def _silo_id(self) -> str | None:
        """The silo this actuator belongs to, so safety.py can look up its
        temperature before letting anyone stop the fan over hot grain."""
        config = self._get("/api/config")
        if not isinstance(config, dict) or config.get("silo") is None:
            return None
        return f"silo-{config.get('silo')}"

    def list_motors(self, plant_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        relay = self._relay()
        if relay is None:
            # Older firmware has no such endpoint. An empty list is the
            # truth for those builds; inventing a motor would be worse.
            return []
        motor = self._motor_from_relay(relay, plant_id)
        if kind is not None and motor["kind"] != kind:
            return []
        return [motor]

    def get_motor(self, motor_id: str) -> dict[str, Any] | None:
        relay = self._relay()
        if relay is None or relay.get("id") != motor_id:
            return None
        plant_id = self._plant_id()
        if plant_id is None:
            return None
        return self._motor_from_relay(relay, plant_id)

    def apply_motor_action(self, motor_id: str, action: str) -> dict[str, Any]:
        """Drive the relay, then report the actuator — not the command.

        The success of a write is decided by what the hardware reports
        back about itself. A POST that returned 200 while the actuator
        never engaged is a failure, and this is the layer that has to
        notice.
        """
        state = {"start": "on", "stop": "off"}.get(action)
        if state is None:
            return {"ok": False, "reason": f"unknown action {action!r} (known: start, stop)"}

        try:
            response = self._client.post(
                "/api/relay", json={"id": motor_id, "state": state}
            )
        except httpx.HTTPError:
            return {
                "ok": False,
                "reason": f"no response from SiloScan module at {self.host} while driving {motor_id}",
            }

        if response.status_code == 404:
            return {"ok": False, "reason": f"module has no actuator named {motor_id!r}"}
        if response.status_code >= 400:
            return {"ok": False, "reason": f"module refused the write (HTTP {response.status_code})"}

        try:
            relay = response.json()
        except ValueError:
            return {"ok": False, "reason": "module returned a non-JSON response"}

        if relay.get("mismatch"):
            return {
                "ok": False,
                "reason": self._mismatch_reason(relay),
                "state": "fault",
                "drive": relay.get("drive"),
            }
        return {"ok": True, "state": relay.get("state"), "commanded": relay.get("commanded")}
