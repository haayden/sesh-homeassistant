"""Sesh: dabs, rigs and cheers from the Sesh rig app, as Home Assistant entities and events."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SeshApi, SeshError
from .const import CONF_TOKEN, DOMAIN
from .coordinator import SeshCoordinator

PLATFORMS = [Platform.SENSOR, Platform.EVENT]
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
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SeshConfigEntry) -> bool:
    api = SeshApi(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    coordinator = SeshCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_create_background_task(hass, coordinator.listen_ntfy(), "sesh ntfy")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SeshConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
