"""Config flow for the evse-load-balancer integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.helpers.selector import NumberSelector

from .exceptions.validation_exception import ValidationExceptionError 

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OPTION_CHARGE_LIMIT_HYSTERESIS = "charge_limit_hysteresis"
OPTION_MAX_FUSE_LOAD_AMPS = "max_fuse_load_amps" # Optional max_fuse_load_amps

DEFAULT_VALUES: dict[str, Any] = {
    OPTION_CHARGE_LIMIT_HYSTERESIS: 15,
    OPTION_MAX_FUSE_LOAD_AMPS: 0, # Default 0 means no override for max_fuse_load_amps
}


async def validate_init_input(
    _hass: HomeAssistant, # Mark _hass as unused if not performing hass-specific validation
    data: dict[str, Any],
) -> dict[str, Any]:
    """Validate the input data for the options flow."""
    # Add any specific validation for OPTION_MAX_FUSE_LOAD_AMPS if needed beyond NumberSelector
    return data


class EvseLoadBalancerOptionsFlow(OptionsFlow):
    """Handle an options flow for evse-load-balancer."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    @staticmethod
    def get_option_value(config_entry: ConfigEntry, key: str) -> Any:
        """Get the value of an option from the config entry."""
        # Use .get() for DEFAULT_VALUES to avoid KeyError if a new default is added here but not elsewhere
        return config_entry.options.get(key, DEFAULT_VALUES.get(key))

    def _options_schema(self) -> vol.Schema:
        """Define the schema for the options flow."""
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
                        "min": 0,  # 0 typically signifies no override
                        "max": main_fuse_size_from_config_data, # Cap at the main fuse size
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "A",
                    }
                ),
            }
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            processed_input = user_input.copy()
            try:
                # Ensure OPTION_MAX_FUSE_LOAD_AMPS is an integer
                if OPTION_MAX_FUSE_LOAD_AMPS in processed_input and processed_input[OPTION_MAX_FUSE_LOAD_AMPS] is not None:
                    processed_input[OPTION_MAX_FUSE_LOAD_AMPS] = int(processed_input[OPTION_MAX_FUSE_LOAD_AMPS])

                input_data = await validate_init_input(self.hass, processed_input) # self.hass *is* available in OptionsFlow handlers
            
            except ValidationExceptionError as ex:
                errors[ex.base] = ex.key
            except ValueError: 
                errors["base"] = "invalid_number_format" 
            
            if not errors:
                return self.async_create_entry(title="", data=input_data)

        return self.async_show_form(
            step_id="init", data_schema=self._options_schema(), errors=errors
        )
