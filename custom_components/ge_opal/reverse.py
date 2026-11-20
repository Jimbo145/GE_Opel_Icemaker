"""Guided reverse-engineering helpers: snapshots, diffs and capture sessions.

Experimental developer tooling. Everything here is read-only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import CONF_ADDRESS, DOMAIN
from .coordinator import OpalCoordinator
from .devtools import _hex, _require_client, _resolve, _subscribe_all, _unsubscribe

_LOGGER = logging.getLogger(__name__)

SERVICE_SNAPSHOT = "snapshot"
SERVICE_CAPTURE_START = "capture_start"
SERVICE_CAPTURE_MARK = "capture_mark"
SERVICE_CAPTURE_STOP = "capture_stop"
MAX_SNAPSHOTS = 25

_ADDRESS = {vol.Optional(CONF_ADDRESS): cv.string}
SNAPSHOT_SCHEMA = vol.Schema(
    {
        **_ADDRESS,
        vol.Required("label"): cv.string,
        vol.Optional("compare_to"): cv.string,
        vol.Optional("notify", default=True): cv.boolean,
    }
)
CAPTURE_START_SCHEMA = vol.Schema(
    {
        **_ADDRESS,
        vol.Required("label"): cv.string,
        vol.Optional("poll_seconds", default=2): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=60)
        ),
    }
)
CAPTURE_MARK_SCHEMA = vol.Schema({**_ADDRESS, vol.Required("note"): cv.string})
CAPTURE_STOP_SCHEMA = vol.Schema(
    {**_ADDRESS, vol.Optional("notify", default=True): cv.boolean}
)


async def _read_all(client) -> dict[str, str]:
    """Read every readable characteristic; return uuid -> hex (errors skipped)."""
    values: dict[str, str] = {}
    for service in client.services:
        for char in service.characteristics:
            if "read" not in char.properties:
                continue
            try:
                values[char.uuid.lower()] = _hex(await client.read_gatt_char(char))
            except Exception:
                _LOGGER.debug("Unable to read %s", char.uuid, exc_info=True)
    return values


def diff_snapshots(before: dict[str, str], after: dict[str, str]) -> list[dict[str, Any]]:
    """Return per-characteristic, per-byte differences between two snapshots."""
    changes: list[dict[str, Any]] = []
    for uuid in sorted(set(before) | set(after)):
        old, new = before.get(uuid), after.get(uuid)
        if old == new:
            continue
        old_b = bytes.fromhex(old) if old else b""
        new_b = bytes.fromhex(new) if new else b""
        bytes_changed = []
        for i in range(max(len(old_b), len(new_b))):
            a = old_b[i] if i < len(old_b) else None
            b = new_b[i] if i < len(new_b) else None
            if a != b:
                bytes_changed.append(
                    {
                        "index": i,
                        "before": None if a is None else f"{a:02x} ({a:08b})",
                        "after": None if b is None else f"{b:02x} ({b:08b})",
                        "xor_bits": None if a is None or b is None else f"{a ^ b:08b}",
                    }
                )
        changes.append(
            {"characteristic": uuid, "before": old, "after": new, "bytes": bytes_changed}
        )
    return changes


def _format_diff(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "No readable characteristic changed."
    lines = []
    for change in changes:
        lines.append(f"**{change['characteristic']}**: `{change['before']}` -> `{change['after']}`")
        for b in change["bytes"]:
            lines.append(
                f"- byte {b['index']}: {b['before']} -> {b['after']} (changed bits {b['xor_bits']})"
            )
    return "\n".join(lines)


def _notify(hass: HomeAssistant, title: str, message: str, notification_id: str) -> None:
    persistent_notification.async_create(
        hass, message, title=title, notification_id=notification_id
    )


async def async_snapshot(
    hass: HomeAssistant,
    coordinator: OpalCoordinator,
    label: str,
    compare_to: str | None,
    notify: bool,
) -> dict[str, Any]:
    """Read all readable characteristics, store under label, diff vs a previous one."""
    client = _require_client(coordinator)
    previous_label = compare_to
    if previous_label is None and coordinator.snapshots:
        previous_label = next(reversed(coordinator.snapshots))
    if compare_to is not None and compare_to not in coordinator.snapshots:
        raise HomeAssistantError(
            f"No snapshot labelled '{compare_to}'. Existing: {', '.join(coordinator.snapshots) or 'none'}"
        )
    async with coordinator._write_lock:
        values = await _read_all(client)
    coordinator.snapshots.pop(label, None)
    coordinator.snapshots[label] = values
    while len(coordinator.snapshots) > MAX_SNAPSHOTS:
        coordinator.snapshots.pop(next(iter(coordinator.snapshots)))
    result: dict[str, Any] = {
        "label": label,
        "taken_at": dt_util.utcnow().isoformat(),
        "values": values,
    }
    if previous_label and previous_label != label:
        changes = diff_snapshots(coordinator.snapshots[previous_label], values)
        result["compared_to"] = previous_label
        result["changes"] = changes
        report = f"Snapshot **{label}** vs **{previous_label}**\n\n{_format_diff(changes)}"
    else:
        report = f"Baseline snapshot **{label}** stored ({len(values)} readable characteristics). Change one thing on the Opal, then take another snapshot."
    _LOGGER.info("Opal snapshot report: %s", report)
    if notify:
        _notify(hass, "GE Opal snapshot", report, f"{DOMAIN}_snapshot")
    return result


async def _poll_loop(coordinator: OpalCoordinator, client, poll_seconds: float) -> None:
    """Log changes in readable characteristics during a capture."""
    capture = coordinator.capture
    last = capture["last_values"]
    while True:
        await asyncio.sleep(poll_seconds)
        if not client.is_connected:
            capture["events"].append(
                {"time": dt_util.utcnow().isoformat(), "type": "disconnected"}
            )
            return
        async with coordinator._write_lock:
            values = await _read_all(client)
        for change in diff_snapshots(last, values):
            capture["events"].append(
                {
                    "time": dt_util.utcnow().isoformat(),
                    "type": "change",
                    "characteristic": change["characteristic"],
                    "before": change["before"],
                    "after": change["after"],
                    "label": capture["current_note"],
                }
            )
            _LOGGER.info(
                "Opal capture change %s: %s -> %s",
                change["characteristic"], change["before"], change["after"],
            )
        last.update(values)


async def async_capture_start(
    hass: HomeAssistant, coordinator: OpalCoordinator, label: str, poll_seconds: float
) -> dict[str, Any]:
    """Start a capture: baseline snapshot, notification subscriptions, polling."""
    if coordinator.capture is not None:
        raise HomeAssistantError(
            f"A capture ('{coordinator.capture['label']}') is already running; call capture_stop first."
        )
    client = _require_client(coordinator)
    async with coordinator._write_lock:
        baseline = await _read_all(client)
        subscribed = await _subscribe_all(coordinator, client)
    coordinator.snapshots[f"capture:{label}:start"] = baseline
    coordinator.capture = {
        "label": label,
        "started": dt_util.utcnow().isoformat(),
        "start_seq": coordinator.notification_seq,
        "baseline": baseline,
        "last_values": dict(baseline),
        "events": [],
        "current_note": label,
        "subscribed": subscribed,
        "client": client,
    }
    coordinator.capture["task"] = hass.async_create_task(
        _poll_loop(coordinator, client, poll_seconds),
        name=f"GE Opal capture {label}",
    )
    return {
        "label": label,
        "baseline_characteristics": len(baseline),
        "subscribed": [c.uuid for c in subscribed],
        "hint": "Perform one action, call ge_opal.capture_mark with a note describing it, then ge_opal.capture_stop.",
    }


def async_capture_mark(coordinator: OpalCoordinator, note: str) -> dict[str, Any]:
    """Annotate the running capture with what you just did."""
    capture = coordinator.capture
    if capture is None:
        raise HomeAssistantError("No capture is running; call capture_start first.")
    capture["current_note"] = note
    event = {"time": dt_util.utcnow().isoformat(), "type": "mark", "note": note}
    capture["events"].append(event)
    _LOGGER.warning("Opal capture mark: %s", note)
    return event


async def async_capture_stop(
    hass: HomeAssistant, coordinator: OpalCoordinator, notify: bool
) -> dict[str, Any]:
    """Stop the capture and return a timeline plus a start-to-end diff."""
    capture = coordinator.capture
    if capture is None:
        raise HomeAssistantError("No capture is running.")
    coordinator.capture = None
    capture["task"].cancel()
    try:
        await capture["task"]
    except asyncio.CancelledError:
        pass
    client = capture["client"]
    await _unsubscribe(client, capture["subscribed"])
    end_values = capture["last_values"]
    if client.is_connected:
        async with coordinator._write_lock:
            end_values = await _read_all(client)
    coordinator.snapshots[f"capture:{capture['label']}:end"] = end_values

    events = list(capture["events"]) + [
        {
            "time": e["time"],
            "type": "notification",
            "characteristic": e["characteristic"],
            "hex": e["hex"],
        }
        for e in coordinator.notification_log
        if e["seq"] > capture["start_seq"]
    ]
    events.sort(key=lambda e: e["time"])
    changes = diff_snapshots(capture["baseline"], end_values)
    result = {
        "label": capture["label"],
        "started": capture["started"],
        "stopped": dt_util.utcnow().isoformat(),
        "timeline": events,
        "start_to_end_changes": changes,
    }
    coordinator.last_capture = result
    lines = [f"Capture **{capture['label']}**: {len(events)} events", ""]
    for e in events:
        detail = (
            e.get("note")
            or (f"{e.get('characteristic')} {e.get('before')} -> {e.get('after')}" if e["type"] == "change" else None)
            or (f"{e.get('characteristic')} {e.get('hex')}" if e["type"] == "notification" else "")
        )
        lines.append(f"- {e['time']} **{e['type']}** {detail}")
    lines += ["", "Start-to-end diff:", _format_diff(changes)]
    report = "\n".join(lines)
    _LOGGER.info("Opal capture report:\n%s", report)
    if notify:
        _notify(hass, "GE Opal capture", report, f"{DOMAIN}_capture")
    return result


def async_register_guided_services(hass: HomeAssistant) -> None:
    """Register snapshot and capture services."""

    async def _snapshot(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return await async_snapshot(
            hass, coordinator, call.data["label"], call.data.get("compare_to"),
            call.data["notify"],
        )

    async def _start(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return await async_capture_start(
            hass, coordinator, call.data["label"], call.data["poll_seconds"]
        )

    async def _mark(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return async_capture_mark(coordinator, call.data["note"])

    async def _stop(call: ServiceCall) -> dict[str, Any]:
        coordinator = _resolve(hass, call.data.get(CONF_ADDRESS))
        return await async_capture_stop(hass, coordinator, call.data["notify"])

    for name, handler, schema in (
        (SERVICE_SNAPSHOT, _snapshot, SNAPSHOT_SCHEMA),
        (SERVICE_CAPTURE_START, _start, CAPTURE_START_SCHEMA),
        (SERVICE_CAPTURE_MARK, _mark, CAPTURE_MARK_SCHEMA),
        (SERVICE_CAPTURE_STOP, _stop, CAPTURE_STOP_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
