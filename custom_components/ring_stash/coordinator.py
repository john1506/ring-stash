"""
DataUpdateCoordinator for Ring Stash.

Responsibilities:
- Periodic polling for new clips across all configured doorbells
- Retry pending clips (URL not yet available) on a shorter interval
- Atomic download with duplicate prevention via persistent Store
- Retention-based cleanup of old clips
- Reporting structured data to sensor entities

Token handling is fully delegated to TokenManager (api.py).
No credential values appear in logs, state, or attributes.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RingApiClient, RingAuthError, RingApiError, TokenManager
from .const import (
    CLIP_RETRY_INTERVAL_S,
    CLIP_RETRY_MAX_S,
    DEFAULT_DOWNLOAD_PATH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_KIND_LABEL = {"ding": "Doorbell", "motion": "Motion", "on_demand": "Live"}
_SAFE_RE = re.compile(r"[^a-z0-9_\-]")
_HISTORY_PAGE_LIMIT = 100


@dataclass
class ClipInfo:
    """Public data about one downloaded clip — safe to surface in entity attributes."""
    ding_id: str
    doorbell_id: str
    doorbell_name: str
    kind: str
    recorded_at: datetime
    filename: str
    size_bytes: int


@dataclass
class DoorbellData:
    """Coordinator data snapshot for one doorbell."""
    name: str
    last_clip: ClipInfo | None = None
    clips_today: int = 0
    clips_this_week: int = 0
    clips_this_month: int = 0
    clips_total: int = 0
    clips_motion: int = 0
    clips_doorbell: int = 0
    clips_live: int = 0
    storage_bytes: int = 0


@dataclass
class _PendingClip:
    """A clip whose URL wasn't ready yet — queued for retry."""
    ding_id: str
    doorbell_id: str
    doorbell_name: str
    kind: str
    recorded_at: datetime
    ai_description: str = ""
    queued_at: float = field(default_factory=time.monotonic)

    def is_expired(self) -> bool:
        return time.monotonic() - self.queued_at > CLIP_RETRY_MAX_S


