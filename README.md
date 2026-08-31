# industrial-mcp

> An MCP server that gives Claude (or any MCP-compatible AI host) read
> access to industrial sensor data and **safety-gated** control over
> motors and actuators.

The hard part of *Physical AI* isn't getting a language model to talk
about a factory — it's getting it to **act** on one without anyone
losing sleep. That requires three boring things working together:

1. **Tool schemas** the model can understand and call correctly.
2. **Safety preconditions** that block dangerous actions even when the
   model is confident.
3. **An audit trail** that survives the next post-mortem.

This repo is a working implementation of all three, in under 500 lines
of Python, using [FastMCP][fastmcp]. Default mode is read-only and
ships with a deterministic mock adapter modeling a 7-silo grain
storage facility, so it runs in CI with no infrastructure.

The read path is **verified end to end against real hardware**: an
ESP32-S3 with DS18B20 probes on a OneWire mux, answering a model's
questions about grain temperature over the LAN. Warming one probe by
hand moved `max_temp_c` from 28.0 to 31.0 — and dropped the *average*,
because the other probe was in cold water. That measurement is why
these tools return `min`, `max`, and `faulted_sensors` instead of one
reassuring number. Evidence, including what is **not** verified:
[`docs/hardware-verification.md`](docs/hardware-verification.md)
([español](docs/verificacion-hardware.md)).

[fastmcp]: https://github.com/modelcontextprotocol/python-sdk

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│  MCP host  (Claude Desktop, Claude Code, Cursor, etc.)              │
│   user prompt ──► model decides which tool to call                  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  stdio  (JSON-RPC over MCP)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  industrial-mcp  (this repo)                                        │
│                                                                     │
│   server.py ── wires tools, env config, audit log                   │
│      │                                                              │
│      ├── tools.py ── 5 read tools + 1 write tool (dry-run default)  │
│      │                                                              │
│      ├── safety.py ── preconditions, advisory warnings              │
│      │                                                              │
│      ├── audit.py ── append-only JSONL of executed actions          │
│      │                                                              │
│      └── adapters/                                                  │
│            ├── base.py    ← PlantAdapter protocol (the contract)    │
│            ├── mock.py    ← default, deterministic demo data        │
│            └── esp32.py   ← HTTP to a live SiloScan module          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
   [MQTT broker]      [internal REST API]   [historian / TSDB]
```

---

## Quick start

Run the server, talk to it from Claude Desktop, see it work.

```bash
# 1. Run the server with the mock adapter (no infra required)
uvx industrial-mcp@latest

# 2. Add this to ~/Library/Application Support/Claude/claude_desktop_config.json
#    (macOS) — see examples/claude-desktop-config.json
```

Then in Claude Desktop:

> *¿Qué silos tienen alerta crítica hoy?*

Claude calls `list_plants` → `get_active_alerts` → answers with the
data. See [`examples/demo-transcript.md`](examples/demo-transcript.md)
for a full session.

To enable live (non-dry-run) execution against the mock adapter:

```bash
INDUSTRIAL_MCP_ALLOW_WRITES=true uvx industrial-mcp@latest
```

This flag does **not** affect dry runs — those always work. It only
unlocks the path where a tool call would actually mutate state.

---

## Tools exposed over MCP

| Tool | Type | Purpose |
| --- | --- | --- |
| `list_plants` | read | List facilities the server can see. |
| `list_silos` | read | Silos at a plant with capacity in tons. |
| `get_silo_thermometry` | read | Latest thermometry snapshot (min/avg/max °C). |
| `list_motors` | read | Motors at a plant, optionally filtered by kind. |
| `get_active_alerts` | read | Active alerts, filterable by severity. |
| `trigger_motor_action` | **write** | Start/stop a motor — dry-run by default, safety-gated, audited. |

Tool schemas live in [`src/industrial_mcp/tools.py`](src/industrial_mcp/tools.py).
Keep their docstrings short and exact — they become the model's tool
descriptions.

---

## Why three layers of safety, not one

`trigger_motor_action` will only execute when **all** of the following
hold:

1. The LLM explicitly sets `dry_run=False`.
2. The call includes an `operator_id` and a `reason`.
3. The server itself was started with `INDUSTRIAL_MCP_ALLOW_WRITES=true`.
4. Every precondition in `safety.evaluate_motor_action` passes.

Step 3 is the one that matters most. The model can hallucinate
`dry_run=False`; the operator field can be spoofed by a clever prompt;
the safety check can have a bug. But if the server was started
read-only, **none of that touches a motor**. The deploy posture is the
last word, not the prompt.

Every executed call is appended to an append-only JSONL audit log:

```json
{"ts": 1779600000.12, "actor": "op-42", "action": "motor.start",
 "target": "fan-7-1", "outcome": "applied",
 "details": {"reason": "silo-7 at 32.1°C, manual fan-on"}}
