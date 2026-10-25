"""The Sesh API, the same one the Sesh app talks to."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class SeshError(Exception):
    """The API could not be reached, or answered with an error."""


class SeshAuthError(SeshError):
    """The token is no good any more: sign in again."""


class SeshApi:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str | None = None) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self.token = token

    async def _call(self, method: str, path: str, **kw: Any) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with self._session.request(
                method, f"{self.url}/{path}", headers=headers, timeout=aiohttp.ClientTimeout(total=20), **kw
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status == 401 and self.token:
                    raise SeshAuthError(data.get("error") if isinstance(data, dict) else "sign in")
                if resp.status >= 400 and not (isinstance(data, dict) and data.get("needPin")):
                    raise SeshError(data.get("error") if isinstance(data, dict) else f"HTTP {resp.status}")
                return data if isinstance(data, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise SeshError(str(e) or type(e).__name__) from e

    async def sign_in(self, name: str, pin: str) -> dict:
        return await self._call("POST", "auth/name", json={"name": name, "pin": pin})

    async def logout(self) -> None:
        await self._call("POST", "auth/logout")

    async def leaderboard(self, since_ms: int) -> list[dict]:
        return (await self._call("GET", "leaderboard", params={"since": since_ms})).get("rows", [])

    async def dabs(self, since_ms: int, tz_min: int) -> list[dict]:
        return (await self._call("GET", "dabs", params={"since": since_ms, "tz": tz_min})).get("days", [])

    async def sync(self, since_ms: int) -> dict:
        return await self._call("GET", "sync", params={"since": since_ms})

    async def alerts(self) -> dict:
        return await self._call("GET", "alerts")

    async def cheers(self, to: str) -> None:
        await self._call("POST", "cheers", json={"to": to})
