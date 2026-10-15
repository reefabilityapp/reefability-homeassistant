"""Constants for the Reefability integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "reefability"
MANUFACTURER: Final = "Reefability"
MODEL: Final = "Reef tank"
CONFIGURATION_URL: Final = "https://reefability.com"

API_URL: Final = "https://reefability.com/api/home-assistant"

CONF_TOKEN: Final = "token"
CONF_MAPPINGS: Final = "mappings"

SCAN_INTERVAL: Final = timedelta(minutes=5)
PUSH_FLUSH_INTERVAL: Final = timedelta(seconds=30)
PUSH_SNAPSHOT_INTERVAL: Final = timedelta(minutes=15)
PUSH_BATCH_MAX: Final = 200
PUSH_QUEUE_MAX: Final = 2000

# Parameters a reefer can log by hand (the API's own list).
LOG_PARAMETERS: Final = [
    "Alkalinity",
    "Calcium",
    "Magnesium",
    "Nitrate",
    "Phosphate",
    "Temperature",
    "Salinity",
    "pH",
    "ORP",
    "Potassium",
    "Ammonia",
    "Nitrite",
]

# Tank probe roles accepted by ha_push, as (options field key, API role).
PROBE_ROLES: Final = [
    ("param_temperature", "param:Temperature"),
    ("param_salinity", "param:Salinity"),
    ("param_ph", "param:pH"),
    ("param_orp", "param:ORP"),
    ("param_alkalinity", "param:Alkalinity"),
    ("param_calcium", "param:Calcium"),
    ("param_magnesium", "param:Magnesium"),
    ("param_nitrate", "param:Nitrate"),
    ("param_phosphate", "param:Phosphate"),
    ("param_potassium", "param:Potassium"),
]
# Single-entity room roles, as (options field key, API role).
ROOM_ROLES: Final = [
    ("room_temperature", "room_temperature"),
    ("room_humidity", "room_humidity"),
    ("co2", "co2"),
]
# Multi-entity roles, as (options field key, API role).
MULTI_ROLES: Final = [
    ("leak", "leak"),
    ("power", "power"),
    ("other", "other"),
]
ROLE_BY_FIELD: Final = {k: r for k, r in (*PROBE_ROLES, *ROOM_ROLES, *MULTI_ROLES)}
MULTI_FIELDS: Final = {k for k, _ in MULTI_ROLES}

# Options-flow sections.
SECTION_PROBES: Final = "probes"
SECTION_ROOM: Final = "room"
SECTION_MORE: Final = "more"

# Reading names with an entity translation (anything else keeps the API's name).
KNOWN_READINGS: Final = {
    "Alkalinity": "alkalinity",
    "Calcium": "calcium",
    "Magnesium": "magnesium",
    "Nitrate": "nitrate",
    "Phosphate": "phosphate",
    "Temperature": "temperature",
    "Salinity": "salinity",
    "pH": "ph",
    "ORP": "orp",
    "Potassium": "potassium",
    "Ammonia": "ammonia",
    "Nitrite": "nitrite",
}

ATTR_TANK_ID: Final = "tank_id"
ATTR_PARAMETER: Final = "parameter"
ATTR_VALUE: Final = "value"
ATTR_UNIT: Final = "unit"
ATTR_TASK_ID: Final = "task_id"
ATTR_QUESTION: Final = "question"

SERVICE_LOG_READING: Final = "log_reading"
SERVICE_COMPLETE_TASK: Final = "complete_task"
SERVICE_ASK: Final = "ask"
