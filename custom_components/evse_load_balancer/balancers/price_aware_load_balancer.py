"""Price-aware load balancer that considers electricity pricing."""

from __future__ import annotations

import logging
from time import time
from typing import TYPE_CHECKING

from ..const import Phase
from .optimised_load_balancer import OptimisedLoadBalancer
from .util.nord_pool_client import NordPoolClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class PriceAwareLoadBalancer(OptimisedLoadBalancer):
    """Load balancer that considers electricity pricing for charging decisions."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        max_limits: dict[Phase, float],
        min_charge_current: int = 6,  # Minimum current the charger supports
        hold_off_period: int = 30,
        price_hysteresis_period: int = 5,
        nord_pool_entity_id: str | None = None,
        price_threshold_percentile: float,
        price_upper_percentile: float,
        high_price_charge_percentage: float,
        high_price_disable_charger_switch: str | None = None,
    ) -> None:
        """
        Initialize the price-aware load balancer.

        Args:
            hass: Home Assistant instance
            max_limits: Maximum current limits per phase
            min_charge_current: The minimum current the charger supports (Amperes)
            hold_off_period: Period between updates before returning new value (seconds)
            price_hysteresis_period: Hysteresis period for price changes (minutes)
            nord_pool_entity_id: Entity ID of the Nord Pool sensor
            price_threshold_percentile: Percentile threshold for low price (0.0-1.0)
            price_upper_percentile: Percentile threshold for no charging (0.0-1.0)
            high_price_charge_percentage: Percentage of max power during medium prices (0.0-1.0)
            high_price_disable_charger_switch: Entity ID of the switch to disable charging (optional)
        """
        super().__init__(max_limits=max_limits, hold_off_period=hold_off_period)
        self._hass = hass
        self._max_limits = max_limits  # Store max limits (fuse size per phase)
        self._min_charge_current = min_charge_current
        self._price_hysteresis_period = price_hysteresis_period * 60  # Convert to seconds
        self._price_threshold_percentile = price_threshold_percentile
        self._price_upper_percentile = price_upper_percentile
        self._high_price_charge_percentage = high_price_charge_percentage
        self._nord_pool_client = None
        if nord_pool_entity_id:
            self._nord_pool_client = NordPoolClient(hass, nord_pool_entity_id)
        self._high_price_disable_charger_switch = high_price_disable_charger_switch

    async def async_setup(self) -> None:
        """Set up the price-aware load balancer."""
        if self._nord_pool_client:
            success = await self._nord_pool_client.async_setup()
            if success:
                _LOGGER.debug("Price-aware load balancer setup complete")
            else:
                _LOGGER.error("Failed to setup Nord Pool client")
        else:
            _LOGGER.debug("Price-aware load balancer setup without Nord Pool integration")

    def compute_availability(
        self,
        available_currents: dict[Phase, int],
        now: float = time(),
    ) -> dict[Phase, int]:
        """
        Override to apply price-aware current limits directly and manage charger switch.
        
        This method determines the correct charging current based on electricity prices
        and ensures the physical charger switch reflects the intended state (on/off),
        making it resilient to Home Assistant restarts.
        """
        final_currents = {}
        
        # Determine the target currents based on price
        is_low_price = not self._nord_pool_client or self._nord_pool_client.is_low_price_period(
            self._price_threshold_percentile / 100.0
        )

        if is_low_price:
            _LOGGER.debug("PriceAware: Low prices - using optimised balancer logic")
            final_currents = super().compute_availability(available_currents, now)
        else:
            # Medium or high prices: Apply price-based limits directly
            _LOGGER.debug("PriceAware: Medium/High prices - applying direct limits")
            price_limited_currents = {}
            is_very_high_price = self._nord_pool_client.is_high_price_period(
                self._price_upper_percentile / 100.0
            )
            
            for phase in available_currents:
                original_max = self._max_limits[phase]
                
                if is_very_high_price:
                    new_limit = 0
                else: # Medium price
                    new_limit = original_max * (self._high_price_charge_percentage / 100.0)

                # Ensure the new limit is not below the charger's operational threshold
                if 0 < new_limit < self._min_charge_current:
                    _LOGGER.debug(
                        f"PriceAware: Calculated limit {new_limit:.1f}A is below minimum "
                        f"of {self._min_charge_current}A, pausing."
                    )
                    new_limit = 0
                
                price_limited_currents[phase] = int(new_limit)
            final_currents = price_limited_currents

        # Determine the desired switch state based on the final currents
        # If any current is > 0, the charger should be on. Otherwise, it should be paused.
        is_paused = all(current == 0 for current in final_currents.values())
        
        # Reconcile the switch state
        if self._high_price_disable_charger_switch:
            switch_state = self._hass.states.get(self._high_price_disable_charger_switch)
            is_switch_actually_on = switch_state and switch_state.state == "on"
            
            # Turn the switch ON if it should be active but it's currently off
            if not is_paused and not is_switch_actually_on:
                _LOGGER.info(
                    "PriceAware: Charger is resuming, turning on switch %s",
                    self._high_price_disable_charger_switch,
                )
                self._hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._high_price_disable_charger_switch}, blocking=False
                )
            # Turn the switch OFF if it should be paused but it's currently on
            elif is_paused and is_switch_actually_on:
                _LOGGER.info(
                    "PriceAware: Charger is paused, turning off switch %s",
                    self._high_price_disable_charger_switch,
                )
                self._hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._high_price_disable_charger_switch}, blocking=False
                )

        return final_currents

    def get_price_info(self) -> dict[str, any]:
        """Get current price information for debugging/monitoring."""
        if not self._nord_pool_client:
            return {}
            
        return {
            "current_price": self._nord_pool_client.get_current_price(),
            "is_low_price": self._nord_pool_client.is_low_price_period(
                self._price_threshold_percentile / 100.0
            ),
            "is_very_high_price": self._nord_pool_client.is_high_price_period(
                self._price_upper_percentile / 100.0
            ),
            "price_threshold_percentile": self._price_threshold_percentile,
            "price_upper_percentile": self._price_upper_percentile,
            "high_price_charge_percentage": self._high_price_charge_percentage,
        }

    def is_price_limiting_active(self) -> bool:
        """Check if price limiting is currently active (medium or high price periods)."""
        if not self._nord_pool_client:
            return False
        
        is_low_price = self._nord_pool_client.is_low_price_period(
            self._price_threshold_percentile / 100.0
        )
        # Active during medium and high price periods
        return not is_low_price
