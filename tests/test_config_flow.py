"""Config flow, reauth and options flow."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.reefability.config_flow import token_unique_id
from custom_components.reefability.const import (
    API_URL,
    CONF_MAPPINGS,
    CONF_TOKEN,
    DOMAIN,
)

from .conftest import TOKEN, FakeApi, setup_entry


async def test_user_flow_success(hass: HomeAssistant, api: FakeApi) -> None:
    """A good token creates the entry, titled Reefability, unique id = hash."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: f"  Glance {TOKEN.upper()} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Reefability"
    assert result["data"] == {CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == token_unique_id(TOKEN)
    assert api.posts_for("whoami")


async def test_user_flow_invalid_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """401 from whoami shows invalid_auth and the form again."""
    aioclient_mock.post(API_URL, status=401, json={"ok": False, "error": "revoked"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_bad_format(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token of the wrong shape is refused before any request."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "not-a-token"}
    )
    assert result["errors"] == {CONF_TOKEN: "invalid_token"}
    assert aioclient_mock.call_count == 0


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A server error shows cannot_connect."""
    aioclient_mock.post(API_URL, status=500, json={"ok": False, "error": "server"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: TOKEN}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(hass: HomeAssistant, api: FakeApi) -> None:
    """The same token twice aborts."""
    MockConfigEntry(
        domain=DOMAIN, data={CONF_TOKEN: TOKEN}, unique_id=token_unique_id(TOKEN)
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_on_401(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """A 401 on the poll starts reauth; a new token fixes the entry."""
    api.get_status = 401
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1 and flows[0]["context"]["source"] == "reauth"

    api.get_status = 200
    new_token = "f" * 48
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_TOKEN: new_token}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == new_token
    assert config_entry.unique_id == token_unique_id(new_token)


async def test_options_flow_stores_mapping(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """Pick a tank, then entities per role; the mapping lands in options."""
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tank": "2"}
    )
    assert result["step_id"] == "entities"
    assert result["description_placeholders"] == {"tank": "Frag Tank"}
    user_input: dict[str, Any] = {
        "probes": {"param_temperature": "sensor.sump_temp", "param_ph": "sensor.ph"},
        "room": {"room_humidity": "sensor.room_rh"},
        "more": {"leak": ["binary_sensor.sump_leak"], "power": [], "other": []},
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_MAPPINGS] == {
        "2": {
            "param_temperature": "sensor.sump_temp",
            "param_ph": "sensor.ph",
            "room_humidity": "sensor.room_rh",
            "leak": ["binary_sensor.sump_leak"],
        }
    }
    # The entry reloaded with the new mapping and pushed the (missing) states: nothing.
    assert config_entry.runtime_data.push.targets == {
        "sensor.sump_temp": ("param:Temperature", 2),
        "sensor.ph": ("param:pH", 2),
        "sensor.room_rh": ("room_humidity", 2),
        "binary_sensor.sump_leak": ("leak", 2),
    }

    # Running it again for the other tank keeps the first tank's mapping.
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tank": "1"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"probes": {"param_salinity": "sensor.sal"}, "room": {}, "more": {}},
    )
    await hass.async_block_till_done()
    assert set(config_entry.options[CONF_MAPPINGS]) == {"1", "2"}


async def test_options_flow_single_tank_skips_choice(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """With one tank the flow opens straight on the entities step."""
    api.get_payload["tanks"] = api.get_payload["tanks"][:1]
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "entities"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"probes": {}, "room": {"co2": "sensor.co2"}, "more": {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_MAPPINGS] == {"1": {"co2": "sensor.co2"}}
