"""Reefability services: log_reading, complete_task, ask."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import ReefabilityError, ReefabilityResponseError
from .const import (
    ATTR_PARAMETER,
    ATTR_QUESTION,
    ATTR_TANK_ID,
    ATTR_TASK_ID,
    ATTR_UNIT,
    ATTR_VALUE,
    DOMAIN,
    LOG_PARAMETERS,
    SERVICE_ASK,
    SERVICE_COMPLETE_TASK,
    SERVICE_LOG_READING,
)
from .errors import as_ha_error

if TYPE_CHECKING:
    from . import ReefabilityConfigEntry


def _device_id(value: Any) -> str:
    """Accept one device id, or a one-item list (what a device selector may send)."""
    if isinstance(value, list):
        if len(value) != 1:
            raise vol.Invalid("pick one tank")
        value = value[0]
    return cv.string(value)


_TANK_FIELDS = {
    vol.Optional(ATTR_DEVICE_ID): _device_id,
    vol.Optional(ATTR_TANK_ID): vol.Coerce(int),
}


_PARAM_BY_KEY = {p.lower(): p for p in LOG_PARAMETERS}


def _parameter(value: object) -> str:
    """The dropdown sends lowercase keys (alkalinity); YAML may say Alkalinity. Both become the Reefability name."""
    key = str(value).strip().lower()
    if key not in _PARAM_BY_KEY:
        raise vol.Invalid(f"unknown parameter: {value}")
    return _PARAM_BY_KEY[key]

LOG_READING_SCHEMA = vol.Schema(
    {
        **_TANK_FIELDS,
        vol.Required(ATTR_PARAMETER): _parameter,
        vol.Required(ATTR_VALUE): vol.Coerce(float),
        vol.Optional(ATTR_UNIT): cv.string,
    }
)
COMPLETE_TASK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): _device_id,
        vol.Required(ATTR_TASK_ID): vol.Coerce(int),
    }
)
ASK_SCHEMA = vol.Schema(
    {
        **_TANK_FIELDS,
        vol.Required(ATTR_QUESTION): vol.All(cv.string, vol.Length(min=3, max=500)),
    }
)


def _loaded_entries(hass: HomeAssistant) -> list[ReefabilityConfigEntry]:
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]


def _entry_for_device(
    hass: HomeAssistant, device_id: str
) -> tuple[ReefabilityConfigEntry, int]:
    device = dr.async_get(hass).async_get(device_id)
    tank_id: int | None = None
    if device is not None:
        for domain, ident in device.identifiers:
            if domain == DOMAIN:
                try:
                    tank_id = int(ident)
                except ValueError:
                    continue
                break
    if device is None or tank_id is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="unknown_tank"
        )
    for entry in _loaded_entries(hass):
        if entry.entry_id in device.config_entries:
            return entry, tank_id
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_loaded"
    )


def resolve_tank(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[ReefabilityConfigEntry, int]:
    """Find the account and tank a service call means."""
    if data.get(ATTR_DEVICE_ID):
        return _entry_for_device(hass, data[ATTR_DEVICE_ID])
    entries = _loaded_entries(hass)
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )
    if (tank_id := data.get(ATTR_TANK_ID)) is not None:
        for entry in entries:
            if tank_id in (entry.runtime_data.coordinator.data or {}):
                return entry, tank_id
        if len(entries) == 1:
            return entries[0], tank_id
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="unknown_tank"
        )
    tanks = [
        (entry, tid)
        for entry in entries
        for tid in (entry.runtime_data.coordinator.data or {})
    ]
    if len(tanks) == 1:
        return tanks[0]
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="tank_required"
    )


async def _async_log_reading(hass: HomeAssistant, call: ServiceCall) -> None:
    entry, tank_id = resolve_tank(hass, call.data)
    data = entry.runtime_data
    try:
        await data.client.async_log(
            tank_id,
            call.data[ATTR_PARAMETER],
            call.data[ATTR_VALUE],
            call.data.get(ATTR_UNIT),
        )
    except ReefabilityError as err:
        raise as_ha_error(hass, entry, err) from err
    await data.coordinator.async_request_refresh()


async def _async_complete_task(hass: HomeAssistant, call: ServiceCall) -> None:
    task_id: int = call.data[ATTR_TASK_ID]
    if call.data.get(ATTR_DEVICE_ID):
        entries = [_entry_for_device(hass, call.data[ATTR_DEVICE_ID])[0]]
    else:
        entries = _loaded_entries(hass)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )

        # Try the account that listed this task first.
        def _lists(entry: ReefabilityConfigEntry) -> bool:
            return any(
                t.get("id") == task_id
                for tank in (entry.runtime_data.coordinator.data or {}).values()
                for t in tank.task_items
            )

        entries.sort(key=lambda e: not _lists(e))
    last_error: ReefabilityError | None = None
    for entry in entries:
        try:
            await entry.runtime_data.client.async_complete_task(task_id)
        except ReefabilityResponseError as err:
            last_error = err
            if err.error == "not_found":
                continue
            raise as_ha_error(hass, entry, err) from err
        except ReefabilityError as err:
            raise as_ha_error(hass, entry, err) from err
        await entry.runtime_data.coordinator.async_request_refresh()
        return
    assert last_error is not None
    raise as_ha_error(hass, entries[-1], last_error) from last_error


async def _async_ask(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry, tank_id = resolve_tank(hass, call.data)
    try:
        reply = await entry.runtime_data.client.async_ask(
            tank_id, call.data[ATTR_QUESTION]
        )
    except ReefabilityError as err:
        raise as_ha_error(hass, entry, err) from err
    answer = reply.get("text") or reply.get("say") or ""
    return {"answer": str(answer)}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services (once, for every account)."""

    async def log_reading(call: ServiceCall) -> None:
        await _async_log_reading(hass, call)

    async def complete_task(call: ServiceCall) -> None:
        await _async_complete_task(hass, call)

    async def ask(call: ServiceCall) -> ServiceResponse:
        return await _async_ask(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_LOG_READING, log_reading, schema=LOG_READING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COMPLETE_TASK, complete_task, schema=COMPLETE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ASK,
        ask,
        schema=ASK_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
