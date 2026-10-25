"""Sign in with the name and PIN from the Sesh app (Menu shows the PIN)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SeshApi, SeshError
from .const import CONF_PIN, CONF_TOKEN, DEFAULT_URL, DOMAIN


def _key(name: str) -> str:
    # The account a name belongs to, as the API matches it: case and spacing don't matter.
    return " ".join(name.split()).lower()


class SeshConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _sign_in(self, url: str, name: str, pin: str) -> tuple[dict | None, str | None]:
        api = SeshApi(async_get_clientsession(self.hass), url)
        try:
            res = await api.sign_in(name, pin)
        except SeshError as e:
            return None, "wrong_pin" if "PIN" in str(e) else "cannot_connect"
        if res.get("needPin"):
            return None, "wrong_pin"
        if res.get("created"):
            # The API makes a new account for a name it hasn't seen: that was a typo, not a sign-in.
            api.token = res.get("token")
            try:
                await api.logout()
            except SeshError:
                pass
            return None, "no_account"
        return res, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            res, err = await self._sign_in(url, user_input[CONF_NAME], user_input[CONF_PIN])
            if res:
                await self.async_set_unique_id(_key(res["username"]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Sesh ({res['username']})",
                    data={CONF_URL: url, CONF_NAME: res["username"], CONF_TOKEN: res["token"]},
                )
            errors["base"] = err or "unknown"
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=(user_input or {}).get(CONF_NAME, "")): str,
                vol.Required(CONF_PIN): str,
                vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, DEFAULT_URL)): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            res, err = await self._sign_in(entry.data[CONF_URL], entry.data[CONF_NAME], user_input[CONF_PIN])
            if res:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: res["token"]})
            errors["base"] = err or "unknown"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PIN): str}),
            description_placeholders={"name": entry.data[CONF_NAME]},
            errors=errors,
        )
