"""The Reefability integration: reef tank readings, tasks and alerts."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ReefabilityClient
from .const import CONF_MAPPINGS, CONF_TOKEN, DOMAIN
from .coordinator import ReefabilityCoordinator
from .push import HaPushManager, mapping_targets

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class ReefabilityData:
    """What one config entry keeps at runtime."""

    client: ReefabilityClient
    coordinator: ReefabilityCoordinator
    push: HaPushManager


type ReefabilityConfigEntry = ConfigEntry[ReefabilityData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once."""
    from .services import async_setup_services  # noqa: PLC0415

    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ReefabilityConfigEntry) -> bool:
    """Set up one Reefability account."""
    client = ReefabilityClient(async_get_clientsession(hass), entry.data[CONF_TOKEN])
    coordinator = ReefabilityCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    push = HaPushManager(
        hass, entry, client, mapping_targets(entry.options.get(CONF_MAPPINGS, {}))
    )
    entry.runtime_data = ReefabilityData(
        client=client, coordinator=coordinator, push=push
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    push.async_start()
    entry.async_on_unload(push.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ReefabilityConfigEntry
) -> None:
    """Reload so the push listener follows the new mapping."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: ReefabilityConfigEntry
) -> bool:
    """Unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
