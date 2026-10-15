"""Reading and summary sensors for Reefability tanks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from . import ReefabilityConfigEntry
from .const import KNOWN_READINGS
from .coordinator import ReefabilityCoordinator, Tank
from .entity import ReefabilityTankEntity, add_tank_entities

try:  # Home Assistant 2025.2+
    from homeassistant.helpers.entity_platform import (
        AddConfigEntryEntitiesCallback as AddEntitiesCallback,
    )
except ImportError:  # pragma: no cover
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

PARALLEL_UPDATES = 0

_PH_CLASS: SensorDeviceClass | None = getattr(SensorDeviceClass, "PH", None)

_UNITS: dict[str, tuple[str | None, SensorDeviceClass | None]] = {
    "°f": (UnitOfTemperature.FAHRENHEIT, SensorDeviceClass.TEMPERATURE),
    "f": (UnitOfTemperature.FAHRENHEIT, SensorDeviceClass.TEMPERATURE),
    "°c": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "c": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "ppm": ("ppm", None),
    "ppb": ("ppb", None),
    "ppt": ("ppt", None),
    "dkh": ("dKH", None),
    "sg": ("SG", None),
    "mv": ("mV", None),
    "meq/l": ("meq/L", None),
    "mg/l": ("mg/L", None),
    "%": (PERCENTAGE, None),
}


def map_unit(
    unit: str | None, name: str
) -> tuple[str | None, SensorDeviceClass | None]:
    """Map the API's unit to a Home Assistant unit and device class."""
    raw = (unit or "").strip()
    low = raw.lower()
    if low in _UNITS:
        return _UNITS[low]
    if low == "ph" or (not low and name.lower() == "ph"):
        return None, _PH_CLASS
    return (raw or None), None


def truncate(value: Any, dec: Any) -> float | int | None:
    """Cut a value to `dec` digits without ever rounding it up (8.0794 -> 8.07)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if not isinstance(dec, int) or isinstance(dec, bool) or dec < 0 or dec > 6:
        return float(number)
    cut = number.quantize(Decimal(1).scaleb(-dec), rounding=ROUND_DOWN)
    return int(cut) if dec == 0 else float(cut)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReefabilityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for every tank, and for readings that show up later."""
    coordinator = entry.runtime_data.coordinator

    def build(tank: Tank) -> list[tuple[str, Callable[[], SensorEntity]]]:
        out: list[tuple[str, Callable[[], SensorEntity]]] = []
        for description in SUMMARY_SENSORS:
            out.append(
                (
                    f"{tank.id}_{description.key}",
                    lambda d=description: SummarySensor(coordinator, tank, d),
                )
            )
        for name, reading in tank.readings.items():
            out.append(
                (
                    f"{tank.id}_{reading_key(name)}",
                    lambda n=name, r=reading: ReadingSensor(coordinator, tank, n, r),
                )
            )
        return out

    entry.async_on_unload(add_tank_entities(coordinator, async_add_entities, build))


def reading_key(name: str) -> str:
    """Stable key for one reading name."""
    return f"reading_{slugify(name)}"


class ReadingSensor(ReefabilityTankEntity, SensorEntity):
    """The latest value of one parameter on one tank."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ReefabilityCoordinator,
        tank: Tank,
        name: str,
        reading: dict[str, Any],
    ) -> None:
        """Initialise from the first reading seen."""
        super().__init__(coordinator, tank, reading_key(name))
        self.reading_name = name
        if name in KNOWN_READINGS:
            self._attr_translation_key = KNOWN_READINGS[name]
        else:
            self._attr_name = name
        unit, device_class = map_unit(reading.get("unit"), name)
        self._attr_device_class = device_class
        self._fixed_unit = unit
        dec = reading.get("dec")
        if isinstance(dec, int) and not isinstance(dec, bool) and 0 <= dec <= 6:
            self._attr_suggested_display_precision = dec

    @property
    def reading(self) -> dict[str, Any] | None:
        """This parameter in the latest poll."""
        tank = self.tank
        return tank.readings.get(self.reading_name) if tank else None

    @property
    def available(self) -> bool:
        """Unavailable when the reading is no longer reported."""
        reading = self.reading
        return (
            super().available
            and reading is not None
            and truncate(reading.get("value"), reading.get("dec")) is not None
        )

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Unit; temperature follows what the API reports now (°F or °C)."""
        reading = self.reading
        if (
            reading is not None
            and self._attr_device_class == SensorDeviceClass.TEMPERATURE
        ):
            unit, device_class = map_unit(reading.get("unit"), self.reading_name)
            if device_class == SensorDeviceClass.TEMPERATURE:
                return unit
        return self._fixed_unit

    @property
    def native_value(self) -> float | int | None:
        """The value, cut to the reading's digits (never rounded up)."""
        reading = self.reading
        if reading is None:
            return None
        return truncate(reading.get("value"), reading.get("dec"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Status, ideal range and where the reading came from."""
        reading = self.reading or {}
        return {
            "status": reading.get("status"),
            "ideal_low": reading.get("lo"),
            "ideal_high": reading.get("hi"),
            "ideal_range_source": reading.get("range"),
            "measured_at": reading.get("at"),
            "source": reading.get("source"),
            # the newest reading was flagged suspect in Reefability (Check probe): the value above is the newest
            # trustworthy one, the suspect one is here so an automation can react to it
            "check_probe": bool(reading.get("check_probe")),
            "suspect_value": reading.get("suspect_value"),
            "suspect_at": reading.get("suspect_at"),
        }


@dataclass(frozen=True, kw_only=True)
class SummarySensorDescription(SensorEntityDescription):
    """A per-tank count."""

    value_fn: Callable[[Tank], int | None]
    attrs_fn: Callable[[Tank], dict[str, Any]]


def _task_list(tank: Tank, overdue: bool) -> dict[str, Any]:
    items = [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "due": t.get("due"),
        }
        for t in tank.task_items
        if bool(t.get("overdue")) is overdue
    ]
    return {"tasks": items}


SUMMARY_SENSORS: tuple[SummarySensorDescription, ...] = (
    SummarySensorDescription(
        key="tasks_today",
        translation_key="tasks_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda t: t.tasks_today,
        attrs_fn=lambda t: _task_list(t, overdue=False),
    ),
    SummarySensorDescription(
        key="tasks_overdue",
        translation_key="tasks_overdue",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda t: t.tasks_overdue,
        attrs_fn=lambda t: _task_list(t, overdue=True),
    ),
    SummarySensorDescription(
        key="out_of_range_count",
        translation_key="out_of_range_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda t: (
            t.out_of_range
            if t.out_of_range is not None
            else len(t.out_of_range_names())
        ),
        attrs_fn=lambda t: {"readings": t.out_of_range_names()},
    ),
)


class SummarySensor(ReefabilityTankEntity, SensorEntity):
    """Tasks due today, tasks overdue, readings out of range."""

    entity_description: SummarySensorDescription

    def __init__(
        self,
        coordinator: ReefabilityCoordinator,
        tank: Tank,
        description: SummarySensorDescription,
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, tank, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        """The count."""
        tank = self.tank
        return self.entity_description.value_fn(tank) if tank else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Task names (the API lists at most five) or the reading names."""
        tank = self.tank
        return self.entity_description.attrs_fn(tank) if tank else None
