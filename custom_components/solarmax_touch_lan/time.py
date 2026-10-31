"""Time platform for SolarTouch LAN — time-based control charge/discharge window."""

from __future__ import annotations

from datetime import time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, TIME_SETTINGS
from .coordinator import SolarTouchLANCoordinator
from .entity import SolarTouchEntity, device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TimeControlTime(coordinator, entry, key, name, register)
        for key, name, register in TIME_SETTINGS
    )


def _decode(raw: int | float | None) -> time | None:
    """High byte = hour, low byte = minute."""
    if raw is None:
        return None
    raw = int(raw)
    hour, minute = raw >> 8, raw & 0xFF
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


class TimeControlTime(SolarTouchEntity, TimeEntity):
    """Start or end of the charge/discharge window used in Time-based Control mode."""

    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: SolarTouchLANCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        register: int,
    ) -> None:
        self._coordinator = coordinator
        self._key = key
        self._register = register
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_time_{register}"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> time | None:
        return _decode(self._coordinator.data.get(self._key))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pending = _decode(self._coordinator.pending_value(self._register))
        return {"pending_value": pending.strftime("%H:%M") if pending else None}

    async def async_set_value(self, value: time) -> None:
        # Queued and retried in the background until the inverter confirms it.
        self._coordinator.async_request_write(
            self._attr_name,
            self._register,
            (value.hour << 8) | value.minute,
            data_key=self._key,
        )
