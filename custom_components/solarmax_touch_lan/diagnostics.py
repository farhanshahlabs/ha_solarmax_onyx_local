"""Diagnostics download for SolarTouch LAN (Settings → Devices → ⋮ → Download diagnostics)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_SERIAL, DOMAIN
from .coordinator import SolarTouchLANCoordinator

TO_REDACT = {"host", CONF_SERIAL, "name", "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "status": {
            "connection_status": coordinator.status,
            "last_poll": coordinator.last_poll,
            "next_poll_at": coordinator.next_poll_at,
            "rated_power_kw": coordinator.rated_power_kw,
            "inverter_clock": coordinator.inverter_clock,
            "clock_offset_seconds": coordinator.clock_offset_seconds,
            "pending_writes": {
                address: {
                    "name": p.name,
                    "value": p.value,
                    "cycles": p.attempts,
                    "deadline": p.deadline,
                }
                for address, p in coordinator.pending_writes.items()
            },
            "blocks_read_in_parts": sorted(coordinator.client.refused_blocks),
            "avg_request_seconds": coordinator.client.avg_request_seconds,
        },
        "data": dict(sorted(coordinator.data.items())),
    }
