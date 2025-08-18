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
            nord_pool_entity_id: Entity ID of the Nord Pool sensor (optional)
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
        self._is_charger_disabled_by_price = False

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
        Override to apply price-aware current limits directly.
        
        During medium/high price periods, return the price-limited current directly
        instead of relying on OptimisedLoadBalancer's complex availability logic.
        """
        _LOGGER.debug("PriceAware: Computing availability with price-aware max limits")
        
        if not self._nord_pool_client:
            # No Nord Pool integration - use parent logic
            return super().compute_availability(available_currents, now)
        
        is_low_price = self._nord_pool_client.is_low_price_period(
            self._price_threshold_percentile / 100.0
        )
        
        # When prices are low, ensure the charger switch is on if we control it
        if is_low_price:
            if self._high_price_disable_charger_switch and self._is_charger_disabled_by_price:
                _LOGGER.debug(
                    f"PriceAware: Price is low, ensuring switch {self._high_price_disable_charger_switch} is on"
                )
                self._hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._high_price_disable_charger_switch}, blocking=False
                )
                self._is_charger_disabled_by_price = False
            
            _LOGGER.debug("PriceAware: Low prices - using optimised balancer logic")
            return super().compute_availability(available_currents, now)
        
        # Medium or high prices: Apply price-based limits directly
        price_limited_currents = {}
        is_very_high_price = self._nord_pool_client.is_high_price_period(
            self._price_upper_percentile / 100.0
        )
        
        for phase in available_currents:
            original_max = self._max_limits[phase]
            
            if is_very_high_price:
                # Very high prices: Pause charging
                new_limit = 0
                _LOGGER.debug(f"PriceAware: Very high prices - pausing {phase.value} (0A)")
            else:
                # Medium prices: Reduce charging to a percentage of max
                new_limit = original_max * (self._high_price_charge_percentage / 100.0)
                _LOGGER.debug(
                    f"PriceAware: Medium prices - reducing {phase.value} to {new_limit:.1f}A "
                    f"({self._high_price_charge_percentage}%)"
                )

            # Ensure the new limit is not below the charger's operational threshold.
            # If it is, it should be set to 0 to pause charging.
            if 0 < new_limit < self._min_charge_current:
                _LOGGER.debug(
                    f"PriceAware: Calculated limit {new_limit:.1f}A is below minimum "
                    f"of {self._min_charge_current}A, pausing."
                )
                new_limit = 0

            price_limited_currents[phase] = int(new_limit)

        # Determine if the charger should be paused (all phase currents are zero)
        is_paused = all(current == 0 for current in price_limited_currents.values())

        # Handle charger switch state based on the paused state
        if self._high_price_disable_charger_switch:
            if is_paused and not self._is_charger_disabled_by_price:
                _LOGGER.debug(
                    f"PriceAware: Charger is paused, turning off switch {self._high_price_disable_charger_switch}"
                )
                self._hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": self._high_price_disable_charger_switch},
                    blocking=False,
                )
                self._is_charger_disabled_by_price = True
            elif not is_paused and self._is_charger_disabled_by_price:
                _LOGGER.debug(
                    f"PriceAware: Charger is resuming, turning on switch {self._high_price_disable_charger_switch}"
                )
                self._hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": self._high_price_disable_charger_switch},
                    blocking=False,
                )
                self._is_charger_disabled_by_price = False

        return price_limited_currents

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
