"""Number platform for SolarTouch LAN — writable inverter registers."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NUMBER_DEFINITIONS
from .coordinator import SolarTouchLANCoordinator
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [SolarTouchNumber(coordinator, entry, defn) for defn in NUMBER_DEFINITIONS]
    )


class SolarTouchNumber(NumberEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.AUTO

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
        self._attr_native_max_value = defn["max"]
        self._attr_native_step = defn["step"]
        self._attr_native_unit_of_measurement = defn["unit"]
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._coordinator.data.get(self._defn["key"])

    async def async_set_native_value(self, value: float) -> None:
        int_value = int(value)
        is_uint32 = self._defn["data_type"] == "uint32"
        await self._coordinator.async_write_register(
            self._defn["register"], int_value, uint32=is_uint32
        )
        self._coordinator.data[self._defn["key"]] = value
        self.async_write_ha_state()
