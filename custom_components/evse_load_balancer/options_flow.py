"""Config flow for the evse-load-balancer integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, OptionsFlow, ConfigFlowResult # Added ConfigFlowResult
from homeassistant.helpers.selector import NumberSelector

from .exceptions.validation_exception import ValidationExceptionError 

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OPTION_CHARGE_LIMIT_HYSTERESIS = "charge_limit_hysteresis"
OPTION_MAX_FUSE_LOAD_AMPS = "max_fuse_load_amps"

DEFAULT_VALUES: dict[str, Any] = {
    OPTION_CHARGE_LIMIT_HYSTERESIS: 15,
    OPTION_MAX_FUSE_LOAD_AMPS: 0, 
}


async def validate_init_input(
    _hass: HomeAssistant, 
    data: dict[str, Any],
) -> dict[str, Any]:
    """Validate the input data for the options flow."""
    return data


class EvseLoadBalancerOptionsFlow(OptionsFlow):
    """Handle an options flow for evse-load-balancer."""

    # MODIFIED: __init__ no longer takes config_entry as an argument.
    # Home Assistant core will set self.config_entry after initialization for HA >= 2024.11
    def __init__(self) -> None:
        """Initialize options flow."""
        # self.config_entry is set by HA core if HA version >= 2024.11
        # For older versions, the config_flow.py's async_get_options_flow
        # passes it if needed: EvseLoadBalancerOptionsFlow(config_entry=config_entry)
        # However, to support the new way, __init__ should not require it.
        # If this OptionsFlow is *only* for HA >= 2024.11 (based on your config_flow.py logic),
        # then self.config_entry will be available in other methods like _options_schema.
        # If this __init__ is also called by the <2024.11 path in config_flow.py,
        # then config_flow.py should handle setting self.config_entry on the instance it creates.
        # The change in config_flow.py's async_get_options_flow makes sure to pass it for older HA.
        # For newer HA, self.config_entry is automatically populated.
        pass


    @staticmethod
    def get_option_value(config_entry: ConfigEntry, key: str) -> Any:
        """Get the value of an option from the config entry."""
        return config_entry.options.get(key, DEFAULT_VALUES.get(key))

    def _options_schema(self) -> vol.Schema:
        """Define the schema for the options flow."""
        # self.config_entry is available here, set by HA core or by the older path in async_get_options_flow
        options_values = self.config_entry.options
        
        main_fuse_size_from_config_data = self.config_entry.data.get("fuse_size", 32) 

        return vol.Schema(
            {
                vol.Required(
                    OPTION_CHARGE_LIMIT_HYSTERESIS,
                    default=options_values.get(
                        OPTION_CHARGE_LIMIT_HYSTERESIS,
                        DEFAULT_VALUES[OPTION_CHARGE_LIMIT_HYSTERESIS],
                    ),
                ): NumberSelector(
                    {
                        "min": 1,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "minutes",
                    }
                ),
                vol.Optional(
                    OPTION_MAX_FUSE_LOAD_AMPS,
                    default=options_values.get(
                        OPTION_MAX_FUSE_LOAD_AMPS,
                        DEFAULT_VALUES[OPTION_MAX_FUSE_LOAD_AMPS],
                    ),
                ): NumberSelector(
                    {
                        "min": 0,
                        "max": main_fuse_size_from_config_data,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "A",
                    }
                ),
            }
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult: # MODIFIED: Correct return type hint
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            processed_input = user_input.copy()
            try:
                if OPTION_MAX_FUSE_LOAD_AMPS in processed_input and processed_input[OPTION_MAX_FUSE_LOAD_AMPS] is not None:
                    processed_input[OPTION_MAX_FUSE_LOAD_AMPS] = int(processed_input[OPTION_MAX_FUSE_LOAD_AMPS])

                # self.hass is available in OptionsFlow handlers
                input_data = await validate_init_input(self.hass, processed_input) 
            
            except ValidationExceptionError as ex:
                errors[ex.base] = ex.key
            except ValueError: 
                errors["base"] = "invalid_number_format" 
            
            if not errors:
                return self.async_create_entry(title="", data=input_data)

        return self.async_show_form(
            step_id="init", data_schema=self._options_schema(), errors=errors
        )
