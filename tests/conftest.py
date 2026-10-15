"""Fixtures for the Reefability tests."""

from __future__ import annotations

from collections.abc import Callable
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.reefability.const import API_URL, CONF_TOKEN, DOMAIN

TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef"
FIXTURE = Path(__file__).parent / "fixtures" / "get_tanks.json"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""
    return


@pytest.fixture
def get_payload() -> dict[str, Any]:
    """The live GET reply captured on 2026-10-14 (two tanks)."""
    return json.loads(FIXTURE.read_text())


class FakeApi:
    """Answers GET with a payload and POST by action; records every POST body."""

    def __init__(self, get_payload: dict[str, Any]) -> None:
        self.get_payload = get_payload
        self.get_status = 200
        self.posts: list[dict[str, Any]] = []
        self.replies: dict[str, Callable[[dict[str, Any]], tuple[int, dict]]] = {
            "whoami": lambda b: (
                200,
                {
                    "ok": True,
                    "tanks": [
                        {"id": t["id"], "name": t["name"]}
                        for t in self.get_payload["tanks"]
                    ],
                },
            ),
            "log": lambda b: (200, {"ok": True, "saved": True, "say": "Logged."}),
            "complete_task": lambda b: (200, {"ok": True, "say": "Done."}),
            "feed": lambda b: (200, {"ok": True, "say": "Feed mode is on."}),
            "ask": lambda b: (
                200,
                {
                    "ok": True,
                    "say": "Spoken answer.",
                    "text": "Alkalinity looks steady.",
                },
            ),
            "ha_push": lambda b: (
                200,
                {"ok": True, "readings": len(b["states"]), "leak_events": 0, "kept": 0},
            ),
        }

    def posts_for(self, action: str) -> list[dict[str, Any]]:
        return [p for p in self.posts if p.get("action") == action]

    async def handle(
        self, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        if method.upper() == "GET":
            return AiohttpClientMockResponse(
                method,
                url,
                status=self.get_status,
                json=copy.deepcopy(self.get_payload),
            )
        body = data if isinstance(data, dict) else json.loads(data or "{}")
        self.posts.append(body)
        status, reply = self.replies[body["action"]](body)
        return AiohttpClientMockResponse(method, url, status=status, json=reply)


@pytest.fixture
def api(aioclient_mock: AiohttpClientMocker, get_payload: dict[str, Any]) -> FakeApi:
    """A fake Reefability API on the real URL."""
    fake = FakeApi(get_payload)
    aioclient_mock.get(API_URL, side_effect=fake.handle)
    aioclient_mock.post(API_URL, side_effect=fake.handle)
    return fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A Reefability entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Reefability",
        data={CONF_TOKEN: TOKEN},
        unique_id="test-unique",
    )


async def setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Add and set up the entry."""
    entry.add_to_hass(hass)
    if options is not None:
        hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def tank_device(
    hass: HomeAssistant, entry: MockConfigEntry, tank_id: int
) -> dr.DeviceEntry:
    """The device of one tank."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, str(tank_id)) in device.identifiers:
            return device
    raise AssertionError(f"no device for tank {tank_id}")
