# ESP32 fixtures

Responses from a real SiloScan module (`agrostar-s3-onewire`, device
`banco-silo3`) captured over the LAN: the sensor captures on 2026-08-28 against
firmware 0.3.0, the relay captures on 2026-08-30 against 0.4.0.

| File | Source | Notes |
|---|---|---|
| `esp32_status.json` | **recorded** | `GET /api/status` |
| `esp32_config.json` | **recorded** | `GET /api/config` |
| `esp32_last.json` | **recorded** | `GET /api/last` — summary of the last full scan |
| `esp32_historial.json` | **recorded** | `GET /api/historial` — note `temp_max: null`, not `0` |
| `esp32_probe_i0.json` | **recorded** | `GET /api/probe?i=0` with **no cable attached** |
| `esp32_logs.txt` | **recorded** | `GET /api/logs` — plain text ring buffer, not JSON |
| `esp32_probe_i0_populated.json` | **recorded** | `GET /api/probe?i=0`, one DS18B20 warmed by hand — 31.5 °C |
| `esp32_probe_i8_populated.json` | **recorded** | `GET /api/probe?i=8`, one DS18B20 chilled in a glass — 15.5 °C |
| `esp32_last_populated.json` | **recorded** | `GET /api/last` with both cables present |
| `esp32_probe_fault.json` | **recorded** | `GET /api/probe?i=0` with the data line briefly grounded — ROM enumerates, scratchpad fails CRC, `estado: fault` / `temp: null` |
| `esp32_relay_off.json` | **recorded** | `GET /api/relay` with GPIO5 low — `state` is the pin, `commanded` is the order |
| `esp32_relay_on.json` | **recorded** | `GET /api/relay` with GPIO5 high |

**Every fixture here is recorded from hardware. None are derived.** The fault
capture took 451 polls over 98 seconds of wiggling one connector; it is the
same physical probe as `esp32_probe_i0_populated.json` (identical `rom`), which
is what makes the pair useful — the same sensor, reading and then not reading.

Per sensor, `estado` can only be `valid` or `fault`. The vocabulary in
`payload.h:31-35` also defines `open` (`CH_EMPTY`), but `owScanCable` cannot
emit it at the sensor level: a sensor is only added to the array once its ROM
enumerates, and `owReadSensor` then overwrites `kind` with
`classifyChannel(true, true, …)`, which never returns `CH_EMPTY`. An absent
cable surfaces as `present: false` with an empty `sensores` array instead —
that is `esp32_probe_i0.json`.

The empty captures were taken with **nothing plugged into the mux**, which is
why every temperature is absent. That is not a degenerate case to work around —
it is the fixture behind one of the most important tests in the suite: a module
that cannot see a sensor must not report a temperature.

The two populated captures were taken simultaneously with one probe warm and
one cold, 16 degrees apart and both valid. Their average, 23.5 °C, resembles
neither — which is the case the snapshot's `min_temp_c` / `max_temp_c` exist to
keep visible.

There is no `esp32_relay_mismatch.json`, and the absence is deliberate. The test
suite covers that branch with a payload constructed inline in
`test_esp32_adapter.py`, which says so at the point of use — a constructed
payload is fine as long as nothing in this folder implies it came off a device.

⚠️ **This file used to say the capture needed "a jumper holding GPIO5 down while
the firmware drives it high." Do not do that** — shorting a driven-HIGH ESP32-S3
output to ground draws far past the 40 mA per-pad maximum, and any resistor big
enough to be safe cannot pull the pin down anyway. It was also measuring the
wrong failure: reading back your own output only catches a dead GPIO driver, not
a relay that failed to close.

Firmware 0.6.0 adds a separate feedback input (`PIN_RELAY_FB`), so the fault is
produced by **removing a wire** instead. Once that is flashed and the capture is
real, this fixture will exist and this paragraph goes away.
