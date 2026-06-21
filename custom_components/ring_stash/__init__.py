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

import json
import logging
from pathlib import Path

from aiohttp.web_exceptions import HTTPForbidden
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.http import (
    KEY_HASS_USER,
    HomeAssistantView,
    StaticPathConfig,
)
from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)

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

FRONTEND_JS = Path(__file__).parent / "frontend" / "ring-stash-viewer.js"
FRONTEND_VERSION = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)["version"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ring Stash from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})

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

    domain_data[entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
    }
    domain_data["_active_entry_id"] = entry.entry_id
    domain_data["_download_path"] = Path(download_path)

    # Register sensor (and any future) platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the REST API endpoints for the frontend panel once; the views
    # resolve the current config dynamically from hass.data on each request.
    if not domain_data.get("_views_registered"):
        hass.http.register_view(RingClipListView())
        hass.http.register_view(RingClipFilenamesView())
        hass.http.register_view(RingClipLockView())
        hass.http.register_view(RingClipLabelView())
        hass.http.register_view(RingClipMediaView())
        hass.http.register_view(RingClipDeleteView())
        hass.http.register_view(RingClipRestoreView())
        hass.http.register_view(RingDeletedClipsView())
        domain_data["_views_registered"] = True

    # Update the sidebar panel config on every reload so title and frontend
    # metadata follow the latest options.
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=panel_title,
        sidebar_icon="mdi:doorbell-video",
        frontend_url_path="ring-stash",
        config={
            "_panel_custom": {
                "name": "ring-stash-viewer",
                "js_url": f"/ring_stash_frontend/ring-stash-viewer.js?v={FRONTEND_VERSION}",
                "embed_iframe": False,
                "trust_external": False,
            },
            "panel_title": panel_title,
        },
        require_admin=True,
        update=True,
    )

    # Serve the frontend JS once. Clip media is served by RingClipMediaView so
    # the active download path can change without requiring a full restart.
    if not domain_data.get("_frontend_static_registered"):
        await hass.http.async_register_static_paths([
            StaticPathConfig(
                "/ring_stash_frontend",
                str(FRONTEND_JS.parent),
                cache_headers=True,
            ),
        ])
        domain_data["_frontend_static_registered"] = True

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Ring Stash config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)

        remaining_entry_ids = [
            key for key, value in domain_data.items()
            if not key.startswith("_") and isinstance(value, dict)
        ]
        if domain_data.get("_active_entry_id") == entry.entry_id:
            next_entry_id = remaining_entry_ids[0] if remaining_entry_ids else None
            if next_entry_id is None:
                domain_data.pop("_active_entry_id", None)
                domain_data.pop("_download_path", None)
            else:
                domain_data["_active_entry_id"] = next_entry_id
                next_coordinator = domain_data[next_entry_id].get(DATA_COORDINATOR)
                if next_coordinator is not None:
                    domain_data["_download_path"] = next_coordinator._download_path

        if not remaining_entry_ids:
            async_remove_panel(hass, "ring-stash", warn_if_unknown=False)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_domain_data(hass) -> dict:
    """Return the Ring Stash domain data dict."""
    return hass.data.get(DOMAIN, {})


def _get_active_entry_id(hass) -> str | None:
    """Return the active Ring Stash config-entry id, if any."""
    return _get_domain_data(hass).get("_active_entry_id")


def _get_download_path(hass) -> Path:
    """Return the current clip download path for the active config entry."""
    return Path(_get_domain_data(hass).get("_download_path", DEFAULT_DOWNLOAD_PATH))


def _get_coordinator(hass, entry_id: str | None = None):
    """Return the RingClipCoordinator for the given or active entry_id."""
    if entry_id is None:
        entry_id = _get_active_entry_id(hass)
    if entry_id is None:
        return None
    domain_data = _get_domain_data(hass)
    entry_data  = domain_data.get(entry_id, {})
    return entry_data.get(DATA_COORDINATOR)


