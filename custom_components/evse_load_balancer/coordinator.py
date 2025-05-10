"""Main coordinator for load balacer."""

import logging
from datetime import datetime, timedelta
from functools import cached_property
from math import floor
from time import time
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

# Assuming these are aliases for your local files
from . import config_flow as cf
from . import options_flow as of
from .balancers.optimised_load_balancer import (
    OptimisedLoadBalancer,
)
from .chargers.charger import Charger
from .const import (
    COORDINATOR_STATE_AWAITING_CHARGER,
    COORDINATOR_STATE_MONITORING_LOAD,
    DOMAIN,
    EVENT_ACTION_NEW_CHARGER_LIMITS,
    EVENT_ATTR_ACTION,
    EVENT_ATTR_NEW_LIMITS,
    EVSE_LOAD_BALANCER_COORDINATOR_EVENT,
)
from .meters.meter import Meter, Phase

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceEntry

_LOGGER = logging.getLogger(__name__)

# Number of seconds between each check cycle
EXECUTION_CYCLE_DELAY: int = 1

# Number of seconds between each charger update. This setting
# makes sure that the charger is not updated too frequently and
# allows a change of the charger's limit to actually take affect
MIN_CHARGER_UPDATE_DELAY: int = 30


class EVSELoadBalancerCoordinator:
    """Coordinator for the EVSE Load Balancer."""

    _last_check_timestamp: str | None = None # Added | None for type hint consistency
    _last_charger_target_update: tuple[dict[Phase, int], int] | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        meter: Meter,
        charger: Charger,
    ) -> None:
        """Initialize the coordinator."""
        self.hass: HomeAssistant = hass
        self.config_entry: ConfigEntry = config_entry

        self._unsub: list[CALLBACK_TYPE] = []
        self._sensors: list[SensorEntity] = []

        self._fuse_size: int = config_entry.data.get(cf.CONF_FUSE_SIZE, 0) # Ensure type consistency

        self._meter: Meter = meter
        self._charger: Charger = charger # Charger instance is stored here

        device_registry = dr.async_get(self.hass)
        # Attempt to get the device, handle if it might not exist immediately (though it should)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        if device is None:
            _LOGGER.warning(
                f"Could not retrieve device for entry {self.config_entry.entry_id} during coordinator init."
            )
        self._device: DeviceEntry | None = device # Allow for None if not found, though unlikely


    async def async_setup(self) -> None:
        """Set up the coordinator and its managed components."""
        _LOGGER.debug(f"Coordinator async_setup started for {self.config_entry.entry_id}")

        # Call async_setup_mqtt for the charger if it has such a method
        # This is crucial for MQTT-based chargers like the new AminaCharger
        if hasattr(self._charger, "async_setup_mqtt") and callable(
            getattr(self._charger, "async_setup_mqtt")
        ):
            _LOGGER.info(
                f"Coordinator setup: Calling async_setup_mqtt for charger '{self._charger.__class__.__name__}'"
            )
            try:
                await self._charger.async_setup_mqtt()
            except Exception as e:
                _LOGGER.error(
                    f"Error during async_setup_mqtt for charger {self._charger.__class__.__name__}: {e}"
                )
                # Depending on the severity, you might want to raise an exception or
                # return a status to prevent the integration from fully loading if MQTT setup is critical.
                # For now, we log the error and continue coordinator setup.

        # Set up the periodic update cycle
        self._unsub.append(
            async_track_time_interval(
                self.hass,
                self._execute_update_cycle,
                timedelta(seconds=EXECUTION_CYCLE_DELAY),
            )
        )
        # Add listener for options flow updates
        self._unsub.append(
            self.config_entry.add_update_listener(self._handle_options_update)
        )

        # Initialize the load balancing algorithm
        self._balancer_algo = OptimisedLoadBalancer(
            recovery_window=of.EvseLoadBalancerOptionsFlow.get_option_value( # type: ignore
                self.config_entry, of.OPTION_CHARGE_LIMIT_HYSTERESIS # type: ignore
            )
            * 60
        )
        _LOGGER.debug(f"Coordinator async_setup complete for {self.config_entry.entry_id}")


    async def async_unload(self) -> None:
        """Unload the coordinator and its managed components."""
        _LOGGER.debug(f"Coordinator async_unload started for {self.config_entry.entry_id}")

        # Call async_unload_mqtt for the charger if it has such a method
        # This is crucial to clean up MQTT subscriptions for chargers like AminaCharger
        if hasattr(self._charger, "async_unload_mqtt") and callable(
            getattr(self._charger, "async_unload_mqtt")
        ):
            _LOGGER.info(
                f"Coordinator unload: Calling async_unload_mqtt for charger '{self._charger.__class__.__name__}'"
            )
            try:
                await self._charger.async_unload_mqtt()
            except Exception as e:
                _LOGGER.error(
                    f"Error during async_unload_mqtt for charger {self._charger.__class__.__name__}: {e}"
                )
        
        # Unsubscribe from all listeners (time interval, options update)
        for unsub_method in self._unsub:
            unsub_method()
        self._unsub.clear()
        _LOGGER.debug(f"Coordinator async_unload complete for {self.config_entry.entry_id}")


    async def _handle_options_update(
        self, hass: HomeAssistant, entry: ConfigEntry # pylint: disable=unused-argument
    ) -> None:
        """Handle options update."""
        _LOGGER.debug(f"Reloading config entry {entry.entry_id} due to options update.")
        # The `hass` argument is not used here, but it's part of the listener signature.
        # We can mark it as unused for linters if preferred.
        await self.hass.config_entries.async_reload(entry.entry_id)

    def register_sensor(self, sensor: SensorEntity) -> None:
        """Register a sensor to be updated by the coordinator."""
        if sensor not in self._sensors:
            self._sensors.append(sensor)

    def unregister_sensor(self, sensor: SensorEntity) -> None:
        """Unregister a sensor."""
        if sensor in self._sensors:
            self._sensors.remove(sensor)

    def get_available_current_for_phase(self, phase: Phase) -> int | None:
        """Get the available current for a given phase based on meter readings and fuse size."""
        active_current = self._meter.get_active_phase_current(phase)
        if active_current is not None:
            # Ensure fuse_size is treated as an integer or float for calculation
            fuse_limit = float(self._fuse_size) 
            available = fuse_limit - active_current
            # Available current cannot be more than the fuse size itself if we consider only consumption
            # And it cannot be negative if we only consider available capacity.
            # The balancer algo will handle what to do with this.
            # For this function, let's return the calculated headroom, possibly negative if overloaded.
            # The min(self._fuse_size, floor(self.fuse_size - active_current)) was a bit confusing.
            # Let's simplify: available headroom up to fuse_size.
            # If fuse_size is 16A, and active_current is 10A, available is 6A.
            # If active_current is -5A (production), available relative to consumption is 16 - (-5) = 21A.
            # The balancer needs available_currents which means how much can be ADDED.
            return floor(available)
        return None


    def _get_available_currents(self) -> dict[Phase, int] | None:
        """Check all phases and return the available current for each."""
        available_currents = {}
        for phase in self._available_phases:
            current = self.get_available_current_for_phase(phase)
            if current is None:
                _LOGGER.error(
                    f"Available current for phase {phase.value} is None. Cannot proceed with balancing."
                )
                return None # If any phase is unknown, we can't balance reliably
            available_currents[phase] = current
        
        return available_currents

    @cached_property
    def _available_phases(self) -> list[Phase]:
        """Get the available phases based on the user's configuration (1 or 3 phase)."""
        phase_count = int(self.config_entry.data.get(cf.CONF_PHASE_COUNT, 3))
        return list(Phase)[:phase_count]

    @property
    def fuse_size(self) -> float: # Changed to float for consistency if calculations use float
        """Get the configured main fuse size."""
        return float(self.config_entry.data.get(cf.CONF_FUSE_SIZE, 0))

    @property
    def get_load_balancing_state(self) -> str:
        """Get the current load balancing state."""
        if self._should_check_charger():
            return COORDINATOR_STATE_MONITORING_LOAD
        return COORDINATOR_STATE_AWAITING_CHARGER

    @property
    def get_last_check_timestamp(self) -> str | None: # Ensure type hint matches attribute
        """Get the timestamp of the last check cycle."""
        return self._last_check_timestamp
    
    @property
    def charger_is_car_connected(self) -> bool | None:
        """Get the car connected state from the managed charger."""
        if self._charger:
            try:
                return self._charger.car_connected()
            except Exception as e:
                _LOGGER.error(f"Error getting car_connected from charger: {e}")
                return None
        _LOGGER.debug("Charger instance not available in coordinator for charger_is_car_connected")
        return None

    @property
    def charger_ev_status(self) -> str | None:
        """Get the raw EV status string from the managed charger."""
        if self._charger:
            if hasattr(self._charger, "get_ev_status") and callable(
                getattr(self._charger, "get_ev_status")
            ):
                try:
                    return self._charger.get_ev_status()
                except Exception as e:
                    _LOGGER.error(f"Error getting ev_status from charger's get_ev_status method: {e}")
                    return None
        _LOGGER.debug("Charger instance not available in coordinator for charger_ev_status")
        return None

    @callback
    def _execute_update_cycle(self, now: datetime) -> None: # 'now' is the current time, passed by async_track_time_interval
        """Execute the main update cycle for load balancing."""
        self._last_check_timestamp = datetime.now().astimezone().isoformat() # Store as ISO string

        if not self._should_check_charger():
            _LOGGER.debug("Charger is not in a state to be checked (e.g., not connected or not able to charge). Skipping update cycle.")
            self._async_update_sensors() # Update sensors even if not balancing
            return

        available_currents = self._get_available_currents()
        current_charger_setting = self._charger.get_current_limit()
        max_charger_current = self._charger.get_max_current_limit()

        if available_currents is None:
            _LOGGER.warning("Available current from meter is unknown. Cannot adjust charger limit.")
            return

        if current_charger_setting is None:
            _LOGGER.warning(
                "Current charger limit is not available. Cannot adjust limit."
            )
            return

        if max_charger_current is None:
            _LOGGER.warning(
                "Max charger hardware current is not available. Cannot adjust limit."
            )
            return

        # Update sensors with the latest data before computing new limits
        self._async_update_sensors()

        # Compute new charger limits using the balancing algorithm
        new_charger_settings = self._balancer_algo.compute_new_limits(
            current_limits=current_charger_setting,
            available_currents=available_currents, # This is expected to be headroom
            max_limits=max_charger_current,
            now=now.timestamp(), # Pass current time to algorithm
        )

        if new_charger_settings is None:
            _LOGGER.debug("Balancer algorithm returned no new settings. No changes to apply.")
            return

        # Check if the new settings are different from the current ones
        has_changed_values = any(
            new_charger_settings[phase] != current_charger_setting.get(phase) # Use .get for safety
            for phase in new_charger_settings
            if phase in current_charger_setting # Only compare phases present in both
        )
        # Also check if the set of phases changed, though compute_new_limits should return for all relevant phases
        if len(new_charger_settings) != len(current_charger_setting):
             has_changed_values = True


        if has_changed_values:
            if self._may_update_charger_settings():
                _LOGGER.info(f"New computed charger settings: {new_charger_settings}, current: {current_charger_setting}. Applying update.")
                self._update_charger_settings(new_charger_settings)
            else:
                _LOGGER.debug(
                    f"Charger settings update deferred due to MIN_CHARGER_UPDATE_DELAY. New: {new_charger_settings}, Current: {current_charger_setting}"
                )
        else:
            _LOGGER.debug(f"No change in computed charger settings: {new_charger_settings}. No update needed.")


    def _async_update_sensors(self) -> None:
        """Update all registered sensor states."""
        for sensor in self._sensors:
            if sensor.enabled and sensor.hass: # Ensure sensor is still enabled and part of HA
                sensor.async_write_ha_state()

    def _should_check_charger(self) -> bool:
        """Check if the charger is in a state where its current limit should be managed."""
        # This relies on the charger implementation of can_charge()
        return self._charger.can_charge()

    def _may_update_charger_settings(self) -> bool:
        """Check if enough time has passed since the last charger settings update."""
        if self._last_charger_target_update is None:
            return True # No previous update, so okay to update

        _target_limits, last_update_time = self._last_charger_target_update
        if int(time()) - last_update_time >= MIN_CHARGER_UPDATE_DELAY: # Use >= for clarity
            return True

        _LOGGER.debug(
            "Charger settings update blocked by MIN_CHARGER_UPDATE_DELAY. "
            "Last update at: %s (Current time: %s)",
            datetime.fromtimestamp(last_update_time).isoformat(),
            datetime.fromtimestamp(int(time())).isoformat(),
        )
        return False

    def _update_charger_settings(self, new_limits: dict[Phase, int]) -> None:
        """Update the charger with new current limits and record the update."""
        _LOGGER.info(f"Updating charger with new settings: {new_limits}")
        self._last_charger_target_update = (
            new_limits,
            int(time()), # Record time of this target setting
        )
        self._emit_charger_event(EVENT_ACTION_NEW_CHARGER_LIMITS, new_limits)
        # Create a task to call the async set_current_limit method of the charger
        self.hass.async_create_task(self._charger.set_current_limit(new_limits))

    def _emit_charger_event(self, action: str, new_limits: dict[Phase, int]) -> None:
        """Emit an event to Home Assistant's event bus, associated with the device."""
        if self._device and self._device.id: # Ensure device object and id exist
            self.hass.bus.async_fire(
                EVSE_LOAD_BALANCER_COORDINATOR_EVENT,
                {
                    ATTR_DEVICE_ID: self._device.id,
                    EVENT_ATTR_ACTION: action,
                    EVENT_ATTR_NEW_LIMITS: new_limits,
                },
            )
            _LOGGER.info(
                f"Emitted charger event for device {self._device.id}: action='{action}', new_limits={new_limits}"
            )
        else:
            _LOGGER.warning("Cannot emit charger event: coordinator's device ID is not set.")