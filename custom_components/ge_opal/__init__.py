"""GE Opal Ice Maker integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, DATA_COORDINATOR, DOMAIN, PLATFORMS
from .coordinator import OpalCoordinator
from .devtools import async_register_services
from .explore import async_register_explore_services
from .reverse import async_register_guided_services
from .pairing import async_unpair

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
    async_register_services(hass)
    async_register_guided_services(hass)
    async_register_explore_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a configured Opal device."""
    coordinator = OpalCoordinator(hass, entry.data[CONF_ADDRESS])
    await coordinator.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_COORDINATOR: coordinator
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a configured Opal device."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    await runtime[DATA_COORDINATOR].async_stop()
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Best-effort removal of the Bluetooth bond when the entry is deleted."""
    address = entry.data[CONF_ADDRESS]
    error = await async_unpair(hass, address)
    if error is None:
        _LOGGER.info("Removed Bluetooth bond for GE Opal %s", address)
    else:
        _LOGGER.warning("Could not unpair GE Opal %s: %s", address, error)