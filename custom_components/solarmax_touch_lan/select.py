"""Select platform for SolarTouch LAN."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    OPERATION_MODE_OPTIONS,
    OPERATION_MODE_REGISTER,
    CONNECTION_MODE_ALWAYS_ON,
    CONNECTION_MODE_STANDBY,
)
from .coordinator import SolarTouchLANCoordinator
from .sensor import _device_info

_MODE_BY_VALUE = {v: k for k, v in OPERATION_MODE_OPTIONS.items()}

SYNC_MODE_STANDBY = "Standby Mode"
SYNC_MODE_CONTINUOUS = "Continuous Sync Mode"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SyncModeSelect(coordinator, entry),
        InverterOperationModeSelect(coordinator, entry),
    ])


class SyncModeSelect(SelectEntity):
    """Dropdown to switch between Standby Mode and Continuous Sync Mode."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Sync Mode"
    _attr_icon = "mdi:sync"
    _attr_options = [SYNC_MODE_STANDBY, SYNC_MODE_CONTINUOUS]

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_sync_mode"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        if self._coordinator._mode == CONNECTION_MODE_ALWAYS_ON:
            return SYNC_MODE_CONTINUOUS
        return SYNC_MODE_STANDBY

    async def async_select_option(self, option: str) -> None:
        if option == SYNC_MODE_CONTINUOUS:
            await self._coordinator.async_set_mode(CONNECTION_MODE_ALWAYS_ON)
        else:
            await self._coordinator.async_set_mode(CONNECTION_MODE_STANDBY)


class InverterOperationModeSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Inverter Operation Mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:solar-power-variant"
    _attr_options = list(OPERATION_MODE_OPTIONS.keys())

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_operation_mode"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        self._coordinator.register_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.unregister_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        raw = self._coordinator.data.get("inverter_operation_mode_raw")
        if raw is None:
            return None
        return _MODE_BY_VALUE.get(int(raw))

    async def async_select_option(self, option: str) -> None:
        value = OPERATION_MODE_OPTIONS.get(option)
        if value is None:
            return
        await self._coordinator.async_write_register(OPERATION_MODE_REGISTER, value)
        self._coordinator.data["inverter_operation_mode_raw"] = value
        self.async_write_ha_state()
