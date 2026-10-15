"""Entities built from the live GET payload."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.reefability.const import DOMAIN
from custom_components.reefability.sensor import map_unit, truncate

from .conftest import TOKEN, FakeApi, setup_entry, tank_device


def _eid(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


async def test_devices_and_sensors(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """One device per tank, a sensor per reading, summary sensors."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_entry(hass, config_entry)

    device = tank_device(hass, config_entry, 1)
    assert device.name == "Display Tank"
    assert device.manufacturer == "Reefability"
    assert device.model == "Reef tank"
    assert device.configuration_url == "https://reefability.com"
    assert tank_device(hass, config_entry, 2).name == "Frag Tank"

    alk = hass.states.get(_eid(hass, "sensor", "1_reading_alkalinity"))
    assert alk.entity_id == "sensor.display_tank_alkalinity"
    assert alk.state == "8.64"
    assert alk.attributes["unit_of_measurement"] == "dKH"
    assert alk.attributes["state_class"] == "measurement"
    assert alk.attributes["status"] == "ok"
    assert alk.attributes["ideal_low"] == 7.5
    assert alk.attributes["ideal_high"] == 9
    assert alk.attributes["measured_at"] == "2026-10-14T17:47:36+00:00"
    assert alk.attributes["source"] == "AquaWiz KHA"

    # Never rounded up: Salinity 1.0008 with dec 3 is 1.0, Calcium 432.5 with dec 0 is 432.
    sal = hass.states.get(_eid(hass, "sensor", "1_reading_salinity"))
    assert float(sal.state) == 1.0
    assert sal.attributes["unit_of_measurement"] == "SG"
    ca = hass.states.get(_eid(hass, "sensor", "2_reading_calcium"))
    assert ca.state == "432"
    assert ca.attributes["unit_of_measurement"] == "ppm"

    temp = hass.states.get(_eid(hass, "sensor", "1_reading_temperature"))
    assert temp.attributes["device_class"] == "temperature"
    assert temp.attributes["unit_of_measurement"] == "°F"
    assert float(temp.state) == 77.4

    ph = hass.states.get(_eid(hass, "sensor", "1_reading_ph"))
    assert ph.state == "7.84"
    assert ph.attributes.get("device_class") == "ph"
    assert "unit_of_measurement" not in ph.attributes

    nitrite = hass.states.get(_eid(hass, "sensor", "1_reading_nitrite"))
    assert nitrite.state == "0.1"
    assert nitrite.attributes["source"] is None

    today = hass.states.get(_eid(hass, "sensor", "1_tasks_today"))
    overdue = hass.states.get(_eid(hass, "sensor", "1_tasks_overdue"))
    oor = hass.states.get(_eid(hass, "sensor", "1_out_of_range_count"))
    assert today.state == "0"
    assert overdue.state == "11"
    assert overdue.attributes["tasks"][0] == {
        "id": 1001,
        "name": "Quarantine check",
        "due": "2026-09-20",
    }
    assert oor.state == "5"
    assert set(oor.attributes["readings"]) == {
        "Calcium",
        "Nitrate",
        "Phosphate",
        "Salinity",
        "pH",
    }
    assert today.entity_id == "sensor.display_tank_tasks_due_today"


