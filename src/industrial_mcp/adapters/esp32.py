"""Adapter for a live SiloScan ESP32-S3 module over HTTP.

Talks to the device's own web server on the plant LAN — not to the cloud
backend. The device is the only place where a sensor's failure state
exists: the round it uploads carries temperatures, but the per-channel
classification (``valid`` / ``open`` / ``fault``) is computed on the
module and is what keeps a dead probe from being read as a cold one.

Firmware: ``agrostar-s3-onewire`` 0.3.0 — DS18B20 chains behind a 16-way
mux, one OneWire rail. Endpoints used:

``GET /api/status``     device health, firmware, silo
``GET /api/config``     configured silo and alert threshold
``GET /api/probe?i=N``  live read of one cable: per-sensor estado + temp

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
    ) -> None:
        self.host = host
        # Which mux channels to read. One instrumented cable is the
        # default: probing all 16 costs a OneWire conversion each.
        self.cables = cables if cables is not None else [0]
        self._client = client or httpx.Client(
            base_url=f"http://{host}", timeout=timeout_s
        )

    @classmethod
    def from_env(cls) -> Esp32Adapter:
        host = os.environ.get("INDUSTRIAL_MCP_ESP32_HOST")
        if not host:
            raise ValueError(
                "adapter 'esp32' requires INDUSTRIAL_MCP_ESP32_HOST (device IP or hostname)"
            )
        raw = os.environ.get("INDUSTRIAL_MCP_ESP32_CABLES", "0")
        cables = [int(part) for part in raw.split(",") if part.strip()]
        return cls(host=host, cables=cables)

    # ── transport ─────────────────────────────────────────────────

    def _get(self, path: str) -> dict[str, Any] | list[Any] | None:
        """GET and parse JSON. Returns None on any failure — never raises.

        A tool that raises turns an unreachable module into a broken
        server; a tool that returns None lets the caller say "I could not
        reach the device", which is the true answer.
        """
        try:
            response = self._client.get(path)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

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
    # Firmware 0.3.0 exposes no relay or motor endpoint. Returning empty
    # lists and a refusal is the truth; a fake success here would be the
    # exact failure the safety layer exists to prevent.

    def list_motors(self, plant_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        return []

    def get_motor(self, motor_id: str) -> dict[str, Any] | None:
        return None

    def apply_motor_action(self, motor_id: str, action: str) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": "SiloScan firmware 0.3.0 exposes no motor or relay endpoint",
        }
