"""Small async client for the Reefability assistant API."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import API_URL

DEFAULT_TIMEOUT = 20
ASK_TIMEOUT = 40  # the Reefer AI answer can take up to ~15 s server side


class ReefabilityError(Exception):
    """Base error."""


class ReefabilityAuthError(ReefabilityError):
    """The token is missing, invalid or revoked (HTTP 401)."""


class ReefabilityConnectionError(ReefabilityError):
    """Network trouble or a server error."""


class ReefabilityResponseError(ReefabilityError):
    """The API answered ok:false."""

    def __init__(self, error: str | None, say: str | None) -> None:
        """Keep the machine code and the human sentence."""
        super().__init__(say or error or "unknown error")
        self.error = error
        self.say = say


class ReefabilityClient:
    """Talks to https://.../functions/v1/assistant with a Glance token."""

    def __init__(
        self, session: aiohttp.ClientSession, token: str, url: str = API_URL
    ) -> None:
        """Initialise with Home Assistant's shared aiohttp session."""
        self._session = session
        self._token = token
        self._url = url

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Glance {self._token}"}

    async def _request(
        self, method: str, payload: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                self._url,
                headers=self._headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 401:
                    raise ReefabilityAuthError("token invalid or revoked")
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise ReefabilityConnectionError(
                        f"unexpected reply (HTTP {resp.status})"
                    ) from err
                if resp.status >= 500:
                    say = data.get("say") if isinstance(data, dict) else None
                    raise ReefabilityConnectionError(say or f"HTTP {resp.status}")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ReefabilityConnectionError(str(err) or type(err).__name__) from err
        if not isinstance(data, dict):
            raise ReefabilityConnectionError("unexpected reply")
        if data.get("ok") is False:
            raise ReefabilityResponseError(data.get("error"), data.get("say"))
        return data

    async def async_get_tanks(self) -> list[dict[str, Any]]:
        """GET the per-tank summaries."""
        data = await self._request("GET", None, DEFAULT_TIMEOUT)
        tanks = data.get("tanks")
        return (
            [t for t in tanks if isinstance(t, dict)] if isinstance(tanks, list) else []
        )

    async def async_action(
        self, action: str, *, timeout: float = DEFAULT_TIMEOUT, **fields: Any
    ) -> dict[str, Any]:
        """POST {action, ...fields}."""
        return await self._request("POST", {"action": action, **fields}, timeout)

    async def async_whoami(self) -> list[dict[str, Any]]:
        """Return [{id, name}] for the token's tanks."""
        data = await self.async_action("whoami")
        tanks = data.get("tanks")
        return (
            [t for t in tanks if isinstance(t, dict)] if isinstance(tanks, list) else []
        )

    async def async_log(
        self, tank_id: int, parameter: str, value: float, unit: str | None
    ) -> dict[str, Any]:
        """Save a reading (confirm:true, so it is stored right away)."""
        fields: dict[str, Any] = {
            "tank": tank_id,
            "parameter": parameter,
            "value": value,
            "confirm": True,
        }
        if unit:
            fields["unit"] = unit
        return await self.async_action("log", **fields)

    async def async_complete_task(self, task_id: int) -> dict[str, Any]:
        """Tick a task off."""
        return await self.async_action("complete_task", task_id=task_id)

    async def async_feed(self, tank_id: int, on: bool) -> dict[str, Any]:
        """Start or stop feed mode (needs a Reefability Display)."""
        return await self.async_action("feed", tank=tank_id, on=on)

    async def async_ask(self, tank_id: int, question: str) -> dict[str, Any]:
        """Ask Reefer AI a short question."""
        return await self.async_action(
            "ask", timeout=ASK_TIMEOUT, tank=tank_id, question=question
        )

    async def async_push(self, states: list[dict[str, Any]]) -> dict[str, Any]:
        """Send Home Assistant states (at most 200 per call)."""
        return await self.async_action("ha_push", states=states[:200])
