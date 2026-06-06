"""Sensor platform for SolarTouch LAN."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_DEFINITIONS
from .coordinator import SolarTouchLANCoordinator

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
        SolarTouchSensor(coordinator, entry, defn) for defn in SENSOR_DEFINITIONS
    ]

    # Part 1 — data-sync diagnostic sensors
    entities += [
        ConnectionStatusSensor(coordinator, entry),
        LiveSessionRemainingSensor(coordinator, entry),
        NextPollInSensor(coordinator, entry),
        LastPollSensor(coordinator, entry),
    ]

    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.data.get("name", entry.title),
        manufacturer="SolarMax",
        model="Onyx Hybrid Inverter",
        configuration_url=f"http://{entry.data['host']}",
    )


# ── Part 3 / Part 2 — inverter data sensors ───────────────────────────────────

class SolarTouchSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

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

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self._coordinator.data.get(self._defn["key"])

    @property
    def available(self) -> bool:
        return self._defn["key"] in self._coordinator.data


# ── Part 1 — data-sync diagnostic sensors ────────────────────────────────────

class ConnectionStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "04. Connection Status"
    _attr_icon = "mdi:lan-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_connection_status"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._coordinator.status


class LiveSessionRemainingSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "03. Live Session Remaining"
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_live_session_remaining"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return self._coordinator.live_session_remaining


class NextPollInSensor(SensorEntity):
    """Countdown to the next automatic poll — updates every second."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "01. Next Poll In"
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:timer-sand"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_next_poll_in"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return self._coordinator.next_poll_in


class LastPollSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "02. Last Poll Since"
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_last_poll"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        last = self._coordinator.last_poll
        if last is None:
            return None
        return int((datetime.now(timezone.utc) - last).total_seconds())
