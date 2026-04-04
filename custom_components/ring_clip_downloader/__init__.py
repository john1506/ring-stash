"""
Ring Clip Downloader — Home Assistant custom integration.

Automatically downloads Ring doorbell clips to local storage using the
existing Ring integration's auth token. Never stores or exposes credentials.
Provides:
  - Sensor entities (last clip, clips today)
  - REST API endpoint for the frontend panel clip listing
  - Sidebar panel (ring-clip-viewer web component)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.frontend import async_register_built_in_panel

from .const import (
    CONF_DOWNLOAD_PATH,
    CONF_POLL_INTERVAL,
    CONF_RETENTION_DAYS,
    CONF_RING_ENTRY_ID,
    DATA_COORDINATOR,
    DEFAULT_DOWNLOAD_PATH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import RingClipCoordinator

_LOGGER = logging.getLogger(__name__)

FRONTEND_URL = "/ring_clip_downloader_panel"
FRONTEND_JS = Path(__file__).parent / "frontend" / "ring-clip-viewer.js"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ring Clip Downloader from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Resolve the Ring config entry referenced at setup time
    ring_entry_id = entry.data.get(CONF_RING_ENTRY_ID)
    ring_entry = hass.config_entries.async_get_entry(ring_entry_id)
    if ring_entry is None:
        _LOGGER.error(
            "Ring integration entry %s not found — re-configure Ring Clip Downloader",
            ring_entry_id,
        )
        return False

    # Merge options (options flow values override initial data)
    config = {**entry.data, **entry.options}
    download_path = config.get(CONF_DOWNLOAD_PATH, DEFAULT_DOWNLOAD_PATH)
    retention_days = int(config.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
    poll_interval = int(config.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))

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

    # Register the REST API endpoint for the frontend panel
    hass.http.register_view(RingClipListView(download_path))

    # Register the sidebar panel
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Ring Clips",
        sidebar_icon="mdi:doorbell-video",
        frontend_url_path="ring-clips",
        config={
            "_panel_custom": {
                "name": "ring-clip-viewer",
                "js_url": "/ring_clip_downloader_frontend/ring-clip-viewer.js",
                "embed_iframe": False,
                "trust_external": False,
            }
        },
        require_admin=False,
    )

    # Serve the frontend JS
    hass.http.register_static_path(
        "/ring_clip_downloader_frontend",
        str(FRONTEND_JS.parent),
        cache_headers=True,
    )

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Ring Clip Downloader config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


# ── REST API view ─────────────────────────────────────────────────────────────

class RingClipListView(HomeAssistantView):
    """
    GET /api/ring_clip_downloader/clips

    Returns a JSON list of clip metadata for the frontend panel.
    Requires HA authentication (HomeAssistantView enforces this by default).
    File paths are never included in the response — only filenames served
    via the /media endpoint.
    """

    url = "/api/ring_clip_downloader/clips"
    name = "api:ring_clip_downloader:clips"
    requires_auth = True  # HA enforces Bearer token validation automatically

    def __init__(self, download_path: str) -> None:
        self._download_path = Path(download_path)

    async def get(self, request) -> list:
        from aiohttp.web import Response
        import json

        hass = request.app["hass"]
        clips = await hass.async_add_executor_job(self._scan_clips)
        return Response(
            body=json.dumps(clips),
            content_type="application/json",
        )

    def _scan_clips(self) -> list[dict]:
        """
        Read clip metadata from the storage directory.
        Only filename, size, and timestamps derived from the filename are
        returned — no full filesystem paths, no tokens, no account data.
        """
        clips = []
        if not self._download_path.exists():
            return clips

        for f in sorted(self._download_path.glob("*.mp4"), reverse=True):
            try:
                stat = f.stat()
            except OSError:
                continue

            name = f.stem  # e.g. front_door_2026-04-04_14-01-25_Doorbell
            parts = name.rsplit("_", 1)
            kind = parts[-1] if len(parts) > 1 else "Unknown"
            # Parse date from filename — safe, no secrets
            date_parts = name.split("_")
            recorded_at = ""
            try:
                # Format: {doorbell}_{YYYY-MM-DD}_{HH-MM-SS}_{Kind}
                idx = next(
                    i for i, p in enumerate(date_parts) if len(p) == 10 and p[4] == "-"
                )
                date_str = date_parts[idx]
                time_str = date_parts[idx + 1].replace("-", ":")
                recorded_at = f"{date_str}T{time_str}+00:00"
                doorbell = " ".join(date_parts[:idx]).replace("_", " ").title()
            except (StopIteration, IndexError):
                doorbell = name
                recorded_at = ""

            clips.append(
                {
                    "filename": f.name,
                    "doorbell": doorbell,
                    "kind": kind,
                    "recorded_at": recorded_at,
                    "size_kb": round(stat.st_size / 1024, 1),
                }
            )

        return clips
