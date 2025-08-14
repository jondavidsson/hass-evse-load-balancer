"""Tests for the EVSELoadBalancerCoordinator."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evse_load_balancer.const import (
    DOMAIN,
    EVENT_ACTION_NEW_CHARGER_LIMITS,
    EVENT_ATTR_ACTION,
    EVENT_ATTR_NEW_LIMITS,
    EVSE_LOAD_BALANCER_COORDINATOR_EVENT,
    Phase,
)
from custom_components.evse_load_balancer.coordinator import (
    EVSELoadBalancerCoordinator,
    MIN_CHARGER_UPDATE_DELAY,
)
from .helpers.mock_charger import MockCharger
from custom_components.evse_load_balancer import options_flow as of
from custom_components.evse_load_balancer import config_flow as cf

TEST_CHARGER_ID = "test_charger_id_1"


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    # Mock fire event
    hass.bus.async_fire = MagicMock()
    # Mock async_track_time_interval
    hass.helpers.event.async_track_time_interval = MagicMock(return_value=MagicMock())
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_balancer",
        data={"fuse_size": 25},
    )


@pytest.fixture
def mock_config_entry_single_phase():
    """Create a mock config entry for single phase setup."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_balancer_single",
        data={
            "fuse_size": 25,
            cf.CONF_PHASE_COUNT: 1,
        },
    )


@pytest.fixture
def mock_meter():
    """Create a mock meter."""
    meter = MagicMock()

    def get_active_phase_current(phase):
        return 14 if phase == Phase.L1 else 16
    meter.get_active_phase_current.side_effect = get_active_phase_current
    return meter


@pytest.fixture
def mock_meter_single_phase():
    """Create a mock meter for single phase testing."""
    meter = MagicMock()

    # Mock active_phase_current - only L1 should be used
    def get_active_phase_current(phase):
        if phase == Phase.L1:
            return 14
        else:
            # In single phase setup, L2 and L3 should not be called
            raise ValueError(f"Phase {phase} should not be used in single phase setup")
    meter.get_active_phase_current.side_effect = get_active_phase_current
    return meter


@pytest.fixture
def mock_charger():
    """Create a mock charger."""
    charger = MockCharger(initial_current=16, charger_id=TEST_CHARGER_ID)
    charger.set_can_charge(True)

    # Patch all public methods with MagicMock, but call the original method as well
    for attr_name in dir(charger):
        # Skip private/protected and non-callable attributes
        if attr_name.startswith("_"):
            continue
        attr = getattr(charger, attr_name)
        if callable(attr):
            original_method = attr
            setattr(
                charger,
                attr_name,
                MagicMock(side_effect=original_method)
            )

    return charger


@pytest.fixture
def mock_charger_single_phase(mock_charger):
    """Create a mock charger for single phase testing."""
    # For single phase, set synced phases to True to ensure proper behavior
    mock_charger._synced_phases = True

    return mock_charger


@pytest.fixture
def mock_balancer_algo():
    """Create a mock balancer algorithm."""
    algo = MagicMock()
    algo.compute_availability.return_value = {
        Phase.L1: -2,  # Overcurrent on L1
        Phase.L2: 3,   # Available current on L2
        Phase.L3: 5,   # Available current on L3
    }
    return algo


@pytest.fixture
def mock_balancer_algo_single_phase():
    """Create a mock balancer algorithm for single phase."""
    algo = MagicMock()
    algo.compute_availability.return_value = {
        Phase.L1: -2,  # Overcurrent on L1
    }
    return algo


@pytest.fixture
def mock_power_allocator():
    """Create a mock power allocator."""
    allocator = MagicMock()
    allocator.should_monitor.return_value = True
    allocator.update_allocation.return_value = {
        TEST_CHARGER_ID: {
            Phase.L1: 14,  # Reduced from 16 to 14
            Phase.L2: 16,  # No change
            Phase.L3: 16,  # No change
        }
    }
    return allocator


@pytest.fixture
def mock_power_allocator_single_phase():
    """Create a mock power allocator for single phase."""
    allocator = MagicMock()
    allocator.should_monitor.return_value = True
    allocator.update_allocation.return_value = {
        TEST_CHARGER_ID: {
            Phase.L1: 14,  # Reduced from 16 to 14
        }
    }
    return allocator


