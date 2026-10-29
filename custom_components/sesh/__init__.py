"""Sesh: dabs, rigs and cheers from the Sesh rig app, as Home Assistant entities and events."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SeshApi, SeshError
from .const import CONF_TOKEN, DOMAIN, PANEL, SITE
from .coordinator import SeshCoordinator

try:
    from . import ble
except ImportError as err:  # no Bluetooth libraries in this install: the panel uses the browser's Bluetooth
    logging.getLogger(__name__).info("Sesh panel without Home Assistant's Bluetooth: %s", err)
    ble = None

PLATFORMS = [Platform.SENSOR, Platform.EVENT]
VERSION = "0.3.0"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SeshConfigEntry = ConfigEntry[SeshCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def send_cheers(call: ServiceCall) -> None:
        entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
        if not entries:
            raise HomeAssistantError("Sesh is not set up")
        try:
            await entries[0].runtime_data.api.cheers(call.data["to"])
        except SeshError as e:
            raise HomeAssistantError(f"Sesh: {e}") from e

    hass.services.async_register(DOMAIN, "send_cheers", send_cheers, schema=vol.Schema({vol.Required("to"): cv.string}))
    if ble:
        ble.async_setup(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig("/sesh_panel", str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SeshConfigEntry) -> bool:
    api = SeshApi(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    coordinator = SeshCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_create_background_task(hass, coordinator.listen_ntfy(), "sesh ntfy")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The app itself in the sidebar. One panel however many Sesh accounts are set up.
    if PANEL not in hass.data.get(frontend.DATA_PANELS, {}):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL,
            webcomponent_name="sesh-panel",
            module_url=f"/sesh_panel/sesh-panel.js?v={VERSION}",
            sidebar_title="Sesh",
            sidebar_icon="mdi:fire",
            require_admin=False,
            config={"url": SITE},
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SeshConfigEntry) -> bool:
    others = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED]
    if not others:
        frontend.async_remove_panel(hass, PANEL)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
