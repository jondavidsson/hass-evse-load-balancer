"""PowerAllocator for managing charger power allocation."""

import logging
from math import floor
from time import time

from .chargers.charger import Charger
from .const import Phase

_LOGGER = logging.getLogger(__name__)


class ChargerState:
    """Tracks internal allocation state for a single charger."""

    def __init__(self, charger: Charger) -> None:
        """Initialize charger state."""
        self.charger = charger
        self.requested_current: dict[Phase, int] | None = None
        self.last_set_current: dict[Phase, int] | None = None
        self.last_update_time: int = 0
        self.manual_override_detected: bool = False

    def initialize(self) -> bool:
        """Initialize with current charger settings."""
        current_limits = self.charger.get_current_limit()
        if current_limits:
            self.requested_current = dict(current_limits)
            self.last_set_current = dict(current_limits)
            _LOGGER.info("Charger initialized with limits: %s", current_limits)
            return True
        _LOGGER.warning("Could not initialize charger - no current limits available")
        return False

    def detect_manual_override(self) -> bool:
        """Check if the charger has been manually overridden."""
        current_setting = self.charger.get_current_limit()

        if not current_setting or not self.last_set_current:
            return False

        # Check if current differs from what we last set
        if any(
            current_setting[phase] != self.last_set_current[phase]
            for phase in current_setting
        ):
            self.requested_current = dict(current_setting)
            self.manual_override_detected = True
            _LOGGER.info(
                "Manual override detected for charger. New requested current: %s",
                current_setting
            )
            return True

        return False


class PowerAllocator:
    """
    Manages power allocation to multiple EV chargers based on available current.

    Responsibilities:
    - Track original requested currents for each charger
    - Distribute available power among chargers using selected strategy
    - Reduce charger power when available current is negative
    - Restore charger power when more current is available
    - Handle manual overrides by users

    All without actually updating the chargers, which is done in the coordinator.
    """

    def __init__(self, _chargers: dict[str, ChargerState] | None = None) -> None:
        """Initialize the power allocator."""
        self._chargers: dict[str, ChargerState] = _chargers if _chargers else {}

    def add_charger(self, charger_id: str, charger: Charger) -> bool:
        """
        Add a charger to be managed by the allocator.

        Args:
            charger_id: Unique ID for the charger
            charger: The charger instance

        Returns:
            True if successfully added

        """
        if charger_id in self._chargers:
            _LOGGER.warning("Charger %s already exists in PowerAllocator", charger_id)
            return False

        charger_state = ChargerState(charger)
        if charger_state.initialize():
            self._chargers[charger_id] = charger_state
            _LOGGER.info("Added charger %s to PowerAllocator", charger_id)
            return True
        return False

    def remove_charger(self, charger_id: str) -> bool:
        """Remove a charger from the allocator."""
        if charger_id in self._chargers:
            del self._chargers[charger_id]
            _LOGGER.info("Removed charger %s from PowerAllocator", charger_id)
            return True
        return False

    @property
    def _active_chargers(self) -> dict[str, ChargerState]:
        """Return a dictionary of chargers that can take a charge."""
        return {
            charger_id: state for charger_id, state
            in self._chargers.items() if state.charger.can_charge()
        }

    def should_monitor(self) -> bool:
        """Check if any charger is connected and should be monitored."""
        return len(self._active_chargers) > 0

    def update_allocation(self,
                          available_currents: dict[Phase, int],
                          now: float = time()) -> dict[str, dict[Phase, int]]:
        """
        Update power allocation for all chargers based on available current.

        Args:
            available_currents: Dictionary of available current per phase
            now: Current timestamp

        Returns:
            Dict mapping charger_id to new current limits (empty if no updates)

        """
        if not self._active_chargers:
            return {}

        # Check for manual overrides
        for state in self._active_chargers.values():
            state.detect_manual_override()

        # Allocate current based on strategy
        allocated_currents = self._allocate_current(available_currents)

        # Create result dictionary for chargers that need updating
        result = {}
        for charger_id, new_limits in allocated_currents.items():
            state = self._chargers[charger_id]
            current_setting = state.charger.get_current_limit()

            if not current_setting:
                continue

            # Check if update is needed
            has_changes = False
            if state.charger.has_synced_phase_limits():
                min_current = min(current_setting.values())
                min_new = min(new_limits.values()) if new_limits else min_current
                has_changes = min_new != min_current
            else:
                has_changes = any(
                    new_limits[phase] != current_setting[phase]
                    for phase in new_limits
                )

            if has_changes:
                result[charger_id] = new_limits
                state.last_set_current = dict(new_limits)
                state.last_update_time = int(now)
                state.manual_override_detected = False

        return result

    def _allocate_current(self,
                          available_currents: dict[Phase, int]
                          ) -> dict[str, dict[Phase, int]]:
        """
        Allocate current proportionally to requested currents.

        For negative available current (overcurrent), distribute cuts proportionally.
        For positive available current, distribute increases proportionally.
        """
        result = {}

        # Handle overcurrent and recovery separately for each phase
        for phase, available_current in available_currents.items():
            if available_current < 0:
                # Overcurrent situation - distribute cuts proportionally
                self._distribute_cuts(phase, available_current, result)
            elif available_current > 0:
                # Recovery situation - distribute increases proportionally
                self._distribute_increases(phase, available_current, result)

        return result

    def _distribute_cuts(self,
                         phase: Phase,
                         deficit: int,
                         result: dict[str, dict[Phase, int]]) -> None:
        """Distribute current cuts proportionally during overcurrent."""
        charger_currents = []
        total_current = 0

        # Collect current settings for active chargers
        for charger_id, state in self._active_chargers.items():
            current_setting = state.charger.get_current_limit()
            if not current_setting:
                continue

            current = current_setting[phase]
            charger_currents.append((charger_id, current))
            total_current += current

        if total_current == 0:
            return  # No active chargers or all at minimum

        # Calculate cuts proportionally
        for charger_id, current in charger_currents:
            # Calculate proportional cut based on current usage
            proportion = current / total_current
            cut = floor(deficit * proportion)

            state = self._chargers[charger_id]
            current_setting = state.charger.get_current_limit()

            if charger_id not in result:
                result[charger_id] = current_setting.copy()

            result[charger_id][phase] = max(0, current_setting[phase] + int(cut))

    def _distribute_increases(self,
                              phase: Phase,
                              surplus: int,
                              result: dict[str, dict[Phase, int]]) -> None:
        """Distribute current increases proportionally during recovery."""
        potential_increases = []
        total_potential = 0

        # Calculate potential increases for each charger
        for charger_id, state in self._active_chargers.items():
            current_setting = state.charger.get_current_limit()
            if not current_setting or not state.requested_current:
                continue

            current = current_setting[phase]
            requested = state.requested_current[phase]

            if requested > current:
                potential = requested - current
                potential_increases.append((charger_id, potential))
                total_potential += potential

        if total_potential == 0:
            return  # No potential increases

        # Calculate increases proportionally
        for charger_id, potential in potential_increases:
            proportion = potential / total_potential
            increase = min(surplus * proportion, potential)

            state = self._chargers[charger_id]
            current_setting = state.charger.get_current_limit()

            if charger_id not in result:
                result[charger_id] = current_setting.copy()

            result[charger_id][phase] = current_setting[phase] + int(increase)