@pytest.fixture
def coordinator(
    mock_hass, mock_config_entry, mock_meter, mock_charger, mock_balancer_algo, mock_power_allocator
):
    """Create a coordinator with mocked dependencies."""
    with patch(
        "custom_components.evse_load_balancer.coordinator.OptimisedLoadBalancer",
        return_value=mock_balancer_algo,
    ), patch(
        "custom_components.evse_load_balancer.coordinator.PowerAllocator",
        return_value=mock_power_allocator,
    ):
        coordinator = EVSELoadBalancerCoordinator(
            hass=mock_hass,
            config_entry=mock_config_entry,
            meter=mock_meter,
            charger=mock_charger,
        )

        # Mock needed properties and methods
        coordinator._last_charger_target_update = None
        coordinator._device = MagicMock()
        coordinator._sensors = [MagicMock(), MagicMock()]

        # Set up the balancer to use our mock
        coordinator._balancer_algo = mock_balancer_algo
        coordinator._power_allocator = mock_power_allocator

        return coordinator


@pytest.fixture
def coordinator_single_phase(
    mock_hass, mock_config_entry_single_phase, mock_meter_single_phase,
    mock_charger_single_phase, mock_balancer_algo_single_phase, mock_power_allocator_single_phase
):
    """Create a coordinator with single phase setup."""
    with patch(
        "custom_components.evse_load_balancer.coordinator.OptimisedLoadBalancer",
        return_value=mock_balancer_algo_single_phase,
    ), patch(
        "custom_components.evse_load_balancer.coordinator.PowerAllocator",
        return_value=mock_power_allocator_single_phase,
    ):
        coordinator = EVSELoadBalancerCoordinator(
            hass=mock_hass,
            config_entry=mock_config_entry_single_phase,
            meter=mock_meter_single_phase,
            charger=mock_charger_single_phase,
        )

        # Mock needed properties and methods
        coordinator._last_charger_target_update = None
        coordinator._device = MagicMock()
        coordinator._sensors = [MagicMock(), MagicMock()]

        # Set up the balancer to use our mock
        coordinator._balancer_algo = mock_balancer_algo_single_phase
        coordinator._power_allocator = mock_power_allocator_single_phase

        return coordinator


def test_sensor_updates(coordinator):
    """Test that sensors are updated properly."""
    # Execute an update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Check that async_write_ha_state was called on each sensor
    for sensor in coordinator._sensors:
        assert sensor.async_write_ha_state.called


def test_charger_allocation(coordinator):
    """Test that charger currents are allocated correctly."""
    # Execute an update cycle
    now = datetime.now()
    coordinator._execute_update_cycle(now)

    # Verify balancer was called with correct parameters
    coordinator._balancer_algo.compute_availability.assert_called_once()
    args = coordinator._balancer_algo.compute_availability.call_args[1]
    assert "available_currents" in args
    assert "now" in args

    # Verify power allocator was called with balancer results
    coordinator._power_allocator.update_allocation.assert_called_once()
    allocation_args = coordinator._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {
        Phase.L1: -2,
        Phase.L2: 3,
        Phase.L3: 5
    }

    # Verify charger was updated with allocation results
    coordinator._charger.set_current_limit.assert_called_once_with({
        Phase.L1: 14,
        Phase.L2: 16,
        Phase.L3: 16,
    })


def test_no_update_when_available_current_unknown(coordinator):
    """Test that no update happens when available current is unknown."""
    # Set meter to return None
    coordinator._meter.get_active_phase_current.side_effect = lambda phase: None

    # Execute update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Verify balancer was not called
    coordinator._balancer_algo.compute_availability.assert_not_called()
    coordinator._power_allocator.update_allocation.assert_not_called()
    coordinator._charger.set_current_limit.assert_not_called()


def test_only_update_when_currents_have_changed(coordinator):
    """Tests that no update happens on the allocator when the currents haven't changed."""
    # Execute an update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Verify balancer and allocator were called
    coordinator._balancer_algo.compute_availability.assert_called_once()
    coordinator._power_allocator.update_allocation.assert_called_once()
    allocation_args = coordinator._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {
        Phase.L1: -2,
        Phase.L2: 3,
        Phase.L3: 5
    }

    # Call second time, with same values, verify no update
    coordinator._execute_update_cycle(datetime.now())
    assert coordinator._balancer_algo.compute_availability.call_count == 2
    assert coordinator._power_allocator.update_allocation.call_count == 1

    # Mock different values
    coordinator._balancer_algo.compute_availability.return_value = dict.fromkeys(Phase, 2)

    coordinator._execute_update_cycle(datetime.now())
    assert coordinator._balancer_algo.compute_availability.call_count == 3
    assert coordinator._power_allocator.update_allocation.call_count == 2
    allocation_args = coordinator._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {
        Phase.L1: 2,
        Phase.L2: 2,
        Phase.L3: 2
    }


