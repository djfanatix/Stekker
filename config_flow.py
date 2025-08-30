import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, BIDDING_ZONES_MAP, DEFAULT_BIDDING_ZONE

_LOGGER = logging.getLogger(__name__)


class StekkerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stekker."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            bidding_zone = user_input["bidding_zone"]

            _LOGGER.debug("Config flow completed with bidding_zone=%s", bidding_zone)

            return self.async_create_entry(
                title=f"Stekker ({bidding_zone})",
                data={"bidding_zone": bidding_zone},
            )

        schema = vol.Schema(
            {
                vol.Required(
                    "bidding_zone", default=DEFAULT_BIDDING_ZONE
                ): vol.In(list(BIDDING_ZONES_MAP.keys()))
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)