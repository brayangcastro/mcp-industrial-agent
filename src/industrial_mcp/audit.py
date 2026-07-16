"""Append-only audit log for every state-changing operation.

Every call to a control tool (e.g. ``trigger_motor_action`` with
``dry_run=False``) is recorded as one JSON line in the audit file.
Reads are NOT logged here — they would dilute the signal and have no
safety implication.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


class AuditLog:
    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def record(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "ts": time.time(),
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
            "details": details or {},
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
