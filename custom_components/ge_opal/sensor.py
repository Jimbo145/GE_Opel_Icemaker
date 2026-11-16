"""Sensors for GE Opal Ice Maker."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import OpalCoordinator
from .entity import OpalEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up Opal sensors."""
    coordinator: OpalCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            OpalMakeStateCodeSensor(coordinator),
            OpalMakeStateSensor(coordinator),
            OpalIceBinSensor(coordinator),
            OpalCleaningPhaseSensor(coordinator),
        ]
    )


class OpalMakeStateCodeSensor(OpalEntity, SensorEntity):
    """Raw numeric ice-making state."""

    _attr_native_unit_of_measurement = None
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Make State Code"
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "make_state_code")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.make_state


class OpalMakeStateSensor(OpalEntity, SensorEntity):
    """Human-readable ice-making state."""

    _attr_name = "Make State"
    _attr_icon = "mdi:ice-pop"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "make_state")

    @property
    def native_value(self) -> str:
        return self.coordinator.make_state_name


class OpalIceBinSensor(OpalEntity, SensorEntity):
    """Ice bin in/out state."""

    _attr_name = "Ice Bin State"
    _attr_icon = "mdi:ice-cream"

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "ice_bin_state")

    @property
    def native_value(self) -> str:
        return self.coordinator.ice_bin_name


class OpalCleaningPhaseSensor(OpalEntity, SensorEntity):
    """Raw cleaning phase; semantics have not yet been reverse engineered."""

    _attr_name = "Cleaning Phase"
    _attr_icon = "mdi:spray-bottle"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OpalCoordinator) -> None:
        super().__init__(coordinator, "cleaning_phase")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("cleaning_phase")
