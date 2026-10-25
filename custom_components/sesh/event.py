"""Event entities, for automations picked in the UI. The same events also go on the bus as
sesh_dab (type my_dab / friend_dab) and sesh_cheers."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SeshConfigEntry
from .coordinator import SeshCoordinator
from .sensor import account_device


async def async_setup_entry(hass: HomeAssistant, entry: SeshConfigEntry, add: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    add([SeshEvent(c, "dab", ["my_dab", "friend_dab"], "mdi:fire"), SeshEvent(c, "cheers", ["cheers"], "mdi:glass-mug-variant")])


class SeshEvent(EventEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: SeshCoordinator, key: str, types: list[str], icon: str) -> None:
        self.coordinator = coordinator
        self._attr_translation_key = key
        self._attr_event_types = types
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{key}_event"
        self._attr_device_info = account_device(coordinator)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.on_event(self._got))

    @callback
    def _got(self, kind: str, data: dict) -> None:
        if kind in self._attr_event_types:
            self._trigger_event(kind, data)
            self.async_write_ha_state()
