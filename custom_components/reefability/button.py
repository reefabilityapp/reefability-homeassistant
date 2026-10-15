"""Feed-mode buttons for Reefability tanks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant

from . import ReefabilityConfigEntry
from .api import ReefabilityError
from .coordinator import ReefabilityCoordinator, Tank
from .entity import ReefabilityTankEntity, add_tank_entities
from .errors import as_ha_error

try:  # Home Assistant 2025.2+
    from homeassistant.helpers.entity_platform import (
        AddConfigEntryEntitiesCallback as AddEntitiesCallback,
    )
except ImportError:  # pragma: no cover
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class FeedButtonDescription(ButtonEntityDescription):
    """Start or stop."""

    on: bool


BUTTONS: tuple[FeedButtonDescription, ...] = (
    FeedButtonDescription(key="feed_start", translation_key="feed_start", on=True),
    FeedButtonDescription(key="feed_stop", translation_key="feed_stop", on=False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReefabilityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the feed buttons for every tank."""
    coordinator = entry.runtime_data.coordinator

    def build(tank: Tank) -> list[tuple[str, Callable[[], ButtonEntity]]]:
        return [
            (f"{tank.id}_{d.key}", lambda d=d: FeedButton(coordinator, tank, d))
            for d in BUTTONS
        ]

    entry.async_on_unload(add_tank_entities(coordinator, async_add_entities, build))


async def async_feed(
    coordinator: ReefabilityCoordinator, tank_id: int, on: bool
) -> None:
    """Call feed; no_display and other API errors become HomeAssistantError."""
    try:
        await coordinator.client.async_feed(tank_id, on)
    except ReefabilityError as err:
        raise as_ha_error(coordinator.hass, coordinator.config_entry, err) from err


class FeedButton(ReefabilityTankEntity, ButtonEntity):
    """Start feed mode / Stop feed mode."""

    entity_description: FeedButtonDescription

    def __init__(
        self,
        coordinator: ReefabilityCoordinator,
        tank: Tank,
        description: FeedButtonDescription,
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, tank, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Send feed on/off."""
        await async_feed(self.coordinator, self.tank_id, self.entity_description.on)
