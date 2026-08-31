# Changelog

Notable changes to `industrial-mcp`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project has not
cut a tagged release yet, so everything below `0.1.0` lives under Unreleased.

## [Unreleased]

The stretch where this stopped being a scaffold. Both the read and the write
path now run against physical hardware — a SiloScan module (`agrostar-s3-onewire`)
with DS18B20 probes on a OneWire mux and a relay on GPIO5.

### Added

- `PlantAdapter` Protocol in `adapters/base.py` — the structural contract every
  adapter satisfies. `INDUSTRIAL_MCP_ADAPTER` now actually selects the adapter;
  `Config` had been reading that variable with nothing consuming it.
- `Esp32Adapter` (`adapters/esp32.py`) — HTTP to a live module on the LAN.
  Read path via `GET /api/status`, `/api/config`, `/api/probe?i=N`; write path
  via `GET`/`POST /api/relay` on firmware 0.4.0 and later.
- `INDUSTRIAL_MCP_ESP32_HOST` and `INDUSTRIAL_MCP_ESP32_CABLES` environment
  variables. `CABLES` defaults to channel `0` alone — each channel costs a
  OneWire conversion, so the default is cheap rather than complete.
- `sensors_total`, `sensors_valid` and `faulted_sensors` on the thermometry
  snapshot, so a sensor failure reaches the model instead of vanishing into an
  average.
- `docs/hardware-verification.md` and `docs/verificacion-hardware.md` — the
  evidence, in English and Spanish, including an explicit *Not verified*
  section.
- Hardware-recorded fixtures in `tests/fixtures/`, including a real CRC fault
  captured over 451 polls and both relay states.
- `scan_devices` tool and `adapters/discovery.py` — find modules that took a
  new DHCP lease. Name lookup first (`<device_id>.local`, firmware 0.5.0+),
  subnet sweep second. The tool is read-only and **never edits configuration**:
  `claude_desktop_config.json` is read once at host startup, so rewriting it
  would fix a stale address one restart too late, and a model-writable file
  that decides which tools load with which environment is a privilege
  escalation path, not a convenience.
- Optional `INDUSTRIAL_MCP_ESP32_DEVICE_ID`. When set, the adapter repoints
  itself mid-session if the configured host goes quiet — but only to a device
  that identifies itself as that id.
- This changelog.

### Changed

- The write path decides success by **reading the pin back**, not by the module
  accepting the POST. A 200 response with the pin still low returns
  `ok: False` and `state: "fault"`.
- `trigger_motor_action` derives plant context from the motor's own `plant_id`
  instead of the hardcoded `"plant-nw-1"`. A motor without one is refused
  rather than evaluated against the wrong plant.
- `Tools` is typed against `PlantAdapter` instead of importing `MockAdapter` —
  the coupling was to a type, not to behaviour.
- `examples/demo-transcript.md` is now a real captured session. It used to be
  an invented 7-silo plant with 28 fans, which sat badly in a repo arguing that
  models should not report numbers nobody measured.
- `examples/architecture.txt` no longer diagrams an `mqtt.py` that was never
  written.
- README `Status` is a table separating what is verified from what is not.

### Fixed

- **Dangerous instruction removed from the docs.** Four files told readers to
  trigger a mismatch with "a jumper holding GPIO5 down while the firmware
  drives it high". Shorting a driven-HIGH ESP32-S3 output to ground draws well
  past the 40 mA per-pad maximum, and a resistor large enough to be safe cannot
  pull the pin down at all. It was also the wrong measurement: reading back your
  own output detects a dead GPIO driver, not a relay that failed to close.
  Replaced with the feedback-input approach.
- An unknown `INDUSTRIAL_MCP_ADAPTER` value raises at startup instead of
  silently falling back to the mock. Serving fake data when someone asked for
  the plant is worse than refusing to start.
- A fixture that asserted a per-sensor `open` state the firmware cannot emit.
  Every fixture is now recorded from hardware; none are derived.

### Known limits

Carried here deliberately rather than left to the commit log:

- The verified actuator is a **logic-level GPIO with an LED**, not a motor.
  Inrush current, interlocks and run-feedback contacts are untouched.
- The `mismatch` → `fault` branch is implemented and unit-tested but has
  **never been triggered on hardware**. Firmware 0.6.0 adds a separate feedback
  input so the fault can be produced by removing a wire; that firmware is not
  yet flashed or verified.
- Only one actuator exists (`fan-1`); `RELAY_ID` is a compile-time constant in
  the firmware.
- The module has **no authentication**. Acceptable on a lab LAN over stdio,
  where nothing reaches it from outside. Not acceptable exposed.
- Discovery costs the full request timeout before it starts — a stale host
  takes ~5 s to fail, then ~5 s more to relocate and retry. Correct, not fast.
- Polling `/api/probe` faster than about once every 5 s wedges the module's
  HTTP server permanently — the OneWire read blocks the AsyncTCP callback. The
  board keeps answering ping, which makes it look like a network fault. Fix
  belongs in the firmware.
- No MQTT or OPC UA adapter.

## [0.1.0] — 2026-07-15

### Added

- Initial scaffold: six MCP tools over stdio (`list_plants`, `list_silos`,
  `get_silo_thermometry`, `list_motors`, `get_active_alerts`,
  `trigger_motor_action`).
- Three-layer write gate — `dry_run` default, `operator_id` + `reason`
  required, `INDUSTRIAL_MCP_ALLOW_WRITES` deploy posture — plus safety
  preconditions and advisory warnings in `safety.py`.
- Append-only JSONL audit log of executed actions. Dry runs are not logged.
- Deterministic mock adapter modelling a 7-silo grain facility, so CI runs
  with no infrastructure.
