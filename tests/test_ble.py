"""The sidebar app's Bluetooth through Home Assistant (ble.py), against a fake rig."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bleak.exc import BleakError

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.sesh import ble

RIG = "AA:BB:CC:DD:EE:FF"
SVC = "0000fee7-0000-1000-8000-00805f9b34fb"
TX = "0000fec1-0000-1000-8000-00805f9b34fb"
RX = "0000fec2-0000-1000-8000-00805f9b34fb"


def info(address, name, services=(), makers=(), rssi=-60, source="proxy1"):
    return SimpleNamespace(
        address=address, name=name, rssi=rssi, source=source, service_uuids=list(services), manufacturer_data={m: b"" for m in makers}
    )


class FakeRig:
    """A Switch 2 as bleak sees it. The first read wants pairing, like the Peak's PUP read."""

    def __init__(self, disconnected_callback):
        self.dropped = disconnected_callback
        self.is_connected = True
        tx = SimpleNamespace(uuid=TX, properties=["write", "write-without-response"])
        rx = SimpleNamespace(uuid=RX, properties=["notify", "read"])
        self.services = [SimpleNamespace(uuid=SVC, characteristics=[tx, rx])]
        self.writes = []
        self.listeners = {}
        self.paired = 0

    async def write_gatt_char(self, char, data, response):
        self.writes.append((char.uuid, bytes(data), response))

    async def read_gatt_char(self, char):
        if not self.paired:
            raise BleakError("Insufficient authentication")
        return bytearray(b"\x01\x02")

    async def pair(self):
        self.paired += 1

    async def start_notify(self, char, cb):
        self.listeners[char.uuid] = cb

    async def stop_notify(self, char):
        del self.listeners[char.uuid]

    async def disconnect(self):
        self.is_connected = False
        self.dropped(self)


@pytest.fixture
def radio():
    rigs = {}

    async def connect(client_class, device, name, disconnected_callback=None, **kw):
        rigs[device.address] = FakeRig(disconnected_callback)
        return rigs[device.address]

    seen = [
        info(RIG, "Switch 2", services=[SVC]),
        info("11:22:33:44:55:66", "PACKS PEAK PRO", makers=[3075], rssi=-80),
        info("99:99:99:99:99:99", "99:99:99:99:99:99", rssi=-40),
    ]
    with (
        patch.object(ble.bluetooth, "async_discovered_service_info", return_value=seen),
        patch.object(ble.bluetooth, "async_scanner_count", return_value=1),
        patch.object(ble.bluetooth, "async_scanner_by_source", return_value=SimpleNamespace(name="Living room proxy")),
        patch.object(ble.bluetooth, "async_ble_device_from_address", side_effect=lambda h, a, connectable: SimpleNamespace(address=a)),
        patch.object(ble.bluetooth, "async_last_service_info", side_effect=lambda h, a, connectable: next(i for i in seen if i.address == a)),
        patch.object(ble.bleak_retry_connector, "establish_connection", side_effect=connect),
    ):
        yield rigs


@pytest.fixture
async def ws(hass: HomeAssistant, hass_ws_client):
    hass.config.components.add("bluetooth")
    ble.async_setup(hass)
    client = await hass_ws_client(hass)
    events = []

    async def call(cmd, **args):
        # Events can arrive before the answer (a read's value, a disconnect's drop): kept for event().
        await client.send_json_auto_id({"type": "sesh/ble", "cmd": cmd, **args})
        while (msg := await client.receive_json())["type"] == "event":
            events.append(msg)
        return msg

    async def event():
        return events.pop(0) if events else await client.receive_json()

    client.call = call
    client.event = event
    return client


async def test_info_and_scan(hass, ws, radio):
    await ws.send_json_auto_id({"type": "sesh/ble/info"})
    assert (await ws.receive_json())["result"] == {"available": True, "scanners": 1}

    # Sesh's own filters: the Switch by service, the Puffco by Puffco's company id.
    msg = await ws.call("scan", filters=[{"services": [SVC]}, {"manufacturerData": [{"companyIdentifier": 3075}]}])
    assert msg["result"]["devices"] == [
        {"id": RIG, "name": "Switch 2", "rssi": -60, "via": "Living room proxy"},
        {"id": "11:22:33:44:55:66", "name": "PACKS PEAK PRO", "rssi": -80, "via": "Living room proxy"},
    ]
    everything = (await ws.call("scan", acceptAllDevices=True))["result"]["devices"]
    # A device with no name of its own shows as unnamed, not as its address.
    assert everything[0] == {"id": "99:99:99:99:99:99", "name": None, "rssi": -40, "via": "Living room proxy"}


