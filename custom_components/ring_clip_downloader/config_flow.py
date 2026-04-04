"""Config flow for Ring Clip Downloader.

Discovers the existing Ring integration automatically — the user never
enters credentials here. We only ask for storage and retention preferences.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_DOWNLOAD_PATH,
    CONF_POLL_INTERVAL,
    CONF_RETENTION_DAYS,
    CONF_RING_ENTRY_ID,
    DEFAULT_DOWNLOAD_PATH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _get_ring_entries(hass) -> list:
    return hass.config_entries.async_entries("ring")


def _options_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_DOWNLOAD_PATH,
                default=defaults.get(CONF_DOWNLOAD_PATH, DEFAULT_DOWNLOAD_PATH),
            ): TextSelector(),
            vol.Required(
                CONF_RETENTION_DAYS,
                default=defaults.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=365, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_POLL_INTERVAL,
                default=defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=60, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
    )


class RingClipDownloaderConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle initial setup via the UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        ring_entries = _get_ring_entries(self.hass)
        if not ring_entries:
            return self.async_abort(reason="ring_not_configured")

        # Prevent duplicate entries
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            path = Path(user_input[CONF_DOWNLOAD_PATH])
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                errors[CONF_DOWNLOAD_PATH] = "invalid_path"

            if not errors:
                return self.async_create_entry(
                    title="Ring Clip Downloader",
                    data={
                        CONF_RING_ENTRY_ID: ring_entries[0].entry_id,
                        CONF_DOWNLOAD_PATH: str(path),
                        CONF_RETENTION_DAYS: int(user_input[CONF_RETENTION_DAYS]),
                        CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_options_schema({}),
            errors=errors,
            description_placeholders={
                "ring_account": ring_entries[0].title if ring_entries else "",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RingClipOptionsFlow(config_entry)


class RingClipOptionsFlow(OptionsFlow):
    """Handle options (reconfiguration) from the integrations page."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            path = Path(user_input[CONF_DOWNLOAD_PATH])
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                errors[CONF_DOWNLOAD_PATH] = "invalid_path"

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_DOWNLOAD_PATH: str(path),
                        CONF_RETENTION_DAYS: int(user_input[CONF_RETENTION_DAYS]),
                        CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                    },
                )

        current = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
            errors=errors,
        )
