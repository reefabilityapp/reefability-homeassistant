"""Send to Reefability: batching and ha_push."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.reefability.const import CONF_MAPPINGS
from custom_components.reefability.push import mapping_targets

from .conftest import FakeApi, setup_entry

OPTIONS = {
    CONF_MAPPINGS: {
        "1": {
            "param_temperature": "sensor.sump_temp",
            "room_humidity": "sensor.room_rh",
            "leak": ["binary_sensor.sump_leak"],
            "other": ["sensor.offline_thing"],
        }
    }
}


def _seed_states(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "sensor.sump_temp",
        "25.6",
        {
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "friendly_name": "Sump probe",
        },
    )
    hass.states.async_set("sensor.room_rh", "48", {"unit_of_measurement": "%"})
    hass.states.async_set(
        "binary_sensor.sump_leak",
        "off",
        {"device_class": "moisture", "friendly_name": "Sump leak"},
    )
    hass.states.async_set("sensor.offline_thing", STATE_UNAVAILABLE)


def test_mapping_targets() -> None:
    """Options become entity_id -> (role, tank)."""
    assert mapping_targets(OPTIONS[CONF_MAPPINGS]) == {
        "sensor.sump_temp": ("param:Temperature", 1),
        "sensor.room_rh": ("room_humidity", 1),
        "binary_sensor.sump_leak": ("leak", 1),
        "sensor.offline_thing": ("other", 1),
    }
    assert mapping_targets({"x": {"leak": ["a.b"]}, "2": {"nope": "a.b"}}) == {}


async def test_startup_snapshot_then_batched_changes(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """Snapshot at start, changes queued and flushed every 30 s, leak at once."""
    _seed_states(hass)
    await setup_entry(hass, config_entry, OPTIONS)

    pushes = api.posts_for("ha_push")
    assert len(pushes) == 1
    states = {s["entity_id"]: s for s in pushes[0]["states"]}
    # unavailable is skipped
    assert set(states) == {
        "sensor.sump_temp",
        "sensor.room_rh",
        "binary_sensor.sump_leak",
    }
    temp = states["sensor.sump_temp"]
    assert temp["role"] == "param:Temperature"
    assert temp["tank_id"] == 1
    assert temp["unit"] == "°C"
    assert temp["device_class"] == "temperature"
    assert temp["name"] == "Sump probe"
    assert temp["state"] == "25.6"
    assert temp["value"] == 25.6
    assert temp["changed_at"]
    assert states["binary_sensor.sump_leak"]["value"] is None

    # Two quick changes of one sensor are sent once, with the newest value.
    hass.states.async_set("sensor.sump_temp", "25.7", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.sump_temp", "25.8", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.room_rh", "unknown")
    await hass.async_block_till_done()
    assert len(api.posts_for("ha_push")) == 1  # still queued
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    pushes = api.posts_for("ha_push")
    assert len(pushes) == 2
    assert [(s["entity_id"], s["state"]) for s in pushes[1]["states"]] == [
        ("sensor.sump_temp", "25.8")
    ]

    # A leak turning wet goes out straight away.
    hass.states.async_set("binary_sensor.sump_leak", "on", {"device_class": "moisture"})
    await hass.async_block_till_done()
    pushes = api.posts_for("ha_push")
    assert len(pushes) == 3
    assert pushes[2]["states"][0]["entity_id"] == "binary_sensor.sump_leak"
    assert pushes[2]["states"][0]["role"] == "leak"
    assert pushes[2]["states"][0]["state"] == "on"

    # Every 15 minutes everything is sent again, stamped now.
    before = len(api.posts_for("ha_push"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=16))
    await hass.async_block_till_done()
    snapshot = api.posts_for("ha_push")[before:]
    sent = {s["entity_id"] for p in snapshot for s in p["states"]}
    assert {"sensor.sump_temp", "binary_sensor.sump_leak"} <= sent


async def test_batches_of_200(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """More than 200 mapped entities go out in several calls."""
    others = [f"sensor.thing_{i}" for i in range(450)]
    for entity_id in others:
        hass.states.async_set(entity_id, "1")
    await setup_entry(hass, config_entry, {CONF_MAPPINGS: {"1": {"other": others}}})
    sizes = [len(p["states"]) for p in api.posts_for("ha_push")]
    assert sizes == [200, 200, 50]


async def test_failed_push_is_retried(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """A server error keeps the states for the next flush."""
    _seed_states(hass)
    ok_reply = api.replies["ha_push"]
    api.replies["ha_push"] = lambda b: (500, {"ok": False, "error": "server"})
    await setup_entry(hass, config_entry, OPTIONS)
    assert len(api.posts_for("ha_push")) == 1
    api.replies["ha_push"] = ok_reply
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    pushes = api.posts_for("ha_push")
    assert len(pushes) == 2
    assert len(pushes[1]["states"]) == 3


async def test_no_mapping_no_push(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """Without options nothing is pushed."""
    await setup_entry(hass, config_entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=16))
    await hass.async_block_till_done()
    assert not api.posts_for("ha_push")


async def test_own_entities_never_sent_back(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """A Reefability entity mapped back to Reefability (a loop) is dropped; others still go."""
    _seed_states(hass)
    options = {
        CONF_MAPPINGS: {
            "1": {
                "param_temperature": "sensor.sump_temp",
                "leak": ["binary_sensor.sump_leak", "binary_sensor.display_tank_leak"],
            }
        }
    }
    await setup_entry(hass, config_entry, options)
    sent = {s["entity_id"] for p in api.posts_for("ha_push") for s in p["states"]}
    assert "binary_sensor.display_tank_leak" not in sent
    assert {"sensor.sump_temp", "binary_sensor.sump_leak"} <= sent
