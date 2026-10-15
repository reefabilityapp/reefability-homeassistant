"""Base entity for Reefability tanks."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONFIGURATION_URL, DOMAIN, MANUFACTURER, MODEL
from .coordinator import ReefabilityCoordinator, Tank


def tank_device_info(tank_id: int, name: str) -> DeviceInfo:
    """Device registry entry for one tank."""
    return DeviceInfo(
        identifiers={(DOMAIN, str(tank_id))},
        name=name,
        manufacturer=MANUFACTURER,
        model=MODEL,
        configuration_url=CONFIGURATION_URL,
    )


class ReefabilityTankEntity(CoordinatorEntity[ReefabilityCoordinator]):
    """An entity that belongs to one tank."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ReefabilityCoordinator, tank: Tank, key: str
    ) -> None:
        """Initialise; unique id is `<tank_id>_<key>`."""
        super().__init__(coordinator)
        self.tank_id = tank.id
        self._attr_unique_id = f"{tank.id}_{key}"
        self._attr_device_info = tank_device_info(tank.id, tank.name)

    @property
    def tank(self) -> Tank | None:
        """This entity's tank in the latest poll, if it is still there."""
        data = self.coordinator.data or {}
        return data.get(self.tank_id)

    @property
    def available(self) -> bool:
        """Unavailable when the poll failed or the tank disappeared."""
        return super().available and self.tank is not None


def add_tank_entities(
    coordinator: ReefabilityCoordinator,
    async_add_entities: Callable[[Iterable[Entity]], None],
    build: Callable[[Tank], list[tuple[str, Callable[[], Entity]]]],
) -> Callable[[], None]:
    """Add entities for every tank now and for new keys after each poll.

    `build(tank)` returns (unique_id, factory) pairs; ids already added are skipped,
    so a reading that disappears keeps its entity (it turns unavailable).
    """
    known: set[str] = set()

    @callback
    def _check() -> None:
        new: list[Entity] = []
        for tank in (coordinator.data or {}).values():
            for unique_id, factory in build(tank):
                if unique_id not in known:
                    known.add(unique_id)
                    new.append(factory())
        if new:
            async_add_entities(new)

    _check()
    return coordinator.async_add_listener(_check)
