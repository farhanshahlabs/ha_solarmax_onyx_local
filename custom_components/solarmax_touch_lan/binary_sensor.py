"""Binary sensor platform for SolarTouch LAN."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarTouchLANCoordinator
from .sensor import _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CloudSyncActiveSensor(coordinator, entry)])


class CloudSyncActiveSensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Daily Cloud Sync Active"
    _attr_icon = "mdi:cloud-sync-outline"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_cloud_sync_active"
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
        return self._coordinator._cloud_sync_active
