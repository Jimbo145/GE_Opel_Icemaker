"""Config flow for GE Opal Ice Maker."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from .const import (
    ADVERTISEMENT_SERVICE_UUID,
    CONF_ADDRESS,
    DEFAULT_NAME,
    DOMAIN,
)


class OpalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for an Opal ice maker."""

    VERSION = 1

    async def _create_entry(self, address: str, name: str) -> FlowResult:
        """Create a unique config entry."""
        normalized = address.upper()
        await self.async_set_unique_id(normalized)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=name or DEFAULT_NAME,
            data={CONF_ADDRESS: normalized, CONF_NAME: name or DEFAULT_NAME},
        )

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle discovery from the Opal advertisement."""
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {
            "name": discovery_info.name or DEFAULT_NAME
        }
        return await self._create_entry(
            discovery_info.address, discovery_info.name or DEFAULT_NAME
        )

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle manual configuration."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if not bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            ):
                errors[CONF_ADDRESS] = "device_not_found"
            else:
                return await self._create_entry(address, user_input.get(CONF_NAME, DEFAULT_NAME))

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async_step_import = async_step_user
