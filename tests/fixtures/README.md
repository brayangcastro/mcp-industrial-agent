# ESP32 fixtures

Responses from a real SiloScan module (`agrostar-s3-onewire` 0.3.0, device
`banco-silo3`) captured on 2026-08-28 over the LAN.

| File | Source | Notes |
|---|---|---|
| `esp32_status.json` | **recorded** | `GET /api/status` |
| `esp32_config.json` | **recorded** | `GET /api/config` |
| `esp32_last.json` | **recorded** | `GET /api/last` — summary of the last full scan |
| `esp32_historial.json` | **recorded** | `GET /api/historial` — note `temp_max: null`, not `0` |
| `esp32_probe_i0.json` | **recorded** | `GET /api/probe?i=0` with **no cable attached** |
| `esp32_logs.txt` | **recorded** | `GET /api/logs` — plain text ring buffer, not JSON |
| `esp32_probe_i0_populated.derived.json` | **derived** | Shape from `web_server.h:34-46` + `payload.h:31-35`. Replace with a recorded capture once a DS18B20 cable is attached. |

The recorded captures were all taken with **nothing plugged into the mux**,
which is why every temperature is absent. That is not a degenerate case to
work around — it is the fixture behind the most important test in the
suite: a module that cannot see a sensor must not report a temperature.