def _is_safe_filename(filename: str) -> bool:
    """Return True if the value is a plain basename with no path separators."""
    return (
        bool(filename)
        and filename not in {".", ".."}
        and "/" not in filename
        and "\\" not in filename
        and filename == Path(filename).name
    )


def _normalize_search_text(value: str) -> str:
    """Normalize clip metadata for case-insensitive text search."""
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _clip_matches_search(search: str, *fields: str) -> bool:
    """Return True when every search term appears somewhere in the clip metadata."""
    terms = _normalize_search_text(search).split()
    if not terms:
        return True

    searchable = " ".join(_normalize_search_text(field) for field in fields if field)
    return all(term in searchable for term in terms)


# ── REST API view ─────────────────────────────────────────────────────────────

class RingClipListView(HomeAssistantView):
    """
    GET /api/ring_stash/clips

    Returns a JSON list of clip metadata for the frontend panel.
    Requires HA authentication (HomeAssistantView enforces this by default).
    File paths are never included in the response — only filenames served
    via the authenticated clip media endpoint.
    """

    url = "/api/ring_stash/clips"
    name = "api:ring_stash:clips"
    requires_auth = True  # HA enforces Bearer token validation automatically

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
        search    = q.get("search",    "").strip()

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass)
        download_path = _get_download_path(hass)
        result = await hass.async_add_executor_job(
            self._scan_clips, download_path, coordinator, limit, offset, from_date, to_date, search
        )
        return Response(body=json.dumps(result), content_type="application/json")

    def _scan_clips(
        self,
        download_path: Path,
        coordinator,
        limit: int,
        offset: int,
        from_date: str,
        to_date: str,
        search: str,
    ) -> dict:
        """
        Scan the clip directory, apply filters, and return one page.

        Returns {"total": N, "clips": [...]} where total is the count of all
        clips matching the active filters (not just the current page), so the
        frontend can decide when all pages have been loaded.

        Only filename-derived metadata is returned — no full paths, no tokens.
        """
        if not download_path.exists():
            return {"total": 0, "clips": []}

        # First pass: collect and filter without stat() — stat only the page we need
        matched: list[tuple] = []
        for f in sorted(download_path.glob("*.mp4"), reverse=True):
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

            label = coordinator.get_label(f.name) if coordinator else ""
            ai_description = coordinator.get_ai_description(f.name) if coordinator else ""

            if search and not _clip_matches_search(
                search,
                f.name,
                doorbell,
                kind,
                label,
                ai_description,
            ):
                continue

            matched.append((f, file_date, file_time, doorbell, kind, label, ai_description))

        total = len(matched)

        # Sum sizes for all matched files (not just the current page) so the
        # toolbar can show accurate total storage regardless of pagination state.
        total_bytes = 0
        for f, *_ in matched:
            try:
                total_bytes += f.stat().st_size
            except OSError:
                pass

        page  = matched[offset : offset + limit]

        clips = []
        for f, file_date, file_time, doorbell, kind, label, ai_description in page:
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
                "label":          label,
                "ai_description": ai_description,
            })

        return {"total": total, "total_bytes": total_bytes, "clips": clips}


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
        if not _is_safe_filename(filename):
            return Response(status=400, text="Invalid filename")

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass)
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

    async def get(self, request):
        from aiohttp.web import Response
        import json

        hass      = request.app["hass"]
        filenames = await hass.async_add_executor_job(self._list_filenames, _get_download_path(hass))
        return Response(body=json.dumps({"filenames": filenames}), content_type="application/json")

    def _list_filenames(self, download_path: Path) -> list[str]:
        if not download_path.exists():
            return []
        return [f.name for f in download_path.glob("*.mp4")]


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
        if not _is_safe_filename(filename):
            return Response(status=400, text="Invalid filename")

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if coordinator is None:
            return Response(status=503, text="Coordinator not available")

        await coordinator.async_set_label(filename, label)
        return Response(
            body=json.dumps({"filename": filename, "label": label}),
            content_type="application/json",
        )


class RingClipDeleteView(HomeAssistantView):
    """
    POST /api/ring_stash/delete
    Body: {"filename": "front_door_2026-04-04_14-01-25_Doorbell.mp4"}

    Deletes a clip from disk and tombstones its ding_id so the coordinator
    will not automatically re-download it. Use /restore to reverse this.
    """

    url = "/api/ring_stash/delete"
    name = "api:ring_stash:delete"
    requires_auth = True

    async def post(self, request):
        from aiohttp.web import Response
        import json

        try:
            body = await request.json()
        except Exception:
            return Response(status=400, text="Invalid JSON")

        filename = body.get("filename", "")
        if not _is_safe_filename(filename):
            return Response(status=400, text="Invalid filename")

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if coordinator is None:
            return Response(status=503, text="Coordinator not available")

        await coordinator.async_delete_clip(filename)
        return Response(body=json.dumps({"deleted": filename}), content_type="application/json")


class RingClipRestoreView(HomeAssistantView):
    """
    POST /api/ring_stash/restore
    Body: {"filename": "front_door_2026-04-04_14-01-25_Doorbell.mp4"}

    Removes a clip's tombstone so the coordinator will attempt to re-download
    it from Ring on the next poll cycle (requires Ring to still have the clip).
    """

    url = "/api/ring_stash/restore"
    name = "api:ring_stash:restore"
    requires_auth = True

    async def post(self, request):
        from aiohttp.web import Response
        import json

        try:
            body = await request.json()
        except Exception:
            return Response(status=400, text="Invalid JSON")

        filename = body.get("filename", "")
        if not _is_safe_filename(filename):
            return Response(status=400, text="Invalid filename")

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if coordinator is None:
            return Response(status=503, text="Coordinator not available")

        await coordinator.async_restore_clip(filename)
        return Response(body=json.dumps({"restored": filename}), content_type="application/json")


class RingDeletedClipsView(HomeAssistantView):
    """
    GET /api/ring_stash/deleted

    Returns the list of tombstoned (manually deleted) clips with filename-derived
    metadata so the frontend can show a restorable deleted items view.
    """

    url = "/api/ring_stash/deleted"
    name = "api:ring_stash:deleted"
    requires_auth = True

    async def get(self, request):
        from aiohttp.web import Response
        import json

        hass        = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if coordinator is None:
            return Response(body=json.dumps({"clips": []}), content_type="application/json")

        raw = coordinator.deleted_clips()  # [{"ding_id": ..., "filename": ...}]
        clips = [self._parse(entry) for entry in raw]
        return Response(body=json.dumps({"clips": clips}), content_type="application/json")

    @staticmethod
    def _parse(entry: dict) -> dict:
        # entry comes from coordinator.deleted_clips() — always has "filename" key
        name       = Path(entry.get("filename", "")).stem
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
        recorded_at = f"{file_date}T{file_time}+00:00" if file_date and file_time else ""
        return {
            "filename":    entry["filename"],
            "ding_id":     entry["ding_id"],
            "doorbell":    doorbell,
            "kind":        kind,
            "recorded_at": recorded_at,
        }


class RingClipMediaView(HomeAssistantView):
    """
    GET /ring_stash_media/{filename}

    Serves clip files from the currently configured download directory.
    Home Assistant authentication is required because these files contain
    private camera footage. Access is restricted to administrators to match
    the admin-only sidebar panel.
    """

    url = "/ring_stash_media/{filename}"
    name = "api:ring_stash:media"
    requires_auth = True

    async def get(self, request, filename):
        from aiohttp.web import FileResponse, Response

        if not request[KEY_HASS_USER].is_admin:
            raise HTTPForbidden

        if not _is_safe_filename(filename):
            return Response(status=400, text="Invalid filename")

        path = _get_download_path(request.app["hass"]) / filename
        is_file = await request.app["hass"].async_add_executor_job(path.is_file)
        if not is_file:
            return Response(status=404, text="Not found")
        return FileResponse(path)
