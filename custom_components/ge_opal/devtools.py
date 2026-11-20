"""Experimental developer tools for exploring the Opal's BLE protocol.

These helpers are for reverse engineering only. Reads are harmless; writes
are explicit opt-in via ``allow_write``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import CONF_ADDRESS, DATA_COORDINATOR, DOMAIN
from .coordinator import READ_CHARACTERISTICS, OpalCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_PROBE = "probe"
SERVICE_SEND_RAW = "send_raw"
MAX_LISTEN_SECONDS = 120
MAX_RAW_BYTES = 64

PROBE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ADDRESS): cv.string,
        vol.Optional("read_values", default=True): cv.boolean,
        vol.Optional("listen_seconds", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_LISTEN_SECONDS)
        ),
    }
)
SEND_RAW_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ADDRESS): cv.string,
        vol.Required("characteristic"): cv.string,
        vol.Required("data"): cv.string,
        vol.Optional("allow_write", default=False): cv.boolean,
        vol.Optional("response"): cv.boolean,
        vol.Optional("listen_seconds", default=3): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_LISTEN_SECONDS)
        ),
    }
)


def _hex(data: bytes | bytearray) -> str:
    return bytes(data).hex(" ")


def _resolve(hass: HomeAssistant, address: str | None) -> OpalCoordinator:
    """Find the coordinator for an address, or the only configured one."""
    runtimes = hass.data.get(DOMAIN, {})
    coordinators: list[OpalCoordinator] = [
        runtime[DATA_COORDINATOR] for runtime in runtimes.values()
    ]
    if address:
        for coordinator in coordinators:
            if coordinator.address == address.strip().upper():
                return coordinator
        raise HomeAssistantError(f"No configured Opal with address {address}")
    if len(coordinators) == 1:
        return coordinators[0]
    raise HomeAssistantError(
        "Specify 'address': " + (", ".join(c.address for c in coordinators) or "no Opal is configured")
    )


def _require_client(coordinator: OpalCoordinator):
    client = coordinator.client
    if client is None or not client.is_connected:
        raise HomeAssistantError(
            "The Opal is not connected. Press Reconnect Bluetooth or wait for the connection."
        )
    return client


def _dump_services(client) -> list[dict[str, Any]]:
    """Describe cached GATT services without any I/O."""
    services = []
    for service in client.services:
        services.append(
            {
                "uuid": service.uuid,
                "characteristics": [
                    {
                        "uuid": char.uuid,
                        "handle": char.handle,
                        "properties": list(char.properties),
                        "descriptors": [d.uuid for d in char.descriptors],
                    }
                    for char in service.characteristics
                ],
            }
        )
    return services


def dump_cached_services(coordinator: OpalCoordinator) -> list[dict[str, Any]]:
    """Return cached service layout for diagnostics (no GATT operations)."""
    client = coordinator.client
    if client is None or not client.is_connected:
        return []
    return _dump_services(client)


async def _subscribe_all(coordinator: OpalCoordinator, client) -> list:
    """Subscribe to every notifying characteristic the coordinator doesn't already."""
    subscribed = []
    known = {uuid.lower() for uuid in READ_CHARACTERISTICS.values()}
    for service in client.services:
        for char in service.characteristics:
            flags = list(char.properties)
            if char.uuid.lower() in known:
                continue  # already subscribed by the coordinator when supported
            if "notify" not in flags and "indicate" not in flags:
                continue
            try:
                await client.start_notify(
                    char,
                    lambda sender, data: coordinator.log_notification(sender.uuid, data),
                )
                subscribed.append(char)
            except Exception:
                _LOGGER.debug("Could not subscribe to %s", char.uuid, exc_info=True)
    return subscribed


async def _unsubscribe(client, subscribed: list) -> None:
    for char in subscribed:
        try:
            await client.stop_notify(char)
        except Exception:
            _LOGGER.debug("Could not unsubscribe from %s", char.uuid, exc_info=True)

