"""Binary sensor platform for SolarTouch LAN."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SolarTouchLANCoordinator
from .entity import SolarTouchEntity, device_info as _device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CloudSyncActiveSensor(coordinator, entry)])


class CloudSyncActiveSensor(SolarTouchEntity, BinarySensorEntity):
    _attr_name = "05. Daily Cloud Sync Active"
    _attr_icon = "mdi:cloud-sync-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_cloud_sync_active"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool:
        return self._coordinator._cloud_sync_active
