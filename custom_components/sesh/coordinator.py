"""Polls the Sesh API and turns new dabs and cheers into Home Assistant events.

Dabs come from the leaderboard: anyone's "today" going up is a dab (the same count the app shows).
Friends' dabs and cheers also arrive instantly over the account's ntfy topic, the one the phone
app's dab alerts use. Nothing here reads GET /live: that hands each cheers out once, and the
phone's in-app banner should keep getting them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SeshApi, SeshAuthError, SeshError
from .const import DOMAIN, EVENT_CHEERS, EVENT_DAB, KINDS, POLL, SYNC_EVERY

_LOG = logging.getLogger(__name__)

# An ntfy alert and the leaderboard both see a friend's dab; the leaderboard step that follows an
# alert within this window is the same dab.
CREDIT_FOR_S = 180


@dataclass
class SeshData:
    me: str
    rows: list[dict]
    today: dict[str, int]
    rigs: list[dict] = field(default_factory=list)
    last_session: dict | None = None


class SeshCoordinator(DataUpdateCoordinator[SeshData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SeshApi) -> None:
        super().__init__(hass, _LOG, name=DOMAIN, update_interval=POLL, config_entry=entry)
        self.api = api
        self._day: str | None = None
        self._seen: dict[str, int] | None = None  # name -> today, from the last poll
        self._credit: dict[str, list[float]] = {}  # name -> times ntfy already announced a dab
        self._sync_in = 0
        self._rigs: list[dict] = []
        self._last_session: dict | None = None
        self._listeners_ev: list[Callable[[str, dict], None]] = []

    # ---- events -------------------------------------------------------------------------

    def on_event(self, fn: Callable[[str, dict], None]) -> Callable[[], None]:
        """Event entities subscribe here; returns the unsubscribe."""
        self._listeners_ev.append(fn)
        return lambda: self._listeners_ev.remove(fn)

    @callback
    def _fire(self, kind: str, data: dict) -> None:
        self.hass.bus.async_fire(EVENT_DAB if kind in ("my_dab", "friend_dab") else EVENT_CHEERS, {"type": kind, **data})
        for fn in list(self._listeners_ev):
            fn(kind, data)

    def _dab(self, name: str, me: bool, today: int | None, source: str) -> None:
        self._fire("my_dab" if me else "friend_dab", {"name": name, "me": me, "today": today, "source": source})

    # ---- polling ------------------------------------------------------------------------

    async def _async_update_data(self) -> SeshData:
        start = dt_util.start_of_local_day()
        day = start.date().isoformat()
        since = int(start.timestamp() * 1000)
        offset = dt_util.now().utcoffset()
        tz_min = int(offset.total_seconds() // 60) if offset else 0
        try:
            rows = await self.api.leaderboard(since)
            days = await self.api.dabs(since, tz_min)
            self._sync_in -= 1
            if self._sync_in <= 0:
                await self._sync()
                self._sync_in = SYNC_EVERY
        except SeshAuthError as e:
            raise ConfigEntryAuthFailed(str(e)) from e
        except SeshError as e:
            raise UpdateFailed(str(e)) from e

        today = {k: 0 for k in KINDS}
        for d in days:
            if d.get("day") == day:
                k = d.get("kind") or "other"
                today[k] = today.get(k, 0) + int(d.get("n") or 0)

        now_counts = {r["name"]: int(r.get("today") or 0) for r in rows}
        if self._seen is not None and self._day == day:
            for r in rows:
                step = now_counts[r["name"]] - self._seen.get(r["name"], now_counts[r["name"]])
                credit = [t for t in self._credit.get(r["name"], []) if time.time() - t < CREDIT_FOR_S]
                while step > 0:
                    step -= 1
                    if credit and not r.get("me"):
                        credit.pop(0)
                        continue
                    self._dab(r["name"], bool(r.get("me")), now_counts[r["name"]], "leaderboard")
                    if r.get("me"):
                        # The app logs the session when the dab ends: read it in about two minutes.
                        self._sync_in = min(self._sync_in, 8)
                self._credit[r["name"]] = credit
        self._seen, self._day = now_counts, day

        me_row = next((r for r in rows if r.get("me")), None)
        return SeshData(me=me_row["name"] if me_row else "", rows=rows, today=today, rigs=self._rigs, last_session=self._last_session)

    async def _sync(self) -> None:
        # Sessions carry temperature traces, so only ask for recent ones; a week back on the first read.
        back = 2 if self._last_session else 7
        data = await self.api.sync(int((time.time() - back * 86400) * 1000))
        self._rigs = data.get("devices") or []
        sessions = data.get("sessions") or []
        if sessions:
            last = max(sessions, key=lambda s: s.get("at") or 0)
            self._last_session = {k: v for k, v in last.items() if k != "trace"}

    # ---- ntfy: instant friends' dabs and cheers -------------------------------------------

    async def listen_ntfy(self) -> None:
        """Runs for the life of the config entry. Reconnects with backoff."""
        session = self.api._session  # noqa: SLF001 - the same HA-managed session
        last_id: str | None = None
        wait = 5
        while True:
            try:
                info = await self.api.alerts()
                url = f"{info['server'].rstrip('/')}/{info['topic']}/json"
                params = {"since": last_id} if last_id else {}
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=None, sock_read=120)) as resp:
                    resp.raise_for_status()
                    wait = 5
                    async for raw in resp.content:
                        msg = _parse(raw)
                        if not msg or msg.get("event") != "message":
                            continue
                        last_id = msg.get("id") or last_id
                        self._ntfy(msg)
            except asyncio.CancelledError:
                raise
            except SeshAuthError:
                return  # polling raises the re-auth
            except (SeshError, aiohttp.ClientError, asyncio.TimeoutError, KeyError) as e:
                _LOG.debug("ntfy stream dropped: %s", e)
            await asyncio.sleep(wait)
            wait = min(wait * 2, 300)

    @callback
    def _ntfy(self, msg: dict) -> None:
        title = str(msg.get("title") or "")
        tags = msg.get("tags") or []
        if "beers" in tags and title.endswith(" says cheers"):
            self._fire("cheers", {"name": title[: -len(" says cheers")]})
        elif "fire" in tags and title.endswith(" is dabbing"):
            name = title[: -len(" is dabbing")]
            self._credit.setdefault(name, []).append(time.time())
            self._dab(name, False, None, "alert")


def _parse(raw: bytes) -> dict[str, Any] | None:
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None