def test_no_update_when_charger_shouldnt_be_checked(coordinator):
    """Test that no update happens when charger shouldn't be checked."""
    # Set power allocator to indicate charger shouldn't be monitored
    coordinator._power_allocator.should_monitor.return_value = False

    # Execute update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Verify balancer was not called
    coordinator._balancer_algo.compute_availability.assert_not_called()
    coordinator._power_allocator.update_allocation.assert_not_called()
    coordinator._charger.set_current_limit.assert_not_called()


def test_no_update_too_frequent(coordinator):
    """Test that no update happens when last update was too recent."""
    # Set recent update time
    coordinator._last_charger_target_update = (
        {Phase.L1: 10, Phase.L2: 10, Phase.L3: 10},
        int(datetime.now().timestamp()) - 10,  # 10 seconds ago (less than MIN_CHARGER_UPDATE_DELAY)
    )

    # Execute update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Balancer and allocator should still be called
    assert coordinator._balancer_algo.compute_availability.called
    assert coordinator._power_allocator.update_allocation.called

    # But charger should not be updated
    coordinator._charger.set_current_limit.assert_not_called()


def test_update_after_delay(coordinator):
    """Test that update happens after sufficient delay."""
    min_charge_minutes = of.EvseLoadBalancerOptionsFlow.get_option_value(
        coordinator.config_entry, of.OPTION_CHARGE_LIMIT_HYSTERESIS
    )
    # Set update time far enough in the past
    coordinator._last_charger_target_update = (
        {Phase.L1: 10, Phase.L2: 10, Phase.L3: 10},
        int(datetime.now().timestamp()) - (MIN_CHARGER_UPDATE_DELAY + 10 + (min_charge_minutes * 60)),
    )

    # Execute update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Charger should be updated
    coordinator._charger.set_current_limit.assert_called_once()


def test_event_fired_on_update(coordinator):
    """Test that an event is fired when charger is updated."""
    # Execute update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Check that event was fired
    coordinator.hass.bus.async_fire.assert_called_once_with(
        EVSE_LOAD_BALANCER_COORDINATOR_EVENT,
        {
            EVENT_ATTR_ACTION: EVENT_ACTION_NEW_CHARGER_LIMITS,
            EVENT_ATTR_NEW_LIMITS: {
                Phase.L1: 14,
                Phase.L2: 16,
                Phase.L3: 16,
            },
            "device_id": coordinator._device.id,
        },
    )


def test_no_available_current_change(coordinator):
    """Test behavior when available current hasn't changed."""
    # Set initial state
    coordinator._available_currents = {
        Phase.L1: 5,
        Phase.L2: 10,
        Phase.L3: 15,
    }

    # Set meter to return same values
    coordinator._meter.get_available_current.return_value = {
        Phase.L1: 5,
        Phase.L2: 10,
        Phase.L3: 15,
    }

    # Execute update cycle
    coordinator._execute_update_cycle(datetime.now())

    # Balancer should still be called (since handling all measurement scenarios is its job)
    assert coordinator._balancer_algo.compute_availability.called


def test_sensor_updates_single_phase(coordinator_single_phase):
    """Test that sensors are updated properly in single phase setup."""
    # Execute an update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Check that async_write_ha_state was called on each sensor
    for sensor in coordinator_single_phase._sensors:
        assert sensor.async_write_ha_state.called


def test_charger_allocation_single_phase(coordinator_single_phase):
    """Test that charger currents are allocated correctly in single phase setup."""
    # Execute an update cycle
    now = datetime.now()
    coordinator_single_phase._execute_update_cycle(now)

    # Verify balancer was called with correct parameters
    coordinator_single_phase._balancer_algo.compute_availability.assert_called_once()
    args = coordinator_single_phase._balancer_algo.compute_availability.call_args[1]
    assert "available_currents" in args
    assert "now" in args

    # Verify power allocator was called with balancer results
    coordinator_single_phase._power_allocator.update_allocation.assert_called_once()
    allocation_args = coordinator_single_phase._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {
        Phase.L1: -2,
    }

    # Verify charger was updated with allocation results
    coordinator_single_phase._charger.set_current_limit.assert_called_once_with({
        Phase.L1: 14,
    })


