# SolarTouch LAN

Local Modbus TCP integration for **SolarMax Onyx hybrid inverters** — no cloud, no internet required.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/farhanshahlabs/ha_solarmax_onyx_local/actions/workflows/validate.yml/badge.svg)](https://github.com/farhanshahlabs/ha_solarmax_onyx_local/actions/workflows/validate.yml)

---

## ⚠️ Important — Cloud Sync Warning

> When this integration is active in **Always On** mode, the inverter **will not send data** to the SolarTouch app or [cloudinverter.net](https://cloudinverter.net). This is a hardware limitation of the SolarMax Onyx inverter — it cannot maintain a local Modbus TCP connection and a cloud connection at the same time.
>
> Use **Standby mode** if you need cloud data during the day, or enable the **Daily Cloud Sync** option to automatically pause the local connection at 23:45 each night so the inverter can push one end-of-day data point to the cloud before midnight.

---

## Features

- **Local only** — communicates directly with the inverter over your LAN via Modbus TCP (port 502)
- **Two connection modes:**
  - **Standby** (recommended) — connection stays closed; press **Go Live** to activate a 5-minute live session
  - **Always On** — connection stays open continuously for real-time data
- **On-demand refresh** — press **Refresh Now** to instantly poll all sensors
- **Writable controls** — change operation mode, charge/discharge limits, and smart load thresholds directly from the Home Assistant UI
- **Daily cloud sync** — optional automatic 6-minute pause at 23:45 to let the inverter sync to SolarTouch / cloudinverter.net
- **HACS compatible** — install and update directly from HACS

---

## Sensors

### Fast sensors (updated every 30 seconds)
- Live PV Total Power
- PV1/PV2 Voltage, Current, Power
- Battery Power, State of Charge
- Load Power, Grid Power
- CT Clamp Power, Light Load Power

### Slow sensors (updated every 5 minutes)
- Battery temperature, voltage, current
- Battery charge/discharge energy (today + lifetime)
- Grid import/export energy (today + lifetime)
- Solar energy produced (today + lifetime)
- Inverter temperature, operating hours
- Grid frequency, load voltage/current
- Smart load power and SOC thresholds
- Operation mode, charge/discharge limits

---

## Writable Controls

All controls appear as native Home Assistant entities (no YAML helpers needed):

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

In **Standby mode**, changing any control automatically activates a live session so the write is confirmed.

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

## Related

- [SolarTouch](https://github.com/farhanshahlabs/ha_solarmax_onyx) — cloud-based integration using the SolarTouch API (requires internet)
