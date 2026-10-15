"""Polling coordinator for Reefability."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ReefabilityAuthError,
    ReefabilityClient,
    ReefabilityError,
)
from .const import DOMAIN, SCAN_INTERVAL

if TYPE_CHECKING:
    from . import ReefabilityConfigEntry

_LOGGER = logging.getLogger(__name__)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class Tank:
    """One tank's summary, parsed defensively from the GET reply."""

    id: int
    name: str
    readings: dict[str, dict[str, Any]] = field(default_factory=dict)
    out_of_range: int | None = None
    tasks_today: int | None = None
    tasks_overdue: int | None = None
    task_items: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Tank | None:
        """Build from one element of the GET reply's `tanks` list."""
        tank_id = _int(raw.get("id"))
        if tank_id is None:
            inner = raw.get("tank")
            tank_id = _int(inner.get("id")) if isinstance(inner, dict) else None
        if tank_id is None:
            return None
        name = str(raw.get("name") or f"Tank {tank_id}")
        readings: dict[str, dict[str, Any]] = {}
        for reading in raw.get("readings") or []:
            if isinstance(reading, dict) and reading.get("name"):
                readings[str(reading["name"])] = reading
        tasks = raw.get("tasks") if isinstance(raw.get("tasks"), dict) else {}
        items = [t for t in (tasks.get("items") or []) if isinstance(t, dict)]
        alerts = [a for a in (raw.get("alerts") or []) if isinstance(a, dict)]
        return cls(
            id=tank_id,
            name=name,
            readings=readings,
            out_of_range=_int(raw.get("out_of_range")),
            tasks_today=_int(tasks.get("today")),
            tasks_overdue=_int(tasks.get("overdue")),
            task_items=items,
            alerts=alerts,
        )

    def alerts_of(self, kind: str) -> list[dict[str, Any]]:
        """Alerts of one kind ("leak", "offline")."""
        return [a for a in self.alerts if a.get("kind") == kind]

    def out_of_range_names(self) -> list[str]:
        """Reading names whose status is high or low."""
        return [
            name
            for name, r in self.readings.items()
            if r.get("status") in ("high", "low")
        ]


class ReefabilityCoordinator(DataUpdateCoordinator[dict[int, Tank]]):
    """Fetches every tank's summary every 5 minutes."""

    config_entry: ReefabilityConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ReefabilityConfigEntry,
        client: ReefabilityClient,
    ) -> None:
        """Initialise."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[int, Tank]:
        try:
            raw = await self.client.async_get_tanks()
        except ReefabilityAuthError as err:
            raise ConfigEntryAuthFailed("The Reefability token was revoked") from err
        except ReefabilityError as err:
            raise UpdateFailed(f"Reefability is not answering: {err}") from err
        tanks: dict[int, Tank] = {}
        for item in raw:
            tank = Tank.from_api(item)
            if tank is not None:
                tanks[tank.id] = tank
        return tanks
