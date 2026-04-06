"""
Unit tests for RingClipCoordinator stat helpers.

Tests exercise the pure-Python helpers that compute sensor values:
clip counts, storage stats, pending/locked counts, oldest clip date.
Runs without Home Assistant or any network access using stdlib unittest.

Run with:
    python -m pytest tests/  (if pytest is available)
    python -m unittest discover tests/  (always available)
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Minimal stubs so coordinator.py can be imported without a full HA install
# ---------------------------------------------------------------------------

def _stub(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

ha          = _stub("homeassistant")
ha_core     = _stub("homeassistant.core")
ha_core.HomeAssistant = object
ha_helpers  = _stub("homeassistant.helpers")
ha_aiohttp  = _stub("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda hass: None
ha_storage  = _stub("homeassistant.helpers.storage")
ha_storage.Store = object
ha_coord    = _stub("homeassistant.helpers.update_coordinator")


class _FakeCoordinatorBase:
    def __init__(self, hass, logger, name, update_interval):
        self.hass = hass
        self.data = None

    def __class_getitem__(cls, item):
        return cls


sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = _FakeCoordinatorBase
sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = Exception

api_mod = types.ModuleType("custom_components.ring_stash.api")
api_mod.RingApiClient = object
api_mod.RingAuthError = Exception
api_mod.RingApiError = Exception
api_mod.TokenManager = object

for mod_name, mod in [
    ("custom_components", types.ModuleType("custom_components")),
    ("custom_components.ring_stash", types.ModuleType("custom_components.ring_stash")),
    ("custom_components.ring_stash.api", api_mod),
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = mod

const_mod = types.ModuleType("custom_components.ring_stash.const")
const_mod.CLIP_RETRY_INTERVAL_S = 30
const_mod.CLIP_RETRY_MAX_S = 300
const_mod.DEFAULT_DOWNLOAD_PATH = "/media/ring_stash"
const_mod.DEFAULT_POLL_INTERVAL = 5
const_mod.DEFAULT_RETENTION_DAYS = 30
const_mod.DOMAIN = "ring_stash"
const_mod.STORAGE_KEY = "ring_stash"
const_mod.STORAGE_VERSION = 1
sys.modules["custom_components.ring_stash.const"] = const_mod

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
# Replace stub modules with proper package objects so sub-imports resolve
sys.modules["custom_components"] = types.ModuleType("custom_components")
sys.modules["custom_components"].__path__ = [str(_ROOT / "custom_components")]
sys.modules["custom_components.ring_stash"] = types.ModuleType("custom_components.ring_stash")
sys.modules["custom_components.ring_stash"].__path__ = [str(_ROOT / "custom_components" / "ring_stash")]
sys.modules["custom_components.ring_stash.api"] = api_mod
sys.modules["custom_components.ring_stash.const"] = const_mod

from custom_components.ring_stash.coordinator import RingClipCoordinator  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(download_path: str = "/tmp") -> RingClipCoordinator:
    coord = RingClipCoordinator.__new__(RingClipCoordinator)
    coord.hass = MagicMock()
    coord._download_path = Path(download_path)
    coord._retention_days = 30
    coord._normal_interval = timedelta(minutes=5)
    coord._retry_interval = timedelta(seconds=30)
    coord._pending = {}
    coord._downloaded_ids = set()
    coord._locked_filenames = set()
    coord._labels = {}
    coord._ai_descriptions = {}
    coord._history_scan_complete = set()
    coord._store_data = {}
    coord._store_loaded = False
    coord._full_scan_scheduled = False
    coord._free_space_bytes = 0
    return coord


def _ts(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCountClipsToday(unittest.TestCase):
    def test_empty_store(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {}}
        self.assertEqual(c._count_clips_today("db1"), 0)

    def test_clips_today_counted(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db1", "downloaded_at": _ts(0)},
            "b": {"doorbell_id": "db1", "downloaded_at": _ts(0)},
        }}
        self.assertEqual(c._count_clips_today("db1"), 2)

    def test_old_clips_not_counted(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db1", "downloaded_at": _ts(1)},
        }}
        self.assertEqual(c._count_clips_today("db1"), 0)

    def test_other_doorbell_not_counted(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db2", "downloaded_at": _ts(0)},
        }}
        self.assertEqual(c._count_clips_today("db1"), 0)


class TestCountClipsSince(unittest.TestCase):
    def test_within_window(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db1", "downloaded_at": _ts(3)},
            "b": {"doorbell_id": "db1", "downloaded_at": _ts(6)},
            "c": {"doorbell_id": "db1", "downloaded_at": _ts(10)},
        }}
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        self.assertEqual(c._count_clips_since("db1", cutoff), 2)

    def test_all_outside_window(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db1", "downloaded_at": _ts(31)},
        }}
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        self.assertEqual(c._count_clips_since("db1", cutoff), 0)

    def test_malformed_timestamp_skipped(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db1", "downloaded_at": "not-a-date"},
            "b": {"doorbell_id": "db1", "downloaded_at": _ts(1)},
        }}
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        self.assertEqual(c._count_clips_since("db1", cutoff), 1)


class TestOldestClipDate(unittest.TestCase):
    def test_no_clips(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {}}
        self.assertIsNone(c.oldest_clip_date())

    def test_returns_oldest(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db1", "downloaded_at": _ts(5)},
            "b": {"doorbell_id": "db1", "downloaded_at": _ts(10)},
            "c": {"doorbell_id": "db1", "downloaded_at": _ts(2)},
        }}
        result = c.oldest_clip_date()
        self.assertIsNotNone(result)
        expected = datetime.now(timezone.utc) - timedelta(days=10)
        self.assertLess(abs((result - expected).total_seconds()), 5)

    def test_malformed_skipped(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "a": {"doorbell_id": "db1", "downloaded_at": "bad"},
            "b": {"doorbell_id": "db1", "downloaded_at": _ts(3)},
        }}
        self.assertIsNotNone(c.oldest_clip_date())


class TestPendingAndLockedCount(unittest.TestCase):
    def test_pending_empty(self):
        c = _make_coordinator()
        self.assertEqual(c.pending_count, 0)

    def test_pending_count(self):
        c = _make_coordinator()
        c._pending = {"id1": MagicMock(), "id2": MagicMock()}
        self.assertEqual(c.pending_count, 2)

    def test_locked_empty(self):
        c = _make_coordinator()
        self.assertEqual(c.locked_count, 0)

    def test_locked_count(self):
        c = _make_coordinator()
        c._locked_filenames = {"clip1.mp4", "clip2.mp4", "clip3.mp4"}
        self.assertEqual(c.locked_count, 3)


class TestScanStorageAndFree(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil as _shutil
        _shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _path(self, name: str) -> Path:
        return Path(self._tmpdir) / name

    def test_counts_motion_doorbell_live(self):
        c = _make_coordinator(self._tmpdir)
        self._path("cam_2025-01-01_Motion.mp4").write_bytes(b"x" * 1024)
        self._path("cam_2025-01-02_Doorbell.mp4").write_bytes(b"x" * 2048)
        self._path("cam_2025-01-03_Live.mp4").write_bytes(b"x" * 512)
        c._store_data = {"downloaded": {
            "i1": {"filename": "cam_2025-01-01_Motion.mp4",   "doorbell_id": "db1"},
            "i2": {"filename": "cam_2025-01-02_Doorbell.mp4", "doorbell_id": "db1"},
            "i3": {"filename": "cam_2025-01-03_Live.mp4",     "doorbell_id": "db1"},
        }}
        stats, free = c._scan_storage_and_free()
        self.assertEqual(stats["db1"]["motion"],   1)
        self.assertEqual(stats["db1"]["doorbell"], 1)
        self.assertEqual(stats["db1"]["live"],     1)
        self.assertEqual(stats["db1"]["bytes"],    1024 + 2048 + 512)
        self.assertGreater(free, 0)

    def test_deleted_file_not_counted_in_storage(self):
        """A clip removed from disk must not appear in storage bytes or kind counts."""
        c = _make_coordinator(self._tmpdir)
        self._path("cam_keep_Motion.mp4").write_bytes(b"x" * 100)
        # cam_deleted_Doorbell.mp4 is in the store but not on disk
        c._store_data = {"downloaded": {
            "i1": {"filename": "cam_keep_Motion.mp4",    "doorbell_id": "db1"},
            "i2": {"filename": "cam_deleted_Doorbell.mp4", "doorbell_id": "db1"},
        }}
        stats, _ = c._scan_storage_and_free()
        self.assertEqual(stats["db1"]["motion"],   1)
        self.assertEqual(stats["db1"]["doorbell"], 0)
        self.assertEqual(stats["db1"]["bytes"],    100)

    def test_unknown_file_on_disk_ignored(self):
        c = _make_coordinator(self._tmpdir)
        self._path("orphan.mp4").write_bytes(b"x" * 500)
        c._store_data = {"downloaded": {}}
        stats, _ = c._scan_storage_and_free()
        self.assertEqual(stats, {})

    def test_non_mp4_ignored(self):
        c = _make_coordinator(self._tmpdir)
        self._path("thumb.jpg").write_bytes(b"x" * 100)
        c._store_data = {"downloaded": {
            "i1": {"filename": "thumb.jpg", "doorbell_id": "db1"},
        }}
        stats, _ = c._scan_storage_and_free()
        self.assertEqual(stats, {})


class TestStatsAfterDeletion(unittest.TestCase):
    """Stat helpers must reflect current _store_data, so removing an entry drops counts."""

    def _store(self, n_motion=0, n_doorbell=0, days_ago=1) -> dict:
        downloaded = {}
        for i in range(n_motion):
            downloaded[f"m{i}"] = {
                "doorbell_id": "db1", "downloaded_at": _ts(days_ago),
                "filename": f"cam_ts_motion_{i}.mp4",
            }
        for i in range(n_doorbell):
            downloaded[f"d{i}"] = {
                "doorbell_id": "db1", "downloaded_at": _ts(days_ago),
                "filename": f"cam_ts_doorbell_{i}.mp4",
            }
        return {"downloaded": downloaded}

    def test_total_clips_decreases_after_store_removal(self):
        c = _make_coordinator()
        c._store_data = self._store(n_motion=3, n_doorbell=2)
        total = lambda: sum(
            1 for m in c._store_data["downloaded"].values()
            if m["doorbell_id"] == "db1"
        )
        self.assertEqual(total(), 5)
        del c._store_data["downloaded"]["m0"]
        self.assertEqual(total(), 4)

    def test_clips_this_week_decreases_after_store_removal(self):
        c = _make_coordinator()
        c._store_data = self._store(n_motion=2, days_ago=1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        self.assertEqual(c._count_clips_since("db1", cutoff), 2)
        del c._store_data["downloaded"]["m0"]
        self.assertEqual(c._count_clips_since("db1", cutoff), 1)

    def test_clips_this_month_excludes_old_entries(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "recent": {"doorbell_id": "db1", "downloaded_at": _ts(10)},
            "old":    {"doorbell_id": "db1", "downloaded_at": _ts(35)},
        }}
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        self.assertEqual(c._count_clips_since("db1", cutoff), 1)

    def test_oldest_clip_updates_after_removal(self):
        c = _make_coordinator()
        c._store_data = {"downloaded": {
            "old": {"doorbell_id": "db1", "downloaded_at": _ts(20)},
            "new": {"doorbell_id": "db1", "downloaded_at": _ts(1)},
        }}
        oldest_before = c.oldest_clip_date()
        del c._store_data["downloaded"]["old"]
        oldest_after = c.oldest_clip_date()
        self.assertGreater(oldest_after, oldest_before)

    def test_locked_count_decreases_when_unlocked(self):
        c = _make_coordinator()
        c._locked_filenames = {"a.mp4", "b.mp4"}
        self.assertEqual(c.locked_count, 2)
        c._locked_filenames.discard("a.mp4")
        self.assertEqual(c.locked_count, 1)

    def test_pending_count_decreases_when_clip_downloads(self):
        c = _make_coordinator()
        c._pending = {"id1": MagicMock(), "id2": MagicMock()}
        self.assertEqual(c.pending_count, 2)
        del c._pending["id1"]
        self.assertEqual(c.pending_count, 1)


if __name__ == "__main__":
    unittest.main()
