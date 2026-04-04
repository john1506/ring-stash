"""Sensor entities for Ring Clip Downloader."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import DoorbellData, RingClipCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RingClipCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    # Wait for first data load so we know which doorbells exist
    await coordinator.async_config_entry_first_refresh()

    entities: list[SensorEntity] = []
    for doorbell_id, data in coordinator.data.items():
        entities.append(RingLastClipSensor(coordinator, entry, doorbell_id, data.name))
        entities.append(RingClipsTodaySensor(coordinator, entry, doorbell_id, data.name))

    async_add_entities(entities)


class _RingClipBase(CoordinatorEntity[RingClipCoordinator], SensorEntity):
    """Base class for Ring Clip sensors."""

    def __init__(
        self,
        coordinator: RingClipCoordinator,
        entry: ConfigEntry,
        doorbell_id: str,
        doorbell_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._doorbell_id = doorbell_id
        self._doorbell_name = doorbell_name
        self._entry = entry

    @property
    def _doorbell_data(self) -> DoorbellData | None:
        if self.coordinator.data:
            return self.coordinator.data.get(self._doorbell_id)
        return None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._doorbell_id)},
            "name": self._doorbell_name,
            "manufacturer": "Ring",
            "model": "Doorbell",
            "via_device": (DOMAIN, self._entry.entry_id),
        }


class RingLastClipSensor(_RingClipBase):
    """Timestamp and metadata of the most recently downloaded clip."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:doorbell-video"

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_last_clip"
        self._attr_name = f"{doorbell_name} Last Clip"

    @property
    def native_value(self) -> datetime | None:
        data = self._doorbell_data
        if data and data.last_clip:
            return data.last_clip.recorded_at
        return None

    @property
    def extra_state_attributes(self) -> dict:
        data = self._doorbell_data
        if not data or not data.last_clip:
            return {}
        clip = data.last_clip
        return {
            "kind": clip.kind,
            "filename": clip.filename,
            "size_kb": round(clip.size_bytes / 1024, 1) if clip.size_bytes else None,
            "doorbell": clip.doorbell_name,
        }


class RingClipsTodaySensor(_RingClipBase):
    """Number of clips downloaded today for this doorbell."""

    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = "total"

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_clips_today"
        self._attr_name = f"{doorbell_name} Clips Today"

    @property
    def native_value(self) -> int:
        data = self._doorbell_data
        return data.clips_today if data else 0

    @property
    def extra_state_attributes(self) -> dict:
        data = self._doorbell_data
        if not data:
            return {}
        return {"total_clips": data.clips_total}
