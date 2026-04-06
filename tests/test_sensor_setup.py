"""Unit tests for dynamic Ring Stash sensor setup."""
from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path


def _stub(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# ---------------------------------------------------------------------------
# Minimal stubs so sensor.py can be imported without Home Assistant installed
# ---------------------------------------------------------------------------

_stub("homeassistant")
ha_core = _stub("homeassistant.core")
ha_core.HomeAssistant = object
ha_core.callback = lambda func: func

sensor_mod = _stub("homeassistant.components.sensor")


class _SensorEntity:
    def __class_getitem__(cls, item):
        return cls


class _SensorDeviceClass:
    TIMESTAMP = "timestamp"
    DATA_SIZE = "data_size"


class _SensorStateClass:
    TOTAL_INCREASING = "total_increasing"
    MEASUREMENT = "measurement"
    TOTAL = "total"


sensor_mod.SensorEntity = _SensorEntity
sensor_mod.SensorDeviceClass = _SensorDeviceClass
sensor_mod.SensorStateClass = _SensorStateClass

config_entries = _stub("homeassistant.config_entries")
config_entries.ConfigEntry = object

const_mod = _stub("homeassistant.const")
const_mod.UnitOfInformation = types.SimpleNamespace(MEGABYTES="MB", GIGABYTES="GB")

entity_platform = _stub("homeassistant.helpers.entity_platform")
entity_platform.AddEntitiesCallback = object

ha_helpers = _stub("homeassistant.helpers")
ha_coord = _stub("homeassistant.helpers.update_coordinator")


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls


ha_coord.CoordinatorEntity = _CoordinatorEntity

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.modules["custom_components"] = types.ModuleType("custom_components")
sys.modules["custom_components"].__path__ = [str(_ROOT / "custom_components")]
sys.modules["custom_components.ring_stash"] = types.ModuleType("custom_components.ring_stash")
sys.modules["custom_components.ring_stash"].__path__ = [
    str(_ROOT / "custom_components" / "ring_stash")
]

ring_const = types.ModuleType("custom_components.ring_stash.const")
ring_const.DATA_COORDINATOR = "coordinator"
ring_const.DOMAIN = "ring_stash"
sys.modules["custom_components.ring_stash.const"] = ring_const

ring_coord = types.ModuleType("custom_components.ring_stash.coordinator")


@dataclass
class DoorbellData:
    name: str
    last_clip: object | None = None
    clips_today: int = 0
    clips_this_week: int = 0
    clips_this_month: int = 0
    clips_total: int = 0
    clips_motion: int = 0
    clips_doorbell: int = 0
    clips_live: int = 0
    storage_bytes: int = 0


class RingClipCoordinator:
    pass


ring_coord.DoorbellData = DoorbellData
ring_coord.RingClipCoordinator = RingClipCoordinator
sys.modules["custom_components.ring_stash.coordinator"] = ring_coord

from custom_components.ring_stash.sensor import async_setup_entry  # noqa: E402


class _DummyCoordinator:
    def __init__(self, data: dict[str, DoorbellData]) -> None:
        self.data = data
        self._listeners: list = []

    async def async_config_entry_first_refresh(self) -> None:
        return None

    def async_add_listener(self, listener):
        self._listeners.append(listener)

        def _remove_listener():
            self._listeners.remove(listener)

        return _remove_listener

    def fire_update(self) -> None:
        for listener in list(self._listeners):
            listener()


class _DummyEntry:
    def __init__(self) -> None:
        self.entry_id = "entry-1"
        self.unload_callbacks: list = []

    def async_on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)


class _DummyHass:
    def __init__(self, coordinator: _DummyCoordinator) -> None:
        self.data = {
            "ring_stash": {
                "entry-1": {
                    "coordinator": coordinator,
                }
            }
        }


class TestSensorSetup(unittest.IsolatedAsyncioTestCase):
    async def test_new_doorbells_get_entities_after_refresh(self):
        coordinator = _DummyCoordinator({"front": DoorbellData(name="Front Door")})
        hass = _DummyHass(coordinator)
        entry = _DummyEntry()
        batches: list[list] = []

        await async_setup_entry(hass, entry, lambda entities: batches.append(list(entities)))

        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 18)

        coordinator.data["back"] = DoorbellData(name="Back Door")
        coordinator.fire_update()

        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[1]), 9)
        self.assertIn("ring_stash_back_last_clip", {e._attr_unique_id for e in batches[1]})


if __name__ == "__main__":
    unittest.main()
