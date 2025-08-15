"""Price-aware load balancer that considers electricity prices."""

import logging
from datetime import datetime
from typing import Any

from ..balancers.optimised_load_balancer import OptimisedLoadBalancer
from ..const import Phase
from .nord_pool_client import NordPoolClient

_LOGGER = logging.getLogger(__name__)


class PriceAwareLoadBalancer(OptimisedLoadBalancer):
    """Load balancer that optimizes charging based on electricity prices."""

    def __init__(
        self,
        max_limits: dict[Phase, int],
        hysteresis_period: int = 15,
        nord_pool_entity: str = "sensor.nordpool",
        enable_price_optimization: bool = True,
        price_threshold: float | None = None,
        min_charging_hours: int = 4,
    ) -> None:
        """Initialize the price-aware load balancer.
        
        Args:
            max_limits: Maximum current limits per phase
            hysteresis_period: Hysteresis period in minutes
            nord_pool_entity: Entity ID for Nord Pool sensor
            enable_price_optimization: Enable/disable price optimization
            price_threshold: Custom price threshold (SEK/kWh), None = use low_price indicator
            min_charging_hours: Minimum consecutive hours needed for optimal charging
        """
        # Convert minutes to seconds for OptimisedLoadBalancer
        super().__init__(max_limits=max_limits, hold_off_period=hysteresis_period * 60)
        
        self._enable_price_optimization = enable_price_optimization
        self._price_threshold = price_threshold
        self._min_charging_hours = min_charging_hours
        self._nord_pool_client: NordPoolClient | None = None
        self._nord_pool_entity = nord_pool_entity
        
        # Price optimization state
        self._price_charging_window: list[datetime] | None = None
        self._last_price_check: datetime | None = None

    def set_hass(self, hass) -> None:
        """Set Home Assistant instance and initialize Nord Pool client."""
        if hasattr(super(), 'set_hass'):
            super().set_hass(hass)
        
        # Initialize Nord Pool client when we have access to hass
        self._nord_pool_client = NordPoolClient(hass, self._nord_pool_entity)
        _LOGGER.debug("Initialized Nord Pool client with entity: %s", self._nord_pool_entity)

    def _update_price_charging_window(self) -> None:
        """Update the optimal charging window based on current prices."""
        if not self._nord_pool_client or not self._enable_price_optimization:
            return

        try:
            # Find the cheapest period for charging
            optimal_hours = self._nord_pool_client.find_cheapest_hours(
                duration_hours=self._min_charging_hours,
                start_from_now=True
            )
            
            if optimal_hours:
                self._price_charging_window = optimal_hours
                _LOGGER.debug(
                    "Updated optimal charging window: %s to %s",
                    optimal_hours[0].strftime("%H:%M"),
                    optimal_hours[-1].strftime("%H:%M")
                )
            else:
                _LOGGER.warning("Could not determine optimal charging window")
                
        except Exception as e:
            _LOGGER.error("Error updating price charging window: %s", e)

    def _is_price_optimal_for_charging(self) -> bool:
        """Check if current time is optimal for charging based on price."""
        if not self._enable_price_optimization or not self._nord_pool_client:
            return True  # Allow charging if price optimization is disabled

        if not self._nord_pool_client.is_available():
            _LOGGER.warning("Nord Pool data not available, allowing charging")
            return True

        try:
            # Use simple threshold or low_price indicator
            return self._nord_pool_client.should_charge_now(self._price_threshold)
            
        except Exception as e:
            _LOGGER.error("Error checking price optimization: %s", e)
            return True  # Allow charging on error

    def compute_availability(
        self,
        max_limits: dict[Phase, int],
        current_loads: dict[Phase, int],
        charger_requests: dict[Any, dict[Phase, int]],
    ) -> dict[Any, dict[Phase, int]]:
        """Compute availability with price optimization consideration."""
        
        # Update price window periodically
        from homeassistant.util import dt as dt_util
        now = dt_util.utcnow()
        
        if (self._last_price_check is None or 
            (now - self._last_price_check).total_seconds() > 3600):  # Check every hour
            self._update_price_charging_window()
            self._last_price_check = now

        # Check if charging should be allowed based on price
        price_allows_charging = self._is_price_optimal_for_charging()
        
        if not price_allows_charging:
            _LOGGER.info("Price optimization: blocking charging due to high electricity prices")
            # Return zero allocations for all chargers
            return {charger_id: dict.fromkeys(Phase, 0) for charger_id in charger_requests}

        # Get current price for logging
        if self._nord_pool_client and self._enable_price_optimization:
            current_price = self._nord_pool_client.get_current_price()
            currency = self._nord_pool_client.get_currency()
            if current_price is not None:
                _LOGGER.debug(
                    "Price optimization: allowing charging at %.3f %s/kWh",
                    current_price, currency
                )

        # Use parent logic for actual load balancing
        return super().compute_availability(max_limits, current_loads, charger_requests)

    def get_price_info(self) -> dict[str, Any]:
        """Get current price information for diagnostics."""
        if not self._nord_pool_client:
            return {"error": "Nord Pool client not initialized"}

        if not self._nord_pool_client.is_available():
            return {"error": "Nord Pool data not available"}

        try:
            return {
                "current_price": self._nord_pool_client.get_current_price(),
                "currency": self._nord_pool_client.get_currency(),
                "is_low_price": self._nord_pool_client.is_low_price_period(),
                "should_charge": self._is_price_optimal_for_charging(),
                "optimization_enabled": self._enable_price_optimization,
                "price_threshold": self._price_threshold,
                "statistics": self._nord_pool_client.get_price_statistics(),
            }
        except Exception as e:
            return {"error": str(e)}