```

Dry runs are not logged. They're not interesting and they'd dilute
the signal.

---

## Adapters

The contract every adapter must satisfy lives in
`src/industrial_mcp/adapters/base.py` as a `PlantAdapter` Protocol —
`list_plants`, `list_silos`, `get_silo_thermometry`, `list_motors`,
`get_motor`, `get_plant_context`, `get_active_alerts`,
`apply_motor_action`. Motor records must carry `plant_id`; the tool
layer resolves plant context from the motor rather than from a
constant, so a motor without one is refused instead of evaluated
against the wrong plant.

`INDUSTRIAL_MCP_ADAPTER` selects which one runs. It knows `mock`
(deterministic, shipped, used by CI) and `esp32` (HTTP to a live
SiloScan thermometry module). An unrecognized name raises at startup —
the server never falls back to the mock, because answering questions
about a plant you are not connected to is worse than refusing to start.

```bash
INDUSTRIAL_MCP_ADAPTER=esp32 \
INDUSTRIAL_MCP_ESP32_HOST=192.168.1.100 \
INDUSTRIAL_MCP_ESP32_CABLES=0,8 \
uv run industrial-mcp
```

`INDUSTRIAL_MCP_ESP32_CABLES` defaults to channel `0` alone — each
channel costs a OneWire conversion, so the default is cheap rather than
complete. Leaving it out silently drops every other probe.

Two things the `esp32` adapter does that are worth copying into your
own adapter, both of them about not laundering a sensor fault into a
plausible number:

- A reading counts only when the firmware classified it `valid` **and**
  the temperature is actually present. Averaging a missing reading as
  0 °C is how a hot silo reads cool.
- An unreachable module returns `{"error": "device_unreachable"}`, never
  an exception and never a stale number wearing a fresh timestamp. The
  timestamp comes from the machine doing the read, because the module's
  clock is only right after an NTP sync.

To talk to a different plant, drop a sibling module implementing the
Protocol and add its name to `build_adapter` in `server.py`.

---

## Development

```bash
git clone https://github.com/brayangcastro/mcp-industrial-agent
cd mcp-industrial-agent
uv sync --extra dev
uv run pytest -v
uv run ruff check src tests
```

CI runs the same two commands on Python 3.11 and 3.12 — see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## FAQ

**Why not just give the model raw API keys?**
Because then every prompt injection is one HTTP call away from a
production motor. The MCP surface is narrow on purpose: the model
sees six functions, not your AWS console.

**Why a separate `dry_run` flag instead of a confirmation step?**
Confirmation steps add latency and break flow. The dry run returns
the *outcome the model would have caused* — preconditions, warnings,
state delta — as data the model can keep reasoning over. Live
execution is then a one-line escalation, not a five-prompt dance.

**Is the audit log enough for compliance?**
No. It's enough to reconstruct *what happened*. It is not enough on
its own for IEC 62443, IATF 16949, or similar. Pair it with your
plant's existing change-management system; this repo is a starting
point, not a finished compliance story.

**Is this only a mock? Where's MQTT / OPC UA?**
No longer only a mock — the `esp32` adapter reads a live device over
HTTP, and the read path is verified against it. MQTT and OPC UA are
still absent, and they are usually plant-specific anyway: the broker
URLs, topic structures, and ACLs you can publish publicly are usually
zero. The `PlantAdapter` Protocol is the extension point.

**So the safety gate is proven against real equipment?**
No, and this is the honest limit. The verified part is the **read**
path. The module's firmware exposes no relay or motor endpoint, so
`list_motors` returns `[]` and `apply_motor_action` refuses with a
stated reason. The three-layer write gate still guards a dictionary in
memory. Wiring it to a physical relay is the next piece of work.

---

## Status

Read path verified against hardware; write path not yet. Not 1.0 —
the shapes are stable, but expect breaking changes in non-public APIs.

| Area | State |
| --- | --- |
| `mock` adapter | Shipped, deterministic, used by CI |
| `esp32` adapter, read path | **Verified against a live module** — [evidence](docs/hardware-verification.md) |
| Safety gate + audit log | Implemented and tested; **still guarding in-memory state** |
| Write path against real hardware | Not done |
| MQTT / OPC UA adapters | Not started |

## Author

Built and maintained by [Brayan Castro](https://ingebc.com) — Ing.
Mecatrónica (ITESM Sonora Norte), operating BC Ingeniería from Guasave,
Sinaloa. Background: 4+ years building IoT systems for agroindustrial
grain handling (firmware ESP32 + thermocouple MUX + cloud), backend
services for property management and POS, and conversational AI agents
on top of Claude / GPT-4o. Reach out: `info@ingebc.com`.

## License

MIT — see [LICENSE](LICENSE).
