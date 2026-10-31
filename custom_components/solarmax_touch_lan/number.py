"""Number platform for SolarTouch LAN — writable inverter registers and poll settings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    NUMBER_DEFINITIONS,
    RATED_POWER_MIN_KW,
    RATED_POWER_MAX_KW,
)
from .coordinator import SolarTouchLANCoordinator
from .entity import SolarTouchEntity, device_info as _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        SolarTouchNumber(coordinator, entry, defn) for defn in NUMBER_DEFINITIONS
    ]
    entities.append(PeriodicIntervalNumber(coordinator, entry))
    entities.append(RatedPowerNumber(coordinator, entry))
    async_add_entities(entities)


class SolarTouchNumber(SolarTouchEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: SolarTouchLANCoordinator,
        entry: ConfigEntry,
        defn: dict,
    ) -> None:
        self._coordinator = coordinator
        self._defn = defn
        self._attr_unique_id = f"{entry.entry_id}_number_{defn['register']}"
        self._attr_name = defn["name"]
        self._attr_native_min_value = defn["min"]
        self._attr_native_step = defn["step"]
        self._attr_native_unit_of_measurement = defn["unit"]
        self._attr_device_info = _device_info(entry)

    @property
    def native_max_value(self) -> float:
        if self._defn.get("max_from_rated_power"):
            return self._coordinator.rated_power_kw * 1000
        return self._defn["max"]

    @property
    def native_value(self) -> float | None:
        return self._coordinator.data.get(self._defn["key"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"pending_value": self._coordinator.pending_value(self._defn["register"])}

    async def async_set_native_value(self, value: float) -> None:
        # Queued and retried in the background until the inverter confirms it.
        # The entity keeps showing the inverter's actual value until then, with
        # the requested one in the ``pending_value`` attribute.
        self._coordinator.async_request_write(
            self._defn["name"],
            self._defn["register"],
            int(value),
            uint32=self._defn["data_type"] == "uint32",
            data_key=self._defn["key"],
            data_value=value,
        )


class PeriodicIntervalNumber(SolarTouchEntity, NumberEntity):
    """Number of minutes between automatic polls in Periodic mode."""

    _attr_name = "5. Periodic Poll Interval"
    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_periodic_interval"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float:
        return float(self._coordinator._periodic_interval_minutes)

    async def async_set_native_value(self, value: float) -> None:
        await self._coordinator.async_set_periodic_interval(int(value))
        self.async_write_ha_state()


class RatedPowerNumber(SolarTouchEntity, NumberEntity):
    """Inverter rated AC power; caps the power setting entities.

    Detected from the inverter at startup. Setting it here overrides the
    detected value permanently.
    """

    _attr_name = "11. Inverter Rated Power"
    _attr_icon = "mdi:flash"
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = RATED_POWER_MIN_KW
    _attr_native_max_value = RATED_POWER_MAX_KW
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kW"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_rated_power"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float:
        return self._coordinator.rated_power_kw

    async def async_set_native_value(self, value: float) -> None:
        await self._coordinator.async_set_rated_power(round(value, 1))
