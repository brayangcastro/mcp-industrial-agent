"""Tool definitions exposed over MCP.

Read tools return data. Write tools (only one — ``trigger_motor_action``)
default to ``dry_run=True`` and require both an explicit flag flip AND
a server-side opt-in (``INDUSTRIAL_MCP_ALLOW_WRITES=true``) before they
touch anything.

This file is the contract the LLM sees. Keep docstrings short, exact,
and free of marketing prose — they become the model's tool descriptions.
"""

from __future__ import annotations

from typing import Any, Literal

from industrial_mcp.adapters.base import PlantAdapter
from industrial_mcp.audit import AuditLog
from industrial_mcp.config import Config
from industrial_mcp.safety import evaluate_motor_action


class Tools:
    """Bundle of tool implementations bound to one adapter + config."""

    def __init__(self, adapter: PlantAdapter, config: Config, audit: AuditLog) -> None:
        self.adapter = adapter
        self.config = config
        self.audit = audit

    # ── read tools ────────────────────────────────────────────────

    def list_plants(self) -> list[dict[str, Any]]:
        """List industrial facilities the server can see."""
        return self.adapter.list_plants()

    def list_silos(self, plant_id: str) -> list[dict[str, Any]]:
        """List grain silos at a plant, with capacity in metric tons."""
        return self.adapter.list_silos(plant_id)

    def get_silo_thermometry(self, silo_id: str) -> dict[str, Any]:
        """Return the latest thermometry snapshot for one silo.

        Includes fill percent, cable count, and min/max/avg grain
        temperature in Celsius. Use this when the operator asks about
        a specific silo's health.
        """
        return self.adapter.get_silo_thermometry(silo_id)

    def list_motors(
        self, plant_id: str, kind: Literal["fan", "conveyor", "elevator"] | None = None
    ) -> list[dict[str, Any]]:
        """List motors at a plant. Optionally filter by kind."""
        return self.adapter.list_motors(plant_id, kind=kind)

    def get_active_alerts(
        self, plant_id: str, min_severity: Literal["info", "warning", "critical"] = "info"
    ) -> list[dict[str, Any]]:
        """List active alerts at a plant, optionally filtered by minimum severity."""
        return self.adapter.get_active_alerts(plant_id, min_severity=min_severity)

    # ── write tools (safety-gated) ────────────────────────────────

    def trigger_motor_action(
        self,
        motor_id: str,
        action: Literal["start", "stop"],
        dry_run: bool = True,
        operator_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Request a start or stop of an industrial motor.

        Defaults to a DRY RUN: returns what would happen, evaluates
        safety preconditions, and lists warnings — without sending any
        command to the field. To actually execute:

        1. Pass ``dry_run=False``.
        2. Pass ``operator_id`` (recorded in the audit log).
        3. Pass ``reason`` (short free-text justification).
        4. The server itself must be started with
           ``INDUSTRIAL_MCP_ALLOW_WRITES=true``; otherwise the call is
           rejected even when the LLM sets the flags.

        Every executed command is appended to an audit log; dry-run
        calls are not logged.
        """
        motor = self.adapter.get_motor(motor_id)
        if motor is None:
            return {
                "ok": False,
                "phase": "lookup",
                "reason": f"unknown motor {motor_id!r}",
            }

        # The plant comes from the motor record, never from a constant and
        # never from the model: evaluating a fan against the wrong plant's
        # silo temperatures would pass safety checks that should have failed.
        plant_id = motor.get("plant_id")
        if not plant_id:
            return {
                "ok": False,
                "phase": "lookup",
                "reason": f"motor {motor_id!r} has no plant_id; adapter cannot resolve plant context",
            }

        plant_ctx = self.adapter.get_plant_context(plant_id)
        result = evaluate_motor_action(motor, action, plant_ctx)

        report = {
            "motor_id": motor_id,
            "current_state": motor.get("state"),
            "requested_action": action,
            "would_execute": result.allowed,
            "preconditions": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in result.checks
            ],
            "warnings": result.warnings,
        }

        if dry_run:
            return {"phase": "dry_run", **report, "next_step": "set dry_run=False, provide operator_id and reason"}

        if not operator_id or not reason:
            return {
                "ok": False,
                "phase": "rejected",
                "reason": "live execution requires operator_id and reason",
                **report,
            }

        if not self.config.allow_writes:
            return {
                "ok": False,
                "phase": "rejected_server_policy",
                "reason": "server started in read-only mode (INDUSTRIAL_MCP_ALLOW_WRITES is not true)",
                **report,
            }

        if not result.allowed:
            self.audit.record(
                actor=operator_id,
                action=f"motor.{action}",
                target=motor_id,
                outcome="blocked_by_safety",
                details={"checks": [c.name for c in result.checks if not c.passed]},
            )
            return {
                "ok": False,
                "phase": "blocked_by_safety",
                **report,
            }

        applied = self.adapter.apply_motor_action(motor_id, action)
        self.audit.record(
            actor=operator_id,
            action=f"motor.{action}",
            target=motor_id,
            outcome="applied" if applied.get("ok") else "failed",
            details={"reason": reason, "warnings": result.warnings},
        )
        return {"ok": applied.get("ok", False), "phase": "executed", **report, **applied}
