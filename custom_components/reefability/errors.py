"""Turn API errors into readable Home Assistant errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import ReefabilityAuthError, ReefabilityError, ReefabilityResponseError
from .const import DOMAIN

if TYPE_CHECKING:
    from . import ReefabilityConfigEntry


def as_ha_error(
    hass: HomeAssistant, entry: ReefabilityConfigEntry, err: ReefabilityError
) -> HomeAssistantError:
    """Map one API error to a translated HomeAssistantError (401 starts reauth)."""
    if isinstance(err, ReefabilityResponseError):
        if err.error == "no_display":
            return HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_display"
            )
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"message": str(err)},
        )
    if isinstance(err, ReefabilityAuthError):
        entry.async_start_reauth(hass)
        return HomeAssistantError(
            translation_domain=DOMAIN, translation_key="auth_failed"
        )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cannot_connect",
        translation_placeholders={"message": str(err)},
    )
