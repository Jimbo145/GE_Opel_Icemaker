"""Targeted exploration tools for unknown Opal characteristics.

Experimental developer tooling. Everything here is read-only: it reads
characteristics and descriptors and subscribes to notifications, but never
writes.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import CONF_ADDRESS, DOMAIN
from .coordinator import READ_CHARACTERISTICS, OpalCoordinator
from .devtools import (
    VENDOR_SERVICE_UUID,
    _hex,
    _require_client,
    _resolve,
    _unsubscribe,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_DECODE = "decode"
SERVICE_WATCH = "watch"
SERVICE_EXPLORE_SERVICE = "explore_service"

MAX_WATCH_SECONDS = 900
MAX_EXPLORE_LISTEN_SECONDS = 120

# Friendly names accepted by ge_opal.watch. "vendor" expands to every
# characteristic of the unexplored vendor service.
CHARACTERISTIC_ALIASES = {
    "make_state": "097a2751-ca0d-432f-87b5-7d2f31e45551",
    "make_ice": "79994230-4b04-40cd-85c9-02dd1a8d4dd0",
    "light": "37988f00-ea39-4a2d-9983-afad6535c02e",
    "schedule_enabled": "b45163b3-1092-4725-95dc-1a43ac4a9b88",
    "schedule": "9e1ae873-cb5e-4485-9884-5c5a3ad60e47",
    "clean_cycle": "efe4bd77-0600-47d7-b3f6-dc81af0d9aaf",
    "ice_bin": "5bcbf6b1-de80-94b6-0f4b-99fb984707b6",
    "temperature": "bd205030-b5ce-4847-b78d-83bff1450a6b",
    "firmware": "cf88a5b6-6687-4f14-8e21-bb9e78a40ecc",
    "ota_version": "318db1f5-67f1-119b-6a41-1eeca0c744ce",
    "unknown_14ff": "14ff6dfb-36fa-4456-927d-759e1a9a8446",
    "unknown_4fb3": "4fb34ab1-6207-e5a0-484f-e24a7f638fff",
}

_ADDRESS = {vol.Optional(CONF_ADDRESS): cv.string}
DECODE_SCHEMA = vol.Schema({vol.Required("data"): cv.string})
WATCH_SCHEMA = vol.Schema(
    {
        **_ADDRESS,
        vol.Required("characteristics"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("duration", default=60): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=MAX_WATCH_SECONDS)
        ),
        vol.Optional("interval", default=2): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=60)
        ),
        vol.Optional("label", default="watch"): cv.string,
    }
)
EXPLORE_SERVICE_SCHEMA = vol.Schema(
    {
        **_ADDRESS,
        vol.Optional("service", default=VENDOR_SERVICE_UUID): cv.string,
        vol.Optional("listen_seconds", default=10): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_EXPLORE_LISTEN_SECONDS)
        ),
    }
)


def decode_value(data: bytes | bytearray) -> dict[str, Any]:
    """Return several plausible interpretations of a raw value.

    Nothing here knows the real format; it just lays the options side by side
    so changes can be compared against a known reference.
    """
    data = bytes(data)
    n = len(data)
    out: dict[str, Any] = {
        "hex": data.hex(" "),
        "length": n,
        "bytes": list(data),
        "bits": " ".join(f"{b:08b}" for b in data),
    }
    if n == 0:
        return out
    out["uint8"] = data[0]
    out["int8"] = struct.unpack("b", data[:1])[0]
    if n >= 2:
        out["uint16_le"] = struct.unpack("<H", data[:2])[0]
        out["uint16_be"] = struct.unpack(">H", data[:2])[0]
        out["int16_le"] = struct.unpack("<h", data[:2])[0]
        out["int16_be"] = struct.unpack(">h", data[:2])[0]
    if n >= 4:
        out["uint32_le"] = struct.unpack("<I", data[:4])[0]
        out["uint32_be"] = struct.unpack(">I", data[:4])[0]
        for name, fmt in (("float32_le", "<f"), ("float32_be", ">f")):
            value = struct.unpack(fmt, data[:4])[0]
            if math.isfinite(value) and abs(value) < 1e6:
                out[name] = round(value, 3)
    if all(0x20 <= b < 0x7F for b in data):
        out["ascii"] = data.decode("ascii")
    if n <= 2:
        raw = out.get("uint16_le", out["uint8"]) if n == 2 else out["uint8"]
        out["temperature_guesses"] = {
            "raw": raw,
            "raw_as_F_to_C": round((raw - 32) * 5 / 9, 1),
            "raw_as_C_to_F": round(raw * 9 / 5 + 32, 1),
            "raw_div_2": raw / 2,
            "raw_div_10": raw / 10,
            "raw_div_100": raw / 100,
        }
    return out


def _short_decode(data: bytes) -> str:
    d = decode_value(data)
    parts = [f"u8={d['uint8']}"] if "uint8" in d else []
    if "uint16_le" in d:
        parts.append(f"u16le={d['uint16_le']} u16be={d['uint16_be']}")
    if "ascii" in d:
        parts.append(f"ascii={d['ascii']!r}")
    return " ".join(parts)


def _resolve_characteristics(client, names: list[str]) -> dict[str, Any]:
    """Map watch inputs (aliases, UUIDs, 'vendor') to GATT characteristics."""
    found: dict[str, Any] = {}
    for raw in names:
        name = raw.strip().lower()
        if name == "vendor":
            service = client.services.get_service(VENDOR_SERVICE_UUID)
            if service is None:
                raise HomeAssistantError(
                    f"Vendor service {VENDOR_SERVICE_UUID} not found on this device"
                )
            for char in service.characteristics:
                found[char.uuid.lower()] = char
            continue
        uuid = CHARACTERISTIC_ALIASES.get(name, name)
        char = client.services.get_characteristic(uuid)
        if char is None:
            raise HomeAssistantError(
                f"Characteristic '{raw}' not found. Known names: "
                + ", ".join(sorted(CHARACTERISTIC_ALIASES))
                + ", vendor, or a UUID."
            )
        found[char.uuid.lower()] = char
    return found


async def async_watch(
    hass: HomeAssistant,
    coordinator: OpalCoordinator,
    names: list[str],
    duration: int,
    interval: float,
    label: str,
) -> dict[str, Any]:
    """Poll chosen characteristics and log only changes, with decoded guesses."""
    client = _require_client(coordinator)
    chars = _resolve_characteristics(client, names)
    known = {uuid.lower() for uuid in READ_CHARACTERISTICS.values()}
    start_seq = coordinator.notification_seq

    subscribed = []
    async with coordinator._write_lock:
        for uuid, char in chars.items():
            flags = char.properties
            if uuid in known or ("notify" not in flags and "indicate" not in flags):
                continue
            try:
                await client.start_notify(
                    char, lambda sender, data: coordinator.log_notification(sender.uuid, data)
                )
                subscribed.append(char)
            except Exception:
                _LOGGER.debug("Could not subscribe to %s", uuid, exc_info=True)

    last: dict[str, bytes] = {}
    events: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    try:
        while True:
            if not client.is_connected:
                events.append({"time": dt_util.utcnow().isoformat(), "type": "disconnected"})
                break
            async with coordinator._write_lock:
                for uuid, char in chars.items():
                    if "read" not in char.properties:
                        continue
                    try:
                        value = bytes(await client.read_gatt_char(char))
                    except Exception as err:
                        errors[uuid] = repr(err)
                        continue
                    if last.get(uuid) == value:
                        continue
                    event = {
                        "time": dt_util.utcnow().isoformat(),
                        "type": "initial" if uuid not in last else "change",
                        "characteristic": uuid,
                        "before": _hex(last[uuid]) if uuid in last else None,
                        "after": _hex(value),
                        "decoded": decode_value(value),
                    }
                    last[uuid] = value
                    events.append(event)
                    _LOGGER.info(
                        "Opal watch %s %s: %s -> %s (%s)",
                        label, uuid, event["before"], event["after"], _short_decode(value),
                    )
            if loop.time() + interval >= deadline:
                break
            await asyncio.sleep(interval)
    finally:
        if client.is_connected:
            await _unsubscribe(client, subscribed)

    for entry in coordinator.notification_log:
        if entry["seq"] > start_seq and entry["characteristic"].lower() in chars:
            events.append(
                {
                    "time": entry["time"],
                    "type": "notification",
                    "characteristic": entry["characteristic"].lower(),
                    "after": entry["hex"],
                    "decoded": decode_value(bytes.fromhex(entry["hex"].replace(" ", ""))),
                }
            )
    events.sort(key=lambda e: e["time"])
    result = {
        "label": label,
        "watched": sorted(chars),
        "not_readable": sorted(u for u, c in chars.items() if "read" not in c.properties),
        "read_errors": errors,
        "events": events,
    }
    coordinator.last_watch = result
    return result


async def async_explore_service(
    coordinator: OpalCoordinator, service_uuid: str, listen_seconds: int
) -> dict[str, Any]:
    """Enumerate one service completely and read it (read-only, no writes)."""
    client = _require_client(coordinator)
    service_uuid = service_uuid.strip().lower()
    service = client.services.get_service(service_uuid)
    if service is None:
        return {
            "service": service_uuid,
            "present": False,
            "available_services": [s.uuid for s in client.services],
            "hint": "The service is not exposed on this connection. Some services only appear when unbonded or in a special mode.",
        }

    characteristics: list[dict[str, Any]] = []
    subscribed = []
    start_seq = coordinator.notification_seq
    async with coordinator._write_lock:
        for char in service.characteristics:
            entry: dict[str, Any] = {
                "uuid": char.uuid,
                "handle": char.handle,
                "properties": list(char.properties),
                "descriptors": [],
            }
            for desc in char.descriptors:
                d_entry: dict[str, Any] = {"uuid": desc.uuid, "handle": desc.handle}
                try:
                    d_entry["value_hex"] = _hex(await client.read_gatt_descriptor(desc.handle))
                except Exception as err:
                    d_entry["read_error"] = repr(err)
                entry["descriptors"].append(d_entry)
            if "read" in char.properties:
                try:
                    value = bytes(await client.read_gatt_char(char))
                    entry["decoded"] = decode_value(value)
                except Exception as err:
                    entry["read_error"] = repr(err)
            if "notify" in char.properties or "indicate" in char.properties:
                try:
                    await client.start_notify(
                        char,
                        lambda sender, data: coordinator.log_notification(sender.uuid, data),
                    )
                    subscribed.append(char)
                    entry["subscribed"] = True
                except Exception as err:
                    entry["subscribed"] = False
                    entry["subscribe_error"] = repr(err)
            characteristics.append(entry)
        try:
            await asyncio.sleep(listen_seconds)
        finally:
            await _unsubscribe(client, subscribed)

    uuids = {c["uuid"].lower() for c in characteristics}
    result = {
        "service": service_uuid,
        "present": True,
        "characteristics": characteristics,
        "notifications": [
            e
            for e in coordinator.notification_log
            if e["seq"] > start_seq and e["characteristic"].lower() in uuids
        ],
    }
    coordinator.last_explore = result
    for char in characteristics:
        _LOGGER.info(
            "Opal explore %s [%s] %s",
            char["uuid"],
            ",".join(char["properties"]),
            char.get("decoded", {}).get("hex", char.get("read_error", "-")),
        )
    return result


def async_register_explore_services(hass: HomeAssistant) -> None:
    """Register decode, watch and explore_service."""

    async def _decode(call: ServiceCall) -> dict[str, Any]:
        try:
            data = bytes.fromhex(call.data["data"].replace("0x", "").replace(",", " "))
        except ValueError as err:
            raise HomeAssistantError(f"'data' must be hex, e.g. '1c 00': {err}")
        return decode_value(data)

    async def _watch(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return await async_watch(
            hass, coordinator, call.data["characteristics"], call.data["duration"],
            call.data["interval"], call.data["label"],
        )

    async def _explore(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return await async_explore_service(
            coordinator, call.data["service"], call.data["listen_seconds"]
        )

    for name, handler, schema in (
        (SERVICE_DECODE, _decode, DECODE_SCHEMA),
        (SERVICE_WATCH, _watch, WATCH_SCHEMA),
        (SERVICE_EXPLORE_SERVICE, _explore, EXPLORE_SERVICE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
