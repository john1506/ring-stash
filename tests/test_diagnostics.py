"""Unit tests for Ring Stash diagnostics."""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import timedelta
from pathlib import Path


def _stub(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# ---------------------------------------------------------------------------
# Minimal stubs so diagnostics.py can be imported without Home Assistant
# ---------------------------------------------------------------------------

_stub("homeassistant")
ha_core = _stub("homeassistant.core")
ha_core.HomeAssistant = object

ha_config_entries = _stub("homeassistant.config_entries")
ha_config_entries.ConfigEntry = object

ha_components = _stub("homeassistant.components")
ha_diagnostics = _stub("homeassistant.components.diagnostics")


def async_redact_data(data, to_redact):
    redacted = dict(data)
    for key in to_redact:
        if key in redacted:
            redacted[key] = "**REDACTED**"
    return redacted


ha_diagnostics.async_redact_data = async_redact_data

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.modules["custom_components"] = types.ModuleType("custom_components")
sys.modules["custom_components"].__path__ = [str(_ROOT / "custom_components")]
sys.modules["custom_components.ring_stash"] = types.ModuleType("custom_components.ring_stash")
sys.modules["custom_components.ring_stash"].__path__ = [
    str(_ROOT / "custom_components" / "ring_stash")
]
ring_const = types.ModuleType("custom_components.ring_stash.const")
ring_const.CONF_DOWNLOAD_PATH = "download_path"
ring_const.DATA_COORDINATOR = "coordinator"
ring_const.DOMAIN = "ring_stash"
sys.modules["custom_components.ring_stash.const"] = ring_const

from custom_components.ring_stash.diagnostics import async_get_config_entry_diagnostics  # noqa: E402


class _DummyPending:
    def __init__(self, doorbell_id: str, kind: str, queued_at: float) -> None:
        self.doorbell_id = doorbell_id
        self.kind = kind
        self.queued_at = queued_at


class _DummyCoordinator:
    def __init__(self, download_path: Path) -> None:
        self._download_path = download_path
        self._store_data = {
            "downloaded": {
                "ding-1": {
                    "filename": "front_door_1.mp4",
                    "doorbell_id": "front",
                    "kind": "motion",
                    "recorded_at": "2026-04-05T12:00:00+00:00",
                    "downloaded_at": "2026-04-06T12:00:00+00:00",
                },
                "ding-2": {
                    "filename": "missing_from_disk.mp4",
                    "doorbell_id": "front",
                    "kind": "ding",
                    "downloaded_at": "2026-04-06T12:05:00+00:00",
                },
            }
        }
        self._pending = {"pending-1": _DummyPending("front", "motion", 10.0)}
        self._locked_filenames = {"front_door_1.mp4"}
        self._labels = {"front_door_1.mp4": "Parcel"}
        self._ai_descriptions = {"front_door_1.mp4": "A person is at the door."}
        self._history_scan_complete = {"front"}
        self.data = {"front": object()}
        self.update_interval = timedelta(seconds=20)
        self.free_space_bytes = 123456


class _DummyHass:
    def __init__(self, coordinator: _DummyCoordinator | None) -> None:
        self.data = {
            "ring_stash": {
                "entry-1": {"coordinator": coordinator} if coordinator else {}
            }
        }

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _DummyEntry:
    def __init__(self) -> None:
        self.entry_id = "entry-1"
        self.data = {"download_path": "/media/ring_clips"}
        self.options = {"download_path": "/media/ring_archive"}


class TestDiagnostics(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil as _shutil

        _shutil.rmtree(self._tmpdir, ignore_errors=True)

    async def test_loaded_diagnostics_summarize_store_and_archive(self):
        (self._tmpdir / "front_door_1.mp4").write_bytes(b"x" * 64)
        (self._tmpdir / "disk_only.mp4").write_bytes(b"x" * 32)
        coordinator = _DummyCoordinator(self._tmpdir)
        hass = _DummyHass(coordinator)
        entry = _DummyEntry()

        result = await async_get_config_entry_diagnostics(hass, entry)

        self.assertTrue(result["loaded"])
        self.assertEqual(result["entry"]["data"]["download_path"], "**REDACTED**")
        self.assertEqual(result["entry"]["options"]["download_path"], "**REDACTED**")
        self.assertEqual(result["store"]["downloaded"]["count"], 2)
        self.assertEqual(result["store"]["downloaded"]["missing_recorded_at"], 1)
        self.assertEqual(result["archive"]["disk_mp4_count"], 2)
        self.assertEqual(result["archive"]["store_only_count"], 1)
        self.assertEqual(result["archive"]["disk_only_count"], 1)
        self.assertEqual(result["pending"]["count"], 1)
        self.assertEqual(result["runtime"]["doorbell_count"], 1)
        self.assertEqual(result["runtime"]["update_interval_seconds"], 20)

    async def test_unloaded_entry_returns_minimal_diagnostics(self):
        hass = _DummyHass(None)
        entry = _DummyEntry()

        result = await async_get_config_entry_diagnostics(hass, entry)

        self.assertFalse(result["loaded"])
        self.assertEqual(result["entry"]["data"]["download_path"], "**REDACTED**")


if __name__ == "__main__":
    unittest.main()
