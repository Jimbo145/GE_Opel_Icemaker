"""Shared connect/pair/unpair helpers for GE Opal."""

from __future__ import annotations

import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import CHAR_MAKE_STATE, CONNECT_TIMEOUT, GATT_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

ERROR_MESSAGES = {
    "device_not_found": "The Opal is not visible to Home Assistant Bluetooth. Move it closer or check the adapter/proxy.",
    "cannot_connect": "Could not connect to the Opal. Make sure it is powered and not connected to another client.",
    "not_an_opal": "Connected, but the device does not expose the Opal service.",
    "pairing_failed": "The Opal rejected access and pairing failed. Put it in pairing mode and try again.",
    "pairing_unsupported": "This Bluetooth adapter or proxy cannot pair. Pair on the Home Assistant host with bluetoothctl.",
    "unpair_failed": "Could not remove the Bluetooth bond from the Opal.",
}


async def _async_connect(
    hass: HomeAssistant, address: str, attempts: int = 3
) -> tuple[BleakClientWithServiceCache | None, str | None]:
    """Connect to the device; return (client, error_key)."""
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        return None, "device_not_found"
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            name="GE Opal Ice Maker",
            max_attempts=attempts,
            timeout=CONNECT_TIMEOUT,
            use_services_cache=False,
        )
    except Exception:  # BLE errors vary by backend/platform
        _LOGGER.debug("Could not connect to Opal %s", address, exc_info=True)
        return None, "cannot_connect"
    return client, None


async def _async_disconnect(client: BleakClientWithServiceCache) -> None:
    try:
        await client.disconnect()
    except Exception:
        _LOGGER.debug("Error disconnecting from Opal", exc_info=True)


async def _async_can_read_state(client: BleakClientWithServiceCache) -> bool:
    """Return True if a protected state characteristic can be read."""
    characteristic = client.services.get_characteristic(CHAR_MAKE_STATE)
    if characteristic is None:
        return False
    try:
        await client.read_gatt_char(characteristic)
    except Exception:
        return False
    return True


async def async_connect_and_pair(
    hass: HomeAssistant, address: str, force_pair: bool = False
) -> str | None:
    """Connect, pairing if needed (or when forced); return an error key or None."""
    client, error = await _async_connect(hass, address)
    if client is None:
        return error
    try:
        if client.services.get_service(GATT_SERVICE_UUID) is None:
            return "not_an_opal"
        if not force_pair and await _async_can_read_state(client):
            return None
        try:
            await client.pair()
        except NotImplementedError:
            return "pairing_unsupported"
        except Exception:
            _LOGGER.debug("Pairing with Opal %s failed", address, exc_info=True)
            return "pairing_failed"
        return None if await _async_can_read_state(client) else "pairing_failed"
    finally:
        await _async_disconnect(client)


async def async_unpair(hass: HomeAssistant, address: str) -> str | None:
    """Remove the Bluetooth bond; return an error key or None."""
    client, error = await _async_connect(hass, address, attempts=2)
    if client is None:
        return error
    try:
        await client.unpair()
    except NotImplementedError:
        return "pairing_unsupported"
    except Exception:
        _LOGGER.debug("Unpairing Opal %s failed", address, exc_info=True)
        return "unpair_failed"
    finally:
        await _async_disconnect(client)
    return None