def test_no_update_when_available_current_unknown_single_phase(coordinator_single_phase):
    """Test that no update happens when available current is unknown in single phase setup."""
    # Set meter to return None
    coordinator_single_phase._meter.get_active_phase_current.side_effect = lambda phase: None

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Verify balancer was not called
    coordinator_single_phase._balancer_algo.compute_availability.assert_not_called()
    coordinator_single_phase._power_allocator.update_allocation.assert_not_called()
    coordinator_single_phase._charger.set_current_limit.assert_not_called()


def test_only_update_when_currents_have_changed_single_phase(coordinator_single_phase):
    """Tests that no update happens on the allocator when the currents haven't changed in single phase setup."""
    # Execute an update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Verify balancer and allocator were called
    coordinator_single_phase._balancer_algo.compute_availability.assert_called_once()
    coordinator_single_phase._power_allocator.update_allocation.assert_called_once()
    allocation_args = coordinator_single_phase._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {
        Phase.L1: -2,
    }

    # Call second time, with same values, verify no update
    coordinator_single_phase._execute_update_cycle(datetime.now())
    assert coordinator_single_phase._balancer_algo.compute_availability.call_count == 2
    assert coordinator_single_phase._power_allocator.update_allocation.call_count == 1

    # Mock different values
    coordinator_single_phase._balancer_algo.compute_availability.return_value = {Phase.L1: 2}

    coordinator_single_phase._execute_update_cycle(datetime.now())
    assert coordinator_single_phase._balancer_algo.compute_availability.call_count == 3
    assert coordinator_single_phase._power_allocator.update_allocation.call_count == 2
    allocation_args = coordinator_single_phase._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {
        Phase.L1: 2,
    }


def test_no_update_when_charger_shouldnt_be_checked_single_phase(coordinator_single_phase):
    """Test that no update happens when charger shouldn't be checked in single phase setup."""
    # Set power allocator to indicate charger shouldn't be monitored
    coordinator_single_phase._power_allocator.should_monitor.return_value = False

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Verify balancer was not called
    coordinator_single_phase._balancer_algo.compute_availability.assert_not_called()
    coordinator_single_phase._power_allocator.update_allocation.assert_not_called()
    coordinator_single_phase._charger.set_current_limit.assert_not_called()


def test_no_update_too_frequent_single_phase(coordinator_single_phase):
    """Test that no update happens when last update was too recent in single phase setup."""
    # Set recent update time
    coordinator_single_phase._last_charger_target_update = (
        {Phase.L1: 10},
        int(datetime.now().timestamp()) - 10,  # 10 seconds ago (less than MIN_CHARGER_UPDATE_DELAY)
    )

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Balancer and allocator should still be called
    assert coordinator_single_phase._balancer_algo.compute_availability.called
    assert coordinator_single_phase._power_allocator.update_allocation.called

    # But charger should not be updated
    coordinator_single_phase._charger.set_current_limit.assert_not_called()


def test_update_after_delay_single_phase(coordinator_single_phase):
    """Test that update happens after sufficient delay in single phase setup."""
    min_charge_minutes = of.EvseLoadBalancerOptionsFlow.get_option_value(
        coordinator_single_phase.config_entry, of.OPTION_CHARGE_LIMIT_HYSTERESIS
    )
    # Set update time far enough in the past
    coordinator_single_phase._last_charger_target_update = (
        {Phase.L1: 10},
        int(datetime.now().timestamp()) - (MIN_CHARGER_UPDATE_DELAY + 10 + (min_charge_minutes * 60)),
    )

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Charger should be updated
    coordinator_single_phase._charger.set_current_limit.assert_called_once()


def test_event_fired_on_update_single_phase(coordinator_single_phase):
    """Test that an event is fired when charger is updated in single phase setup."""
    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Check that event was fired
    coordinator_single_phase.hass.bus.async_fire.assert_called_once_with(
        EVSE_LOAD_BALANCER_COORDINATOR_EVENT,
        {
            EVENT_ATTR_ACTION: EVENT_ACTION_NEW_CHARGER_LIMITS,
            EVENT_ATTR_NEW_LIMITS: {
                Phase.L1: 14,
            },
            "device_id": coordinator_single_phase._device.id,
        },
    )


