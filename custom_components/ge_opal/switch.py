"""Switches for GE Opal Ice Maker."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import (
    CHAR_MAKE_ICE_CONTROL,
    CHAR_NIGHT_MODE,
    CHAR_SCHEDULE_ENABLE,
    DATA_COORDINATOR,
    DOMAIN,
)
from .coordinator import OpalCoordinator
from .entity import OpalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up Opal switches."""
    coordinator: OpalCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            OpalNightModeSwitch(coordinator),
            OpalMakeIceSwitch(coordinator),
            OpalScheduleSwitch(coordinator),
        ]
    )


class OpalScheduleSwitch(OpalEntity, SwitchEntity):
    """Enable or disable the Opal's weekly on/off schedule."""

    _attr_name = "Schedule Enabled"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "schedule_enabled")

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get("schedule_enabled")
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write(CHAR_SCHEDULE_ENABLE, 1)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write(CHAR_SCHEDULE_ENABLE, 0)


class OpalNightModeSwitch(OpalEntity, SwitchEntity):
    """Night mode control and state."""

    _attr_name = "Night Mode"
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "night_mode")

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get("night_mode")
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write(CHAR_NIGHT_MODE, 1)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write(CHAR_NIGHT_MODE, 0)


class OpalMakeIceSwitch(OpalEntity, SwitchEntity):
    """Ice production control; state follows the reported Make State value."""

    _attr_name = "Make Ice"
    _attr_icon = "mdi:ice-pop"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "make_ice")

    @property
    def is_on(self) -> bool | None:
        state = self.coordinator.make_state
        return state == 1 if state is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write(CHAR_MAKE_ICE_CONTROL, 1)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write(CHAR_MAKE_ICE_CONTROL, 0)
