# SolarTouch LAN

Local Modbus TCP integration for **SolarMax Onyx hybrid inverters** — no cloud, no internet required.

> [!WARNING]
> **DISCLAIMER: Use at your own risk! Especially when writing any settings.**

> [!NOTE]
> The SolarMax Onyx is an OEM/rebranded inverter (originally manufactured by Senergytec). Other rebrands of the same hardware — such as the **Cellcronic Galaxy 7G** and **Senergy SE-KHB** series — may also work, provided they use a compatible Modbus register map / protocol version. Register layouts can differ between models and firmware versions, so verify against your specific unit before relying on it (especially for writes).

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/farhanshahlabs/ha_solarmax_onyx_local/actions/workflows/validate.yml/badge.svg)](https://github.com/farhanshahlabs/ha_solarmax_onyx_local/actions/workflows/validate.yml)

---

## ⚠️ Important — Cloud Sync

> The SolarMax Onyx inverter cannot maintain a local Modbus TCP connection and a cloud connection at the same time. This integration **closes the connection immediately after every poll** so the inverter is free to push data to the SolarTouch app / [cloudinverter.net](https://cloudinverter.net) between polls.
>
> Use **Standby** or **Periodic** mode if you need regular cloud data. Enable the **Daily Cloud Sync** option to automatically pause local polling at 23:45 so the inverter can push one end-of-day data point before midnight.

---

## Features

- **Local only** — communicates directly with the inverter over your LAN via Modbus TCP (port 502)
- **Three connection modes:**
  - **Standby** (recommended) — no automatic polling; press **Go Live** for a 5-minute live session or **Refresh Now** for a one-shot poll
  - **Periodic** — automatically polls every N minutes (configurable 1–60 min via the **Periodic Poll Interval** control)
  - **Live** — polls fast sensors every 10 s and slow sensors every 60 s
- **Connect → poll → disconnect** — in every mode the connection is opened, data is fetched, and then immediately closed so the inverter resumes cloud sync
- **On-demand refresh** — press **Refresh Now** to instantly poll all sensors
- **Writable controls** — change operation mode, charge/discharge limits, and smart load thresholds directly from HA
- **Daily cloud sync** — optional automatic 6-minute pause at 23:45
- **HACS compatible** — install and update directly from HACS

---

## Sensors

Entities are grouped into three sections on the device page:

### Part 1 — Data Sync (Diagnostic)
| Sensor | Description |
|---|---|
| Connection Status | Current coordinator state: `standby`, `live`, `periodic`, `connecting`, `error` |
| Daily Cloud Sync Active | `on` during the nightly 6-minute cloud sync pause |
| Last Successful Poll | Timestamp of the last successful Modbus read |
| Live Session Remaining | Seconds left in a Go Live session (standby mode) |
| Next Poll In | Seconds until the next automatic poll — updates every second |

### Part 2 — Configuration Registers (Configuration)
These sensors mirror the value of a writable register and sit next to their write controls in the Configuration section.

| Sensor | Register |
|---|---|
| Battery Charging Max Power | 8472 |
| Maximum Grid Charging Power | 8470 |
| Stop Grid Charging Battery SOC | 8471 |
| Battery Stop Charging Maximum SOC | 8473 |
| Discharge End SOC (on grid) | 8522 |
| Discharge End SOC (on battery) | 8475 |
| Max Grid Export Power | 12473 |
| Smart Load Turn ON Battery SOC | 8492 |
| Smart Load Turn OFF Battery SOC | 8493 |
| Inverter Operation Mode Raw | 8448 |
| Off-Grid Mode | 8476 |

### Part 3 — Inverter Data (Sensors)

**Fast sensors (updated every 10 s in Live mode)**
- Live PV Total Power
- PV1 / PV2 Voltage, Current, Power
- Battery Power, State of Charge
- Load Power, Grid Power
- CT Clamp Power, Light Load Power

**Slow sensors (updated every 60 s in Live mode)**
- Battery temperature, voltage, current
- Battery charge/discharge energy (today + lifetime)
- Grid import/export energy (today + lifetime)
- Solar energy produced (today + lifetime)
- Inverter temperature, operating hours
- Grid frequency, load voltage/current
- Smart load power
- Peak production power today

---

## Controls

All controls appear in the **Controls** section of the device page:

| Control | Type | Description |
|---|---|---|
| Sync Mode | Select | Switch between Standby / Periodic / Live |
| Periodic Poll Interval | Number (1–60 min) | Interval for Periodic mode |
| Go Live 5 Min | Button | Start a 5-minute live session (standby mode) |
| Refresh Now | Button | Immediate one-shot poll |
| Pause Sync | Button | Stop any active polling and return to standby |
| Live Mode | Switch | Quick toggle between Live and Standby |

---

## Configuration Section

Writable inverter registers — changes are written directly to the inverter via Modbus:

| Control | Type |
|---|---|
| Inverter Operation Mode | Select |
| Max Battery Charging Power | Number |
| Max Grid Charging Power | Number |
| Stop Grid Charging Battery SOC | Number |
| Max Solar Charging Stop SOC | Number |
| On Grid Discharging Stop SOC | Number |
| Battery Discharge End SOC | Number |
| Max Grid Export Power | Number |
| Smart Load Turn ON Battery SOC | Number |
| Smart Load Turn OFF Battery SOC | Number |
| Inverter Rated Power | Number (kW) — caps the export, grid charging and battery charging power settings |

### Reliable writes — set it once

The inverter shares its single Modbus connection with the cloud uploader, so a
write can fail simply because the cloud is connected at that moment. You do not
need to repeat actions with delays in your automations:

- Setting a value **returns immediately** and is queued.
- The integration keeps reconnecting with backoff, writes the register and
  **reads it back to confirm**, for up to 15 minutes (including across the
  nightly cloud sync pause).
- While waiting, the entity keeps showing the inverter's actual value and the
  requested one is in its `pending_value` attribute. The Connection Status
  sensor lists all `pending_writes`.
- Setting the same control again replaces the pending value (latest wins).
- On success a `solarmax_touch_lan_write_confirmed` event fires. If the inverter
  never confirms, a `solarmax_touch_lan_write_failed` event fires and a
  persistent notification is shown.

```yaml
# Example: get notified when a write fails
trigger:
  - platform: event
    event_type: solarmax_touch_lan_write_failed
action:
  - service: notify.notify
    data:
      message: "Inverter: could not set {{ trigger.event.data.name }} to {{ trigger.event.data.value }}"
```

### Inverter model and rated power

At startup the integration reads the inverter's device-info block (model,
serial number, firmware, rated power) and shows it on the device page. The
rated power sets the maximum of the export, grid charging and battery charging
power controls. If you set **11. Inverter Rated Power** by hand, your value is
kept and auto-detection no longer changes it.

### Status, alerts, battery/BMS and backup sensors

Added sensors (new entities only; existing entities are unchanged):

| Sensor | Notes |
|---|---|
| 50. Inverter Status | Initializing / Standby / On-grid / Off-grid / Fault / Shutdown |
| 51. Active Alerts | Number of active alerts; the `alerts` attribute lists them (e.g. "Grid absent", "Battery under voltage") |
| 52. Battery Health (SOH) | Matches the portal's "Battery Health Status" (register not in the official protocol) |
| 53–55. Battery Capacity, Battery Type, BMS Status | Diagnostic |
| 56–59. BMS charge/discharge voltage and current limits | Diagnostic |
| 60–63. Backup (EPS) voltage, current, frequency, energy today | |
| 64–65. Grid voltage and current | |
| 66–67. Home load energy today / lifetime | |

The device page also shows the master, slave and CSB firmware versions.

### More settings

| Control | Type |
|---|---|
| 12. Max Battery Discharge Power | Number (capped at the inverter rating) |
| 13. / 14. Force Charge Start / End SOC | Number |
| 15. Grid Charging | Switch |
| 16. Time Control Repeat | Select (Once / Every day) |
| 17.–20. Charge / Discharge Start and End Time | Time (used in *Time-based Control* mode) |
| 4. Sync Inverter Clock | Button: sets the inverter clock to Home Assistant's local time |

All of them use the same queued, verified writes as the other controls.
**08. Inverter Clock** shows the inverter's clock and an `offset_seconds`
attribute. Make sure Home Assistant's time zone matches the inverter's
location before syncing the clock.

### Changing settings later

- **Change the inverter's IP address / port / slave ID:** Settings → Devices &
  services → SolarTouch LAN → ⋮ → **Reconfigure**. Entities and history are kept.
- **Options** (Configure): daily cloud-sync pause, and an automatic nightly
  clock sync (03:07).
- **Diagnostics:** ⋮ → *Download diagnostics* gives a file for bug reports
  (IP address, serial and names are removed).

### Poll timing sensors

**06. Next Poll At** and **07. Last Successful Poll** are timestamp sensors.
The older countdown sensors (01–03) still work but update every second, which
writes a lot to the database; new installs get them disabled by default.

### Dashboard card

A card is bundled with the integration and loaded automatically — nothing to
install. Add it from the card picker (**SolarMax Onyx**) or in YAML:

```yaml
type: custom:solarmax-onyx-card
# all optional:
entry_id: <config entry id>   # defaults to the first inverter
title: My inverter
theme: auto                   # auto (follow Home Assistant) | dark | light
range: 24h                    # 6h | 24h | 3d | 7d
series: [solar, home, grid, battery, soc]
```

It shows live solar, home, grid and battery power, battery SOC and
temperatures, today's energy totals, and one interactive chart. Click the
chips to show/hide series, pick a time range, and hover the chart for values.
Home consumption is calculated as solar + grid import − battery charging.

### Spike filtering

The inverter returns all-ones "no data" values (0xFFFF / 0xFFFFFFFF) when a
register is not populated. These are discarded for every sensor, so charts no
longer jump to 65,535 or 42,949,672 kWh. Lifetime energy counters additionally
ignore readings that go down or jump by more than 500 units between polls,
unless the new level persists for 3 polls (a genuine counter reset). Spikes
recorded before this fix can be corrected under **Developer Tools → Statistics**.


---

## Poll Intervals Summary

| Mode | Fast sensors | Slow sensors | Notes |
|---|---|---|---|
| Standby | — | — | No auto-poll; Go Live = 5 s per tick |
| Periodic | All sensors | All sensors | Every N minutes (default 5) |
| Live | Every 10 s | Every 60 s | Connection closed after every tick |

---

## Installation via HACS

1. Open HACS in Home Assistant
2. Go to **Integrations** → **⋮** → **Custom repositories**
3. Add `https://github.com/farhanshahlabs/ha_solarmax_onyx_local` as an **Integration**
4. Search for **SolarTouch LAN** and install
5. Restart Home Assistant
6. Go to **Settings → Integrations → Add Integration → SolarTouch LAN**
7. Enter your inverter's IP address and click **Submit**

---

## Manual Installation

1. Download the latest release zip from [Releases](https://github.com/farhanshahlabs/ha_solarmax_onyx_local/releases)
2. Extract and copy the `custom_components/solarmax_touch_lan` folder to your HA `config/custom_components/` directory
3. Restart Home Assistant
4. Add the integration via **Settings → Integrations → Add Integration → SolarTouch LAN**

---

## Requirements

- Home Assistant 2024.1 or newer
- SolarMax Onyx hybrid inverter reachable on your local network
- Modbus TCP enabled on the inverter (port 502, slave ID 1 by default)

---

## Feedback & Support

Found a bug or have a feature request? Contributions and feedback are welcome.

- **Bug reports & feature requests** — open an issue on the [GitHub Issues](https://github.com/farhanshahlabs/ha_solarmax_onyx_local/issues) page
- **Questions** — use [GitHub Discussions](https://github.com/farhanshahlabs/ha_solarmax_onyx_local/discussions) for general questions or help getting set up
- **Pull requests** — PRs are welcome; please describe what register/feature you are adding and include the source (e.g. inverter manual page or Modbus map)

When reporting a bug, please include:
1. Home Assistant version
2. Integration version (shown in **Settings → Integrations → SolarTouch LAN**)
3. Your inverter model
4. Relevant log lines from **Settings → System → Logs**

---

## Related

- [SolarTouch](https://github.com/farhanshahlabs/ha_solarmax_onyx) — cloud-based integration using the SolarTouch API (requires internet)
