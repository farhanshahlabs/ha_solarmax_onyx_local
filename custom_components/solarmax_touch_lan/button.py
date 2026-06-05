"""Button platform for SolarTouch LAN."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONNECTION_MODE_STANDBY, CONF_CONNECTION_MODE
from .coordinator import SolarTouchLANCoordinator
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = [RefreshButton(coordinator, entry)]
    if entry.data.get(CONF_CONNECTION_MODE) == CONNECTION_MODE_STANDBY:
        entities.append(GoLiveButton(coordinator, entry))
    async_add_entities(entities)


class RefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "1 Refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        await self._coordinator.async_force_refresh()


class GoLiveButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "2 Go Live"
    _attr_icon = "mdi:lan-pending"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_go_live"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        await self._coordinator.async_go_live()
