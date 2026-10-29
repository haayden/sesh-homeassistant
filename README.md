# Sesh for Home Assistant

Your dabs, rigs and cheers from the [Sesh](https://sesh-rigs.vercel.app) app, as Home Assistant
sensors and events. Your rig still connects to your phone. Home Assistant reads the same numbers
the app shows from the Sesh API, so it works from any network.

## Install (HACS)

1. HACS → ⋮ (top right) → **Custom repositories**.
2. Repository: `https://github.com/haayden/sesh-homeassistant`. Type: **Integration**. Click **Add**.
3. Search HACS for **Sesh**, open it, click **Download**, then restart Home Assistant.
4. Settings → Devices & services → **Add integration** → **Sesh**.
5. Enter your Sesh name and PIN. The PIN is in the Sesh app under **Menu**. Leave "Sesh API" as it is.

No HACS? Copy `custom_components/sesh` into your `/config/custom_components/` folder
(the Samba share or File editor add-on), restart, then do step 4.

## What you get

| Entity | What it shows |
|---|---|
| `sensor.sesh_<you>_dabs_today` | Your dabs today, the same count as the app (attributes split it by rig type) |
| `sensor.sesh_<you>_peak_pro_dabs_today` / `_switch_2_dabs_today` | Today, per rig type |
| `sensor.sesh_<you>_lifetime_dabs` | Your lifetime total, from your rigs' own counters |
| `sensor.sesh_<you>_leaderboard_place` | Your place on today's leaderboard (attribute `people` lists everyone) |
| `sensor.sesh_<you>_last_dab` | When your last session was, with its rip, temperature and length |
| `sensor.<rig name>_dabs` | Each of your rigs' own counter, and when the app last saw it |
| `event.sesh_<you>_dab` | Fires `my_dab` or `friend_dab`, with the person's `name` |
| `event.sesh_<you>_cheers` | Fires `cheers` when someone cheers you, with their `name` |

Action: `sesh.send_cheers` with `to: <their Sesh name>`, the same as the Cheers button in the app.

Friends' dabs and cheers arrive within a second or two (through your dab alerts). Your own
dabs show up within about 15 seconds.

## Automation examples

Lights red when you start a dab:

```yaml
triggers:
  - trigger: event
    event_type: sesh_dab
    event_data:
      type: my_dab
actions:
  - action: light.turn_on
    target:
      entity_id: light.living_room
    data:
      color_name: red
```

Flash the lights when anyone cheers you:

```yaml
triggers:
  - trigger: event
    event_type: sesh_cheers
actions:
  - action: light.turn_on
    target:
      entity_id: light.living_room
    data:
      flash: short
```

Cheer a friend back from a dashboard button:

```yaml
type: button
name: Cheers Hayden
icon: mdi:glass-mug-variant
tap_action:
  action: perform-action
  perform_action: sesh.send_cheers
  data:
    to: Hayden
```

## The app itself in the sidebar

Once Sesh is set up, **Sesh** shows up in Home Assistant's sidebar with the whole app in it:
Metrics, the leaderboard, your rips and the rest. Sign in there once with the same name and PIN.

Rigs connect through Home Assistant's own Bluetooth: its adapter, or an
[ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html). Tap **Add a rig**
and pick yours from the list. The rig has to be near the adapter or a proxy, not near the
device you're browsing on, so it works from any browser and from the Home Assistant app.

- ESPHome proxies need `active: true` to connect to a rig (listening alone isn't enough).
- A rig takes one connection at a time. Close the Puffco and Dr. Dabber apps first, and
  Home Assistant lets the rig go about 20 seconds after you close the Sesh panel, so your
  phone can have it again.
- Without Bluetooth in Home Assistant, the panel uses the browser's own Bluetooth instead
  (Chrome and Edge on a computer).
