"""Send to Reefability: push mapped Home Assistant entities with ha_push."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
import logging
import math
from typing import TYPE_CHECKING, Any

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .api import ReefabilityAuthError, ReefabilityClient, ReefabilityError
from .const import (
    DOMAIN,
    MULTI_FIELDS,
    PUSH_BATCH_MAX,
    PUSH_FLUSH_INTERVAL,
    PUSH_QUEUE_MAX,
    PUSH_SNAPSHOT_INTERVAL,
    ROLE_BY_FIELD,
)

if TYPE_CHECKING:
    from . import ReefabilityConfigEntry

_LOGGER = logging.getLogger(__name__)

_SKIP_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN, "", None}


def mapping_targets(mappings: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    """entity_id -> (role, tank_id) from entry.options["mappings"].

    The stored shape is {"<tank_id>": {"<field>": entity_id | [entity_id, ...]}}.
    """
    targets: dict[str, tuple[str, int]] = {}
    for tank_key, fields in (mappings or {}).items():
        try:
            tank_id = int(tank_key)
        except (TypeError, ValueError):
            continue
        if not isinstance(fields, Mapping):
            continue
        for field_key, value in fields.items():
            role = ROLE_BY_FIELD.get(field_key)
            if role is None:
                continue
            entity_ids = value if field_key in MULTI_FIELDS else [value]
            if isinstance(entity_ids, str):
                entity_ids = [entity_ids]
            for entity_id in entity_ids or []:
                if isinstance(entity_id, str) and entity_id:
                    targets[entity_id] = (role, tank_id)
    return targets


def _number(state: str) -> float | None:
    try:
        value = float(state)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def state_payload(
    state: State, role: str, tank_id: int, changed_at: datetime | None = None
) -> dict[str, Any] | None:
    """One ha_push element, or None when the state is not worth sending."""
    if state.state in _SKIP_STATES:
        return None
    when = changed_at or state.last_updated
    return {
        "entity_id": state.entity_id,
        "role": role,
        "name": state.name,
        "unit": state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
        "device_class": state.attributes.get(ATTR_DEVICE_CLASS),
        "state": state.state,
        "value": _number(state.state),
        "changed_at": when.isoformat(),
        "tank_id": tank_id,
    }


class HaPushManager:
    """Queues state changes, flushes every 30 s, snapshots every 15 min."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ReefabilityConfigEntry,
        client: ReefabilityClient,
        targets: dict[str, tuple[str, int]],
    ) -> None:
        """Initialise; call async_start() to begin."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self.targets = targets
        self._queue: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._auth_failed = False
        self.last_result: dict[str, Any] | None = None

    @callback
    def async_start(self) -> None:
        """Listen to the mapped entities and start the timers."""
        # never send Reefability's own entities back (a mapping saved before the pickers hid them, or edited by hand)
        registry = er.async_get(self.hass)
        self.targets = {
            eid: t
            for eid, t in self.targets.items()
            if not ((ent := registry.async_get(eid)) and ent.platform == DOMAIN)
        }
        if not self.targets:
            return
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, list(self.targets), self._async_state_changed
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_flush_timer,
                PUSH_FLUSH_INTERVAL,
                name="reefability push flush",
                cancel_on_shutdown=True,
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_snapshot_timer,
                PUSH_SNAPSHOT_INTERVAL,
                name="reefability push snapshot",
                cancel_on_shutdown=True,
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._async_started))

    @callback
    def async_stop(self) -> None:
        """Stop listening (pending changes are dropped)."""
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        self.entry.async_create_background_task(
            self.hass, self.async_snapshot(), "reefability push snapshot"
        )

    async def _async_flush_timer(self, _now: datetime) -> None:
        await self.async_flush()

    async def _async_snapshot_timer(self, _now: datetime) -> None:
        await self.async_snapshot()

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        target = self.targets.get(event.data["entity_id"])
        if new_state is None or target is None:
            return
        role, tank_id = target
        payload = state_payload(new_state, role, tank_id)
        if payload is None:
            return
        old_state = event.data.get("old_state")
        self._enqueue(payload)
        # A leak sensor turning wet should not wait for the 30 s flush.
        if role == "leak" and (old_state is None or old_state.state != new_state.state):
            self.entry.async_create_background_task(
                self.hass, self.async_flush(), "reefability leak push"
            )

    def _enqueue(self, payload: dict[str, Any]) -> None:
        entity_id = payload["entity_id"]
        if entity_id not in self._queue and len(self._queue) >= PUSH_QUEUE_MAX:
            return
        self._queue[entity_id] = payload

    async def async_snapshot(self) -> None:
        """Queue every mapped entity's current state, then flush.

        Readings are stamped now, so the server stores one at least every
        15 minutes even when the value has not changed (it thins to 10 min).
        A leak sensor keeps its real last-changed time.
        """
        now = dt_util.utcnow()
        for entity_id, (role, tank_id) in self.targets.items():
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            when = state.last_changed if role == "leak" else now
            payload = state_payload(state, role, tank_id, when)
            if payload is not None:
                self._enqueue(payload)
        await self.async_flush()

    async def async_flush(self) -> None:
        """Send the queue in batches of at most 200."""
        if self._auth_failed:
            return
        async with self._lock:
            if not self._queue:
                return
            pending = list(self._queue.values())
            self._queue.clear()
            for start in range(0, len(pending), PUSH_BATCH_MAX):
                batch = pending[start : start + PUSH_BATCH_MAX]
                try:
                    self.last_result = await self.client.async_push(batch)
                except ReefabilityAuthError:
                    self._auth_failed = True
                    self.entry.async_start_reauth(self.hass)
                    return
                except ReefabilityError as err:
                    _LOGGER.warning("Could not send states to Reefability: %s", err)
                    # Keep what failed for the next flush unless newer data arrived.
                    for payload in pending[start:]:
                        if payload["entity_id"] not in self._queue:
                            self._enqueue(payload)
                    return
                _LOGGER.debug(
                    "ha_push sent %s states: %s", len(batch), self.last_result
                )