def test_no_available_current_change_single_phase(coordinator_single_phase):
    """Test behavior when available current hasn't changed in single phase setup."""
    # Set initial state
    coordinator_single_phase._available_currents = {
        Phase.L1: 5,
    }

    # Set meter to return same values
    coordinator_single_phase._meter.get_available_current.return_value = {
        Phase.L1: 5,
    }

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Balancer should still be called (since handling all measurement scenarios is its job)
    assert coordinator_single_phase._balancer_algo.compute_availability.called


# ===== SINGLE PHASE TESTS =====

def test_single_phase_available_phases(coordinator_single_phase):
    """Test that single phase setup only uses L1 phase."""
    available_phases = coordinator_single_phase._available_phases
    assert len(available_phases) == 1
    assert available_phases[0] == Phase.L1


def test_single_phase_charger_allocation_overcurrent(coordinator_single_phase):
    """Test that single phase charger allocation works correctly under overcurrent."""
    # Set up overcurrent scenario for single phase
    coordinator_single_phase._balancer_algo.compute_availability.return_value = {
        Phase.L1: -3,  # Overcurrent on L1
    }

    coordinator_single_phase._power_allocator.update_allocation.return_value = {
        TEST_CHARGER_ID: {
            Phase.L1: 13,  # Reduced from 16 to 13
        }
    }

    # Execute update cycle
    now = datetime.now()
    coordinator_single_phase._execute_update_cycle(now)

    # Verify balancer was called with only L1
    coordinator_single_phase._balancer_algo.compute_availability.assert_called_once()
    args = coordinator_single_phase._balancer_algo.compute_availability.call_args[1]
    assert "available_currents" in args
    assert len(args["available_currents"]) == 1
    assert Phase.L1 in args["available_currents"]

    # Verify power allocator was called with single phase balancer results
    coordinator_single_phase._power_allocator.update_allocation.assert_called_once()
    allocation_args = coordinator_single_phase._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {Phase.L1: -3}

    # Verify charger was updated with single phase allocation results
    coordinator_single_phase._charger.set_current_limit.assert_called_once_with({
        Phase.L1: 13,
    })


def test_single_phase_charger_allocation_recovery(coordinator_single_phase):
    """Test that single phase charger allocation works correctly during recovery."""
    # Set up recovery scenario for single phase
    coordinator_single_phase._balancer_algo.compute_availability.return_value = {
        Phase.L1: 5,  # Available current on L1
    }

    coordinator_single_phase._power_allocator.update_allocation.return_value = {
        TEST_CHARGER_ID: {
            Phase.L1: 16,  # Full current available
        }
    }

    # Execute update cycle
    now = datetime.now()
    coordinator_single_phase._execute_update_cycle(now)

    # Verify balancer was called with only L1
    args = coordinator_single_phase._balancer_algo.compute_availability.call_args[1]
    assert len(args["available_currents"]) == 1
    assert Phase.L1 in args["available_currents"]

    # Verify power allocator was called with single phase balancer results
    allocation_args = coordinator_single_phase._power_allocator.update_allocation.call_args[1]
    assert allocation_args["available_currents"] == {Phase.L1: 5}

    # Verify charger was updated with single phase allocation results
    coordinator_single_phase._charger.set_current_limit.assert_called_once_with({
        Phase.L1: 16,
    })


def test_single_phase_no_update_when_meter_returns_none(coordinator_single_phase):
    """Test that single phase setup handles meter returning None gracefully."""
    # Mock meter to return None for L1
    coordinator_single_phase._meter.get_active_phase_current.side_effect = lambda phase: None

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Verify balancer was not called when meter returns None
    coordinator_single_phase._balancer_algo.compute_availability.assert_not_called()
    coordinator_single_phase._power_allocator.update_allocation.assert_not_called()
    coordinator_single_phase._charger.set_current_limit.assert_not_called()


def test_single_phase_event_fired_on_update(coordinator_single_phase):
    """Test that events are fired correctly for single phase updates."""
    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Check that event was fired with single phase data
    coordinator_single_phase.hass.bus.async_fire.assert_called_once_with(
        EVSE_LOAD_BALANCER_COORDINATOR_EVENT,
        {
            EVENT_ATTR_ACTION: EVENT_ACTION_NEW_CHARGER_LIMITS,
            EVENT_ATTR_NEW_LIMITS: {
                Phase.L1: 14,
            },
            "device_id": coordinator_single_phase._device.id,
        },
    )


