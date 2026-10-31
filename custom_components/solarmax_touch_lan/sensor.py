"""Sensor platform for SolarTouch LAN."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SENSOR_DEFINITIONS,
    ALERT_BITS,
    CONF_MODEL,
    CONF_SERIAL,
    CONF_SW_VERSION,
)
from .coordinator import SolarTouchLANCoordinator
from .entity import SolarTouchEntity, device_info as _device_info

# ── Part 2: sensors that mirror a writable config register ────────────────────
# HA does not allow EntityCategory.CONFIG on sensor entities, so these use
# DIAGNOSTIC.  They appear in the Diagnostic section alongside the data-sync
# sensors, named to match their corresponding Number/Select controls so they
# sort adjacent when the user views all entities.
_CONFIG_REGISTER_KEYS = {
    "battery_charging_max_power",
    "maximum_grid_charging_power",
    "stop_grid_charging_battery_soc",
    "battery_stop_charging_maximum_soc",
    "discharge_end_soc_on_grid",
    "discharge_end_soc_on_battery",
    "max_grid_export_power",
    "smart_load_turn_on_battery_soc",
    "smart_load_turn_off_battery_soc",
    "inverter_operation_mode_raw",
    "off_grid_mode",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Part 3 + Part 2 — inverter data sensors (category driven per key)
    entities: list[SensorEntity] = [
        SolarTouchSensor(coordinator, entry, defn)
        for defn in SENSOR_DEFINITIONS
        if defn.get("entity", True)  # raw registers used only by other sensors
    ]
    entities.append(ActiveAlertsSensor(coordinator, entry))

    # Part 1 — data-sync diagnostic sensors
    entities += [
        ConnectionStatusSensor(coordinator, entry),
        LiveSessionRemainingSensor(coordinator, entry),
        NextPollInSensor(coordinator, entry),
        LastPollSensor(coordinator, entry),
        NextPollAtSensor(coordinator, entry),
        LastSuccessfulPollSensor(coordinator, entry),
        InverterClockSensor(coordinator, entry),
    ]

    async_add_entities(entities)


# ── Part 3 / Part 2 — inverter data sensors ───────────────────────────────────

class SolarTouchSensor(SolarTouchEntity, SensorEntity):

    def __init__(
        self,
        coordinator: SolarTouchLANCoordinator,
        entry: ConfigEntry,
        defn: dict,
    ) -> None:
        self._coordinator = coordinator
        self._defn = defn
        self._attr_unique_id = f"{entry.entry_id}_{defn['key']}"
        self._attr_name = defn["name"]
        self._attr_native_unit_of_measurement = defn["unit"]
        self._attr_device_class = defn["device_class"]
        self._attr_state_class = defn["state_class"]
        self._attr_device_info = _device_info(entry)
        # Part 2: config-register sensors stay in the main Sensors section (no category).
        # HA forbids CONFIG on sensor entities; DIAGNOSTIC would mix them with Part 1.
        if defn.get("category") == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        # Enum sensors map a raw register value to text (e.g. 4 -> "Off-grid").
        self._options: dict[int, str] | None = defn.get("options")
        if self._options:
            self._attr_options = list(self._options.values())

    @property
    def native_value(self) -> Any:
        value = self._coordinator.data.get(self._defn["key"])
        if self._options is not None and value is not None:
            return self._options.get(int(value))
        return value

    @property
    def available(self) -> bool:
        return self._defn["key"] in self._coordinator.data


class ActiveAlertsSensor(SolarTouchEntity, SensorEntity):
    """Active inverter, grid and battery alerts decoded from the error registers.

    State is the number of active alerts; the ``alerts`` attribute lists them.
    """

    _attr_name = "51. Active Alerts"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_active_alerts"
        self._attr_device_info = _device_info(entry)

    def _alerts(self) -> list[str]:
        active = []
        for key, bits in ALERT_BITS.items():
            raw = self._coordinator.data.get(key)
            if raw is None:
                continue
            raw = int(raw)
            active += [text for bit, text in enumerate(bits) if text and raw >> bit & 1]
        return active

    @property
    def available(self) -> bool:
        return any(key in self._coordinator.data for key in ALERT_BITS)

    @property
    def native_value(self) -> int:
        return len(self._alerts())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"alerts": self._alerts()}


# ── Part 1 — data-sync diagnostic sensors ────────────────────────────────────

class ConnectionStatusSensor(SolarTouchEntity, SensorEntity):
    _attr_name = "04. Connection Status"
    _attr_icon = "mdi:lan-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_connection_status"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str:
        return self._coordinator.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "pending_writes": {
                p.name: p.data_value for p in self._coordinator.pending_writes.values()
            }
        }


class LiveSessionRemainingSensor(SolarTouchEntity, SensorEntity):
    _countdown = True
    # Rewritten every second; new installs get the timestamp sensors instead.
    _attr_entity_registry_enabled_default = False
    _attr_name = "03. Live Session Remaining"
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_live_session_remaining"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int:
        return self._coordinator.live_session_remaining


class NextPollInSensor(SolarTouchEntity, SensorEntity):
    """Countdown to the next automatic poll — updates every second."""

    _countdown = True
    # Rewritten every second; new installs get the timestamp sensors instead.
    _attr_entity_registry_enabled_default = False
    _attr_name = "01. Next Poll In"
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:timer-sand"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_next_poll_in"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int:
        return self._coordinator.next_poll_in


class LastPollSensor(SolarTouchEntity, SensorEntity):
    _countdown = True
    # Rewritten every second; new installs get the timestamp sensors instead.
    _attr_entity_registry_enabled_default = False
    _attr_name = "02. Last Poll Since"
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_last_poll"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int | None:
        last = self._coordinator.last_poll
        if last is None:
            return None
        return int((datetime.now(timezone.utc) - last).total_seconds())


class NextPollAtSensor(SolarTouchEntity, SensorEntity):
    """When the next automatic poll is due (a timestamp, so no per-second updates)."""

    _attr_name = "06. Next Poll At"
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_next_poll_at"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        return self._coordinator.next_poll_at


class LastSuccessfulPollSensor(SolarTouchEntity, SensorEntity):
    """When data was last read successfully from the inverter."""

    _attr_name = "07. Last Successful Poll"
    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_last_successful_poll"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        return self._coordinator.last_poll


class InverterClockSensor(SolarTouchEntity, SensorEntity):
    """The inverter's own clock, as of the last poll (local time)."""

    _attr_name = "08. Inverter Clock"
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_inverter_clock"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        return self._coordinator.inverter_clock

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Difference to Home Assistant's clock when it was read (+ = inverter ahead)."""
        offset = self._coordinator.clock_offset_seconds
        return {"offset_seconds": None if offset is None else round(offset)}
