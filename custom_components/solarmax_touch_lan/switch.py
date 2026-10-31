"""Switch platform for SolarTouch LAN — on/off inverter settings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, GRID_CHARGE_REGISTER
from .coordinator import SolarTouchLANCoordinator
from .entity import SolarTouchEntity, device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GridChargeSwitch(coordinator, entry)])


class GridChargeSwitch(SolarTouchEntity, SwitchEntity):
    """Allow the battery to charge from the grid."""

    _attr_name = "15. Grid Charging"
    _attr_icon = "mdi:transmission-tower-import"
    _attr_entity_category = EntityCategory.CONFIG
    _key = "grid_charge_enable"

    def __init__(self, coordinator: SolarTouchLANCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_grid_charge"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        value = self._coordinator.data.get(self._key)
        return None if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pending = self._coordinator.pending_value(GRID_CHARGE_REGISTER)
        return {"pending_value": None if pending is None else bool(pending)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._write(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._write(0)

    def _write(self, value: int) -> None:
        # Queued and retried in the background until the inverter confirms it.
        self._coordinator.async_request_write(
            self._attr_name, GRID_CHARGE_REGISTER, value, data_key=self._key
        )
