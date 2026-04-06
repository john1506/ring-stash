"""Diagnostics support for Ring Stash."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from time import monotonic
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DOWNLOAD_PATH, DATA_COORDINATOR, DOMAIN

TO_REDACT = {CONF_DOWNLOAD_PATH}


def _coordinator_from_hass(hass: HomeAssistant, entry: ConfigEntry):
    """Return the active coordinator for a config entry, if loaded."""
    return hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(DATA_COORDINATOR)


def _summarize_downloaded(downloaded: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Summarize stored clip metadata without exposing per-clip details."""
    by_doorbell = Counter()
    by_kind = Counter()
    missing_recorded_at = 0
    missing_filename = 0

    for meta in downloaded.values():
        doorbell_id = meta.get("doorbell_id")
        if doorbell_id:
            by_doorbell[str(doorbell_id)] += 1

        kind = meta.get("kind")
        if kind:
            by_kind[str(kind)] += 1

        if not meta.get("recorded_at"):
            missing_recorded_at += 1

        if not meta.get("filename"):
            missing_filename += 1

    return {
        "count": len(downloaded),
        "by_doorbell_id": dict(sorted(by_doorbell.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "missing_recorded_at": missing_recorded_at,
        "missing_filename": missing_filename,
    }


def _scan_archive(download_path: Path, downloaded: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compare the on-disk archive against the persistent store."""
    disk_files = set()
    if download_path.exists():
        disk_files = {path.name for path in download_path.glob("*.mp4")}

    store_files = {
        str(filename)
        for meta in downloaded.values()
        if (filename := meta.get("filename"))
    }

    return {
        "download_path_exists": download_path.exists(),
        "disk_mp4_count": len(disk_files),
        "store_file_count": len(store_files),
        "disk_only_count": len(disk_files - store_files),
        "store_only_count": len(store_files - disk_files),
    }


def _summarize_pending(pending: dict[str, Any]) -> dict[str, Any]:
    """Summarize pending retry state."""
    by_doorbell = Counter()
    by_kind = Counter()
    oldest_age_seconds = 0
    now = monotonic()

    for item in pending.values():
        by_doorbell[str(getattr(item, "doorbell_id", ""))] += 1
        by_kind[str(getattr(item, "kind", ""))] += 1
        queued_at = getattr(item, "queued_at", now)
        oldest_age_seconds = max(oldest_age_seconds, int(now - queued_at))

    return {
        "count": len(pending),
        "by_doorbell_id": dict(sorted((k, v) for k, v in by_doorbell.items() if k)),
        "by_kind": dict(sorted((k, v) for k, v in by_kind.items() if k)),
        "oldest_age_seconds": oldest_age_seconds,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = _coordinator_from_hass(hass, entry)

    if coordinator is None:
        return {
            "entry": {
                "data": async_redact_data(dict(entry.data), TO_REDACT),
                "options": async_redact_data(dict(entry.options), TO_REDACT),
            },
            "loaded": False,
        }

    downloaded = dict(coordinator._store_data.get("downloaded", {}))
    archive = await hass.async_add_executor_job(
        _scan_archive, coordinator._download_path, downloaded
    )
    runtime_data = coordinator.data or {}

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "loaded": True,
        "runtime": {
            "doorbell_ids": sorted(runtime_data.keys()),
            "doorbell_count": len(runtime_data),
            "update_interval_seconds": (
                int(coordinator.update_interval.total_seconds())
                if coordinator.update_interval is not None
                else None
            ),
            "history_scan_complete": sorted(coordinator._history_scan_complete),
            "free_space_bytes": coordinator.free_space_bytes,
        },
        "store": {
            "downloaded": _summarize_downloaded(downloaded),
            "locked_count": len(coordinator._locked_filenames),
            "label_count": len(coordinator._labels),
            "ai_description_count": len(coordinator._ai_descriptions),
        },
        "archive": archive,
        "pending": _summarize_pending(coordinator._pending),
    }
