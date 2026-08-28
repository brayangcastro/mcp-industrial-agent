"""Mock adapter — deterministic fake data for development and demos.

Models a single grain-storage facility with 7 silos, ~19 thermocouple
cables per silo, and a small fleet of motors (fans, conveyors,
elevators). Values are seeded by silo id and current minute, so they
change slowly over time without being random across runs.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any


def _seeded(*keys: str | int, lo: float = 0.0, hi: float = 1.0) -> float:
    """Stable pseudo-random in [lo, hi) from a tuple of keys."""
    h = hashlib.sha256(":".join(str(k) for k in keys).encode()).digest()
    v = int.from_bytes(h[:8], "big") / 2**64
    return lo + v * (hi - lo)


_SILOS: list[dict[str, Any]] = [
    {"id": "silo-1", "capacity_t": 8500, "fill_pct": 0.40, "cables": 19},
    {"id": "silo-2", "capacity_t": 8500, "fill_pct": 0.99, "cables": 19},
    {"id": "silo-3", "capacity_t": 8500, "fill_pct": 0.97, "cables": 19},
    {"id": "silo-4", "capacity_t": 5000, "fill_pct": 0.95, "cables": 14},
    {"id": "silo-5", "capacity_t": 5000, "fill_pct": 0.94, "cables": 14},
    {"id": "silo-6", "capacity_t": 1000, "fill_pct": 0.65, "cables": 7},
    {"id": "silo-7", "capacity_t": 15000, "fill_pct": 0.76, "cables": 24},
]


_PLANT_ID = "plant-nw-1"


# Every motor carries its plant: the safety layer resolves plant context
# from the motor record, so a motor without one cannot be actioned.
_MOTORS: list[dict[str, Any]] = [
    # Fans (4 per silo on 1-7)
    *[
        {
            "id": f"fan-{i}-{n}",
            "kind": "fan",
            "plant_id": _PLANT_ID,
            "silo_id": f"silo-{i}",
            "state": "stopped",
        }
        for i in range(1, 8)
        for n in range(1, 5)
    ],
    # Conveyors / elevators
    {"id": "elev-main", "kind": "elevator", "plant_id": _PLANT_ID, "silo_id": None, "state": "stopped"},
    {"id": "conv-top", "kind": "conveyor", "plant_id": _PLANT_ID, "silo_id": None, "state": "stopped"},
    {"id": "conv-pit", "kind": "conveyor", "plant_id": _PLANT_ID, "silo_id": None, "state": "stopped"},
]


class MockAdapter:
    """In-memory adapter. Safe to use in CI and in demos."""

    name = "mock"

    def __init__(self) -> None:
        self._motors = {m["id"]: dict(m) for m in _MOTORS}

    # ── read paths ────────────────────────────────────────────────

    def list_plants(self) -> list[dict[str, Any]]:
        return [
            {
                "id": _PLANT_ID,
                "label": "Northwestern Mexico grain facility",
                "silo_count": len(_SILOS),
                "motor_count": len(_MOTORS),
            }
        ]

    def list_silos(self, plant_id: str) -> list[dict[str, Any]]:
        if plant_id != _PLANT_ID:
            return []
        return [{"id": s["id"], "capacity_t": s["capacity_t"]} for s in _SILOS]

    def get_silo_thermometry(self, silo_id: str) -> dict[str, Any]:
        silo = next((s for s in _SILOS if s["id"] == silo_id), None)
        if silo is None:
            return {"error": f"unknown silo {silo_id!r}"}

        bucket = int(time.time() // 60)
        readings: list[float] = []
        for cable in range(1, silo["cables"] + 1):
            for level in range(1, 8):
                t = _seeded(silo_id, cable, level, bucket, lo=22.0, hi=33.0)
                readings.append(round(t, 1))

        return {
            "silo_id": silo_id,
            "capacity_t": silo["capacity_t"],
            "fill_pct": silo["fill_pct"],
            "cable_count": silo["cables"],
            "min_temp_c": min(readings),
            "max_temp_c": max(readings),
            "avg_temp_c": round(sum(readings) / len(readings), 1),
            "sampled_at": bucket * 60,
        }

    def list_motors(self, plant_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        if plant_id != _PLANT_ID:
            return []
        return [
            dict(m) for m in self._motors.values() if kind is None or m["kind"] == kind
        ]

    def get_motor(self, motor_id: str) -> dict[str, Any] | None:
        m = self._motors.get(motor_id)
        return dict(m) if m else None

    def get_plant_context(self, plant_id: str) -> dict[str, Any]:
        # Mirror live state plus weather, for safety checks.
        silos = {s["id"]: self.get_silo_thermometry(s["id"]) for s in _SILOS}
        return {
            "plant_id": plant_id,
            "silos": silos,
            "weather": {"is_raining": False, "ambient_c": 28.0},
        }

    def get_active_alerts(
        self, plant_id: str, min_severity: str = "info"
    ) -> list[dict[str, Any]]:
        severities = {"info": 0, "warning": 1, "critical": 2}
        threshold = severities.get(min_severity, 0)
        out: list[dict[str, Any]] = []
        ctx = self.get_plant_context(plant_id)
        for silo_id, snap in ctx["silos"].items():
            if isinstance(snap, dict) and snap.get("max_temp_c", 0) >= 30.5:
                sev = "critical" if snap["max_temp_c"] >= 32 else "warning"
                if severities[sev] >= threshold:
                    out.append(
                        {
                            "id": f"alert-{silo_id}-temp",
                            "target": silo_id,
                            "kind": "temperature",
                            "severity": sev,
                            "detail": f"max temp {snap['max_temp_c']}°C",
                        }
                    )
        return out

    # ── write paths ───────────────────────────────────────────────

    def apply_motor_action(self, motor_id: str, action: str) -> dict[str, Any]:
        m = self._motors.get(motor_id)
        if m is None:
            return {"ok": False, "reason": f"unknown motor {motor_id!r}"}
        m["state"] = "running" if action == "start" else "stopped"
        return {"ok": True, "motor_id": motor_id, "new_state": m["state"]}
