"""Config flow for SolarTouch LAN."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback

from .const import (
    CONF_AUTO_CLOCK_SYNC,
    CONF_CONNECTION_MODE,
    CONF_DAILY_CLOUD_SYNC,
    CONF_SLAVE,
    CONNECTION_MODE_STANDBY,
    CONNECTION_MODE_PERIODIC,
    CONNECTION_MODE_LIVE,
    DOMAIN,
    PORT_DEFAULT,
    SLAVE_DEFAULT,
)
from homeassistant.data_entry_flow import AbortFlow as _AbortFlow

from .modbus_client import SolarMaxModbusClient, ModbusConnectionError

_LOGGER = logging.getLogger(__name__)

DEFAULT_INVERTER_NAME = "SolarMax Onyx Inverter"


async def _async_test_connection(host: str, port: int, slave: int) -> str | None:
    """Return an error key, or None if the inverter answers."""
    client = SolarMaxModbusClient(host, port, slave)
    try:
        await client.async_connect(attempts=3)
        await client.async_read_register(8192, "uint16")
    except ModbusConnectionError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error during connection test")
        return "unknown"
    finally:
        await client.async_disconnect()
    return None


class SolarTouchLANConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the SolarTouch LAN setup wizard."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME].strip() or DEFAULT_INVERTER_NAME
            host = user_input[CONF_HOST].strip()
            mode = user_input[CONF_CONNECTION_MODE]
            daily_sync = user_input.get(CONF_DAILY_CLOUD_SYNC, False)

            error = await _async_test_connection(host, PORT_DEFAULT, SLAVE_DEFAULT)
            if error:
                errors["base"] = error

            if not errors:
                await self.async_set_unique_id(f"{host}:{PORT_DEFAULT}:{SLAVE_DEFAULT}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={
                        "name": name,
                        "host": host,
                        "port": PORT_DEFAULT,
                        "slave": SLAVE_DEFAULT,
                        CONF_CONNECTION_MODE: mode,
                        CONF_DAILY_CLOUD_SYNC: daily_sync,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=DEFAULT_INVERTER_NAME): str,
                    vol.Required(CONF_HOST, description={"suggested_value": "192.168.1.2"}): str,
                    vol.Required(CONF_CONNECTION_MODE, default=CONNECTION_MODE_STANDBY): vol.In(
                        {
                            CONNECTION_MODE_STANDBY: "Standby — no auto-refresh, cloud sync always works",
                            CONNECTION_MODE_PERIODIC: "Periodic — poll every N minutes (set interval after setup)",
                            CONNECTION_MODE_LIVE: "Live — poll every 30 s (most responsive, may reduce cloud sync)",
                        }
                    ),
                    vol.Optional(CONF_DAILY_CLOUD_SYNC, default=True): bool,
                }
            ),
            description_placeholders={
                "cloud_warning": (
                    "⚠️ In all modes the connection is closed immediately after "
                    "each poll so the inverter can push data to the cloud."
                )
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SolarTouchLANOptionsFlow(config_entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the inverter's address without removing the integration.

        Entities, their history and all settings are kept.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            slave = user_input[CONF_SLAVE]
            error = await _async_test_connection(host, port, slave)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(f"{host}:{port}:{slave}")
                self._abort_if_unique_id_mismatch_or_configured(entry)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{host}:{port}:{slave}",
                    data={**entry.data, "host": host, "port": port, "slave": slave},
                    reason="reconfigure_successful",
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data["host"]): str,
                    vol.Required(CONF_PORT, default=entry.data.get("port", PORT_DEFAULT)): int,
                    vol.Required(CONF_SLAVE, default=entry.data.get("slave", SLAVE_DEFAULT)): int,
                }
            ),
            errors=errors,
        )

    def _abort_if_unique_id_mismatch_or_configured(self, entry: ConfigEntry) -> None:
        """Abort if the new address belongs to a different configured inverter."""
        for other in self._async_current_entries(include_ignore=False):
            if other.entry_id != entry.entry_id and other.unique_id == self.unique_id:
                raise _AbortFlow("already_configured")


class SolarTouchLANOptionsFlow(OptionsFlow):
    """Settings that have no entity of their own."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DAILY_CLOUD_SYNC, default=current.get(CONF_DAILY_CLOUD_SYNC, False)
                    ): bool,
                    vol.Required(
                        CONF_AUTO_CLOCK_SYNC, default=current.get(CONF_AUTO_CLOCK_SYNC, False)
                    ): bool,
                }
            ),
        )
