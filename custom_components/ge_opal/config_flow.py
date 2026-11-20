"""Config flow for GE Opal Ice Maker."""

from __future__ import annotations

import logging

import voluptuous as vol

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from .const import (
    ADVERTISEMENT_SERVICE_UUID,
    CHAR_MAKE_STATE,
    CONF_ADDRESS,
    CONNECT_TIMEOUT,
    DEFAULT_NAME,
    DOMAIN,
    GATT_SERVICE_UUID,
    OPAL_NAME_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY = "manual"


def _is_opal(info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    """Match the manifest bluetooth matchers: service UUID or OPAL local name."""
    return ADVERTISEMENT_SERVICE_UUID in (
        uuid.lower() for uuid in info.service_uuids
    ) or (info.name or "").upper().startswith(OPAL_NAME_PREFIX)


class OpalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for an Opal ice maker."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: bluetooth.BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}

    async def _async_validate(self, address: str) -> str | None:
        """Connect to the device, pairing if needed; return an error key or None."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        )
        if device is None:
            return "device_not_found"
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                name="GE Opal Ice Maker",
                max_attempts=3,
                timeout=CONNECT_TIMEOUT,
                use_services_cache=True,
            )
        except Exception:  # BLE errors vary by backend/platform
            _LOGGER.debug("Could not connect to Opal %s", address, exc_info=True)
            return "cannot_connect"
        try:
            if client.services.get_service(GATT_SERVICE_UUID) is None:
                return "not_an_opal"
            if await self._async_read_state(client):
                return None
            # The read was rejected, which usually means the device is not bonded.
            try:
                await client.pair()
            except NotImplementedError:
                return "pairing_unsupported"
            except Exception:
                _LOGGER.debug("Pairing with Opal %s failed", address, exc_info=True)
                return "pairing_failed"
            return None if await self._async_read_state(client) else "pairing_failed"
        finally:
            try:
                await client.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting after validation", exc_info=True)

    @staticmethod
    async def _async_read_state(client: BleakClientWithServiceCache) -> bool:
        """Return True if a protected state characteristic can be read."""
        characteristic = client.services.get_characteristic(CHAR_MAKE_STATE)
        if characteristic is None:
            return False
        try:
            await client.read_gatt_char(characteristic)
        except Exception:
            return False
        return True

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
        self._discovery = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or DEFAULT_NAME
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Ask the user to confirm adding the discovered device."""
        assert self._discovery is not None
        name = self._discovery.name or DEFAULT_NAME
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_validate(self._discovery.address.upper())
            if error is None:
                return await self._create_entry(self._discovery.address, name)
            errors["base"] = error
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": name,
                "address": self._discovery.address.upper(),
            },
            errors=errors,
        )

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Let the user pick a discovered Opal or choose manual entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address == MANUAL_ENTRY:
                return await self.async_step_manual()
            error = await self._async_validate(address)
            if error is None:
                return await self._create_entry(
                    address, self._discovered.get(address, DEFAULT_NAME)
                )
            errors["base"] = error
        else:
            current = self._async_current_ids()
            self._discovered = {
                info.address.upper(): info.name or DEFAULT_NAME
                for info in bluetooth.async_discovered_service_info(
                    self.hass, connectable=True
                )
                if _is_opal(info) and info.address.upper() not in current
            }
            if not self._discovered:
                return await self.async_step_manual(no_devices=True)

        options = {
            address: f"{name} ({address})"
            for address, name in self._discovered.items()
        }
        options[MANUAL_ENTRY] = "Enter address manually"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(options)}),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict | None = None, no_devices: bool = False
    ) -> FlowResult:
        """Handle manual configuration by Bluetooth address."""
        errors: dict[str, str] = {}
        if no_devices:
            errors["base"] = "no_devices_found"
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            error = await self._async_validate(address)
            if error is None:
                return await self._create_entry(
                    address, user_input.get(CONF_NAME, DEFAULT_NAME)
                )
            errors[CONF_ADDRESS] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async_step_import = async_step_manual
