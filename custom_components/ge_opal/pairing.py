"""Shared connect/pair/unpair helpers for GE Opal."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .bluez import async_pair_via_dbus, async_remove_device_via_dbus
from .const import CHAR_MAKE_STATE, CONNECT_TIMEOUT, GATT_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

ERROR_MESSAGES = {
    "device_not_found": "The Opal is not visible to Home Assistant Bluetooth. Move it closer or check the adapter/proxy.",
    "cannot_connect": "Could not connect to the Opal. Make sure it is powered and not connected to another client.",
    "not_an_opal": "Connected, but the device does not expose the Opal service.",
    "pairing_failed": "Pairing failed. Put the Opal in pairing mode, make sure it is not bonded to another host or the GE app, and check the log for 'Opal pairing diagnostics'.",
    "link_dropped": "The Bluetooth link dropped repeatedly while pairing.",
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


async def _async_can_read_state(
    client: BleakClientWithServiceCache, diag: dict[str, Any] | None = None
) -> bool:
    """Return True if a protected state characteristic can be read."""
    characteristic = client.services.get_characteristic(CHAR_MAKE_STATE)
    if characteristic is None:
        return False
    try:
        await asyncio.wait_for(client.read_gatt_char(characteristic), READ_TIMEOUT)
    except Exception as err:
        if diag is not None:
            diag.setdefault('read_errors', []).append(f'{type(err).__name__}: {err}')
        return False
    return True


LAST_PAIR_DIAGNOSTICS: dict[str, Any] = {}
PAIR_ATTEMPTS = 3
READ_TIMEOUT = 8.0
BLEAK_PAIR_TIMEOUT = 30.0
DROP_MARKERS = ("no matching connection", "not connected", "disconnected", "unlikely error")


def _backend_info(client: BleakClientWithServiceCache) -> dict[str, str]:
    """Describe the backend serving this connection (local BlueZ vs proxy)."""
    inner = getattr(client, "_backend", None)
    name = type(inner).__name__ if inner is not None else type(client).__name__
    module = type(inner).__module__ if inner is not None else ""
    return {"backend": name, "module": module, "is_bluez": str("bluez" in module.lower() or "BlueZ" in name)}


def _link_dropped(client: BleakClientWithServiceCache | None, *texts: str | None) -> bool:
    if client is None or not client.is_connected:
        return True
    return any(t and any(m in t.lower() for m in DROP_MARKERS) for t in texts)


async def _async_pair_with_fallbacks(
    address: str, client: BleakClientWithServiceCache, diag: dict[str, Any]
) -> str | None:
    """Try the agent-backed BlueZ pairing, then bleak's pair(); record each step."""
    is_bluez = diag["backend_info"]["is_bluez"] == "True"
    if is_bluez:
        diag["stage"] = "dbus_pair"
        diag["dbus"] = await async_pair_via_dbus(address)
        if diag["dbus"]["ok"]:
            return None
        if _link_dropped(client, diag["dbus"].get("error")):
            return "link_dropped"
    diag["stage"] = "bleak_pair"
    try:
        await asyncio.wait_for(client.pair(), BLEAK_PAIR_TIMEOUT)
        return None
    except NotImplementedError as err:
        diag["bleak_pair_error"] = f"NotImplementedError: {err}"
        return None if is_bluez else "pairing_proxy"
    except Exception as err:
        diag["bleak_pair_error"] = f"{type(err).__name__}: {err}"
        _LOGGER.debug("bleak pair() failed", exc_info=True)
        if _link_dropped(client, diag["bleak_pair_error"]):
            return "link_dropped"
    return "pairing_failed" if is_bluez else "pairing_proxy"


async def _async_remove_stale_bond(address: str, diag: dict[str, Any]) -> None:
    """Ask BlueZ to forget the device so the next attempt pairs from scratch."""
    diag["stage"] = "remove_bond"
    diag["remove_bond"] = await async_remove_device_via_dbus(address)


async def _async_attempt(
    hass: HomeAssistant, address: str, force_pair: bool, diag: dict[str, Any]
) -> str | None:
    """One connect + pair + verify sequence on a held-open connection."""
    diag["stage"] = "connect"
    dropped: list[str] = []
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        return "device_not_found"
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            name="GE Opal Ice Maker",
            disconnected_callback=lambda _c: dropped.append(dt_util.utcnow().isoformat()),
            max_attempts=3,
            timeout=CONNECT_TIMEOUT,
            use_services_cache=False,
        )
    except Exception as err:
        diag["connect_error"] = f"{type(err).__name__}: {err}"
        _LOGGER.debug("Could not connect to Opal %s", address, exc_info=True)
        return "cannot_connect"
    diag["backend_info"] = _backend_info(client)
    try:
        diag["stage"] = "service_check"
        if client.services.get_service(GATT_SERVICE_UUID) is None:
            return "not_an_opal"
        diag["stage"] = "read_state"
        if not force_pair and await _async_can_read_state(client, diag):
            return None
        error = await _async_pair_with_fallbacks(address, client, diag)
        if dropped:
            diag["disconnects_during_attempt"] = dropped
            error = error or "link_dropped"
        if error is not None:
            return error
        diag["stage"] = "verify"
        if not client.is_connected or not await _async_can_read_state(client, diag):
            # The bond may only take effect on a fresh connection.
            await _async_disconnect(client)
            await asyncio.sleep(2)
            fresh, err = await _async_connect(hass, address)
            if fresh is None:
                return err or "cannot_connect"
            try:
                if not await _async_can_read_state(fresh, diag):
                    return "pairing_failed"
            finally:
                await _async_disconnect(fresh)
        return None
    finally:
        await _async_disconnect(client)


async def _async_connect_and_pair(
    hass: HomeAssistant, address: str, force_pair: bool = False
) -> str | None:
    """Retry connect+pair, reconnecting if the link drops; return error key or None."""
    LAST_PAIR_DIAGNOSTICS.clear()
    diag = LAST_PAIR_DIAGNOSTICS
    diag.update({"address": address, "force_pair": force_pair, "attempts": []})
    info = bluetooth.async_last_service_info(hass, address, connectable=True)
    if info is not None:
        diag["adapter_source"] = info.source
    error: str | None = None
    try:
        for attempt in range(1, PAIR_ATTEMPTS + 1):
            step: dict[str, Any] = {"attempt": attempt}
            diag["attempts"].append(step)
            try:
                error = await _async_attempt(hass, address, force_pair, step)
            except BaseException as err:
                step["exception"] = f"{type(err).__name__}: {err}"
                raise
            step["result"] = error or "ok"
            if error is None or error not in ("link_dropped", "pairing_failed", "cannot_connect"):
                break
            if attempt == 2 and error != "cannot_connect":
                # Possibly a stale or half-made bond: forget it, then pair fresh.
                await _async_remove_stale_bond(address, step)
            await asyncio.sleep(2 * attempt)
        diag["result"] = error or "paired"
        return "pairing_failed" if error == "link_dropped" else error
    finally:
        if diag.get("result") not in ("paired",) and not any(
            s.get("result") == "ok" for s in diag["attempts"]
        ):
            _LOGGER.warning("Opal pairing diagnostics: %s", diag)


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