async def async_probe(
    coordinator: OpalCoordinator, read_values: bool, listen_seconds: int
) -> dict[str, Any]:
    """Enumerate GATT, read every readable characteristic, and listen for notifications."""
    client = _require_client(coordinator)
    async with coordinator._write_lock:
        services = _dump_services(client)
        if read_values:
            for service in client.services:
                for char in service.characteristics:
                    entry = next(
                        c
                        for s in services
                        for c in s["characteristics"]
                        if c["handle"] == char.handle
                    )
                    if "read" not in char.properties:
                        continue
                    try:
                        entry["value_hex"] = _hex(await client.read_gatt_char(char))
                    except Exception as err:  # BLE errors vary by backend
                        entry["read_error"] = repr(err)
        start_seq = coordinator.notification_seq
        subscribed = await _subscribe_all(coordinator, client)
        try:
            await asyncio.sleep(listen_seconds)
        finally:
            await _unsubscribe(client, subscribed)
        notifications = [e for e in coordinator.notification_log if e["seq"] > start_seq]
    result = {
        "address": coordinator.address,
        "services": services,
        "notifications": notifications,
    }
    coordinator.last_probe = result
    for service in services:
        for char in service["characteristics"]:
            _LOGGER.info(
                "Opal probe %s [%s] value=%s",
                char["uuid"],
                ",".join(char["properties"]),
                char.get("value_hex", char.get("read_error", "-")),
            )
    return result


async def async_send_raw(
    coordinator: OpalCoordinator,
    characteristic: str,
    data: str,
    allow_write: bool,
    response: bool | None,
    listen_seconds: int,
) -> dict[str, Any]:
    """Write raw hex to a characteristic and capture notifications."""
    if not allow_write:
        raise HomeAssistantError(
            "Refusing to write: set allow_write to true to confirm you want to send this raw command."
        )
    try:
        payload = bytes.fromhex(data.replace("0x", "").replace(",", " "))
    except ValueError as err:
        raise HomeAssistantError(f"'data' must be hex, e.g. '01' or '01 00 ff': {err}")
    if not payload or len(payload) > MAX_RAW_BYTES:
        raise HomeAssistantError(f"'data' must be 1-{MAX_RAW_BYTES} bytes")

    client = _require_client(coordinator)
    uuid = characteristic.strip().lower()
    char = client.services.get_characteristic(uuid)
    if char is None:
        raise HomeAssistantError(f"Characteristic {uuid} not found")
    flags = list(char.properties)
    if "write" not in flags and "write-without-response" not in flags:
        raise HomeAssistantError(f"Characteristic {uuid} is not writable ({flags})")
    with_response = response if response is not None else "write" in flags

    async with coordinator._write_lock:
        _LOGGER.warning(
            "Opal raw write to %s: %s (response=%s)", uuid, _hex(payload), with_response
        )
        start_seq = coordinator.notification_seq
        subscribed = await _subscribe_all(coordinator, client)
        try:
            await client.write_gatt_char(char, payload, response=with_response)
            await asyncio.sleep(listen_seconds)
        finally:
            await _unsubscribe(client, subscribed)
        notifications = [e for e in coordinator.notification_log if e["seq"] > start_seq]
        readback = None
        if "read" in char.properties:
            try:
                readback = _hex(await client.read_gatt_char(char))
            except Exception as err:
                readback = f"error: {err!r}"
    await coordinator._read_available()
    result = {
        "characteristic": uuid,
        "written_hex": _hex(payload),
        "with_response": with_response,
        "readback_hex": readback,
        "notifications": notifications,
        "state": dict(coordinator.raw_values),
    }
    _LOGGER.info("Opal raw write result: %s", result)
    return result


def async_register_services(hass: HomeAssistant) -> None:
    """Register experimental developer services."""

    async def _probe(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return await async_probe(
            coordinator, call.data["read_values"], call.data["listen_seconds"]
        )

    async def _send_raw(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return await async_send_raw(
            coordinator,
            call.data["characteristic"],
            call.data["data"],
            call.data["allow_write"],
            call.data.get("response"),
            call.data["listen_seconds"],
        )

    hass.services.async_register(
        DOMAIN, SERVICE_PROBE, _probe, schema=PROBE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_RAW, _send_raw, schema=SEND_RAW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
