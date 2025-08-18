"""Config flow for the evse-load-balancer integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.selector import BooleanSelector, EntitySelector, NumberSelector

from .exceptions.validation_exception import ValidationExceptionError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OPTION_CHARGE_LIMIT_HYSTERESIS = "charge_limit_hysteresis"
OPTION_MAX_FUSE_LOAD_AMPS = "max_fuse_load_amps"
OPTION_ENABLE_PRICE_AWARE = "enable_price_aware"
OPTION_NORD_POOL_ENTITY = "nord_pool_entity"
OPTION_PRICE_THRESHOLD_PERCENTILE = "price_threshold_percentile"
OPTION_PRICE_UPPER_PERCENTILE = "price_upper_percentile"
OPTION_HIGH_PRICE_CHARGE_PERCENTAGE = "high_price_charge_percentage"
OPTION_HIGH_PRICE_DISABLE_CHARGER_SWITCH = "high_price_disable_charger_switch"

DEFAULT_VALUES: dict[str, Any] = {
    OPTION_CHARGE_LIMIT_HYSTERESIS: 15,
    OPTION_MAX_FUSE_LOAD_AMPS: 0,
    OPTION_ENABLE_PRICE_AWARE: False,
    OPTION_NORD_POOL_ENTITY: "",
    OPTION_PRICE_THRESHOLD_PERCENTILE: 30,
    OPTION_PRICE_UPPER_PERCENTILE: 80,
    OPTION_HIGH_PRICE_CHARGE_PERCENTAGE: 25,
    OPTION_HIGH_PRICE_DISABLE_CHARGER_SWITCH: "",
}


async def validate_init_input(
    _hass: HomeAssistant,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Validate the input data for the options flow."""
    return data


class EvseLoadBalancerOptionsFlow(OptionsFlow):
    """Handle an options flow for evse-load-balancer."""

    def __init__(self, config_entry: ConfigEntry | None = None) -> None:
        """
        Initialize options flow.

        @see https://developers.home-assistant.io/blog/2024/11/12/options-flow/
        """
        if config_entry is not None:
            self.config_entry = config_entry
        self._basic_options: dict[str, Any] = {}

    @staticmethod
    def get_option_value(config_entry: ConfigEntry, key: str) -> Any:
        """Get the value of an option from the config entry."""
        return config_entry.options.get(key, DEFAULT_VALUES.get(key))

    def _basic_options_schema(self) -> vol.Schema:
        """Define the schema for the basic options."""
        options_values = self.config_entry.options

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
                        "min": 0,
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
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "A",
                    }
                ),
                vol.Required(
                    OPTION_ENABLE_PRICE_AWARE,
                    default=options_values.get(
                        OPTION_ENABLE_PRICE_AWARE,
                        DEFAULT_VALUES[OPTION_ENABLE_PRICE_AWARE],
                    ),
                ): BooleanSelector(),
            }
        )

    def _price_aware_options_schema(self) -> vol.Schema:
        """Define the schema for price-aware options."""
        options_values = self.config_entry.options

        return vol.Schema(
            {
                vol.Required(
                    OPTION_NORD_POOL_ENTITY,
                    default=options_values.get(
                        OPTION_NORD_POOL_ENTITY,
                        DEFAULT_VALUES[OPTION_NORD_POOL_ENTITY],
                    ),
                ): EntitySelector(
                    {
                        "domain": "sensor",
                    }
                ),
                vol.Optional(
                    OPTION_PRICE_THRESHOLD_PERCENTILE,
                    default=options_values.get(
                        OPTION_PRICE_THRESHOLD_PERCENTILE,
                        DEFAULT_VALUES[OPTION_PRICE_THRESHOLD_PERCENTILE],
                    ),
                ): NumberSelector(
                    {
                        "min": 0,
                        "max": 100,
                        "step": 1,
                        "mode": "slider",
                        "unit_of_measurement": "%",
                    }
                ),
                vol.Optional(
                    OPTION_PRICE_UPPER_PERCENTILE,
                    default=options_values.get(
                        OPTION_PRICE_UPPER_PERCENTILE,
                        DEFAULT_VALUES[OPTION_PRICE_UPPER_PERCENTILE],
                    ),
                ): NumberSelector(
                    {
                        "min": 0,
                        "max": 100,
                        "step": 1,
                        "mode": "slider",
                        "unit_of_measurement": "%",
                    }
                ),
                vol.Optional(
                    OPTION_HIGH_PRICE_CHARGE_PERCENTAGE,
                    default=options_values.get(
                        OPTION_HIGH_PRICE_CHARGE_PERCENTAGE,
                        DEFAULT_VALUES[OPTION_HIGH_PRICE_CHARGE_PERCENTAGE],
                    ),
                ): NumberSelector(
                    {
                        "min": 0,
                        "max": 100,
                        "step": 1,
                        "mode": "slider",
                        "unit_of_measurement": "%",
                    }
                ),
                vol.Optional(
                    OPTION_HIGH_PRICE_DISABLE_CHARGER_SWITCH,
                    default=options_values.get(
                        OPTION_HIGH_PRICE_DISABLE_CHARGER_SWITCH,
                        DEFAULT_VALUES[OPTION_HIGH_PRICE_DISABLE_CHARGER_SWITCH],
                    ),
                ): EntitySelector(
                    {
                        "domain": "switch",
                    }
                ),
            }
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step with basic options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Store the basic options temporarily
            self._basic_options = user_input
            
            # Check if price-aware charging is enabled
            if user_input.get(OPTION_ENABLE_PRICE_AWARE, False):
                return await self.async_step_price_aware()
            else:
                # If price-aware is disabled, create entry with basic options only
                try:
                    input_data = await validate_init_input(self.hass, user_input)
                except ValidationExceptionError as ex:
                    errors[ex.base] = ex.key
                except ValueError:
                    errors["base"] = "invalid_number_format"

                if not errors:
                    return self.async_create_entry(title="", data=input_data)

        return self.async_show_form(
            step_id="init", data_schema=self._basic_options_schema(), errors=errors
        )

    async def async_step_price_aware(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the price-aware options step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Validate that a Nord Pool entity is selected
            if not user_input.get(OPTION_NORD_POOL_ENTITY):
                errors["base"] = "nord_pool_entity_required"
            # Validate that the upper percentile is greater than or equal to the lower percentile
            elif user_input.get(OPTION_PRICE_UPPER_PERCENTILE) < user_input.get(
                OPTION_PRICE_THRESHOLD_PERCENTILE
            ):
                errors["base"] = "invalid_percentile_order"
            else:
                # Combine basic options with price-aware options
                combined_data = {**self._basic_options, **user_input}
                
                try:
                    input_data = await validate_init_input(self.hass, combined_data)
                except ValidationExceptionError as ex:
                    errors[ex.base] = ex.key
                except ValueError:
                    errors["base"] = "invalid_number_format"

                if not errors:
                    return self.async_create_entry(title="", data=input_data)

        return self.async_show_form(
            step_id="price_aware", data_schema=self._price_aware_options_schema(), errors=errors
        )
