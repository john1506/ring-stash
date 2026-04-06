"""Sensor entities for Ring Stash."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation
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

    known_doorbells: set[str] = set()
    entities = _build_global_entities(coordinator, entry)
    for doorbell_id, data in coordinator.data.items():
        entities.extend(_build_doorbell_entities(coordinator, entry, doorbell_id, data.name))
        known_doorbells.add(doorbell_id)
    async_add_entities(entities)

    def _async_add_new_doorbells() -> None:
        if not coordinator.data:
            return

        new_entities: list[SensorEntity] = []
        for doorbell_id, data in coordinator.data.items():
            if doorbell_id in known_doorbells:
                continue
            known_doorbells.add(doorbell_id)
            new_entities.extend(
                _build_doorbell_entities(coordinator, entry, doorbell_id, data.name)
            )

        if new_entities:
            _LOGGER.info(
                "Adding Ring Stash sensors for %d newly discovered doorbell(s)",
                len(new_entities) // 9,
            )
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_doorbells))


def _build_doorbell_entities(
    coordinator: RingClipCoordinator,
    entry: ConfigEntry,
    doorbell_id: str,
    doorbell_name: str,
) -> list[SensorEntity]:
    """Build the full sensor set for one doorbell."""
    return [
        RingLastClipSensor(coordinator, entry, doorbell_id, doorbell_name),
        RingClipsTodaySensor(coordinator, entry, doorbell_id, doorbell_name),
        RingClipsThisWeekSensor(coordinator, entry, doorbell_id, doorbell_name),
        RingClipsThisMonthSensor(coordinator, entry, doorbell_id, doorbell_name),
        RingTotalClipsSensor(coordinator, entry, doorbell_id, doorbell_name),
        RingMotionClipsSensor(coordinator, entry, doorbell_id, doorbell_name),
        RingDoorbellClipsSensor(coordinator, entry, doorbell_id, doorbell_name),
        RingLiveClipsSensor(coordinator, entry, doorbell_id, doorbell_name),
        RingStorageSensor(coordinator, entry, doorbell_id, doorbell_name),
    ]


def _build_global_entities(
    coordinator: RingClipCoordinator,
    entry: ConfigEntry,
) -> list[SensorEntity]:
    """Build the global sensor set shared across all doorbells."""
    return [
        RingGlobalTotalClipsSensor(coordinator, entry),
        RingGlobalStorageSensor(coordinator, entry),
        RingGlobalClipsTodaySensor(coordinator, entry),
        RingGlobalClipsThisWeekSensor(coordinator, entry),
        RingGlobalClipsThisMonthSensor(coordinator, entry),
        RingOldestClipSensor(coordinator, entry),
        RingPendingDownloadsSensor(coordinator, entry),
        RingLockedClipsSensor(coordinator, entry),
        RingFreeSpaceSensor(coordinator, entry),
    ]


# ── Shared base ───────────────────────────────────────────────────────────────

_RING_STASH_DEVICE = {
    "manufacturer": "Ring",
    "model": "Ring Stash",
    "entry_type": "service",
}


class _RingClipBase(CoordinatorEntity[RingClipCoordinator], SensorEntity):
    """Base class for per-doorbell Ring Stash sensors."""

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
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Ring Stash",
            **_RING_STASH_DEVICE,
        }


class _RingGlobalBase(CoordinatorEntity[RingClipCoordinator], SensorEntity):
    """Base class for global (cross-doorbell) Ring Stash sensors."""

    def __init__(self, coordinator: RingClipCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "Ring Stash",
            **_RING_STASH_DEVICE,
        }


# ── Per-doorbell sensors ──────────────────────────────────────────────────────

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
    """Number of clips recorded today for this doorbell."""

    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

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


class RingClipsThisWeekSensor(_RingClipBase):
    """Clips recorded in the last 7 days for this doorbell."""

    _attr_icon = "mdi:calendar-week"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_clips_this_week"
        self._attr_name = f"{doorbell_name} Clips This Week"

    @property
    def native_value(self) -> int:
        data = self._doorbell_data
        return data.clips_this_week if data else 0


class RingClipsThisMonthSensor(_RingClipBase):
    """Clips recorded in the last 30 days for this doorbell."""

    _attr_icon = "mdi:calendar-month"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_clips_this_month"
        self._attr_name = f"{doorbell_name} Clips This Month"

    @property
    def native_value(self) -> int:
        data = self._doorbell_data
        return data.clips_this_month if data else 0


class RingTotalClipsSensor(_RingClipBase):
    """Total clips stored on disk for this doorbell, with a per-kind breakdown."""

    _attr_icon = "mdi:file-video-outline"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_total_clips"
        self._attr_name = f"{doorbell_name} Total Clips"

    @property
    def native_value(self) -> int:
        data = self._doorbell_data
        return data.clips_total if data else 0

    @property
    def extra_state_attributes(self) -> dict:
        data = self._doorbell_data
        if not data:
            return {}
        return {
            "motion": data.clips_motion,
            "doorbell": data.clips_doorbell,
            "live": data.clips_live,
        }


class RingMotionClipsSensor(_RingClipBase):
    """Total motion-triggered clips stored for this doorbell."""

    _attr_icon = "mdi:motion-sensor"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_motion_clips"
        self._attr_name = f"{doorbell_name} Motion Clips"

    @property
    def native_value(self) -> int:
        data = self._doorbell_data
        return data.clips_motion if data else 0


class RingDoorbellClipsSensor(_RingClipBase):
    """Total doorbell-ring clips stored for this doorbell."""

    _attr_icon = "mdi:doorbell"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_doorbell_clips"
        self._attr_name = f"{doorbell_name} Doorbell Clips"

    @property
    def native_value(self) -> int:
        data = self._doorbell_data
        return data.clips_doorbell if data else 0


class RingLiveClipsSensor(_RingClipBase):
    """Total live-view recordings stored for this doorbell."""

    _attr_icon = "mdi:cctv"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_live_clips"
        self._attr_name = f"{doorbell_name} Live Clips"

    @property
    def native_value(self) -> int:
        data = self._doorbell_data
        return data.clips_live if data else 0


class RingStorageSensor(_RingClipBase):
    """Total disk space used by this doorbell's clips."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:harddisk"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry, doorbell_id, doorbell_name) -> None:
        super().__init__(coordinator, entry, doorbell_id, doorbell_name)
        self._attr_unique_id = f"{DOMAIN}_{doorbell_id}_storage_mb"
        self._attr_name = f"{doorbell_name} Storage Used"

    @property
    def native_value(self) -> float:
        data = self._doorbell_data
        if not data:
            return 0.0
        return round(data.storage_bytes / (1024 * 1024), 2)


