// The Sesh app in Home Assistant's sidebar. On a phone, Home Assistant hides its sidebar, so a
// thin bar with the menu button stays on top to get back to it.
class SeshPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this._render();
  }

  set panel(panel) {
    this._panel = panel;
    this._render();
  }

  _render() {
    if (!this._panel) return;
    if (!this._frame) {
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          :host { display: flex; flex-direction: column; height: 100vh; height: 100dvh; background: #0e0e0f; }
          .bar { display: none; align-items: center; height: 56px; padding: 0 4px; color: #fff; background: #0e0e0f; }
          .bar span { font-size: 20px; margin-left: 8px; }
          iframe { flex: 1; width: 100%; border: 0; display: block; }
        </style>
        <div class="bar"><ha-menu-button></ha-menu-button><span>Sesh</span></div>
        <iframe title="Sesh" allow="fullscreen"></iframe>`;
      this._bar = root.querySelector(".bar");
      this._menu = root.querySelector("ha-menu-button");
      this._frame = root.querySelector("iframe");
      this._frame.src = this._panel.config.url;
    }
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    this._bar.style.display = this._narrow ? "flex" : "none";
  }
}

customElements.define("sesh-panel", SeshPanel);
