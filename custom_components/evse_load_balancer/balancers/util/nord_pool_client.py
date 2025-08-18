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
        entity_registry = er.async_get(self.hass)
        if entity_registry.async_get(self.nord_pool_entity_id) is None:
            _LOGGER.error(
                "Nord Pool entity '%s' not found in entity registry",
                self.nord_pool_entity_id,
            )
            return False

        _LOGGER.debug(
            "Nord Pool client setup complete for entity: %s", self.nord_pool_entity_id
        )
        return True

    def _get_current_and_all_prices(self) -> tuple[float | None, list[float]]:
        """
        Get the current price and a list of all available prices (today and tomorrow).

        Returns:
            A tuple containing the current price (or None) and a list of prices.
        """
        state = self.hass.states.get(self.nord_pool_entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning("Nord Pool entity state unavailable: %s", self.nord_pool_entity_id)
            return None, []

        try:
            current_price = float(state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Could not parse current Nord Pool price: %s", state.state)
            return None, []

        price_data = state.attributes
        today_prices = price_data.get("today", [])
        tomorrow_prices = price_data.get("tomorrow", [])

        all_prices = [p for p in today_prices if p is not None]
        if price_data.get("tomorrow_valid", False):
            all_prices.extend([p for p in tomorrow_prices if p is not None])

        return current_price, all_prices

    def get_current_price(self) -> float | None:
        """Get the current electricity price."""
        current_price, _ = self._get_current_and_all_prices()
        if current_price is not None:
            _LOGGER.debug("Nord Pool current price: %.3f", current_price)
        return current_price

    def get_price_data(self) -> dict[str, Any]:
        """Get detailed price data including today's and tomorrow's prices."""
        state = self.hass.states.get(self.nord_pool_entity_id)
        return state.attributes if state else {}

    def is_low_price_period(self, threshold_percentile: float = 0.3) -> bool | None:
        """
        Check if the current period is in the low price range.

        - Always returns True if the current price is <= 0.
        - Otherwise, compares the current price to the percentile of today's and tomorrow's prices.

        Args:
            threshold_percentile: Percentile threshold (0.3 = bottom 30%)

        Returns:
            True if in low price period, False if not, None if data is unavailable.
        """
        current_price, all_prices = self._get_current_and_all_prices()

        if current_price is None:
            return None

        # Always consider charging if the price is zero or negative
        if current_price <= 0:
            _LOGGER.debug("Current price is <= 0, considering it a low-price period.")
            return True

        if not all_prices:
            _LOGGER.warning("No price data available to calculate percentile.")
            return None

        sorted_prices = sorted(all_prices)
        threshold_index = int(len(sorted_prices) * threshold_percentile)
        
        # Clamp index to be within bounds
        if threshold_index >= len(sorted_prices):
            threshold_index = len(sorted_prices) - 1
            
        threshold_price = sorted_prices[threshold_index]
        is_low = current_price <= threshold_price
        
        _LOGGER.debug(
            "Low price check: current=%.3f, threshold=%.3f (percentile=%.0f%%) -> %s",
            current_price,
            threshold_price,
            threshold_percentile * 100,
            is_low,
        )
        return is_low

    def is_high_price_period(self, threshold_percentile: float = 0.8) -> bool:
        """
        Check if the current price is in a high price period.

        - Never considers prices <= 0 as high.
        - Compares the current price to the percentile of today's and tomorrow's prices.

        Args:
            threshold_percentile: Percentile threshold (0.8 = above 80th percentile)

        Returns:
            True if the current price is above the threshold, False otherwise.
        """
        current_price, all_prices = self._get_current_and_all_prices()

        if current_price is None or not all_prices:
            _LOGGER.warning("No price data available to calculate high price period.")
            return False

        # Negative prices should not be considered high
        if current_price <= 0:
            return False

        sorted_prices = sorted(all_prices)
        threshold_index = int(len(sorted_prices) * threshold_percentile)

        # Clamp index to be within bounds
        if threshold_index >= len(sorted_prices):
            threshold_index = len(sorted_prices) - 1

        threshold_price = sorted_prices[threshold_index]
        is_high = current_price > threshold_price

        _LOGGER.debug(
            "High price check: current=%.3f, threshold=%.3f (percentile=%.0f%%) -> %s",
            current_price,
            threshold_price,
            threshold_percentile * 100,
            is_high,
        )
        return is_high

    def find_cheapest_period(self, duration_hours: int = 4) -> list[datetime] | None:
        """
        Find the cheapest consecutive period for charging.
        
        Args:
            duration_hours: Duration of charging period in hours
            
        Returns:
            List of datetime objects for the cheapest period, or None if no data
        """
        price_data = self.get_price_data()
        
        today_prices = price_data.get("today", [])
        tomorrow_prices = price_data.get("tomorrow", [])
        
        if not today_prices:
            return None
            
        now = dt_util.now()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        
        all_prices = []
        
        for i, price in enumerate(today_prices):
            if price is not None:
                price_time = current_hour.replace(hour=i)
                if price_time >= now:
                    all_prices.append({"value": price, "start": price_time.isoformat()})
        
        if price_data.get("tomorrow_valid", False) and tomorrow_prices:
            tomorrow_start = current_hour + timedelta(days=1)
            for i, price in enumerate(tomorrow_prices):
                if price is not None:
                    price_time = tomorrow_start.replace(hour=i)
                    all_prices.append({"value": price, "start": price_time.isoformat()})
        
        if len(all_prices) < duration_hours:
            _LOGGER.warning("Not enough price data for %d hour period", duration_hours)
            return None
        
        best_avg_price = float('inf')
        best_start_idx = -1
        
        for i in range(len(all_prices) - duration_hours + 1):
            period_prices = [p["value"] for p in all_prices[i:i + duration_hours]]
            avg_price = sum(period_prices) / duration_hours
            
            if avg_price < best_avg_price:
                best_avg_price = avg_price
                best_start_idx = i
        
        if best_start_idx == -1:
            return None

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
        is_low_price = self.is_low_price_period(threshold_percentile)
        current_price = self.get_current_price()
        
        _LOGGER.info(
            "Nord Pool charging decision: price=%.3f, low_price_period=%s, threshold_percentile=%.0f%%",
            current_price or 0.0,
            is_low_price,
            threshold_percentile * 100
        )
        
        return is_low_price or False
