"""Shared connect/pair/unpair helpers for GE Opal."""

from __future__ import annotations

import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .bluez import async_pair_via_dbus
from .const import CHAR_MAKE_STATE, CONNECT_TIMEOUT, GATT_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

ERROR_MESSAGES = {
    "device_not_found": "The Opal is not visible to Home Assistant Bluetooth. Move it closer or check the adapter/proxy.",
    "cannot_connect": "Could not connect to the Opal. Make sure it is powered and not connected to another client.",
    "not_an_opal": "Connected, but the device does not expose the Opal service.",
    "pairing_failed": "Pairing failed. Put the Opal in pairing mode, make sure it is not bonded to another host or the GE app, and check the log for 'Opal pairing diagnostics'.",
    "pairing_proxy": "The Opal was reached through a Bluetooth proxy, which cannot pair it. Use the host Bluetooth adapter.",
    "pairing_error": "Unexpected error while pairing. See the Home Assistant log (logger custom_components.ge_opal.pairing).",
    "pairing_unsupported": "This Bluetooth adapter or proxy cannot pair. Use a local Bluetooth adapter on the Home Assistant host.",
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


LAST_PAIR_DIAGNOSTICS: dict[str, Any] = {}


def _backend_info(client: BleakClientWithServiceCache) -> dict[str, str]:
    """Describe the backend serving this connection (local BlueZ vs proxy)."""
    inner = getattr(client, "_backend", None)
    name = type(inner).__name__ if inner is not None else type(client).__name__
    module = type(inner).__module__ if inner is not None else ""
    return {"backend": name, "module": module, "is_bluez": str("bluez" in module.lower() or "BlueZ" in name)}


async def _async_pair_with_fallbacks(
    hass: HomeAssistant, address: str, client: BleakClientWithServiceCache, diag: dict[str, Any]
) -> str | None:
    """Try the agent-backed BlueZ pairing first, then bleak's pair()."""
    is_bluez = diag["backend_info"]["is_bluez"] == "True"
    if is_bluez:
        diag["dbus"] = await async_pair_via_dbus(address)
        if diag["dbus"]["ok"]:
            return None
    try:
        await client.pair()
        return None
    except NotImplementedError as err:
        diag["bleak_pair_error"] = f"NotImplementedError: {err}"
        return None if is_bluez else "pairing_proxy"
    except Exception as err:
        diag["bleak_pair_error"] = f"{type(err).__name__}: {err}"
        _LOGGER.debug("bleak pair() failed", exc_info=True)
    # Possibly a stale half-bond: drop it and try the agent path once more.
    if is_bluez:
        try:
            await client.unpair()
            diag["removed_stale_bond"] = True
        except Exception as err:
            diag["unpair_error"] = f"{type(err).__name__}: {err}"
        diag["dbus_retry"] = await async_pair_via_dbus(address)
        if diag["dbus_retry"]["ok"]:
            return None
    return "pairing_failed" if is_bluez else "pairing_proxy"


async def async_connect_and_pair(
    hass: HomeAssistant, address: str, force_pair: bool = False
) -> str | None:
    """Never raise: log the full traceback and map to an error key."""
    try:
        return await _async_connect_and_pair(hass, address, force_pair)
    except Exception as err:
        LAST_PAIR_DIAGNOSTICS["unhandled_exception"] = f"{type(err).__name__}: {err}"
        _LOGGER.exception(
            "Opal pairing crashed for %s (%s); diagnostics: %s",
            address, type(err).__name__, LAST_PAIR_DIAGNOSTICS,
        )
        return "pairing_error"


async def _async_connect_and_pair(
    hass: HomeAssistant, address: str, force_pair: bool = False
) -> str | None:
    """Connect, pairing if needed (or when forced); return an error key or None."""
    diag: dict[str, Any] = {"address": address, "force_pair": force_pair}
    LAST_PAIR_DIAGNOSTICS.clear()
    LAST_PAIR_DIAGNOSTICS.update(diag)
    info = bluetooth.async_last_service_info(hass, address, connectable=True)
    if info is not None:
        diag["adapter_source"] = info.source
    client, error = await _async_connect(hass, address)
    if client is None:
        diag["result"] = error
        LAST_PAIR_DIAGNOSTICS.update(diag)
        return error
    diag["backend_info"] = _backend_info(client)
    try:
        if client.services.get_service(GATT_SERVICE_UUID) is None:
            diag["result"] = "not_an_opal"
            return "not_an_opal"
        if not force_pair and await _async_can_read_state(client):
            diag["result"] = "already_paired"
            return None
        error = await _async_pair_with_fallbacks(hass, address, client, diag)
        if error is None and not await _async_can_read_state(client):
            # The bond may only take effect on a fresh connection.
            await _async_disconnect(client)
            client, reconnect_error = await _async_connect(hass, address)
            if client is None or not await _async_can_read_state(client):
                error = reconnect_error or "pairing_failed"
                diag["verify_failed"] = True
        diag["result"] = error or "paired"
        return error
    finally:
        LAST_PAIR_DIAGNOSTICS.update(diag)
        if diag.get("result") not in ("paired", "already_paired"):
            _LOGGER.warning("Opal pairing diagnostics: %s", diag)
        if client is not None:
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
