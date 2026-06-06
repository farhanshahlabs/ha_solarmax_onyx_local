"""Button platform for SolarTouch LAN."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarTouchLANCoordinator
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        RefreshButton(coordinator, entry),
        GoLiveButton(coordinator, entry),
        PauseSyncButton(coordinator, entry),
    ])


class RefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "1. Refresh Now"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        await self._coordinator.async_force_refresh()


class GoLiveButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "2. Go Live (5 min)"
    _attr_icon = "mdi:lan-pending"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_go_live"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        await self._coordinator.async_go_live()


class PauseSyncButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "3. Pause Sync"
    _attr_icon = "mdi:pause-circle-outline"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_pause_sync"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        await self._coordinator.async_pause_sync()
