"""Select platform for SolarTouch LAN."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    OPERATION_MODE_OPTIONS,
    OPERATION_MODE_REGISTER,
    CONNECTION_MODE_LIVE,
    CONNECTION_MODE_PERIODIC,
    CONNECTION_MODE_STANDBY,
    TIME_CONTROL_REPEAT_OPTIONS,
    TIME_CONTROL_REPEAT_REGISTER,
)
from .coordinator import SolarTouchLANCoordinator
from .entity import SolarTouchEntity, device_info as _device_info

_MODE_BY_VALUE = {v: k for k, v in OPERATION_MODE_OPTIONS.items()}

SYNC_MODE_STANDBY = "Standby"
SYNC_MODE_PERIODIC = "Periodic"
SYNC_MODE_LIVE = "Live"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SyncModeSelect(coordinator, entry),
        InverterOperationModeSelect(coordinator, entry),
        TimeControlRepeatSelect(coordinator, entry),
    ])


class SyncModeSelect(SolarTouchEntity, SelectEntity):
    """Dropdown to switch between Standby, Periodic, and Live modes."""

    _attr_name = "4. Sync Mode"
    _attr_icon = "mdi:sync"
    _attr_options = [SYNC_MODE_STANDBY, SYNC_MODE_PERIODIC, SYNC_MODE_LIVE]

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_sync_mode"
        self._attr_device_info = _device_info(entry)

    @property
    def current_option(self) -> str:
        if self._coordinator._mode == CONNECTION_MODE_LIVE:
            return SYNC_MODE_LIVE
        if self._coordinator._mode == CONNECTION_MODE_PERIODIC:
            return SYNC_MODE_PERIODIC
        return SYNC_MODE_STANDBY

    async def async_select_option(self, option: str) -> None:
        if option == SYNC_MODE_LIVE:
            await self._coordinator.async_set_mode(CONNECTION_MODE_LIVE)
        elif option == SYNC_MODE_PERIODIC:
            await self._coordinator.async_set_mode(CONNECTION_MODE_PERIODIC)
        else:
            await self._coordinator.async_set_mode(CONNECTION_MODE_STANDBY)


class InverterOperationModeSelect(SolarTouchEntity, SelectEntity):
    _attr_name = "01. Inverter Operation Mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:solar-power-variant"
    _attr_options = list(OPERATION_MODE_OPTIONS.keys())

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_operation_mode"
        self._attr_device_info = _device_info(entry)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pending = self._coordinator.pending_value(OPERATION_MODE_REGISTER)
        return {"pending_value": None if pending is None else _MODE_BY_VALUE.get(pending)}

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
        # Queued and retried in the background until the inverter confirms it.
        self._coordinator.async_request_write(
            self._attr_name,
            OPERATION_MODE_REGISTER,
            value,
            data_key="inverter_operation_mode_raw",
        )


class TimeControlRepeatSelect(SolarTouchEntity, SelectEntity):
    """Whether the time-based control window runs once or every day."""

    _attr_name = "16. Time Control Repeat"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-sync"
    _attr_options = list(TIME_CONTROL_REPEAT_OPTIONS)
    _key = "time_control_everyday"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_time_control_repeat"
        self._attr_device_info = _device_info(entry)

    @property
    def current_option(self) -> str | None:
        raw = self._coordinator.data.get(self._key)
        if raw is None:
            return None
        return next((k for k, v in TIME_CONTROL_REPEAT_OPTIONS.items() if v == int(raw)), None)

    async def async_select_option(self, option: str) -> None:
        # Queued and retried in the background until the inverter confirms it.
        self._coordinator.async_request_write(
            self._attr_name,
            TIME_CONTROL_REPEAT_REGISTER,
            TIME_CONTROL_REPEAT_OPTIONS[option],
            data_key=self._key,
        )
