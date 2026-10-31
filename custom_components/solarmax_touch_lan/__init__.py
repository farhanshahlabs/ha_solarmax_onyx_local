"""SolarTouch LAN — local Modbus TCP integration for SolarMax Onyx inverters."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SENSOR_DEFINITIONS
from .coordinator import SolarTouchLANCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

# Bundled Lovelace card, served from this package and loaded on every
# dashboard so users do not have to install anything separately.
CARD_FILENAME = "solarmax-onyx-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"
_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SolarTouchLANCoordinator(hass, entry)
    await coordinator.async_setup()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_frontend(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options change.

    The coordinator also updates entry.data whenever the sync mode or poll
    interval changes; those must not trigger a reload, so compare options only.
    """
    coordinator: SolarTouchLANCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator and coordinator.options_differ(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SolarTouchLANCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card and expose the entity lookup it uses (once per HA run)."""
    if hass.data.get(_FRONTEND_REGISTERED):
        return
    hass.data[_FRONTEND_REGISTERED] = True

    if "websocket_api" in hass.config.components:
        websocket_api.async_register_command(hass, _ws_list_inverters)
    if "frontend" not in hass.config.components or hass.http is None:
        _LOGGER.debug("Frontend not loaded; the dashboard card is not served")
        return

    path = str(Path(__file__).parent / "www" / CARD_FILENAME)
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, path, True)]
        )
    except ImportError:  # Home Assistant < 2024.7
        hass.http.register_static_path(CARD_URL, path, True)

    # Content hash as cache buster, so browsers pick up a new card after updates.
    digest = await hass.async_add_executor_job(_file_digest, path)
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={digest}")


def _file_digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/inverters"})
@callback
def _ws_list_inverters(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return each configured inverter with its entity_ids keyed by sensor key.

    Lets the card find its entities by unique_id, so it keeps working when
    users rename entities.
    """
    registry = er.async_get(hass)
    keys = [d["key"] for d in SENSOR_DEFINITIONS] + ["connection_status", "last_poll"]
    inverters = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        entities = {}
        for key in keys:
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
            if entity_id:
                entities[key] = entity_id
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        inverters.append({
            "entry_id": entry.entry_id,
            "title": entry.title,
            "model": entry.data.get("model"),
            "rated_power_kw": coordinator.rated_power_kw if coordinator else None,
            "entities": entities,
        })
    connection.send_result(msg["id"], inverters)
