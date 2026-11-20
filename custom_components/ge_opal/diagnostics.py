"""Diagnostics for GE Opal Ice Maker."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DATA_COORDINATOR, DOMAIN
from .devtools import dump_cached_services
from .pairing import LAST_PAIR_DIAGNOSTICS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return safe diagnostic information without issuing GATT operations."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    return {
        "device": {
            "address": entry.data.get("address"),
            "name": entry.data.get("name"),
        },
        "connection": {
            "connected": coordinator.connected,
            "ble_client_connected": bool(
                coordinator.client and coordinator.client.is_connected
            ),
        },
        "state": dict(coordinator.raw_values),
        "developer": {
            "note": "Experimental. Cached service layout and notifications are recorded without extra GATT I/O; run the ge_opal.probe service to populate last_probe.",
            "cached_services": dump_cached_services(coordinator),
            "recent_notifications": list(coordinator.notification_log),
            "last_pair_attempt": dict(LAST_PAIR_DIAGNOSTICS),
            "last_probe": coordinator.last_probe,
            "last_watch": coordinator.last_watch,
            "last_explore": coordinator.last_explore,
            "snapshots": coordinator.snapshots,
            "last_capture": coordinator.last_capture,
        },
        "characteristic_properties": dict(coordinator.characteristic_properties),
        "protocol": {
            "gatt_service_uuid": "3e6763c5-9429-40cc-909e-bebf8c7487be",
            "advertisement_service_uuid": "e2779da7-0a82-4be7-b754-31ed3e727253",
            "uninspected_vendor_service_uuid": "e936877a-8dd0-faa7-b648-f46acda1f27b",
        },
        "note": "Diagnostics do not read unknown characteristics or send control writes.",
    }
