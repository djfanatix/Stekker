import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .sensor import StekkerCoordinator, StekkerSensor
from .const import DOMAIN

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Stekker from a config entry."""
    zone = entry.data["bidding_zone"]
    options = entry.options
    currency = options.get("currency")
    additional_costs = options.get("additional_costs_template")

    coordinator = StekkerCoordinator(hass, zone, currency, additional_costs)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Correct voor Home Assistant 2023.x+
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Stekker config entry."""

    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "sensor")

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok