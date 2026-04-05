"""
Ring Stash — Home Assistant custom integration.

Automatically downloads Ring doorbell clips to local storage using the
existing Ring integration's auth token. Never stores or exposes credentials.
Provides:
  - Sensor entities (last clip, clips today)
  - REST API endpoint for the frontend panel clip listing
  - Sidebar panel (ring-stash-viewer web component)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.components.frontend import async_register_built_in_panel

from .const import (
    CONF_DOWNLOAD_PATH,
    CONF_PANEL_TITLE,
    CONF_POLL_INTERVAL,
    CONF_RETENTION_DAYS,
    CONF_RING_ENTRY_ID,
    DATA_COORDINATOR,
    DEFAULT_DOWNLOAD_PATH,
    DEFAULT_PANEL_TITLE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import RingClipCoordinator

_LOGGER = logging.getLogger(__name__)

FRONTEND_URL = "/ring_stash_panel"
FRONTEND_JS = Path(__file__).parent / "frontend" / "ring-stash-viewer.js"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ring Stash from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Resolve the Ring config entry referenced at setup time
    ring_entry_id = entry.data.get(CONF_RING_ENTRY_ID)
    ring_entry = hass.config_entries.async_get_entry(ring_entry_id)
    if ring_entry is None:
        _LOGGER.error(
            "Ring integration entry %s not found — re-configure Ring Stash",
            ring_entry_id,
        )
        return False

    # Merge options (options flow values override initial data)
    config = {**entry.data, **entry.options}
    download_path = config.get(CONF_DOWNLOAD_PATH, DEFAULT_DOWNLOAD_PATH)
    retention_days = int(config.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
    poll_interval = int(config.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
    panel_title = config.get(CONF_PANEL_TITLE, DEFAULT_PANEL_TITLE)

    coordinator = RingClipCoordinator(
        hass,
        ring_entry=ring_entry,
        download_path=download_path,
        retention_days=retention_days,
        poll_interval=poll_interval,
    )

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
    }

    # Register sensor (and any future) platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the REST API endpoints for the frontend panel
    hass.http.register_view(RingClipListView(download_path, entry.entry_id))
    hass.http.register_view(RingClipFilenamesView(download_path))
    hass.http.register_view(RingClipLockView(entry.entry_id))
    hass.http.register_view(RingClipLabelView(entry.entry_id))

    # Register the sidebar panel (safe to call on every setup — HA updates if already registered)
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=panel_title,
        sidebar_icon="mdi:doorbell-video",
        frontend_url_path="ring-stash",
        config={
            "_panel_custom": {
                "name": "ring-stash-viewer",
                "js_url": "/ring_stash_frontend/ring-stash-viewer.js?v=2.0.0",
                "embed_iframe": False,
                "trust_external": False,
            },
            "panel_title": panel_title,
        },
        require_admin=False,
    )

    # Serve the frontend JS and the downloaded clips (no-auth static paths).
    # aiohttp raises ValueError if the same URL prefix is registered twice, so guard
    # against that when the config entry reloads (e.g. after an options change).
    if not hass.data[DOMAIN].get("_static_registered"):
        await hass.http.async_register_static_paths([
            StaticPathConfig(
                "/ring_stash_frontend",
                str(FRONTEND_JS.parent),
                cache_headers=True,
            ),
            StaticPathConfig(
                "/ring_stash_media",
                download_path,
                cache_headers=False,
            ),
        ])
        hass.data[DOMAIN]["_static_registered"] = True

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Ring Stash config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_coordinator(hass, entry_id: str):
    """Return the RingClipCoordinator for the given entry_id, or None."""
    domain_data = hass.data.get(DOMAIN, {})
    entry_data  = domain_data.get(entry_id, {})
    return entry_data.get(DATA_COORDINATOR)


# ── REST API view ─────────────────────────────────────────────────────────────

class RingClipListView(HomeAssistantView):
    """
    GET /api/ring_stash/clips

    Returns a JSON list of clip metadata for the frontend panel.
    Requires HA authentication (HomeAssistantView enforces this by default).
    File paths are never included in the response — only filenames served
    via the /media endpoint.
    """

    url = "/api/ring_stash/clips"
    name = "api:ring_stash:clips"
    requires_auth = True  # HA enforces Bearer token validation automatically

    def __init__(self, download_path: str, entry_id: str) -> None:
        self._download_path = Path(download_path)
        self._entry_id = entry_id

    async def get(self, request):
        from aiohttp.web import Response
        import json

        q = request.rel_url.query
        try:
            limit  = min(max(int(q.get("limit",  48)), 1), 200)
            offset = max(int(q.get("offset", 0)), 0)
        except (ValueError, TypeError):
            limit, offset = 48, 0
        from_date = q.get("from_date", "")  # YYYY-MM-DD (inclusive)
        to_date   = q.get("to_date",   "")  # YYYY-MM-DD (inclusive)

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass, self._entry_id)
        result = await hass.async_add_executor_job(
            self._scan_clips, coordinator, limit, offset, from_date, to_date
        )
        return Response(body=json.dumps(result), content_type="application/json")

    def _scan_clips(
        self,
        coordinator,
        limit: int,
        offset: int,
        from_date: str,
        to_date: str,
    ) -> dict:
        """
        Scan the clip directory, apply date-range filters, and return one page.

        Returns {"total": N, "clips": [...]} where total is the count of all
        clips matching the date filter (not just the current page), so the
        frontend can decide when all pages have been loaded.

        Only filename-derived metadata is returned — no full paths, no tokens.
        """
        if not self._download_path.exists():
            return {"total": 0, "clips": []}

        # First pass: collect and filter without stat() — stat only the page we need
        matched: list[tuple] = []
        for f in sorted(self._download_path.glob("*.mp4"), reverse=True):
            name       = f.stem
            date_parts = name.split("_")
            file_date = file_time = doorbell = ""
            kind = "Unknown"
            try:
                idx       = next(i for i, p in enumerate(date_parts) if len(p) == 10 and p[4] == "-")
                file_date = date_parts[idx]
                file_time = date_parts[idx + 1].replace("-", ":")
                doorbell  = " ".join(date_parts[:idx]).replace("_", " ").title()
                kind      = date_parts[-1] if len(date_parts) > idx + 2 else "Unknown"
            except (StopIteration, IndexError):
                doorbell = name

            # Date-range filter operates on the YYYY-MM-DD string — fast string compare
            if from_date and file_date and file_date < from_date:
                continue
            if to_date and file_date and file_date > to_date:
                continue

            matched.append((f, file_date, file_time, doorbell, kind))

        total = len(matched)
        page  = matched[offset : offset + limit]

        clips = []
        for f, file_date, file_time, doorbell, kind in page:
            try:
                size_kb = round(f.stat().st_size / 1024, 1)
            except OSError:
                size_kb = 0
            recorded_at = f"{file_date}T{file_time}+00:00" if file_date and file_time else ""
            clips.append({
                "filename":       f.name,
                "doorbell":       doorbell,
                "kind":           kind,
                "recorded_at":    recorded_at,
                "size_kb":        size_kb,
                "locked":         coordinator.is_locked(f.name) if coordinator else False,
                "label":          coordinator.get_label(f.name) if coordinator else "",
                "ai_description": coordinator.get_ai_description(f.name) if coordinator else "",
            })

        return {"total": total, "clips": clips}


class RingClipLockView(HomeAssistantView):
    """
    POST /api/ring_stash/lock
    Body: {"filename": "front_door_2026-04-04_14-01-25_Doorbell.mp4", "locked": true}

    Persists the lock state for a clip in the coordinator's HA Store.
    Locked clips are excluded from retention-based cleanup.
    Only the filename (basename) is accepted — path traversal is rejected.
    """

    url = "/api/ring_stash/lock"
    name = "api:ring_stash:lock"
    requires_auth = True

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def post(self, request):
        from aiohttp.web import Response
        import json

        try:
            body = await request.json()
        except Exception:
            return Response(status=400, text="Invalid JSON")

        filename = body.get("filename", "")
        locked   = bool(body.get("locked", False))

        # Reject anything that isn't a plain filename (no path separators)
        if not filename or "/" in filename or "\\" in filename or filename != Path(filename).name:
            return Response(status=400, text="Invalid filename")

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass, self._entry_id)
        if coordinator is None:
            return Response(status=503, text="Coordinator not available")

        await coordinator.async_set_lock(filename, locked)
        return Response(body=json.dumps({"filename": filename, "locked": locked}), content_type="application/json")


class RingClipFilenamesView(HomeAssistantView):
    """
    GET /api/ring_stash/filenames

    Returns the full list of clip filenames currently on disk.
    Used by the frontend to purge stale IndexedDB thumbnail entries for
    files that have been deleted by the retention policy.

    Only returns filenames (no paths, no metadata, no tokens).
    A plain glob with no stat() calls makes this very cheap even at scale.
    """

    url = "/api/ring_stash/filenames"
    name = "api:ring_stash:filenames"
    requires_auth = True

    def __init__(self, download_path: str) -> None:
        self._download_path = Path(download_path)

    async def get(self, request):
        from aiohttp.web import Response
        import json

        hass      = request.app["hass"]
        filenames = await hass.async_add_executor_job(self._list_filenames)
        return Response(body=json.dumps({"filenames": filenames}), content_type="application/json")

    def _list_filenames(self) -> list[str]:
        if not self._download_path.exists():
            return []
        return [f.name for f in self._download_path.glob("*.mp4")]


class RingClipLabelView(HomeAssistantView):
    """
    POST /api/ring_stash/label
    Body: {"filename": "front_door_2026-04-04_14-01-25_Doorbell.mp4", "label": "Parcel delivery"}

    Stores a user-defined label for a clip in the coordinator's HA Store.
    Send an empty string to remove an existing label.
    Only the filename (basename) is accepted — path traversal is rejected.
    """

    url = "/api/ring_stash/label"
    name = "api:ring_stash:label"
    requires_auth = True

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def post(self, request):
        from aiohttp.web import Response
        import json

        try:
            body = await request.json()
        except Exception:
            return Response(status=400, text="Invalid JSON")

        filename = body.get("filename", "")
        label    = str(body.get("label", "")).strip()

        # Reject anything that isn't a plain filename (no path separators)
        if not filename or "/" in filename or "\\" in filename or filename != Path(filename).name:
            return Response(status=400, text="Invalid filename")

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass, self._entry_id)
        if coordinator is None:
            return Response(status=503, text="Coordinator not available")

        await coordinator.async_set_label(filename, label)
        return Response(
            body=json.dumps({"filename": filename, "label": label}),
            content_type="application/json",
        )
