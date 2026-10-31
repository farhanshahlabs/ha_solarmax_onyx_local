"""Shared base entity for SolarTouch LAN."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, CONF_MODEL, CONF_SERIAL, CONF_SW_VERSION


def device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.data.get("name", entry.title),
        manufacturer="SolarMax",
        model=entry.data.get(CONF_MODEL) or "Onyx Hybrid Inverter",
        serial_number=entry.data.get(CONF_SERIAL),
        sw_version=entry.data.get(CONF_SW_VERSION),
        configuration_url=f"http://{entry.data['host']}",
    )


class SolarTouchEntity(Entity):
    """Pushes state whenever the coordinator notifies its listeners.

    Subclasses set ``_coordinator``, ``_attr_unique_id`` and
    ``_attr_device_info`` in their own ``__init__``. Set ``_countdown = True``
    on entities that show a running countdown so the 1-second ticker
    refreshes them; all others update only when data or status changes.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _countdown = False

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update, countdown=self._countdown)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
