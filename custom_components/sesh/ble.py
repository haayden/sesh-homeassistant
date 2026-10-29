"""Rigs over Home Assistant's Bluetooth, for the app in the sidebar.

The Sesh page asks for rigs through navigator.bluetooth. Inside the panel that is the same stand-in
the phone and PC apps use (ios/Web/polyfill.js in the Sesh repo): every call becomes a small
command (pick a device, connect, list services, read, write, notify) that sesh-panel.js sends to
the websocket commands below. Home Assistant's own adapter or an ESPHome Bluetooth proxy does the
radio work, so the rig only has to be near one of those, not near the browser.

A rig takes one connection, so every open panel shares it. When the last panel closes, the rig is
let go after a short wait (long enough for a page reload) so a phone can have it again.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import bleak_retry_connector
import voluptuous as vol
from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.service import BleakGATTService
from bleak.exc import BleakError

from homeassistant.components import bluetooth, websocket_api
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOG = logging.getLogger(__name__)

# Long enough for the panel's page to reload and pick its rig back up.
RELEASE_AFTER_S = 20
# Errors a rig gives when it wants an encrypted link first (the Peak's PUP read, A2.2 in the
# Sesh repo's docs/PROTOCOL.md).
WANTS_PAIRING = re.compile(r"auth|encrypt|insufficient", re.I)
# Bleak's property names, as Web Bluetooth spells them.
PROPERTIES = {
    "broadcast": "broadcast",
    "read": "read",
    "writeWithoutResponse": "write-without-response",
    "write": "write",
    "notify": "notify",
    "indicate": "indicate",
    "authenticatedSignedWrites": "authenticated-signed-writes",
    "reliableWrite": "reliable-write",
    "writableAuxiliaries": "writable-auxiliaries",
}
COMMANDS = ("scan", "getDevices", "connect", "disconnect", "forget", "services", "characteristics", "read", "write", "notify")


class BleError(Exception):
    """Fails one call. `name` is the DOMException the page sees, like a browser would throw."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


@dataclass
class Link:
    client: BleakClient
    notifying: set[str] = field(default_factory=set)
    paired: bool = False


