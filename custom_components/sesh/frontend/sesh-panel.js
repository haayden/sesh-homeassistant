// The Sesh app in Home Assistant's sidebar. On a phone, Home Assistant hides its sidebar, so a
// thin bar with the menu button stays on top to get back to it.
//
// Bluetooth: with ?ha in its address the page sends its navigator.bluetooth calls up here
// (src/ble/haHost.ts in the Sesh repo) and this panel hands them to Home Assistant (ble.py), whose
// own adapter or ESPHome Bluetooth proxies reach the rig. Picking a rig opens the chooser below.
// Without Bluetooth in Home Assistant the page falls back to the browser's own.
const CANCELLED = { code: "NotFoundError", message: "User cancelled the requestDevice() chooser." };
const SCAN_EVERY_MS = 1500;
// How long the chooser waits before it says why nothing shows up.
const HINT_AFTER_MS = 8000;

const signal = (rssi) => (rssi == null ? "" : rssi > -65 ? "strong signal" : rssi > -80 ? "good signal" : "weak signal");

class SeshPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    this._load();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this._render();
  }

  set panel(panel) {
    this._panel = panel;
    this._render();
  }

  connectedCallback() {
    window.addEventListener("message", this._onMessage);
  }

  disconnectedCallback() {
    window.removeEventListener("message", this._onMessage);
    this._closeChooser(CANCELLED);
    // Home Assistant lets the rig go a little after the last panel stops listening.
    const sub = this._sub;
    this._sub = undefined;
    if (sub) sub.then((unsub) => unsub()).catch(() => {});
  }

  _render() {
    if (!this._panel) return;
    if (!this._frame) {
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          :host { display: flex; flex-direction: column; height: 100vh; height: 100dvh; background: #0e0e0f; position: relative; }
          .bar { display: none; align-items: center; height: 56px; padding: 0 4px; color: #fff; background: #0e0e0f; }
          .bar span { font-size: 20px; margin-left: 8px; }
          iframe { flex: 1; width: 100%; border: 0; display: block; }
          .chooser { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 16px;
            background: rgb(0 0 0 / 0.6); color: #f4f4f5; font: 15px/1.4 Roboto, system-ui, sans-serif; z-index: 1; }
          .chooser[hidden] { display: none; }
          .sheet { width: 100%; max-width: 400px; max-height: 100%; display: flex; flex-direction: column; overflow: hidden;
            background: #18181b; border: 1px solid #2a2a2e; border-radius: 16px; }
          h2 { margin: 0; padding: 20px 20px 2px; font-size: 20px; font-weight: 500; }
          .sub { margin: 0; padding: 0 20px 12px; color: #a1a1aa; font-size: 13px; }
          ul { list-style: none; margin: 0; padding: 0 8px; overflow-y: auto; }
          li button { width: 100%; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 12px;
            border: 0; border-radius: 10px; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; }
          li button:hover, li button:focus-visible { background: #26262b; outline: none; }
          .name { font-weight: 500; }
          .meta { color: #a1a1aa; font-size: 13px; }
          .empty { margin: 0; padding: 8px 20px 4px; color: #d4d4d8; }
          .empty[hidden] { display: none; }
          .actions { display: flex; justify-content: flex-end; padding: 12px; }
          .cancel { padding: 10px 16px; border: 0; border-radius: 999px; background: #26262b; color: #f4f4f5; font: inherit; cursor: pointer; }
          .cancel:hover, .cancel:focus-visible { background: #34343a; outline: none; }
        </style>
        <div class="bar"><ha-menu-button></ha-menu-button><span>Sesh</span></div>
        <iframe title="Sesh" allow="bluetooth; fullscreen"></iframe>
        <div class="chooser" hidden>
          <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sesh-pick-title">
            <h2 id="sesh-pick-title">Pick your rig</h2>
            <p class="sub">Through Home Assistant's Bluetooth</p>
            <ul></ul>
            <p class="empty" aria-live="polite"></p>
            <div class="actions"><button class="cancel" type="button">Cancel</button></div>
          </div>
        </div>`;
      this._bar = root.querySelector(".bar");
      this._menu = root.querySelector("ha-menu-button");
      this._frame = root.querySelector("iframe");
      this._chooser = root.querySelector(".chooser");
      this._list = root.querySelector(".chooser ul");
      this._empty = root.querySelector(".empty");
      root.querySelector(".cancel").addEventListener("click", () => this._closeChooser(CANCELLED));
      this._chooser.addEventListener("click", (e) => e.target === this._chooser && this._closeChooser(CANCELLED));
      this._chooser.addEventListener("keydown", (e) => e.key === "Escape" && this._closeChooser(CANCELLED));
      this._load();
    }
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    this._bar.style.display = this._narrow ? "flex" : "none";
  }

  // Opens the app once both the panel and hass are here: with ?ha when Home Assistant has Bluetooth.
  _load() {
    if (!this._frame || !this._hass || this._loading) return;
    this._loading = true;
    const url = new URL(this._panel.config.url);
    this._origin = url.origin;
    this._hass
      .callWS({ type: "sesh/ble/info" })
      .then((info) => info.available, () => false)
      .then((ha) => {
        if (ha) url.searchParams.set("ha", "1");
        this._frame.src = url.href;
      });
  }

  _onMessage = (e) => {
    const m = e.data && e.data.seshBle;
    if (!m || !this._frame || e.source !== this._frame.contentWindow || e.origin !== this._origin) return;
    this._call(m).then(
      (value) => this._post({ reply: [m.id, true, value] }),
      (err) => this._post({ reply: [m.id, false, { name: err.code || err.name || "NetworkError", message: err.message || String(err) }] }),
    );
  };

  _post(msg) {
    if (this._frame && this._frame.contentWindow) this._frame.contentWindow.postMessage({ seshBle: msg }, this._origin);
  }

  async _call({ id, cmd, ...args }) {
    await this._subscribe();
    if (cmd === "requestDevice") return this._choose(args);
    return this._hass.callWS({ type: "sesh/ble", cmd, ...args });
  }

  // Notifications and drops from the rig, for as long as this panel is open.
  _subscribe() {
    if (!this._sub) {
      this._sub = this._hass.connection.subscribeMessage((event) => this._post({ event }), { type: "sesh/ble/subscribe" });
      this._sub.catch(() => (this._sub = undefined));
    }
    return this._sub;
  }

  // The device chooser: what Home Assistant hears that matches the page's filters, refreshed while open.
  _choose({ filters, acceptAllDevices }) {
    this._closeChooser({ code: "InvalidStateError", message: "Another device chooser opened." });
    return new Promise((resolve, reject) => {
      const opened = Date.now();
      const scan = async () => {
        let found;
        try {
          found = await this._hass.callWS({ type: "sesh/ble", cmd: "scan", filters: filters || [], acceptAllDevices: !!acceptAllDevices });
        } catch (err) {
          return this._closeChooser(err);
        }
        if (this._picking !== pick) return;
        this._showDevices(found, Date.now() - opened > HINT_AFTER_MS);
        pick.timer = setTimeout(scan, SCAN_EVERY_MS);
      };
      const pick = { resolve, reject };
      this._picking = pick;
      this._list.replaceChildren();
      this._empty.textContent = "Looking for rigs… Press your rig's button to wake it.";
      this._empty.hidden = false;
      this._chooser.hidden = false;
      this._chooser.querySelector(".cancel").focus();
      scan();
    });
  }

  _showDevices({ devices, scanners }, waited) {
    const focused = this.shadowRoot.activeElement && this.shadowRoot.activeElement.dataset.id;
    this._list.replaceChildren(
      ...devices.map((d) => {
        const li = document.createElement("li");
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.id = d.id;
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = d.name || "Unnamed device";
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = [d.via && `via ${d.via}`, signal(d.rssi)].filter(Boolean).join(" · ") || d.id;
        b.append(name, meta);
        b.addEventListener("click", () => this._closeChooser(null, { id: d.id, name: d.name || undefined }));
        li.append(b);
        return li;
      }),
    );
    if (focused) {
      const again = this._list.querySelector(`[data-id="${CSS.escape(focused)}"]`);
      if (again) again.focus();
    }
    this._empty.hidden = devices.length > 0;
    if (!scanners)
      this._empty.textContent =
        "Home Assistant has no Bluetooth adapter or proxy that can connect. Add one under Settings → Devices & services → Bluetooth (ESPHome proxies need active: true).";
    else if (waited)
      this._empty.textContent =
        "Nothing yet. Wake the rig, close the Puffco and Dr. Dabber apps, and keep it near Home Assistant's Bluetooth adapter or a proxy.";
  }

  _closeChooser(err, device) {
    const pick = this._picking;
    if (!pick) return;
    this._picking = undefined;
    clearTimeout(pick.timer);
    this._chooser.hidden = true;
    if (device) pick.resolve(device);
    else pick.reject(err || CANCELLED);
  }
}

customElements.define("sesh-panel", SeshPanel);
