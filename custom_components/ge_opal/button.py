"""Buttons for GE Opal Ice Maker."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import OpalCoordinator
from .entity import OpalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up Opal Bluetooth connection buttons."""
    coordinator: OpalCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            OpalUnpairButton(coordinator),
            OpalRepairButton(coordinator),
        ]
    )


class _OpalConnectionButton(OpalEntity, ButtonEntity):
    """Base for buttons that stay available while Bluetooth is disconnected."""

    @property
    def available(self) -> bool:
        return True


class OpalUnpairButton(_OpalConnectionButton):
    """Remove the Bluetooth bond so another host can pair with the Opal."""

    _attr_name = "Unpair"
    _attr_icon = "mdi:link-off"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "unpair")

    async def async_press(self) -> None:
        await self.coordinator.async_unpair()


class OpalRepairButton(_OpalConnectionButton):
    """Unpair, then pair again with this Home Assistant host."""

    _attr_name = "Re-pair"
    _attr_icon = "mdi:link-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "repair")

    async def async_press(self) -> None:
        await self.coordinator.async_repair()