# ── Global sensors ────────────────────────────────────────────────────────────

class RingGlobalTotalClipsSensor(_RingGlobalBase):
    """Total clips stored on disk across all doorbells."""

    _attr_icon = "mdi:video-multiple"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_global_total_clips"
        self._attr_name = "Ring Stash Total Clips"

    @property
    def native_value(self) -> int:
        if not self.coordinator.data:
            return 0
        return sum(d.clips_total for d in self.coordinator.data.values())

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        motion = sum(d.clips_motion for d in self.coordinator.data.values())
        doorbell = sum(d.clips_doorbell for d in self.coordinator.data.values())
        live = sum(d.clips_live for d in self.coordinator.data.values())
        return {"motion": motion, "doorbell": doorbell, "live": live}


class RingGlobalStorageSensor(_RingGlobalBase):
    """Total disk space used by all Ring Stash clips across all doorbells."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:harddisk"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_global_storage_mb"
        self._attr_name = "Ring Stash Total Storage"

    @property
    def native_value(self) -> float:
        if not self.coordinator.data:
            return 0.0
        total_bytes = sum(d.storage_bytes for d in self.coordinator.data.values())
        return round(total_bytes / (1024 * 1024), 2)

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {
            cam: f"{round(d.storage_bytes / (1024 * 1024), 1)} MB"
            for cam, d in self.coordinator.data.items()
        }


class RingGlobalClipsTodaySensor(_RingGlobalBase):
    """Total clips recorded today across all doorbells."""

    _attr_icon = "mdi:calendar-today"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_global_clips_today"
        self._attr_name = "Ring Stash Clips Today"

    @property
    def native_value(self) -> int:
        if not self.coordinator.data:
            return 0
        return sum(d.clips_today for d in self.coordinator.data.values())

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {
            d.name: d.clips_today
            for d in self.coordinator.data.values()
        }


class RingGlobalClipsThisWeekSensor(_RingGlobalBase):
    """Total clips recorded in the last 7 days across all doorbells."""

    _attr_icon = "mdi:calendar-week"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_global_clips_this_week"
        self._attr_name = "Ring Stash Clips This Week"

    @property
    def native_value(self) -> int:
        if not self.coordinator.data:
            return 0
        return sum(d.clips_this_week for d in self.coordinator.data.values())

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {d.name: d.clips_this_week for d in self.coordinator.data.values()}


class RingGlobalClipsThisMonthSensor(_RingGlobalBase):
    """Total clips recorded in the last 30 days across all doorbells."""

    _attr_icon = "mdi:calendar-month"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_global_clips_this_month"
        self._attr_name = "Ring Stash Clips This Month"

    @property
    def native_value(self) -> int:
        if not self.coordinator.data:
            return 0
        return sum(d.clips_this_month for d in self.coordinator.data.values())

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        return {d.name: d.clips_this_month for d in self.coordinator.data.values()}


class RingOldestClipSensor(_RingGlobalBase):
    """Timestamp of the oldest clip in the archive."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_oldest_clip"
        self._attr_name = "Ring Stash Oldest Clip"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.oldest_clip_date()


class RingPendingDownloadsSensor(_RingGlobalBase):
    """Clips queued waiting for their download URL to become ready."""

    _attr_icon = "mdi:cloud-download-outline"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_pending_downloads"
        self._attr_name = "Ring Stash Pending Downloads"

    @property
    def native_value(self) -> int:
        return self.coordinator.pending_count


class RingLockedClipsSensor(_RingGlobalBase):
    """Number of clips locked from automatic retention cleanup."""

    _attr_icon = "mdi:lock-outline"
    _attr_native_unit_of_measurement = "clips"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_locked_clips"
        self._attr_name = "Ring Stash Locked Clips"

    @property
    def native_value(self) -> int:
        return self.coordinator.locked_count


class RingFreeSpaceSensor(_RingGlobalBase):
    """Free disk space on the media partition where clips are stored."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:harddisk"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_free_space_gb"
        self._attr_name = "Ring Stash Free Space"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.free_space_bytes / (1024 ** 3), 2)
