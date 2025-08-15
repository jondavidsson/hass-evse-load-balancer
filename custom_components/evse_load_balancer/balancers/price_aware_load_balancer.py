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
        hold_off_period: int = 30,
        price_hysteresis_period: int = 5,
        nord_pool_entity_id: str | None = None,
        price_threshold_percentile: float = 0.3,
        price_upper_percentile: float = 0.8,
        high_price_charge_percentage: float = 0.25,
    ) -> None:
        """
        Initialize the price-aware load balancer.

        Args:
            hass: Home Assistant instance
            max_limits: Maximum current limits per phase
            hold_off_period: Period between updates before returning new value (seconds)
            price_hysteresis_period: Hysteresis period for price changes (minutes)
            nord_pool_entity_id: Entity ID of the Nord Pool sensor (optional)
            price_threshold_percentile: Percentile threshold for low price (30 = bottom 30%)
            price_upper_percentile: Percentile threshold for no charging (80 = top 20%)
            high_price_charge_percentage: Percentage of max power during medium prices (25 = 25%)
        """
        super().__init__(max_limits=max_limits, hold_off_period=hold_off_period)
        self._hass = hass
        self._max_limits = max_limits  # Store max limits (fuse size per phase)
        self._price_hysteresis_period = price_hysteresis_period * 60  # Convert to seconds
        self._price_threshold_percentile = price_threshold_percentile / 100.0  # Convert to decimal
        self._price_upper_percentile = price_upper_percentile / 100.0  # Convert to decimal
        self._high_price_charge_percentage = high_price_charge_percentage / 100.0  # Convert to decimal
        self._nord_pool_client = None
        if nord_pool_entity_id:
            self._nord_pool_client = NordPoolClient(hass, nord_pool_entity_id)

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
        
        is_low_price = self._nord_pool_client.is_low_price_period(self._price_threshold_percentile)
        is_very_high_price = self._nord_pool_client.is_high_price_period(self._price_upper_percentile)
        
        if is_low_price:
            # Low prices: Use normal OptimisedLoadBalancer logic
            _LOGGER.debug("PriceAware: Low prices - using optimised balancer logic")
            return super().compute_availability(available_currents, now)
        
        # Medium or high prices: Apply price-based limits directly
        price_limited_currents = {}
        
        for phase in available_currents:
            original_max = self._max_limits[phase]
            
            if is_very_high_price:
                # Very high prices: NO CHARGING
                current_limit = 0
                _LOGGER.info(
                    "Very high price period (>%.0f%%): setting current limit to 0A for phase %s",
                    self._price_upper_percentile * 100, phase.name
                )
            else:
                # Medium prices: REDUCED CHARGING
                current_limit = int(original_max * self._high_price_charge_percentage)
                _LOGGER.info(
                    "Medium price period (%.0f%%-%.0f%%): setting current limit to %dA for phase %s (%.0f%% of %dA)",
                    self._price_threshold_percentile * 100, 
                    self._price_upper_percentile * 100,
                    current_limit, phase.name,
                    self._high_price_charge_percentage * 100, original_max
                )
            
            price_limited_currents[phase] = current_limit
        
        _LOGGER.info("PriceAware: Returning price-limited currents: %s", price_limited_currents)
        return price_limited_currents

    def get_price_info(self) -> dict[str, any]:
        """Get current price information for debugging/monitoring."""
        if not self._nord_pool_client:
            return {}
            
        return {
            "current_price": self._nord_pool_client.get_current_price(),
            "is_low_price": self._nord_pool_client.is_low_price_period(self._price_threshold_percentile),
            "is_very_high_price": self._nord_pool_client.is_high_price_period(self._price_upper_percentile),
            "price_threshold_percentile": self._price_threshold_percentile * 100,
            "price_upper_percentile": self._price_upper_percentile * 100,
            "high_price_charge_percentage": self._high_price_charge_percentage * 100,
        }

    def is_price_limiting_active(self) -> bool:
        """Check if price limiting is currently active (medium or high price periods)."""
        if not self._nord_pool_client:
            return False
        
        is_low_price = self._nord_pool_client.is_low_price_period(self._price_threshold_percentile)
        return not is_low_price  # Active during medium and high price periods
