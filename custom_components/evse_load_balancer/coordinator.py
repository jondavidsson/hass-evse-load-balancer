"""Main coordinator for load balacer."""

import logging
from datetime import datetime, timedelta # Ensure datetime is imported
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

    # MODIFIED: Store as datetime object or None
    _last_check_timestamp: datetime | None = None 
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

        self._fuse_size: int = config_entry.data.get(cf.CONF_FUSE_SIZE, 0)

        self._meter: Meter = meter
        self._charger: Charger = charger 

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        if device is None:
            _LOGGER.warning(
                f"Could not retrieve device for entry {self.config_entry.entry_id} during coordinator init."
            )
        self._device: DeviceEntry | None = device


    async def async_setup(self) -> None:
        """Set up the coordinator and its managed components."""
        _LOGGER.debug(f"Coordinator async_setup started for {self.config_entry.entry_id}")

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
                    f"Error during async_setup_mqtt for charger {self._charger.__class__.__name__}: {e}",
                    exc_info=True # Added exc_info for better debugging
                )

        self._unsub.append(
            async_track_time_interval(
                self.hass,
                self._execute_update_cycle,
                timedelta(seconds=EXECUTION_CYCLE_DELAY),
            )
        )
        self._unsub.append(
            self.config_entry.add_update_listener(self._handle_options_update)
        )

        self._balancer_algo = OptimisedLoadBalancer(
            recovery_window=of.EvseLoadBalancerOptionsFlow.get_option_value( 
                self.config_entry, of.OPTION_CHARGE_LIMIT_HYSTERESIS
            )
            * 60
        )
        _LOGGER.debug(f"Coordinator async_setup complete for {self.config_entry.entry_id}")


    async def async_unload(self) -> None:
        """Unload the coordinator and its managed components."""
        _LOGGER.debug(f"Coordinator async_unload started for {self.config_entry.entry_id}")

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
                    f"Error during async_unload_mqtt for charger {self._charger.__class__.__name__}: {e}",
                    exc_info=True # Added exc_info
                )
        
        for unsub_method in self._unsub:
            unsub_method()
        self._unsub.clear()
        _LOGGER.debug(f"Coordinator async_unload complete for {self.config_entry.entry_id}")


    async def _handle_options_update(
        self, hass: HomeAssistant, entry: ConfigEntry 
    ) -> None:
        """Handle options update."""
        _LOGGER.debug(f"Reloading config entry {entry.entry_id} due to options update.")
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
            fuse_limit = float(self._fuse_size) 
            available = fuse_limit - active_current
            return floor(available)
        return None

    def _get_available_currents(self) -> dict[Phase, int] | None:
        """Check all phases and return the available current for each."""
        available_currents = {}
        for phase_obj in self._available_phases: # renamed phase to phase_obj to avoid conflict
            current = self.get_available_current_for_phase(phase_obj)
            if current is None:
                _LOGGER.error(
                    f"Available current for phase {phase_obj.value} is None. Cannot proceed with balancing."
                )
                return None 
            available_currents[phase_obj] = current
        
        return available_currents

    @cached_property
    def _available_phases(self) -> list[Phase]:
        """Get the available phases based on the user's configuration (1 or 3 phase)."""
        phase_count = int(self.config_entry.data.get(cf.CONF_PHASE_COUNT, 3))
        return list(Phase)[:phase_count]

    @property
    def fuse_size(self) -> float: 
        """Get the configured main fuse size."""
        return float(self.config_entry.data.get(cf.CONF_FUSE_SIZE, 0))

    @property
    def get_load_balancing_state(self) -> str:
        """Get the current load balancing state."""
        if self._should_check_charger():
            return COORDINATOR_STATE_MONITORING_LOAD
        return COORDINATOR_STATE_AWAITING_CHARGER

    @property
    # MODIFIED: Return type hint to datetime | None
    def get_last_check_timestamp(self) -> datetime | None: 
        """Get the timestamp of the last check cycle."""
        return self._last_check_timestamp # Return the datetime object
    
    @property
    def charger_is_car_connected(self) -> bool | None:
        """Get the car connected state from the managed charger."""
        if self._charger:
            try:
                return self._charger.car_connected()
            except Exception as e:
                _LOGGER.error(f"Error getting car_connected from charger: {e}", exc_info=True) # Added exc_info
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
                    _LOGGER.error(f"Error getting ev_status from charger's get_ev_status method: {e}", exc_info=True) # Added exc_info
                    return None
        _LOGGER.debug("Charger instance not available in coordinator for charger_ev_status")
        return None

    @callback
    def _execute_update_cycle(self, now: datetime) -> None: 
        """Execute the main update cycle for load balancing."""
        # MODIFIED: Store actual datetime object, not ISO string
        self._last_check_timestamp = datetime.now().astimezone() 

        if not self._should_check_charger():
            _LOGGER.debug("Charger is not in a state to be checked. Skipping update cycle.")
            self._async_update_sensors() 
            return

        available_currents = self._get_available_currents()
        current_charger_setting = self._charger.get_current_limit()
        max_charger_current = self._charger.get_max_current_limit()

        if available_currents is None:
            _LOGGER.warning("Available current from meter is unknown. Cannot adjust charger limit.")
            return

        if current_charger_setting is None:
            _LOGGER.warning("Current charger limit is not available. Cannot adjust limit.")
            return

        if max_charger_current is None:
            _LOGGER.warning("Max charger hardware current is not available. Cannot adjust limit.")
            return

        self._async_update_sensors()

        new_charger_settings = self._balancer_algo.compute_new_limits(
            current_limits=current_charger_setting,
            available_currents=available_currents, 
            max_limits=max_charger_current,
            now=now.timestamp(), 
        )

        if new_charger_settings is None:
            _LOGGER.debug("Balancer algorithm returned no new settings. No changes to apply.")
            return
        
        has_changed_values = False # Initialize
        if current_charger_setting: # Ensure current_charger_setting is not None for comparison
            has_changed_values = any(
                new_charger_settings.get(phase) != current_charger_setting.get(phase) # Use .get for safety on both
                for phase in self._available_phases # Iterate over configured phases
            )
            # Check if any new phase has a value when it didn't before or vice-versa if structure differs
            if set(new_charger_settings.keys()) != set(current_charger_setting.keys()):
                has_changed_values = True
        elif new_charger_settings: # If current was None but new is not
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
            if sensor.enabled and sensor.hass: 
                sensor.async_write_ha_state()

    def _should_check_charger(self) -> bool:
        """Check if the charger is in a state where its current limit should be managed."""
        return self._charger.can_charge()

    def _may_update_charger_settings(self) -> bool:
        """Check if enough time has passed since the last charger settings update."""
        if self._last_charger_target_update is None:
            return True 

        _target_limits, last_update_time = self._last_charger_target_update
        if int(time()) - last_update_time >= MIN_CHARGER_UPDATE_DELAY: 
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
            int(time()), 
        )
        if self._device and self._device.id: # Ensure _device is not None before calling _emit
            self._emit_charger_event(EVENT_ACTION_NEW_CHARGER_LIMITS, new_limits)
        else:
            _LOGGER.warning("Coordinator device not available, cannot emit charger event.")
            
        self.hass.async_create_task(self._charger.set_current_limit(new_limits))

    def _emit_charger_event(self, action: str, new_limits: dict[Phase, int]) -> None:
        """Emit an event to Home Assistant's event bus, associated with the device."""
        # This check is now also in _update_charger_settings, but defensive check here is also fine.
        if self._device and self._device.id: 
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