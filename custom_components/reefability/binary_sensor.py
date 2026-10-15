"""Problem binary sensors for Reefability tanks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant

from . import ReefabilityConfigEntry
from .coordinator import ReefabilityCoordinator, Tank
from .entity import ReefabilityTankEntity, add_tank_entities

try:  # Home Assistant 2025.2+
    from homeassistant.helpers.entity_platform import (
        AddConfigEntryEntitiesCallback as AddEntitiesCallback,
    )
except ImportError:  # pragma: no cover
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

PARALLEL_UPDATES = 0


def _alert_attrs(tank: Tank, kind: str) -> dict[str, Any]:
    alerts = tank.alerts_of(kind)
    return {
        "alerts": [a.get("title") or a.get("name") for a in alerts],
        "devices": [a.get("name") for a in alerts if a.get("name")],
        "since": [a.get("since") for a in alerts],
    }


@dataclass(frozen=True, kw_only=True)
class TankBinarySensorDescription(BinarySensorEntityDescription):
    """A per-tank yes/no."""

    is_on_fn: Callable[[Tank], bool]
    attrs_fn: Callable[[Tank], dict[str, Any]]


BINARY_SENSORS: tuple[TankBinarySensorDescription, ...] = (
    TankBinarySensorDescription(
        key="out_of_range",
        translation_key="out_of_range",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda t: (
            (t.out_of_range or 0) > 0
            if t.out_of_range is not None
            else bool(t.out_of_range_names())
        ),
        attrs_fn=lambda t: {"readings": t.out_of_range_names()},
    ),
    TankBinarySensorDescription(
        key="leak",
        translation_key="leak",
        device_class=BinarySensorDeviceClass.MOISTURE,
        is_on_fn=lambda t: bool(t.alerts_of("leak")),
        attrs_fn=lambda t: _alert_attrs(t, "leak"),
    ),
    TankBinarySensorDescription(
        key="device_offline",
        translation_key="device_offline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda t: bool(t.alerts_of("offline")),
        attrs_fn=lambda t: _alert_attrs(t, "offline"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReefabilityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the binary sensors for every tank."""
    coordinator = entry.runtime_data.coordinator

    def build(tank: Tank) -> list[tuple[str, Callable[[], BinarySensorEntity]]]:
        return [
            (
                f"{tank.id}_{d.key}",
                lambda d=d: TankBinarySensor(coordinator, tank, d),
            )
            for d in BINARY_SENSORS
        ]

    entry.async_on_unload(add_tank_entities(coordinator, async_add_entities, build))


class TankBinarySensor(ReefabilityTankEntity, BinarySensorEntity):
    """Out of range, leak, device offline."""

    entity_description: TankBinarySensorDescription

    def __init__(
        self,
        coordinator: ReefabilityCoordinator,
        tank: Tank,
        description: TankBinarySensorDescription,
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, tank, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """On when there is a problem."""
        tank = self.tank
        return self.entity_description.is_on_fn(tank) if tank else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The alert titles (or out-of-range reading names)."""
        tank = self.tank
        return self.entity_description.attrs_fn(tank) if tank else None