async def test_binary_sensors(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """Out of range, leak, device offline."""
    api.get_payload["tanks"][1]["alerts"] = [
        {
            "kind": "leak",
            "name": "Sump leak",
            "title": "Water detected by Sump leak",
            "since": "2026-10-14T19:00:00+00:00",
        }
    ]
    await setup_entry(hass, config_entry)

    assert (
        hass.states.get(_eid(hass, "binary_sensor", "1_out_of_range")).state == STATE_ON
    )
    offline = hass.states.get(_eid(hass, "binary_sensor", "1_device_offline"))
    assert offline.state == STATE_ON
    assert offline.attributes["device_class"] == "problem"
    assert offline.attributes["alerts"] == ["Controller is offline"]
    assert hass.states.get(_eid(hass, "binary_sensor", "1_leak")).state == STATE_OFF

    leak = hass.states.get(_eid(hass, "binary_sensor", "2_leak"))
    assert leak.state == STATE_ON
    assert leak.attributes["device_class"] == "moisture"
    assert leak.attributes["alerts"] == ["Water detected by Sump leak"]
    assert (
        hass.states.get(_eid(hass, "binary_sensor", "2_device_offline")).state
        == STATE_OFF
    )


async def test_reading_disappears_then_new_one_appears(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """A missing reading turns unavailable; a new reading adds a sensor."""
    await setup_entry(hass, config_entry)
    nitrite_id = _eid(hass, "sensor", "1_reading_nitrite")

    tank = api.get_payload["tanks"][0]
    tank["readings"] = [r for r in tank["readings"] if r["name"] != "Nitrite"]
    tank["readings"].append(
        {
            "name": "Iodine",
            "value": 0.0612,
            "unit": "ppm",
            "dec": 3,
            "status": None,
            "lo": None,
            "hi": None,
            "at": "2026-10-14T10:00:00+00:00",
            "source": "kit",
        }
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()

    assert hass.states.get(nitrite_id).state == STATE_UNAVAILABLE
    iodine = hass.states.get(_eid(hass, "sensor", "1_reading_iodine"))
    assert iodine.entity_id == "sensor.display_tank_iodine"
    assert iodine.state == "0.061"


async def test_poll_failure_makes_entities_unavailable(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """A failing poll marks entities unavailable (not removed)."""
    await setup_entry(hass, config_entry)
    api.get_status = 500
    api.get_payload = {"ok": False, "error": "server"}
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.display_tank_alkalinity").state
        == STATE_UNAVAILABLE
    )


async def test_request_carries_glance_token(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry, aioclient_mock
) -> None:
    """Every call sends Authorization: Glance <token>."""
    await setup_entry(hass, config_entry)
    _method, _url, _data, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == f"Glance {TOKEN}"


def test_truncate_and_units() -> None:
    """Truncation never rounds up; units map to Home Assistant units."""
    assert truncate(8.0794, 2) == 8.07
    assert truncate(1.0008, 3) == 1.0
    assert truncate(432.5, 0) == 432
    assert truncate(-0.129, 2) == -0.12
    assert truncate(None, 2) is None
    assert truncate("x", 2) is None
    assert truncate(5, None) == 5.0
    assert map_unit("°F", "Temperature")[0] == "°F"
    assert map_unit("°C", "Temperature")[0] == "°C"
    assert map_unit("PPM", "Calcium") == ("ppm", None)
    assert map_unit("", "pH")[0] is None
    assert map_unit("", "Iodine") == (None, None)
    assert map_unit("mV", "ORP") == ("mV", None)


async def test_check_probe_attributes(
    hass: HomeAssistant, api: FakeApi, config_entry: MockConfigEntry
) -> None:
    """A suspect newest reading: the sensor keeps the trustworthy value and flags check_probe."""
    for tank in api.get_payload["tanks"]:
        if tank["id"] != 1:
            continue
        for reading in tank["readings"]:
            if reading["name"] == "Salinity":
                reading.update(
                    value=1.026,
                    at="2026-08-25T22:33:43+00:00",
                    check_probe=True,
                    suspect_value=1.0008,
                    suspect_at="2026-10-15T05:47:32+00:00",
                )
    await setup_entry(hass, config_entry)

    sal = hass.states.get(_eid(hass, "sensor", "1_reading_salinity"))
    assert float(sal.state) == 1.026
    assert sal.attributes["check_probe"] is True
    assert sal.attributes["suspect_value"] == 1.0008
    assert sal.attributes["suspect_at"] == "2026-10-15T05:47:32+00:00"

    alk = hass.states.get(_eid(hass, "sensor", "1_reading_alkalinity"))
    assert alk.attributes["check_probe"] is False
    assert alk.attributes["suspect_value"] is None
