"""Nord Pool API client for electricity price data."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class NordPoolClient:
    """Client for accessing Nord Pool electricity price data."""

    def __init__(self, hass: HomeAssistant, nord_pool_entity_id: str) -> None:
        """
        Initialize the Nord Pool client.

        Args:
            hass: Home Assistant instance
            nord_pool_entity_id: Entity ID of the Nord Pool sensor
        """
        self.hass = hass
        self.nord_pool_entity_id = nord_pool_entity_id
        self._price_data: dict[str, Any] = {}
        self._last_update: datetime | None = None
        _LOGGER.debug("Nord Pool client initialized for entity: %s", nord_pool_entity_id)

    async def async_setup(self) -> None:
        """Set up the Nord Pool client."""
        # Verify that the Nord Pool entity exists
        entity_registry = er.async_get(self.hass)
        entity = entity_registry.async_get(self.nord_pool_entity_id)
        
        if entity is None:
            _LOGGER.error(
                "Nord Pool entity '%s' not found in entity registry",
                self.nord_pool_entity_id
            )
            return False
            
        _LOGGER.debug("Nord Pool client setup complete for entity: %s", self.nord_pool_entity_id)
        return True

    def get_current_price(self) -> float | None:
        """Get the current electricity price."""
        state = self.hass.states.get(self.nord_pool_entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning("Nord Pool entity state unavailable")
            return None
            
        try:
            current_price = float(state.state)
            _LOGGER.info(
                "Nord Pool current price: %.3f %s", 
                current_price, 
                state.attributes.get("unit_of_measurement", "")
            )
            return current_price
        except (ValueError, TypeError):
            _LOGGER.warning("Could not parse Nord Pool price: %s", state.state)
            return None

    def get_price_data(self) -> dict[str, Any]:
        """Get detailed price data including today's and tomorrow's prices."""
        state = self.hass.states.get(self.nord_pool_entity_id)
        if state is None:
            return {}
        
        price_data = state.attributes
        
        # Log price data summary
        today_prices = price_data.get("today", [])
        tomorrow_prices = price_data.get("tomorrow", [])
        
        if today_prices:
            valid_today = [p for p in today_prices if p is not None]
            if valid_today:
                _LOGGER.info(
                    "Nord Pool today's prices: %d values, min=%.3f, max=%.3f, avg=%.3f",
                    len(valid_today),
                    min(valid_today),
                    max(valid_today),
                    sum(valid_today) / len(valid_today)
                )
        
        if tomorrow_prices:
            valid_tomorrow = [p for p in tomorrow_prices if p is not None]
            if valid_tomorrow:
                _LOGGER.info(
                    "Nord Pool tomorrow's prices: %d values, min=%.3f, max=%.3f, avg=%.3f",
                    len(valid_tomorrow),
                    min(valid_tomorrow),
                    max(valid_tomorrow),
                    sum(valid_tomorrow) / len(valid_tomorrow)
                )
        
        return price_data

    def is_low_price_period(self, threshold_percentile: float = 0.3) -> bool | None:
        """
        Check if current period is in the low price range.
        
        Args:
            threshold_percentile: Percentile threshold (0.3 = bottom 30%)
            
        Returns:
            True if in low price period, False if high, None if no data
        """
        price_data = self.get_price_data()
        current_price = self.get_current_price()
        
        if current_price is None or not price_data:
            return None
            
        # Get today's prices
        today_prices = price_data.get("today", [])
        if not today_prices:
            return None
            
        # Calculate threshold price using provided percentile
        sorted_prices = sorted([p for p in today_prices if p is not None])
        if not sorted_prices:
            return None
            
        threshold_index = int(len(sorted_prices) * threshold_percentile)
        threshold_price = sorted_prices[threshold_index]
        
        return current_price <= threshold_price

    def is_high_price_period(self, threshold_percentile: float = 0.8) -> bool:
        """
        Check if current price is in a high price period (above percentile threshold).
        
        Args:
            threshold_percentile: Percentile threshold (0.8 = above 80th percentile)
            
        Returns:
            True if current price is above the threshold percentile
        """
        current_price = self.get_current_price()
        if current_price is None:
            return False
            
        price_data = self.get_price_data()
        if not price_data:
            return False
            
        # Get today's prices
        today_prices = price_data.get("today", [])
        if not today_prices:
            return False
            
        # Calculate threshold price using provided percentile
        sorted_prices = sorted([p for p in today_prices if p is not None])
        if not sorted_prices:
            return False
            
        threshold_index = int(len(sorted_prices) * threshold_percentile)
        threshold_price = sorted_prices[threshold_index]
        
        return current_price > threshold_price

    def find_cheapest_period(self, duration_hours: int = 4) -> list[datetime] | None:
        """
        Find the cheapest consecutive period for charging.
        
        Args:
            duration_hours: Duration of charging period in hours
            
        Returns:
            List of datetime objects for the cheapest period, or None if no data
        """
        price_data = self.get_price_data()
        
        # Combine today and tomorrow prices if available
        today_prices = price_data.get("today", [])
        tomorrow_prices = price_data.get("tomorrow", [])
        
        if not today_prices:
            return None
            
        # Create list of (price, datetime) tuples
        now = dt_util.now()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        
        all_prices = []
        
        # Add today's remaining prices
        for i, price in enumerate(today_prices):
            if price is not None:
                price_time = current_hour.replace(hour=i)
                if price_time >= now:  # Only future prices
                    all_prices.append({"value": price, "start": price_time.isoformat()})
        
        # Add tomorrow's prices if available
        if tomorrow_prices:
            tomorrow_start = current_hour + timedelta(days=1)
            for i, price in enumerate(tomorrow_prices):
                if price is not None:
                    price_time = tomorrow_start.replace(hour=i)
                    all_prices.append({"value": price, "start": price_time.isoformat()})
        
        if len(all_prices) < duration_hours:
            _LOGGER.warning("Not enough price data for %d hour period", duration_hours)
            return None
        
        # Find cheapest consecutive period
        best_avg_price = float('inf')
        best_start_idx = 0
        
        for i in range(len(all_prices) - duration_hours + 1):
            period_prices = all_prices[i:i + duration_hours]
            avg_price = sum(p["value"] for p in period_prices) / duration_hours
            
            if avg_price < best_avg_price:
                best_avg_price = avg_price
                best_start_idx = i
        
        # Return the datetime objects for the best period
        best_period = all_prices[best_start_idx:best_start_idx + duration_hours]
        return [dt_util.parse_datetime(p["start"]) for p in best_period]

    def should_charge_now(self, threshold_percentile: float = 0.3, charging_duration_hours: int = 4) -> bool:
        """
        Determine if charging should happen now based on price optimization.
        
        Args:
            threshold_percentile: Percentile threshold for low price detection
            charging_duration_hours: Expected charging duration
            
        Returns:
            True if should charge now, False otherwise
        """
        # Simple logic: charge if in low price period
        is_low_price = self.is_low_price_period(threshold_percentile)
        current_price = self.get_current_price()
        
        _LOGGER.info(
            "Nord Pool charging decision: price=%.3f, low_price_period=%s, threshold_percentile=%.0f%%",
            current_price or 0.0,
            is_low_price,
            threshold_percentile * 100
        )
        
        return is_low_price or False
