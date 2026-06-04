"""Config flow for SolarTouch LAN."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME

from .const import (
    CONF_CONNECTION_MODE,
    CONF_DAILY_CLOUD_SYNC,
    CONNECTION_MODE_ALWAYS_ON,
    CONNECTION_MODE_STANDBY,
    DOMAIN,
    PORT_DEFAULT,
    SLAVE_DEFAULT,
)
from .modbus_client import SolarMaxModbusClient, ModbusConnectionError

_LOGGER = logging.getLogger(__name__)

DEFAULT_INVERTER_NAME = "SolarMax Onyx Inverter"


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

            client = SolarMaxModbusClient(host, PORT_DEFAULT, SLAVE_DEFAULT)
            try:
                await client.async_connect()
                await client.async_read_register(8192, "uint16")
            except ModbusConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during setup test")
                errors["base"] = "unknown"
            finally:
                await client.async_disconnect()

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
                            CONNECTION_MODE_STANDBY: "Standby (recommended — preserves cloud sync)",
                            CONNECTION_MODE_ALWAYS_ON: "Always On (blocks cloud sync)",
                        }
                    ),
                    vol.Optional(CONF_DAILY_CLOUD_SYNC, default=True): bool,
                }
            ),
            description_placeholders={
                "cloud_warning": (
                    "⚠️ Always On mode will block data from reaching "
                    "SolarTouch app and cloudinverter.net."
                )
            },
            errors=errors,
        )
