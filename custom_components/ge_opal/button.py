"""Buttons for GE Opal Ice Maker."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import OpalCoordinator
from .entity import OpalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up Opal Bluetooth connection buttons."""
    coordinator: OpalCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [OpalDisconnectButton(coordinator), OpalReconnectButton(coordinator)]
    )


class _OpalConnectionButton(OpalEntity, ButtonEntity):
    """Base for buttons that stay available while Bluetooth is disconnected."""

    @property
    def available(self) -> bool:
        return True


class OpalDisconnectButton(_OpalConnectionButton):
    """Disconnect Bluetooth and stop auto-reconnecting."""

    _attr_name = "Disconnect Bluetooth"
    _attr_icon = "mdi:bluetooth-off"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "disconnect_bluetooth")

    async def async_press(self) -> None:
        await self.coordinator.async_disconnect_bluetooth()


class OpalReconnectButton(_OpalConnectionButton):
    """Resume Bluetooth connection."""

    _attr_name = "Reconnect Bluetooth"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "reconnect_bluetooth")

    async def async_press(self) -> None:
        await self.coordinator.async_reconnect_bluetooth()