async def test_a_session_with_the_rig(hass, ws, radio):
    await ws.send_json_auto_id({"type": "sesh/ble/subscribe"})
    sub = (await ws.receive_json())["id"]

    assert (await ws.call("services", device=RIG))["error"]["code"] == "NetworkError"
    assert (await ws.call("connect", device=RIG))["success"]
    rig = radio[RIG]
    assert (await ws.call("services", device=RIG))["result"] == [SVC]
    assert (await ws.call("services", device=RIG, service=TX))["error"]["code"] == "NotFoundError"
    chars = (await ws.call("characteristics", device=RIG, service=SVC))["result"]
    assert chars[0] == {
        "uuid": TX,
        "properties": {**{k: False for k in ble.PROPERTIES}, "write": True, "writeWithoutResponse": True},
    }
    assert (await ws.call("characteristics", device=RIG, service=SVC, characteristic=RX))["result"][0]["properties"]["notify"]

    assert (await ws.call("write", device=RIG, service=SVC, characteristic=TX, value=[0xB9, 20], withResponse=False))["success"]
    assert rig.writes == [(TX, b"\xb9\x14", False)]

    assert (await ws.call("notify", device=RIG, service=SVC, characteristic=RX, on=True))["success"]
    rig.listeners[RX](None, bytearray(b"\xa9\x14"))
    event = await ws.event()
    assert event["id"] == sub and event["event"] == {"type": "value", "key": f"{RIG}|{SVC}|{RX}", "value": [0xA9, 0x14]}

    # Wants pairing: paired once, then read again.
    msg = await ws.call("read", device=RIG, service=SVC, characteristic=RX)
    assert rig.paired == 1
    assert (await ws.event())["event"]["value"] == [1, 2]
    assert msg["result"] == [1, 2]

    assert (await ws.call("getDevices"))["result"] == [{"id": RIG, "name": "Switch 2"}]
    assert (await ws.call("disconnect", device=RIG))["success"]
    assert (await ws.event())["event"] == {"type": "disconnected", "device": RIG}
    assert not rig.is_connected
    assert (await ws.call("forget", device=RIG))["success"]
    assert (await ws.call("getDevices"))["result"] == []


async def test_a_dropped_rig_tells_the_page(hass, ws, radio):
    await ws.send_json_auto_id({"type": "sesh/ble/subscribe"})
    await ws.receive_json()
    await ws.call("connect", device=RIG)
    await radio[RIG].disconnect()  # out of range
    assert (await ws.event())["event"] == {"type": "disconnected", "device": RIG}
    assert (await ws.call("services", device=RIG))["error"]["code"] == "NetworkError"


async def test_the_rig_is_let_go_when_the_last_panel_closes(hass, ws, radio):
    await ws.send_json_auto_id({"type": "sesh/ble/subscribe"})
    sub = (await ws.receive_json())["id"]
    await ws.call("connect", device=RIG)
    await ws.send_json_auto_id({"type": "unsubscribe_events", "subscription": sub})
    await ws.receive_json()

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=ble.RELEASE_AFTER_S - 5))
    await hass.async_block_till_done()
    assert radio[RIG].is_connected
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=ble.RELEASE_AFTER_S + 1))
    await hass.async_block_till_done()
    assert not radio[RIG].is_connected


async def test_no_bluetooth_in_home_assistant(hass, hass_ws_client):
    ble.async_setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "sesh/ble/info"})
    assert (await client.receive_json())["result"] == {"available": False, "scanners": 0}
    await client.send_json_auto_id({"type": "sesh/ble", "cmd": "scan"})
    assert (await client.receive_json())["error"]["code"] == "NotSupportedError"