class RingClipCoordinator(DataUpdateCoordinator[dict[str, DoorbellData]]):
    """Coordinator that fetches Ring event history and downloads new clips."""

    def __init__(
        self,
        hass: HomeAssistant,
        ring_entry,
        download_path: str = DEFAULT_DOWNLOAD_PATH,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._tokens = TokenManager(hass, ring_entry)
        self._api = RingApiClient(async_get_clientsession(hass), self._tokens)
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._download_path = Path(download_path)
        self._retention_days = retention_days
        self._normal_interval = timedelta(minutes=poll_interval)
        self._retry_interval = timedelta(seconds=CLIP_RETRY_INTERVAL_S)

        # Clips awaiting URL readiness — keyed by ding_id
        self._pending: dict[str, _PendingClip] = {}
        # Set of ding_ids already downloaded (loaded from Store on first run)
        self._downloaded_ids: set[str] = set()
        # Set of filenames the user has locked (preserved from retention cleanup)
        self._locked_filenames: set[str] = set()
        # User-defined labels: filename → label string
        self._labels: dict[str, str] = {}
        # Ring AI descriptions captured at download time: filename → description
        self._ai_descriptions: dict[str, str] = {}
        # Doorbell IDs that have completed one full paged history scan.
        self._history_scan_complete: set[str] = set()
        self._store_data: dict = {}
        self._store_loaded = False
        # Ensures the one-time background full-history scan is only scheduled once.
        self._full_scan_scheduled = False
        # Global stats updated each cycle
        self._free_space_bytes: int = 0

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=self._normal_interval,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _async_load_store(self) -> None:
        if self._store_loaded:
            return
        raw = await self._store.async_load() or {}
        self._store_data = raw
        self._downloaded_ids = set(raw.get("downloaded", {}).keys())
        self._locked_filenames = set(raw.get("locked", []))
        self._labels = dict(raw.get("labels", {}))
        self._history_scan_complete = {str(v) for v in raw.get("history_scan_complete", [])}
        # Build AI description index from downloaded metadata (captured at download time)
        self._ai_descriptions = {
            m["filename"]: m["ai_description"]
            for m in raw.get("downloaded", {}).values()
            if m.get("filename") and m.get("ai_description")
        }
        self._store_loaded = True

    async def _async_save_store(self) -> None:
        self._store_data["locked"] = list(self._locked_filenames)
        self._store_data["labels"] = self._labels
        self._store_data["history_scan_complete"] = sorted(self._history_scan_complete)
        await self._store.async_save(self._store_data)

    # ── Lock helpers ──────────────────────────────────────────────────────────

    def is_locked(self, filename: str) -> bool:
        """Return True if the given filename is locked (protected from retention cleanup)."""
        return filename in self._locked_filenames

    async def async_set_lock(self, filename: str, locked: bool) -> None:
        """Lock or unlock a clip filename, persisting the state to the Store."""
        if locked:
            self._locked_filenames.add(filename)
        else:
            self._locked_filenames.discard(filename)
        await self._async_save_store()

    # ── Label helpers ─────────────────────────────────────────────────────────

    def get_label(self, filename: str) -> str:
        """Return the user-defined label for a clip, or empty string."""
        return self._labels.get(filename, "")

    async def async_set_label(self, filename: str, label: str) -> None:
        """Set or clear a user-defined label for a clip, persisting to the Store."""
        if label:
            self._labels[filename] = label
        else:
            self._labels.pop(filename, None)
        await self._async_save_store()

    # ── AI description helpers ────────────────────────────────────────────────

    def get_ai_description(self, filename: str) -> str:
        """Return the Ring AI-generated description captured at download time."""
        return self._ai_descriptions.get(filename, "")

    # ── Global stat properties (used by global sensor entities) ──────────────

    @property
    def pending_count(self) -> int:
        """Number of clips queued waiting for their download URL to become ready."""
        return len(self._pending)

    @property
    def locked_count(self) -> int:
        """Number of clips locked from automatic retention cleanup."""
        return len(self._locked_filenames)

    @property
    def free_space_bytes(self) -> int:
        """Free bytes on the media partition (updated each coordinator cycle)."""
        return self._free_space_bytes

    @staticmethod
    def _parse_stored_datetime(meta: dict, *keys: str) -> datetime | None:
        """Return the first valid timestamp found under the given metadata keys."""
        for key in keys:
            raw = meta.get(key)
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(raw)
            except (TypeError, ValueError):
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return None

    def _stored_recorded_at(self, meta: dict) -> datetime | None:
        """Return the clip event time, falling back for older store entries."""
        return self._parse_stored_datetime(meta, "recorded_at", "downloaded_at")

    def oldest_clip_date(self) -> datetime | None:
        """Timestamp of the oldest recorded clip still stored on disk."""
        oldest: datetime | None = None
        for meta in self._store_data.get("downloaded", {}).values():
            dt = self._stored_recorded_at(meta)
            if dt is not None and (oldest is None or dt < oldest):
                oldest = dt
        return oldest

    def _clip_filename(self, doorbell_name: str, recorded_at: datetime, kind: str) -> str:
        safe_name = _SAFE_RE.sub("_", doorbell_name.lower())
        ts = recorded_at.strftime("%Y-%m-%d_%H-%M-%S")
        label = _KIND_LABEL.get(kind, kind)
        return f"{safe_name}_{ts}_{label}.mp4"

    async def _async_ensure_download_dir(self) -> None:
        await self.hass.async_add_executor_job(self._download_path.mkdir, 0o750, True, True)

    async def _async_download_clip(self, pending: _PendingClip) -> ClipInfo | None:
        """Fetch clip URL and download. Returns ClipInfo on success, None if not ready."""
        try:
            url = await self._api.async_get_clip_url(pending.ding_id)
        except RingApiError as exc:
            _LOGGER.warning("Could not fetch clip URL for %s: %s", pending.ding_id, exc)
            return None

        if not url:
            return None  # Ring still processing — caller will retry

        filename = self._clip_filename(pending.doorbell_name, pending.recorded_at, pending.kind)
        dest = self._download_path / filename

        # Skip if somehow already on disk (e.g. from the legacy script)
        exists = await self.hass.async_add_executor_job(dest.exists)
        if exists:
            _LOGGER.debug("Clip already on disk: %s", filename)
        else:
            try:
                # URL is intentionally not logged — it is a time-limited signed credential
                size = await self._api.async_download_clip(url, dest, self.hass)
                _LOGGER.info(
                    "Downloaded %s clip from %s: %s (%d KB)",
                    _KIND_LABEL.get(pending.kind, pending.kind),
                    pending.doorbell_name,
                    filename,
                    size // 1024,
                )
            except Exception as exc:
                _LOGGER.warning("Failed to download clip %s: %s", filename, exc)
                return None

        size_bytes = await self.hass.async_add_executor_job(
            lambda: dest.stat().st_size if dest.exists() else 0
        )

        return ClipInfo(
            ding_id=pending.ding_id,
            doorbell_id=pending.doorbell_id,
            doorbell_name=pending.doorbell_name,
            kind=pending.kind,
            recorded_at=pending.recorded_at,
            filename=filename,
            size_bytes=size_bytes,
        )

    async def _async_process_history(
        self, doorbell_id: str, doorbell_name: str, history: list[dict], *, full_scan: bool = False
    ) -> list[ClipInfo]:
        """Attempt to download any new clips from a history batch."""
        results: list[ClipInfo] = []

        for event in history:
            ding_id = str(event.get("id", ""))
            if not ding_id or ding_id in self._downloaded_ids:
                continue

            if not _event_has_recoverable_clip(event, ready_only=full_scan):
                continue  # No subscription coverage or event not recorded

            kind = event.get("kind", "unknown")
            created_at_raw = event.get("created_at", "")
            try:
                recorded_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                recorded_at = datetime.now(timezone.utc)

            ai_description = _extract_ai_description(event)

            pending = _PendingClip(
                ding_id=ding_id,
                doorbell_id=doorbell_id,
                doorbell_name=doorbell_name,
                kind=kind,
                recorded_at=recorded_at,
                ai_description=ai_description,
            )

            clip = await self._async_download_clip(pending)
            if clip:
                results.append(clip)
                self._downloaded_ids.add(ding_id)
                self._store_data.setdefault("downloaded", {})[ding_id] = {
                    "filename": clip.filename,
                    "doorbell_id": doorbell_id,
                    "doorbell_name": doorbell_name,
                    "kind": clip.kind,
                    "recorded_at": clip.recorded_at.isoformat(),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "ai_description": ai_description,
                }
                if ai_description:
                    self._ai_descriptions[clip.filename] = ai_description
                self._pending.pop(ding_id, None)
            else:
                # Queue for retry if URL wasn't ready yet
                if ding_id not in self._pending:
                    _LOGGER.debug("Clip %s not ready yet, queuing for retry", ding_id)
                    self._pending[ding_id] = pending
                elif ai_description and not self._pending[ding_id].ai_description:
                    self._pending[ding_id].ai_description = ai_description

        return results

    async def _async_recover_history(
        self,
        doorbell_id: str,
        doorbell_name: str,
        *,
        full_scan: bool = False,
    ) -> None:
        """Fetch Ring history and process new downloadable clips.

        When ``full_scan=False`` (default, used during normal update cycles),
        only the most recent page is fetched — fast enough to stay within HA's
        60-second platform-setup window.

        When ``full_scan=True`` (used by the one-time background task), pages
        through the entire Ring history until the doorbell is marked complete in
        ``_history_scan_complete``.
        """
        older_than: str | None = None
        scan_needed = doorbell_id not in self._history_scan_complete

        while True:
            history = await self._api.async_get_history(
                doorbell_id,
                limit=_HISTORY_PAGE_LIMIT,
                older_than=older_than,
            )
            if not history:
                if scan_needed:
                    self._history_scan_complete.add(doorbell_id)
                return

            page_has_recoverable_unknown = any(
                (ding_id := str(event.get("id", "")))
                and ding_id not in self._downloaded_ids
                and _event_has_recoverable_clip(event)
                for event in history
            )

            await self._async_process_history(doorbell_id, doorbell_name, history, full_scan=full_scan)

            if len(history) < _HISTORY_PAGE_LIMIT:
                if scan_needed:
                    self._history_scan_complete.add(doorbell_id)
                return

            next_cursor = str(history[-1].get("id", ""))
            if not next_cursor or next_cursor == older_than:
                return

            # Fast mode: stop after the first page regardless.
            if not full_scan:
                return

            # Full-scan mode: keep going only while there is more work to do.
            if not scan_needed and not page_has_recoverable_unknown:
                return

            older_than = next_cursor

    async def _async_full_history_scan(self) -> None:
        """Background task: page through all Ring history for unscanned doorbells.

        Runs once after the first successful coordinator update so it does not
        block HA startup.  Errors are logged but never raise — the next HA
        restart will retry any doorbells that weren't marked complete.
        """
        _LOGGER.info("Ring Stash: starting background history scan")
        try:
            doorbells = await self._api.async_get_doorbells()
        except (RingApiError, RingAuthError) as exc:
            _LOGGER.warning("Ring Stash: background history scan could not get doorbells: %s", exc)
            return

        for doorbell in doorbells:
            db_id = str(doorbell.get("id", ""))
            if db_id in self._history_scan_complete:
                continue
            db_name = doorbell.get("description") or doorbell.get("name") or db_id
            if isinstance(db_name, dict):
                db_name = db_name.get("name", db_id)
            try:
                await self._async_recover_history(db_id, db_name, full_scan=True)
            except (RingApiError, RingAuthError) as exc:
                _LOGGER.warning("Ring Stash: background scan failed for %s: %s", db_name, exc)

        await self._async_save_store()
        _LOGGER.info("Ring Stash: background history scan complete")

    async def _async_retry_pending(self) -> list[ClipInfo]:
        """Retry any clips whose URL wasn't ready in a previous cycle."""
        results: list[ClipInfo] = []
        expired = [k for k, p in self._pending.items() if p.is_expired()]
        for k in expired:
            _LOGGER.debug("Giving up on clip %s after %ds", k, CLIP_RETRY_MAX_S)
            self._pending.pop(k)

        for ding_id, pending in list(self._pending.items()):
            clip = await self._async_download_clip(pending)
            if clip:
                results.append(clip)
                self._downloaded_ids.add(ding_id)
                self._store_data.setdefault("downloaded", {})[ding_id] = {
                    "filename": clip.filename,
                    "doorbell_id": pending.doorbell_id,
                    "doorbell_name": pending.doorbell_name,
                    "kind": pending.kind,
                    "recorded_at": pending.recorded_at.isoformat(),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "ai_description": pending.ai_description,
                }
                if pending.ai_description:
                    self._ai_descriptions[clip.filename] = pending.ai_description
                self._pending.pop(ding_id)

        return results

    async def _async_cleanup_old_clips(self) -> None:
        """Delete clips older than retention_days and remove them from the store."""
        if self._retention_days <= 0:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        downloaded: dict = self._store_data.get("downloaded", {})
        to_remove = []

        for ding_id, meta in list(downloaded.items()):
            try:
                downloaded_at = datetime.fromisoformat(meta["downloaded_at"])
            except (KeyError, ValueError):
                continue
            if downloaded_at < cutoff:
                filename = meta.get("filename", "")
                if filename:
                    # Skip files the user has explicitly locked (preserved from cleanup)
                    if self.is_locked(filename):
                        continue
                    path = self._download_path / filename
                    await self.hass.async_add_executor_job(_unlink_if_exists_path, path)
                to_remove.append(ding_id)

        for k in to_remove:
            downloaded.pop(k, None)
            self._downloaded_ids.discard(k)

        if to_remove:
            _LOGGER.info("Cleaned up %d clip(s) past %d-day retention", len(to_remove), self._retention_days)

    def _count_clips_today(self, doorbell_id: str) -> int:
        today = datetime.now(timezone.utc).date()
        count = 0
        for meta in self._store_data.get("downloaded", {}).values():
            if meta.get("doorbell_id") != doorbell_id:
                continue
            dt = self._stored_recorded_at(meta)
            if dt is not None and dt.date() == today:
                count += 1
        return count

    def _count_clips_since(self, doorbell_id: str, cutoff: datetime) -> int:
        """Count clips for a doorbell recorded on or after ``cutoff``."""
        count = 0
        for meta in self._store_data.get("downloaded", {}).values():
            if meta.get("doorbell_id") != doorbell_id:
                continue
            dt = self._stored_recorded_at(meta)
            if dt is not None and dt >= cutoff:
                count += 1
        return count

    async def _async_last_clip_for(self, doorbell_id: str, doorbell_name: str) -> ClipInfo | None:
        downloaded = self._store_data.get("downloaded", {})
        all_files: list[dict] = [
            m for m in downloaded.values() if m.get("doorbell_id") == doorbell_id
        ]
        if not all_files:
            return None
        latest = max(
            all_files,
            key=lambda m: self._stored_recorded_at(m) or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        if not latest:
            return None
        filename = latest.get("filename", "")
        if not filename:
            return None
        path = self._download_path / filename
        size = await self.hass.async_add_executor_job(_stat_size, path)
        kind = latest.get("kind", "")
        if not kind:
            parts = filename.rsplit("_", 1)
            kind_label = parts[-1].replace(".mp4", "") if len(parts) > 1 else "unknown"
            kind = {v: k for k, v in _KIND_LABEL.items()}.get(kind_label, kind_label)
        dt = self._stored_recorded_at(latest) or datetime.now(timezone.utc)
        ding_id = next(
            (k for k, v in downloaded.items() if v is latest), ""
        )
        return ClipInfo(
            ding_id=ding_id,
            doorbell_id=doorbell_id,
            doorbell_name=latest.get("doorbell_name", doorbell_name),
            kind=kind,
            recorded_at=dt,
            filename=filename,
            size_bytes=size,
        )

    # ── DataUpdateCoordinator entrypoint ─────────────────────────────────────

    async def _async_update_data(self) -> dict[str, DoorbellData]:
        await self._async_load_store()
        await self._async_ensure_download_dir()

        try:
            doorbells = await self._api.async_get_doorbells()
        except RingAuthError as exc:
            raise UpdateFailed(f"Ring authentication error: {exc}") from exc
        except RingApiError as exc:
            raise UpdateFailed(f"Ring API error: {exc}") from exc

        if not doorbells:
            raise UpdateFailed("No Ring doorbells found on this account")

        # Retry clips from previous cycles whose URL wasn't ready
        await self._async_retry_pending()

        result: dict[str, DoorbellData] = {}

        for doorbell in doorbells:
            db_id = str(doorbell.get("id", ""))
            db_name = doorbell.get("description") or doorbell.get("name") or db_id
            if isinstance(db_name, dict):
                db_name = db_name.get("name", db_id)

            try:
                await self._async_recover_history(db_id, db_name)  # fast mode: 1 page
            except (RingApiError, RingAuthError) as exc:
                _LOGGER.warning("Failed to fetch history for %s: %s", db_name, exc)

            now = datetime.now(timezone.utc)
            week_cutoff  = now - timedelta(days=7)
            month_cutoff = now - timedelta(days=30)
            result[db_id] = DoorbellData(
                name=db_name,
                last_clip=await self._async_last_clip_for(db_id, db_name),
                clips_today=self._count_clips_today(db_id),
                clips_this_week=self._count_clips_since(db_id, week_cutoff),
                clips_this_month=self._count_clips_since(db_id, month_cutoff),
                clips_total=sum(
                    1 for m in self._store_data.get("downloaded", {}).values()
                    if m.get("doorbell_id") == db_id
                ),
            )

        # Populate per-doorbell storage + kind breakdowns via a single dir scan.
        # Also capture free space on the media partition.
        storage, free_bytes = await self.hass.async_add_executor_job(
            self._scan_storage_and_free
        )
        self._free_space_bytes = free_bytes
        for db_id, stats in storage.items():
            if db_id in result:
                result[db_id].storage_bytes  = stats["bytes"]
                result[db_id].clips_motion   = stats["motion"]
                result[db_id].clips_doorbell = stats["doorbell"]
                result[db_id].clips_live     = stats["live"]

        # Schedule the one-time background full-history scan (first update only).
        if not self._full_scan_scheduled and any(
            str(d.get("id", "")) not in self._history_scan_complete for d in doorbells
        ):
            self._full_scan_scheduled = True
            self.hass.async_create_task(self._async_full_history_scan())

        # Cleanup old clips once per cycle
        await self._async_cleanup_old_clips()
        await self._async_save_store()

        # Shorten poll interval while clips are pending to catch URLs sooner
        self.update_interval = (
            self._retry_interval if self._pending else self._normal_interval
        )

        return result


    def _scan_storage_and_free(self) -> tuple[dict[str, dict], int]:
        """Scan the download directory for per-doorbell stats and free space.

        Runs in a thread-pool executor — no async operations allowed here.
        Returns (per_doorbell_stats, free_bytes) where per_doorbell_stats is
        keyed by doorbell_id: {"bytes": int, "motion": int, "doorbell": int, "live": int}
        """
        downloaded = self._store_data.get("downloaded", {})
        file_to_db: dict[str, str] = {
            m["filename"]: m["doorbell_id"]
            for m in downloaded.values()
            if m.get("filename") and m.get("doorbell_id")
        }
        stats: dict[str, dict] = {}
        try:
            for path in self._download_path.iterdir():
                if path.suffix != ".mp4":
                    continue
                db_id = file_to_db.get(path.name)
                if not db_id:
                    continue
                if db_id not in stats:
                    stats[db_id] = {"bytes": 0, "motion": 0, "doorbell": 0, "live": 0}
                try:
                    stats[db_id]["bytes"] += path.stat().st_size
                except OSError:
                    pass
                stem = path.stem.lower()
                if stem.endswith("_motion"):
                    stats[db_id]["motion"] += 1
                elif stem.endswith("_doorbell"):
                    stats[db_id]["doorbell"] += 1
                elif stem.endswith("_live"):
                    stats[db_id]["live"] += 1
        except OSError:
            pass

        try:
            free_bytes = shutil.disk_usage(self._download_path).free
        except OSError:
            free_bytes = 0

        return stats, free_bytes


def _unlink_if_exists_path(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _stat_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _event_has_recoverable_clip(event: dict, *, ready_only: bool = False) -> bool:
    """Return True when the Ring event can still yield a downloadable clip.

    When ``ready_only=True`` (used during full history scans), only "ready"
    clips are considered recoverable — historical "uploading"/"inprogress"
    events are permanently stuck and would just pollute the pending queue.
    """
    recording = event.get("recording") or {}
    status = recording.get("status")
    if ready_only:
        return status == "ready"
    return status in ("ready", "uploading", "inprogress")


def _extract_ai_description(event: dict) -> str:
    """
    Build a human-readable description from Ring's AI/CV event fields.

    Ring Protect plans populate different fields depending on the subscription
    tier and firmware.  We try them in order from most to least specific:
      1. ``description`` — newer plans provide a natural-language summary
      2. ``detection_type`` — array of detected object labels (e.g. ["person"])
      3. ``cv_properties`` — legacy boolean flags per object class
    Returns an empty string when none of these fields are present.
    """
    # 1. Natural-language description (Ring AI on higher-tier plans)
    desc = event.get("description", "")
    if desc and isinstance(desc, str):
        return desc.strip()

    # 2. Structured detection_type array  (e.g. ["person", "package"])
    detections = event.get("detection_type") or []
    if detections and isinstance(detections, list):
        labels = [str(d).replace("_", " ").title() for d in detections if d]
        if labels:
            return ", ".join(labels)

    # 3. Legacy cv_properties boolean map
    cv = event.get("cv_properties") or {}
    if isinstance(cv, dict):
        detected = []
        if cv.get("personDetected"):
            detected.append("Person")
        if cv.get("vehicleDetected"):
            detected.append("Vehicle")
        if cv.get("packageDetected"):
            detected.append("Package")
        if cv.get("motionStarted") or cv.get("otherMotion"):
            detected.append("Motion")
        if detected:
            return ", ".join(dict.fromkeys(detected))  # deduplicate, preserve order

    return ""