def matches(f: dict, info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    """One Web Bluetooth scan filter: every part it names has to match."""
    advertised = {u.lower() for u in info.service_uuids}
    if not set(f.get("services") or []) <= advertised:
        return False
    if f.get("name") is not None and info.name != f["name"]:
        return False
    if f.get("namePrefix") and not (info.name or "").startswith(f["namePrefix"]):
        return False
    return all(m.get("companyIdentifier") in info.manufacturer_data for m in f.get("manufacturerData") or [])


def shown_name(info: bluetooth.BluetoothServiceInfoBleak) -> str | None:
    # Home Assistant fills in the address when a device advertises no name.
    name = info.name
    if not name or name.replace("-", ":").upper() == info.address.upper():
        return None
    return name


class Bridge:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.links: dict[str, Link] = {}
        self._subs: set[Callable[[dict], None]] = set()
        self._release: CALLBACK_TYPE | None = None
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.rigs")
        self._rigs: dict[str, str | None] | None = None

    # ---- events to every open panel --------------------------------------------------------

    @callback
    def subscribe(self, send: Callable[[dict], None]) -> CALLBACK_TYPE:
        self._subs.add(send)
        self._cancel_release()

        @callback
        def unsubscribe() -> None:
            self._subs.discard(send)
            if not self._subs:
                self._schedule_release()

        return unsubscribe

    @callback
    def _emit(self, event: dict) -> None:
        for send in list(self._subs):
            send(event)

    @callback
    def _schedule_release(self) -> None:
        self._cancel_release()
        if self.links:
            self._release = async_call_later(self.hass, RELEASE_AFTER_S, self._let_go)

    @callback
    def _cancel_release(self) -> None:
        if self._release:
            self._release()
            self._release = None

    async def _let_go(self, _now: Any) -> None:
        self._release = None
        if self._subs:
            return
        for address in list(self.links):
            _LOG.debug("No Sesh panel open: letting %s go", address)
            await self._disconnect(address)

    # ---- commands -----------------------------------------------------------------------------

    async def call(self, msg: dict) -> Any:
        if "bluetooth" not in self.hass.config.components:
            raise BleError("NotSupportedError", "Home Assistant's Bluetooth isn't set up.")
        cmd = msg["cmd"]
        if cmd == "scan":
            return self._scan(msg.get("filters") or [], msg.get("acceptAllDevices", False))
        if cmd == "getDevices":
            return [{"id": a, "name": n} for a, n in (await self._remembered()).items()]
        if cmd == "connect":
            return await self._connect(msg["device"])
        if cmd == "disconnect":
            return await self._disconnect(msg["device"])
        if cmd == "forget":
            await self._disconnect(msg["device"])
            rigs = await self._remembered()
            if msg["device"] in rigs:
                del rigs[msg["device"]]
                await self._store.async_save({"rigs": rigs})
            return None
        if cmd == "services":
            return self._services(msg["device"], msg.get("service"))
        if cmd == "characteristics":
            return [
                {"uuid": c.uuid, "properties": {k: v in c.properties for k, v in PROPERTIES.items()}}
                for c in self._characteristics(msg["device"], msg["service"], msg.get("characteristic"))
            ]
        if cmd == "read":
            return await self._read(msg)
        if cmd == "write":
            return await self._write(msg)
        if cmd == "notify":
            return await self._notify(msg)
        raise BleError("NotSupportedError", f"Unknown command {cmd}.")

    @callback
    def _scan(self, filters: list[dict], accept_all: bool) -> dict:
        found: dict[str, dict] = {}
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if not accept_all and not any(matches(f, info) for f in filters):
                continue
            scanner = bluetooth.async_scanner_by_source(self.hass, info.source)
            found[info.address] = {
                "id": info.address,
                "name": shown_name(info),
                "rssi": info.rssi,
                "via": scanner.name if scanner else None,
            }
        devices = sorted(found.values(), key=lambda d: -(d["rssi"] if d["rssi"] is not None else -127))
        return {"devices": devices, "scanners": bluetooth.async_scanner_count(self.hass, connectable=True)}

    async def _remembered(self) -> dict[str, str | None]:
        if self._rigs is None:
            self._rigs = dict(((await self._store.async_load()) or {}).get("rigs", {}))
        return self._rigs

    async def _connect(self, address: str) -> None:
        link = self.links.get(address)
        if link and link.client.is_connected:
            return None
        self.links.pop(address, None)
        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is None:
            raise BleError("NetworkError", "Home Assistant can't hear that rig. Wake it, and keep it near a Bluetooth adapter or proxy.")
        info = bluetooth.async_last_service_info(self.hass, address, connectable=True)
        name = shown_name(info) if info else None

        @callback
        def dropped(client: BleakClient) -> None:
            link = self.links.get(address)
            if link is None or link.client is not client:
                return
            del self.links[address]
            self._emit({"type": "disconnected", "device": address})

        try:
            # Looked up now, not at import: Home Assistant swaps in its proxy-aware client when its
            # Bluetooth starts, and that may be after this file loaded.
            client = await bleak_retry_connector.establish_connection(
                bleak_retry_connector.BleakClientWithServiceCache, device, name or address, disconnected_callback=dropped, max_attempts=3
            )
        except (BleakError, TimeoutError) as e:
            raise BleError("NetworkError", f"Couldn't connect through Home Assistant: {e}") from e
        self.links[address] = Link(client)
        rigs = await self._remembered()
        if address not in rigs or (name and rigs[address] != name):
            rigs[address] = name or rigs.get(address)
            await self._store.async_save({"rigs": rigs})
        if not self._subs:
            self._schedule_release()
        return None

    async def _disconnect(self, address: str) -> None:
        link = self.links.pop(address, None)
        if link is None:
            return None
        try:
            await link.client.disconnect()
        except BleakError as e:
            _LOG.debug("Disconnecting %s: %s", address, e)
        self._emit({"type": "disconnected", "device": address})
        return None

    def _link(self, address: str) -> Link:
        link = self.links.get(address)
        if link is None or not link.client.is_connected:
            raise BleError("NetworkError", "GATT Server is disconnected.")
        return link

    def _services(self, address: str, want: str | None) -> list[str]:
        uuids = list(dict.fromkeys(s.uuid for s in self._link(address).client.services))
        if want is None:
            return uuids
        if want not in uuids:
            raise BleError("NotFoundError", f"No Services matching UUID {want} found in Device.")
        return [want]

    def _service(self, address: str, uuid: str) -> BleakGATTService:
        for s in self._link(address).client.services:
            if s.uuid == uuid:
                return s
        raise BleError("NotFoundError", "Service not found.")

    def _characteristics(self, address: str, service: str, want: str | None) -> list[BleakGATTCharacteristic]:
        first: dict[str, BleakGATTCharacteristic] = {}
        for c in self._service(address, service).characteristics:
            first.setdefault(c.uuid, c)
        chars = list(first.values())
        if want is None:
            return chars
        chars = [c for c in chars if c.uuid == want]
        if not chars:
            raise BleError("NotFoundError", f"No Characteristics matching UUID {want} found in Service.")
        return chars

    async def _gatt(self, msg: dict, what: str, op: Callable[[BleakClient, BleakGATTCharacteristic], Any]) -> Any:
        link = self._link(msg["device"])
        char = self._characteristics(msg["device"], msg["service"], msg["characteristic"])[0]
        try:
            try:
                return await op(link.client, char)
            except BleakError as e:
                # Pair once, the way the PC app does, then try again.
                if link.paired or not WANTS_PAIRING.search(str(e)):
                    raise
                link.paired = True
                _LOG.debug("%s on %s wants pairing: %s", what, msg["device"], e)
                await link.client.pair()
                return await op(link.client, char)
        except (BleakError, NotImplementedError, TimeoutError) as e:
            if not link.client.is_connected:
                raise BleError("NetworkError", "GATT Server is disconnected.") from e
            raise BleError("NotSupportedError", f"{what} failed: {e}") from e

    async def _read(self, msg: dict) -> list[int]:
        value = bytes(await self._gatt(msg, "Read", lambda client, c: client.read_gatt_char(c)))
        # A browser fires characteristicvaluechanged for reads too.
        self._emit({"type": "value", "key": f"{msg['device']}|{msg['service']}|{msg['characteristic']}", "value": list(value)})
        return list(value)

    async def _write(self, msg: dict) -> None:
        data = bytes(msg["value"])
        await self._gatt(msg, "Write", lambda client, c: client.write_gatt_char(c, data, response=msg.get("withResponse", True)))
        return None

    async def _notify(self, msg: dict) -> None:
        key = f"{msg['device']}|{msg['service']}|{msg['characteristic']}"
        link = self._link(msg["device"])
        if msg["on"] == (key in link.notifying):
            return None

        @callback
        def changed(_char: BleakGATTCharacteristic, data: bytearray) -> None:
            self._emit({"type": "value", "key": key, "value": list(data)})

        if msg["on"]:
            await self._gatt(msg, "Subscribing", lambda client, c: client.start_notify(c, changed))
            link.notifying.add(key)
        else:
            link.notifying.discard(key)
            await self._gatt(msg, "Unsubscribing", lambda client, c: client.stop_notify(c))
        return None


# ---- websocket commands (sesh-panel.js) --------------------------------------------------------


@callback
def async_setup(hass: HomeAssistant) -> None:
    bridge = Bridge(hass)

    @websocket_api.websocket_command({vol.Required("type"): "sesh/ble/info"})
    @callback
    def info(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
        on = "bluetooth" in hass.config.components
        connection.send_result(msg["id"], {"available": on, "scanners": bluetooth.async_scanner_count(hass, connectable=True) if on else 0})

    @websocket_api.websocket_command(
        {
            vol.Required("type"): "sesh/ble",
            vol.Required("cmd"): vol.In(COMMANDS),
            vol.Optional("device"): str,
            vol.Optional("service"): vol.Any(str, None),
            vol.Optional("characteristic"): vol.Any(str, None),
            vol.Optional("value"): [vol.All(int, vol.Range(0, 255))],
            vol.Optional("withResponse"): bool,
            vol.Optional("on"): bool,
            vol.Optional("filters"): [dict],
            vol.Optional("acceptAllDevices"): bool,
        }
    )
    @websocket_api.async_response
    async def call(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
        try:
            result = await bridge.call(msg)
        except BleError as e:
            connection.send_error(msg["id"], e.name, str(e))
        except KeyError as e:
            connection.send_error(msg["id"], "TypeError", f"{msg['cmd']} needs {e.args[0]}.")
        else:
            connection.send_result(msg["id"], result)

    @websocket_api.websocket_command({vol.Required("type"): "sesh/ble/subscribe"})
    @callback
    def subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
        connection.subscriptions[msg["id"]] = bridge.subscribe(
            lambda event: connection.send_message(websocket_api.event_message(msg["id"], event))
        )
        connection.send_result(msg["id"])

    for command in (info, call, subscribe):
        websocket_api.async_register_command(hass, command)
