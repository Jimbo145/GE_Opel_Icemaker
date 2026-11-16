"""Base entity for GE Opal."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OpalCoordinator


class OpalEntity(CoordinatorEntity[OpalCoordinator]):
    """Base class for Opal entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OpalCoordinator, description: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{description}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="GE Opal Ice Maker",
            manufacturer="GE Appliances",
            model="Opal Ice Maker",
        )

    @property
    def available(self) -> bool:
        """Return availability based on the live BLE connection."""
        return self.coordinator.connected and super().available
