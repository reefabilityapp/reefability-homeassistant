"""Services and buttons call the right API actions."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.reefability.const import DOMAIN

from .conftest import FakeApi, setup_entry, tank_device


def _device_id(hass: HomeAssistant, entry: MockConfigEntry, tank_id: int) -> str:
    return tank_device(hass, entry, tank_id).id


async def test_log_reading_by_device(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """log_reading sends action log with confirm:true for the device's tank."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "log_reading",
        {
            "device_id": _device_id(hass, config_entry, 2),
            "parameter": "Alkalinity",
            "value": 8.2,
            "unit": "dKH",
        },
        blocking=True,
    )
    assert api.posts_for("log") == [
        {
            "action": "log",
            "tank": 2,
            "parameter": "Alkalinity",
            "value": 8.2,
            "unit": "dKH",
            "confirm": True,
        }
    ]


async def test_log_reading_by_tank_id_and_error(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """tank_id works too; ok:false raises with the server's sentence."""
    await setup_entry(hass, config_entry)
    api.replies["log"] = lambda b: (
        200,
        {
            "ok": False,
            "error": "implausible",
            "say": "50 for calcium doesn't look right.",
        },
    )
    with pytest.raises(HomeAssistantError, match="doesn't look right"):
        await hass.services.async_call(
            DOMAIN,
            "log_reading",
            {"tank_id": 1, "parameter": "Calcium", "value": 50},
            blocking=True,
        )
    assert api.posts_for("log")[0]["tank"] == 1
    assert "unit" not in api.posts_for("log")[0]


async def test_log_reading_needs_tank_with_two_tanks(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """Without a tank and with two tanks the call is refused."""
    await setup_entry(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "log_reading",
            {"parameter": "Calcium", "value": 420},
            blocking=True,
        )
    assert not api.posts_for("log")


async def test_complete_task(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """complete_task sends the task id."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call(
        DOMAIN, "complete_task", {"task_id": 1001}, blocking=True
    )
    assert api.posts_for("complete_task") == [
        {"action": "complete_task", "task_id": 1001}
    ]


async def test_ask_returns_answer(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """ask returns {answer} as the service response."""
    await setup_entry(hass, config_entry)
    response = await hass.services.async_call(
        DOMAIN,
        "ask",
        {
            "device_id": _device_id(hass, config_entry, 1),
            "question": "Is my alkalinity stable?",
        },
        blocking=True,
        return_response=True,
    )
    assert response == {"answer": "Alkalinity looks steady."}
    assert api.posts_for("ask") == [
        {"action": "ask", "tank": 1, "question": "Is my alkalinity stable?"}
    ]


async def test_feed_buttons(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """Start/stop feed mode call feed on/off; no_display is a clear error."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.display_tank_start_feed_mode"},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.display_tank_stop_feed_mode"},
        blocking=True,
    )
    assert api.posts_for("feed") == [
        {"action": "feed", "tank": 1, "on": True},
        {"action": "feed", "tank": 1, "on": False},
    ]

    api.replies["feed"] = lambda b: (
        200,
        {
            "ok": False,
            "error": "no_display",
            "say": "Feed mode needs a Reefability Display.",
        },
    )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.frag_tank_start_feed_mode"},
            blocking=True,
        )
    assert err.value.translation_key == "no_display"
    assert "Display" in str(err.value)


async def test_service_descriptions_load(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """services.yaml parses and lists the three services."""
    from homeassistant.helpers.service import async_get_all_descriptions  # noqa: PLC0415

    await setup_entry(hass, config_entry)
    descriptions = (await async_get_all_descriptions(hass))[DOMAIN]
    assert set(descriptions) == {"log_reading", "complete_task", "ask"}
    assert "parameter" in descriptions["log_reading"]["fields"]
    assert descriptions["ask"]["response"] == {"optional": False}
