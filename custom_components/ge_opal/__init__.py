"""GE Opal Ice Maker integration."""

from __future__ import annotations

import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONNECT_TIMEOUT, DATA_COORDINATOR, DOMAIN, PLATFORMS
from .coordinator import OpalCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
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
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        _LOGGER.debug("Opal %s not reachable; leaving any bond in place", address)
        return
    client = None
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            name="GE Opal Ice Maker",
            max_attempts=2,
            timeout=CONNECT_TIMEOUT,
        )
        await client.unpair()
        _LOGGER.info("Removed Bluetooth bond for GE Opal %s", address)
    except NotImplementedError:
        _LOGGER.debug("Bluetooth backend cannot unpair Opal %s", address)
    except Exception:
        _LOGGER.warning("Could not unpair GE Opal %s", address, exc_info=True)
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting after unpair", exc_info=True)
