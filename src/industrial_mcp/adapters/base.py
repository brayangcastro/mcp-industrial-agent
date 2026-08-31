"""Structural contract every adapter must satisfy.

The tool layer talks to an adapter, never to a transport. A mock holds
dicts in memory; a real one speaks HTTP to a device on the plant LAN.
Both are interchangeable only if they agree on this shape.

Motor records returned by ``get_motor`` and ``list_motors`` must carry
``plant_id`` — the safety layer needs the plant context that owns the
motor, and guessing it is how you evaluate a fan against the wrong silo.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlantAdapter(Protocol):
    """What the tool layer needs from any backend, mock or real."""

    name: str

    def list_plants(self) -> list[dict[str, Any]]: ...
    def list_silos(self, plant_id: str) -> list[dict[str, Any]]: ...
    def get_silo_thermometry(self, silo_id: str) -> dict[str, Any]: ...
    def list_motors(self, plant_id: str, kind: str | None = None) -> list[dict[str, Any]]: ...
    def get_motor(self, motor_id: str) -> dict[str, Any] | None: ...
    def get_plant_context(self, plant_id: str) -> dict[str, Any]: ...
    def get_active_alerts(self, plant_id: str, min_severity: str = "info") -> list[dict[str, Any]]: ...
    def apply_motor_action(self, motor_id: str, action: str) -> dict[str, Any]: ...
