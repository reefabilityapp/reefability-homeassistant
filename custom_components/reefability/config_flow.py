"""Config and options flows for Reefability."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    ReefabilityAuthError,
    ReefabilityClient,
    ReefabilityConnectionError,
    ReefabilityError,
)
from .const import (
    CONF_MAPPINGS,
    CONF_TOKEN,
    DOMAIN,
    PROBE_ROLES,
    SECTION_MORE,
    SECTION_PROBES,
    SECTION_ROOM,
)

_LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"^[0-9a-f]{48}$")
CONF_TANK = "tank"

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


def normalize_token(raw: str) -> str:
    """Accept a pasted token with spaces or a leading 'Glance '."""
    token = (raw or "").strip()
    if token.lower().startswith("glance "):
        token = token[7:]
    return re.sub(r"\s+", "", token).lower()


def token_unique_id(token: str) -> str:
    """The entry's unique id: a hash, so the token itself is never an id."""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


class ReefabilityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Paste a token from the Reefability app."""

    VERSION = 1

    async def _async_validate(self, token: str) -> dict[str, str]:
        if not _TOKEN_RE.match(token):
            return {CONF_TOKEN: "invalid_token"}
        client = ReefabilityClient(async_get_clientsession(self.hass), token)
        try:
            tanks = await client.async_whoami()
        except ReefabilityAuthError:
            return {"base": "invalid_auth"}
        except ReefabilityConnectionError:
            return {"base": "cannot_connect"}
        except ReefabilityError:
            _LOGGER.exception("Unexpected reply from Reefability")
            return {"base": "unknown"}
        if not tanks:
            return {"base": "no_tanks"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The only step: the token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = normalize_token(user_input[CONF_TOKEN])
            errors = await self._async_validate(token)
            if not errors:
                await self.async_set_unique_id(token_unique_id(token))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Reefability", data={CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="user", data_schema=TOKEN_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The token was revoked."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = normalize_token(user_input[CONF_TOKEN])
            errors = await self._async_validate(token)
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    unique_id=token_unique_id(token),
                    data_updates={CONF_TOKEN: token},
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ReefabilityOptionsFlow:
        """Send to Reefability."""
        return ReefabilityOptionsFlow()


def _entities_schema(exclude: list[str] | None = None) -> vol.Schema:
    """The pickers. `exclude`: this integration's own entities, which never go back to Reefability: a Reefability
    leak or reading sensor sent back in would loop (an alert re-sent as a new alert)."""
    ex = {"exclude_entities": exclude} if exclude else {}
    probe_sensor = EntitySelector(
        EntitySelectorConfig(domain=["sensor", "number", "input_number"], **ex)
    )
    probes = {vol.Optional(key): probe_sensor for key, _ in PROBE_ROLES}
    room = {
        vol.Optional("room_temperature"): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature", **ex)
        ),
        vol.Optional("room_humidity"): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="humidity", **ex)
        ),
        vol.Optional("co2"): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="carbon_dioxide", **ex)
        ),
    }
    more = {
        vol.Optional("leak"): EntitySelector(
            EntitySelectorConfig(
                domain="binary_sensor", device_class="moisture", multiple=True, **ex
            )
        ),
        vol.Optional("power"): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "switch"], multiple=True, **ex)
        ),
        vol.Optional("other"): EntitySelector(EntitySelectorConfig(multiple=True, **ex)),
    }
    return vol.Schema(
        {
            vol.Required(SECTION_PROBES): section(
                vol.Schema(probes), {"collapsed": False}
            ),
            vol.Required(SECTION_ROOM): section(vol.Schema(room), {"collapsed": False}),
            vol.Required(SECTION_MORE): section(vol.Schema(more), {"collapsed": False}),
        }
    )


_SECTION_OF: dict[str, str] = {
    **{key: SECTION_PROBES for key, _ in PROBE_ROLES},
    "room_temperature": SECTION_ROOM,
    "room_humidity": SECTION_ROOM,
    "co2": SECTION_ROOM,
    "leak": SECTION_MORE,
    "power": SECTION_MORE,
    "other": SECTION_MORE,
}


def own_entities(hass, entry_id: str) -> list[str]:
    """Every entity this Reefability entry created (its sensors, binary sensors and buttons)."""
    return [e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id)]


class ReefabilityOptionsFlow(OptionsFlow):
    """Pick Home Assistant entities to send to a Reefability tank."""

    def __init__(self) -> None:
        """Initialise."""
        self._tank_id: int | None = None
        self._tanks: dict[int, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the tank (skipped when there is only one)."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        data = entry.runtime_data.coordinator.data or {}
        self._tanks = {tid: tank.name for tid, tank in data.items()}
        if not self._tanks:
            return self.async_abort(reason="no_tanks")
        if len(self._tanks) == 1:
            self._tank_id = next(iter(self._tanks))
            return await self.async_step_entities()
        if user_input is not None:
            self._tank_id = int(user_input[CONF_TANK])
            return await self.async_step_entities()
        mapped = entry.options.get(CONF_MAPPINGS, {})
        options = [
            SelectOptionDict(
                value=str(tid),
                label=f"{name} (linked)" if mapped.get(str(tid)) else name,
            )
            for tid, name in self._tanks.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TANK, default=str(next(iter(self._tanks)))
                ): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entities per role for the chosen tank."""
        assert self._tank_id is not None
        tank_key = str(self._tank_id)
        options = copy.deepcopy(dict(self.config_entry.options))
        mappings: dict[str, Any] = dict(options.get(CONF_MAPPINGS, {}))
        if user_input is not None:
            fields: dict[str, Any] = {}
            for section_key in (SECTION_PROBES, SECTION_ROOM, SECTION_MORE):
                for key, value in (user_input.get(section_key) or {}).items():
                    if value:
                        fields[key] = value
            if fields:
                mappings[tank_key] = fields
            else:
                mappings.pop(tank_key, None)
            options[CONF_MAPPINGS] = mappings
            return self.async_create_entry(data=options)

        current = mappings.get(tank_key, {})
        suggested: dict[str, dict[str, Any]] = {
            SECTION_PROBES: {},
            SECTION_ROOM: {},
            SECTION_MORE: {},
        }
        for key, value in current.items():
            if key in _SECTION_OF:
                suggested[_SECTION_OF[key]][key] = value
        return self.async_show_form(
            step_id="entities",
            data_schema=self.add_suggested_values_to_schema(
                _entities_schema(own_entities(self.hass, self.config_entry.entry_id)), suggested
            ),
            description_placeholders={"tank": self._tanks.get(self._tank_id, tank_key)},
        )
