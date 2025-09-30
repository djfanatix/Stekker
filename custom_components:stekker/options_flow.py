import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN

CURRENCIES = ["EUR/kWh", "NOK/kWh", "SEK/kWh", "DKK/kWh"]


class StekkerOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "currency",
                        default=options.get("currency", "EUR/kWh"),
                    ): vol.In(CURRENCIES),
                    vol.Optional(
                        "additional_costs_template",
                        default=options.get("additional_costs_template", ""),
                    ): str,
                }
            ),
        )


def async_get_options_flow(config_entry: config_entries.ConfigEntry):
    return StekkerOptionsFlowHandler(config_entry)