def test_single_phase_fuse_size_calculation(coordinator_single_phase):
    """Test that fuse size calculation works correctly for single phase."""
    # Test default fuse size
    assert coordinator_single_phase.fuse_size == 25

    # Test available current calculation for single phase
    # With meter returning 14A active current and 25A fuse size
    available_current = coordinator_single_phase.get_available_current_for_phase(Phase.L1)
    assert available_current == 11  # floor(25 - 14) = 11


def test_single_phase_balancer_initialization(coordinator_single_phase):
    """Test that load balancer is initialized correctly for single phase."""
    # The balancer should be initialized with only L1 phase
    # This tests the async_setup method indirectly
    available_phases = coordinator_single_phase._available_phases
    assert len(available_phases) == 1
    assert available_phases[0] == Phase.L1


def test_single_phase_current_stability_check(coordinator_single_phase):
    """Test that single phase current stability checks work correctly."""
    # Set initial availability
    coordinator_single_phase._previous_current_availability = {Phase.L1: 5}

    # Test with same current - should not act
    assert not coordinator_single_phase._should_act_upon_availability({Phase.L1: 5})

    # Test with different current - should act
    assert coordinator_single_phase._should_act_upon_availability({Phase.L1: 3})

    # Test with first run (no previous availability) - should act
    coordinator_single_phase._previous_current_availability = None
    assert coordinator_single_phase._should_act_upon_availability({Phase.L1: 5})


def test_single_phase_multiple_update_cycles(coordinator_single_phase):
    """Test multiple update cycles work correctly for single phase."""
    # First cycle - overcurrent
    coordinator_single_phase._balancer_algo.compute_availability.return_value = {Phase.L1: -2}
    coordinator_single_phase._power_allocator.update_allocation.return_value = {
        TEST_CHARGER_ID: {Phase.L1: 14}
    }

    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Verify first call
    assert coordinator_single_phase._balancer_algo.compute_availability.call_count == 1
    assert coordinator_single_phase._power_allocator.update_allocation.call_count == 1

    # Second cycle - same values, should not update allocator
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Balancer should be called again but allocator should not (no change)
    assert coordinator_single_phase._balancer_algo.compute_availability.call_count == 2
    assert coordinator_single_phase._power_allocator.update_allocation.call_count == 1

    # Third cycle - different values
    coordinator_single_phase._balancer_algo.compute_availability.return_value = {Phase.L1: 3}
    coordinator_single_phase._power_allocator.update_allocation.return_value = {
        TEST_CHARGER_ID: {Phase.L1: 16}
    }

    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Both should be called again
    assert coordinator_single_phase._balancer_algo.compute_availability.call_count == 3
    assert coordinator_single_phase._power_allocator.update_allocation.call_count == 2


def test_single_phase_charger_update_delay(coordinator_single_phase):
    """Test that charger update delay works correctly for single phase."""
    # Set recent update time
    coordinator_single_phase._last_charger_target_update = (
        {Phase.L1: 15},
        int(datetime.now().timestamp()) - 10,  # 10 seconds ago
    )

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Balancer and allocator should still be called
    assert coordinator_single_phase._balancer_algo.compute_availability.called
    assert coordinator_single_phase._power_allocator.update_allocation.called

    # But charger should not be updated due to delay
    coordinator_single_phase._charger.set_current_limit.assert_not_called()


def test_single_phase_charger_update_after_delay(coordinator_single_phase):
    """Test that charger update happens after sufficient delay for single phase."""
    min_charge_minutes = of.EvseLoadBalancerOptionsFlow.get_option_value(
        coordinator_single_phase.config_entry, of.OPTION_CHARGE_LIMIT_HYSTERESIS
    )

    # Set update time far enough in the past
    coordinator_single_phase._last_charger_target_update = (
        {Phase.L1: 15},
        int(datetime.now().timestamp()) - (MIN_CHARGER_UPDATE_DELAY + 10 + (min_charge_minutes * 60)),
    )

    # Execute update cycle
    coordinator_single_phase._execute_update_cycle(datetime.now())

    # Charger should be updated
    coordinator_single_phase._charger.set_current_limit.assert_called_once()
