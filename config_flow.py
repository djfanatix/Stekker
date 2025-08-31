import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, BIDDING_ZONES_MAP, DEFAULT_BIDDING_ZONE

_LOGGER = logging.getLogger(__name__)

# Mapping key → displaynaam
BIDDING_ZONE_NAMES = {
    "BE": "Belgium",
    "NL": "Netherlands",
    "DE-LU": "Germany & Luxembourg",
    "FR": "France",
    "CH": "Switzerland",
    "SE4": "Sweden SE4",
    "SE3": "Sweden SE3",
    "SE1": "Sweden SE1",
    "DK1": "Denmark DK1",
    "DK2": "Denmark DK2",
    "FI": "Finland",
    "NO1": "Norway NO1",
    "NO2": "Norway NO2",
    "NO3": "Norway NO3",
    "NO4": "Norway NO4",
    "NO5": "Norway NO5",
    "LV": "Latvia",
    "LT": "Lithuania",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "RS": "Serbia",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "HU": "Hungary",
    "AT": "Austria",
    "CZ": "Czech Republic",
    "HR": "Croatia",
    "EE": "Estonia"
}


class StekkerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stekker."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            zone = user_input["bidding_zone"]
            _LOGGER.debug("Config flow completed with bidding_zone=%s", zone)

            return self.async_create_entry(
                title=f"Stekker ({BIDDING_ZONE_NAMES[zone]})",
                data={"bidding_zone": zone},
            )

        # Dropdown: enkelvoudig
        schema = vol.Schema(
            {
                vol.Required(
                    "bidding_zone", default=DEFAULT_BIDDING_ZONE
                ): vol.In(BIDDING_ZONE_NAMES)
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)