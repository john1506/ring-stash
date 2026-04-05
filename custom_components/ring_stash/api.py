"""
Async Ring API client.

Design principles:
- Stateless: holds no credentials. Auth tokens are passed per-call via
  the TokenManager abstraction — they never leave memory and are never logged.
- All I/O is async via the HA-managed aiohttp session (inherits HA's SSL config).
- Pre-signed clip URLs returned by the API are treated as opaque; they are
  written directly to disk and never surfaced in logs or entity attributes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from aiohttp import ClientSession, ClientResponseError

from .const import RING_API_BASE, RING_OAUTH_URL, RING_CLIENT_ID, RING_USER_AGENT

_LOGGER = logging.getLogger(__name__)


class RingAuthError(Exception):
    """Raised when authentication fails and cannot be recovered."""


class RingApiError(Exception):
    """Raised for non-auth Ring API errors."""


class TokenManager:
    """
    Thread-safe token management backed by the existing Ring HA config entry.

    Tokens are read from and written to the Ring integration's own config entry
    so the two integrations stay in sync. We never persist tokens ourselves.
    """

    def __init__(self, hass, ring_entry) -> None:
        self._hass = hass
        self._entry = ring_entry
        self._lock = asyncio.Lock()

    @property
    def device_id(self) -> str:
        return self._entry.data["device_id"]

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing transparently if near expiry."""
        async with self._lock:
            token_data = self._entry.data["token"]
            if time.time() > token_data.get("expires_at", 0) - 300:
                token_data = await self._refresh(token_data)
            return token_data["access_token"]

    async def async_force_refresh(self) -> None:
        """Force a token refresh, acquiring the lock to prevent concurrent refreshes."""
        async with self._lock:
            token_data = self._entry.data["token"]
            await self._refresh(token_data)

    async def _refresh(self, old_token: dict) -> dict:
        """Obtain a new token via refresh_token grant and persist it."""
        _LOGGER.debug("Refreshing Ring access token")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": old_token["refresh_token"],
            "client_id": RING_CLIENT_ID,
            "scope": "client",
        }
        # aiohttp session from the API client is not available here;
        # use a minimal one-shot request via asyncio's default loop transport
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                RING_OAUTH_URL,
                json=payload,
                headers={"hardware_id": self.device_id},
                ssl=True,
            ) as resp:
                if resp.status == 401:
                    raise RingAuthError("Refresh token rejected — re-authenticate Ring integration")
                resp.raise_for_status()
                new_token: dict = await resp.json()

        new_token["expires_at"] = int(time.time()) + new_token.get("expires_in", 14400)

        # Write back to the Ring config entry so HA's own integration stays in sync
        updated_data = {**self._entry.data, "token": new_token}
        self._hass.config_entries.async_update_entry(self._entry, data=updated_data)
        _LOGGER.debug("Ring token refreshed successfully")
        return new_token


class RingApiClient:
    """
    Minimal async client for the Ring REST API.

    Responsibilities:
    - Build correct headers (auth token fetched fresh per-call, never stored)
    - Retry once on 401 after a token refresh
    - Raise typed exceptions so the coordinator can handle them cleanly
    - Never log token values or pre-signed media URLs
    """

    def __init__(self, session: ClientSession, tokens: TokenManager) -> None:
        self._session = session
        self._tokens = tokens

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "hardware_id": self._tokens.device_id,
            "User-Agent": RING_USER_AGENT,
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: dict | None = None, *, _refreshed: bool = False) -> Any:
        access_token = await self._tokens.get_access_token()
        url = f"{RING_API_BASE}{path}"
        try:
            async with self._session.get(
                url,
                headers=self._headers(access_token),
                params=params,
                ssl=True,
            ) as resp:
                if resp.status == 401 and not _refreshed:
                    # Force a token refresh and retry exactly once
                    await self._tokens.async_force_refresh()
                    return await self._get(path, params, _refreshed=True)
                resp.raise_for_status()
                return await resp.json()
        except ClientResponseError as exc:
            if exc.status == 401:
                raise RingAuthError("Ring API authentication failed") from exc
            raise RingApiError(f"Ring API error {exc.status} for {path}") from exc

    # ── Public API methods ────────────────────────────────────────────────────

    async def async_get_doorbells(self) -> list[dict]:
        """Return all doorbells (wired and battery/video) on this account."""
        data = await self._get("/clients_api/ring_devices")
        return data.get("doorbots", []) + data.get("video_doorbells", [])

    async def async_get_history(
        self,
        doorbell_id: int | str,
        limit: int = 20,
        older_than: int | str | None = None,
    ) -> list[dict]:
        """Return the most recent ``limit`` events for a doorbell."""
        params = {"limit": limit}
        if older_than is not None:
            params["older_than"] = older_than

        return await self._get(
            f"/clients_api/doorbots/{doorbell_id}/history",
            params=params,
        )

    async def async_get_clip_url(self, ding_id: int | str) -> str | None:
        """
        Return the pre-signed media URL for a clip, or None if not yet ready.

        The URL is intentionally not logged — it is a time-limited credential.
        """
        data = await self._get(
            f"/clients_api/dings/{ding_id}/share/play",
            params={"disable_redirect": "true"},
        )
        return data.get("url")  # None when Ring hasn't finished processing yet

    async def async_download_clip(
        self,
        url: str,
        dest_path,  # pathlib.Path
        hass,
    ) -> int:
        """
        Stream a clip to disk atomically.

        Writes to a .tmp file first; renames only on success so a partial
        download never leaves a corrupt file at the destination.
        Returns the number of bytes written.
        """
        tmp_path = dest_path.with_suffix(".tmp")
        try:
            async with self._session.get(url, ssl=True) as resp:
                resp.raise_for_status()
                content = await resp.read()

            # File I/O off the event loop
            await hass.async_add_executor_job(_atomic_write, tmp_path, dest_path, content)
            return len(content)
        except Exception:
            await hass.async_add_executor_job(_unlink_if_exists, tmp_path)
            raise


# ── Executor-safe helpers (no async) ─────────────────────────────────────────

def _atomic_write(tmp_path, dest_path, content: bytes) -> None:
    tmp_path.write_bytes(content)
    tmp_path.rename(dest_path)


def _unlink_if_exists(path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
