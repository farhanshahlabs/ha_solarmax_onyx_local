"""Switch platform for SolarTouch LAN — connection mode toggle."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONNECTION_MODE_ALWAYS_ON, CONNECTION_MODE_STANDBY
from .coordinator import SolarTouchLANCoordinator
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ConnectionModeSwitch(coordinator, entry)])


class ConnectionModeSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "6. Live Mode"
    _attr_icon = "mdi:refresh-auto"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_always_on_mode"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._coordinator._mode == CONNECTION_MODE_ALWAYS_ON

    async def async_turn_on(self, **kwargs) -> None:
        await self._coordinator.async_set_mode(CONNECTION_MODE_ALWAYS_ON)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_set_mode(CONNECTION_MODE_STANDBY)
