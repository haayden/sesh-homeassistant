"""Sensors: today's dabs (all rigs and per rig type), lifetime dabs, leaderboard place, last dab, rigs."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SeshConfigEntry
from .const import DOMAIN, KINDS
from .coordinator import SeshCoordinator, SeshData


def _me(d: SeshData) -> dict:
    return next((r for r in d.rows if r.get("me")), {})


def _place(d: SeshData) -> int | None:
    ranked = sorted(d.rows, key=lambda r: (-(r.get("today") or 0), -(r.get("total") or 0)))
    return next((i + 1 for i, r in enumerate(ranked) if r.get("me")), None)


def _last_at(d: SeshData) -> datetime | None:
    at = (d.last_session or {}).get("at")
    return dt_util.utc_from_timestamp(at / 1000) if at else None


def _last_attrs(d: SeshData) -> dict[str, Any]:
    s = d.last_session or {}
    out = {"rig": KINDS.get(s.get("kind"), s.get("kind")), "rip": s.get("rip"), "temp_f": s.get("tempF"), "seconds": s.get("seconds")}
    for k, a in (("puffs", "puffs"), ("peakF", "peak_f")):
        if s.get(k) is not None:
            out[a] = s[k]
    return out


@dataclass(frozen=True, kw_only=True)
class SeshSensorDescription(SensorEntityDescription):
    value: Callable[[SeshData], Any]
    attrs: Callable[[SeshData], dict[str, Any]] | None = None


SENSORS = (
    SeshSensorDescription(
        key="dabs_today",
        translation_key="dabs_today",
        icon="mdi:fire",
        native_unit_of_measurement="dabs",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value=lambda d: _me(d).get("today", 0),
        attrs=lambda d: {KINDS.get(k, k): n for k, n in d.today.items()},
    ),
    *(
        SeshSensorDescription(
            key=f"dabs_today_{kind}",
            translation_key=f"dabs_today_{kind}",
            icon="mdi:fire",
            native_unit_of_measurement="dabs",
            state_class=SensorStateClass.TOTAL_INCREASING,
            value=lambda d, kind=kind: d.today.get(kind, 0),
        )
        for kind in KINDS
    ),
    SeshSensorDescription(
        key="lifetime_dabs",
        translation_key="lifetime_dabs",
        icon="mdi:counter",
        native_unit_of_measurement="dabs",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value=lambda d: _me(d).get("total", 0),
    ),
    SeshSensorDescription(
        key="leaderboard",
        translation_key="leaderboard",
        icon="mdi:podium",
        value=_place,
        attrs=lambda d: {
            "people": [{"name": r["name"], "today": r.get("today", 0), "total": r.get("total", 0)} for r in d.rows]
        },
    ),
    SeshSensorDescription(
        key="last_dab",
        translation_key="last_dab",
        icon="mdi:history",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=_last_at,
        attrs=_last_attrs,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: SeshConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add(SeshSensor(coordinator, d) for d in SENSORS)

    known: set[str] = set()

    @callback
    def add_rigs() -> None:
        new = [r for r in coordinator.data.rigs if r.get("serial") and r["serial"] not in known]
        known.update(r["serial"] for r in new)
        if new:
            add(RigSensor(coordinator, r["serial"]) for r in new)

    add_rigs()
    entry.async_on_unload(coordinator.async_add_listener(add_rigs))


def _rig_type(rig: dict) -> str:
    # The Puffco slot is a Peak Pro or a Proxy; the edition says which.
    if str(rig.get("edition") or "").startswith("proxy"):
        return "Proxy"
    return KINDS.get(rig.get("kind"), rig.get("kind") or "Rig")


def account_device(coordinator: SeshCoordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Sesh",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://sesh-rigs.vercel.app",
    )


class SeshSensor(CoordinatorEntity[SeshCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: SeshSensorDescription

    def __init__(self, coordinator: SeshCoordinator, description: SeshSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{description.key}"
        self._attr_device_info = account_device(coordinator)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        f = self.entity_description.attrs
        return f(self.coordinator.data) if f else None


class RigSensor(CoordinatorEntity[SeshCoordinator], SensorEntity):
    """One per rig the account has connected: its own lifetime counter, plus when the app last saw it."""

    _attr_has_entity_name = True
    _attr_translation_key = "rig_dabs"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "dabs"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: SeshCoordinator, serial: str) -> None:
        super().__init__(coordinator)
        self.serial = serial
        rig = self._rig or {}
        kind = _rig_type(rig)
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_rig_{serial}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"rig_{serial}")},
            name=rig.get("name") or kind,
            manufacturer="Dr. Dabber" if rig.get("kind") == "switch2" else "Puffco",
            model=rig.get("model") or kind,
            sw_version=rig.get("firmware"),
            serial_number=serial,
        )

    @property
    def _rig(self) -> dict | None:
        return next((r for r in self.coordinator.data.rigs if r.get("serial") == self.serial), None)

    @property
    def available(self) -> bool:
        return super().available and self._rig is not None

    @property
    def native_value(self) -> int | None:
        return (self._rig or {}).get("total_dabs")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self._rig or {}
        seen = r.get("last_seen")
        return {
            "rig": _rig_type(r),
            "edition": r.get("edition"),
            "last_seen": dt_util.utc_from_timestamp(seen).isoformat() if seen else None,
        }
