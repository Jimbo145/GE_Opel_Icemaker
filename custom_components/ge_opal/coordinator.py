"""Bluetooth connection and data coordinator for GE Opal."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from bleak.backends.characteristic import BleakGATTCharacteristic
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CHAR_CLEANING_PHASE,
    CHAR_ICE_BIN_STATE,
    CHAR_MAKE_ICE_CONTROL,
    CHAR_MAKE_STATE,
    CHAR_NIGHT_MODE,
    CONNECT_TIMEOUT,
    GATT_SERVICE_UUID,
    ICE_BIN_NAMES,
    MAKE_STATE_NAMES,
    POLL_INTERVAL,
    RECONNECT_DELAY,
)
from .pairing import ERROR_MESSAGES, async_connect_and_pair, async_unpair

_LOGGER = logging.getLogger(__name__)

READ_CHARACTERISTICS = {
    "make_state": CHAR_MAKE_STATE,
    "ice_bin_state": CHAR_ICE_BIN_STATE,
    "night_mode": CHAR_NIGHT_MODE,
    "cleaning_phase": CHAR_CLEANING_PHASE,
}


class OpalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Maintain a BLE connection and publish Opal state."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        super().__init__(hass, _LOGGER, name="GE Opal", update_interval=None)
        self.address = address.upper()
        self.client: BleakClient | None = None
        self.connected = False
        self._runner: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()
        self.paused = False
        self._wake_event = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self.notification_log: deque[dict[str, Any]] = deque(maxlen=200)
        self.notification_seq = 0
        self.last_probe: dict[str, Any] | None = None
        self.snapshots: dict[str, dict[str, str]] = {}
        self.capture: dict[str, Any] | None = None
        self.last_capture: dict[str, Any] | None = None
        self.characteristic_properties: dict[str, list[str]] = {}
        self.raw_values: dict[str, int | None] = {
            key: None for key in READ_CHARACTERISTICS
        }
        self.data = dict(self.raw_values)

    async def async_start(self) -> None:
        """Start the reconnecting connection worker."""
        self._runner = self.hass.async_create_task(
            self._connection_worker(), name=f"GE Opal BLE {self.address}"
        )

    async def async_stop(self) -> None:
        """Stop the worker and disconnect cleanly."""
        self._stop_event.set()
        self._disconnect_event.set()
        self._wake_event.set()
        if self.capture and self.capture.get("task"):
            self.capture["task"].cancel()
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
        await self._disconnect()

    async def _sleep(self, timeout: float | None) -> None:
        """Sleep until timeout, a stop request, or a pause/resume wake-up."""
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self._wake_event.clear()

    async def async_disconnect_bluetooth(self) -> None:
        """Disconnect and stop reconnecting until reconnect is requested."""
        self.paused = True
        self._disconnect_event.set()
        self._wake_event.set()
        await self._disconnect()
        self.async_update_listeners()

    async def async_reconnect_bluetooth(self) -> None:
        """Resume the connection worker and connect immediately."""
        self.paused = False
        self._wake_event.set()
        self.async_update_listeners()

    async def async_unpair(self) -> None:
        """Disconnect, stop reconnecting, and remove the Bluetooth bond."""
        await self.async_disconnect_bluetooth()
        error = await async_unpair(self.hass, self.address)
        if error is not None:
            raise HomeAssistantError(ERROR_MESSAGES[error])

    async def async_repair(self) -> None:
        """Unpair, then reconnect and pair again, then resume the worker."""
        await self.async_disconnect_bluetooth()
        error = await async_unpair(self.hass, self.address)
        if error is not None:
            raise HomeAssistantError(ERROR_MESSAGES[error])
        error = await async_connect_and_pair(
            self.hass, self.address, force_pair=True
        )
        if error is not None:
            raise HomeAssistantError(ERROR_MESSAGES[error])
        await self.async_reconnect_bluetooth()

    @staticmethod
    def _first_byte(data: bytearray | bytes) -> int | None:
        """Decode the first characteristic byte, if present."""
        return int(data[0]) if data else None

    def _publish(self, key: str, value: int | None) -> None:
        """Publish a value received by read or notification."""
        self.raw_values[key] = value
        self.async_set_updated_data(dict(self.raw_values))

    def _notification_callback(self, key: str):
        """Build a Bleak notification callback."""
        def _callback(sender: BleakGATTCharacteristic, data: bytearray) -> None:
            self.log_notification(sender.uuid, data)
            self._publish(key, self._first_byte(data))
        return _callback

    def log_notification(self, uuid: str, data: bytes | bytearray) -> None:
        """Record a notification (hex) for developer diagnostics."""
        self.notification_seq += 1
        entry = {
            "seq": self.notification_seq,
            "time": dt_util.utcnow().isoformat(),
            "characteristic": str(uuid).lower(),
            "hex": bytes(data).hex(" "),
        }
        self.notification_log.append(entry)
        _LOGGER.debug("Opal notification %s: %s", entry["characteristic"], entry["hex"])

    async def _read_available(self) -> None:
        """Read supported state characteristics without writing anything."""
        if self.client is None or not self.client.is_connected:
            return
        for key, uuid in READ_CHARACTERISTICS.items():
            characteristic = self.client.services.get_characteristic(uuid)
            if characteristic is None:
                _LOGGER.warning("Opal characteristic %s was not found", uuid)
                continue
            flags = list(characteristic.properties)
            self.characteristic_properties[uuid] = flags
            if "read" not in flags:
                continue
            try:
                value = await self.client.read_gatt_char(characteristic)
            except Exception:  # BLE errors vary by backend/platform
                _LOGGER.debug("Unable to read Opal characteristic %s", uuid, exc_info=True)
                continue
            self._publish(key, self._first_byte(value))

    async def _subscribe_notifications(self) -> None:
        """Subscribe only to characteristics whose runtime flags permit it."""
        if self.client is None:
            return
        for key, uuid in READ_CHARACTERISTICS.items():
            characteristic = self.client.services.get_characteristic(uuid)
            if characteristic is None:
                continue
            flags = list(characteristic.properties)
            self.characteristic_properties[uuid] = flags
            if "notify" not in flags and "indicate" not in flags:
                continue
            try:
                await self.client.start_notify(
                    characteristic, self._notification_callback(key)
                )
                _LOGGER.debug("Subscribed to Opal notifications for %s", key)
            except Exception:
                _LOGGER.debug("Could not subscribe to %s; reads remain enabled", uuid, exc_info=True)

    async def _connection_worker(self) -> None:
        """Connect, subscribe, refresh periodically, and reconnect on loss."""
        while not self._stop_event.is_set():
            if self.paused:
                await self._sleep(None)
                continue
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                _LOGGER.debug("Opal %s is not currently reachable via HA Bluetooth", self.address)
                await self._sleep(RECONNECT_DELAY)
                continue

            try:
                self._disconnect_event.clear()
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    name="GE Opal Ice Maker",
                    disconnected_callback=lambda _client: self._disconnect_event.set(),
                    max_attempts=3,
                    timeout=CONNECT_TIMEOUT,
                    use_services_cache=True,
                )
                self.client = client
                self.connected = True
                self.async_set_updated_data(dict(self.raw_values))
                _LOGGER.info("Connected to GE Opal Ice Maker at %s", self.address)

                service = client.services.get_service(GATT_SERVICE_UUID)
                if service is None:
                    raise RuntimeError(f"Opal GATT service {GATT_SERVICE_UUID} not found")

                await self._subscribe_notifications()
                await self._read_available()

                while not self._stop_event.is_set() and client.is_connected:
                    try:
                        await asyncio.wait_for(
                            self._disconnect_event.wait(), timeout=POLL_INTERVAL
                        )
                        break
                    except asyncio.TimeoutError:
                        await self._read_available()

            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("GE Opal BLE connection failed; will retry", exc_info=True)
            finally:
                await self._disconnect()
                if not self._stop_event.is_set() and not self.paused:
                    await self._sleep(RECONNECT_DELAY)

    async def _disconnect(self) -> None:
        """Disconnect the current client and mark entities unavailable."""
        client, self.client = self.client, None
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting from Opal", exc_info=True)
        if self.connected:
            self.connected = False
            self.async_update_listeners()

    async def async_write(self, characteristic_uuid: str, value: int) -> None:
        """Write a single control byte to the Opal."""
        async with self._write_lock:
            if self.client is None or not self.client.is_connected:
                raise RuntimeError("GE Opal is not connected")
            characteristic = self.client.services.get_characteristic(characteristic_uuid)
            if characteristic is None:
                raise RuntimeError(f"Opal characteristic {characteristic_uuid} not found")
            flags = list(characteristic.properties)
            self.characteristic_properties[characteristic_uuid] = flags
            if "write" not in flags and "write-without-response" not in flags:
                raise RuntimeError(
                    f"Opal characteristic {characteristic_uuid} does not advertise write support"
                )
            response = "write" in flags
            await self.client.write_gatt_char(characteristic, bytes([value]), response=response)
            # Refresh readable state after a control write. The make-ice switch
            # state is derived from Make State, not from the write-only control.
            await self._read_available()

    @property
    def make_state(self) -> int | None:
        """Return the numeric ice-making state."""
        return self.raw_values.get("make_state")

    @property
    def make_state_name(self) -> str:
        """Return a human-readable ice-making state."""
        state = self.make_state
        return MAKE_STATE_NAMES.get(state, "Unknown") if state is not None else "Unknown"

    @property
    def ice_bin_name(self) -> str:
        """Return a human-readable ice-bin state."""
        state = self.raw_values.get("ice_bin_state")
        return ICE_BIN_NAMES.get(state, "Unknown") if state is not None else "Unknown"
