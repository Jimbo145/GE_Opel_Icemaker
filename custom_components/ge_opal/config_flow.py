"""Config flow for GE Opal Ice Maker."""

from __future__ import annotations

import asyncio

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
    OPAL_NAME_PREFIX,
)
from .pairing import async_connect_and_pair

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
        self._pair_task: asyncio.Task | None = None
        self._pair_ctx: tuple[str, str, str] = ("user", "", "")
        self._pair_error: str | None = None

    def _take_errors(self, key: str = "base") -> dict[str, str]:
        """Return (and clear) the error left by the last pairing attempt."""
        errors = {key: self._pair_error} if self._pair_error else {}
        self._pair_error = None
        return errors

    async def _start_pairing(self, return_step: str, address: str, name: str) -> FlowResult:
        """Run connect+pair in the background so the flow step never times out."""
        self._pair_ctx = (return_step, address, name)
        self._pair_task = self.hass.async_create_task(
            async_connect_and_pair(self.hass, address)
        )
        return self.async_show_progress(
            step_id="pairing", progress_action="pairing", progress_task=self._pair_task
        )

    async def async_step_pairing(self, user_input: dict | None = None) -> FlowResult:
        """Wait for the background pairing task."""
        task = self._pair_task
        if task is None:
            return self.async_abort(reason="unknown")
        if not task.done():
            return self.async_show_progress(
                step_id="pairing", progress_action="pairing", progress_task=task
            )
        try:
            self._pair_error = task.result()
        except asyncio.CancelledError:
            self._pair_error = "pairing_error"
        except Exception:
            self._pair_error = "pairing_error"
        return self.async_show_progress_done(next_step_id="pairing_done")

    async def async_step_pairing_done(self, user_input: dict | None = None) -> FlowResult:
        """Create the entry on success, or go back to the originating form."""
        return_step, address, name = self._pair_ctx
        if self._pair_error is None:
            return await self._create_entry(address, name)
        if return_step == "bluetooth_confirm":
            return await self.async_step_bluetooth_confirm()
        if return_step == "manual":
            return await self.async_step_manual()
        return await self.async_step_user()

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
        errors: dict[str, str] = self._take_errors()
        if user_input is not None:
            return await self._start_pairing(
                "bluetooth_confirm", self._discovery.address.upper(), name
            )
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
        errors: dict[str, str] = self._take_errors()
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address == MANUAL_ENTRY:
                return await self.async_step_manual()
            return await self._start_pairing(
                "user", address, self._discovered.get(address, DEFAULT_NAME)
            )
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
        errors: dict[str, str] = self._take_errors(CONF_ADDRESS)
        if no_devices:
            errors["base"] = "no_devices_found"
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            return await self._start_pairing(
                "manual", address, user_input.get(CONF_NAME, DEFAULT_NAME)
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async_step_import = async_step_manual
