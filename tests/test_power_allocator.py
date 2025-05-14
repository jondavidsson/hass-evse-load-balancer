"""Tests for PowerAllocator class."""

import pytest
from custom_components.evse_load_balancer.power_allocator import PowerAllocator
from custom_components.evse_load_balancer.const import Phase
from .helpers.mock_charger import MockCharger


@pytest.fixture
def power_allocator():
    """Fixture for PowerAllocator."""
    return PowerAllocator()


def test_add_charger_successful(power_allocator):
    """Test successfully adding a new charger."""
    # Create a real mock charger with initial current of 10A
    mock_charger = MockCharger(initial_current=10)

    assert power_allocator.add_charger("charger1", mock_charger) is True
    assert "charger1" in power_allocator._chargers
    assert power_allocator._chargers["charger1"].charger == mock_charger
    assert power_allocator._chargers["charger1"].requested_current == {
        Phase.L1: 10, Phase.L2: 10, Phase.L3: 10
    }
    assert power_allocator._chargers["charger1"].last_set_current == {
        Phase.L1: 10, Phase.L2: 10, Phase.L3: 10
    }


def test_add_charger_already_exists(power_allocator):
    """Test adding a charger that already exists."""
    # Add the first charger
    first_charger = MockCharger(initial_current=10)
    power_allocator.add_charger("charger1", first_charger)

    # Try to add another charger with the same ID
    second_charger = MockCharger(initial_current=16)

    assert power_allocator.add_charger("charger1", second_charger) is False
    # The original charger should still be there
    assert power_allocator._chargers["charger1"].charger == first_charger


def test_add_charger_initialization_fails(power_allocator):
    """Test adding a charger that fails to initialize."""
    # Create a mock charger that will return None for get_current_limit
    mock_charger = MockCharger(initial_current=10)
    # Make get_current_limit return None to simulate initialization failure
    mock_charger.get_current_limit = lambda: None

    assert power_allocator.add_charger("charger1", mock_charger) is False
    assert "charger1" not in power_allocator._chargers


def test_should_monitor(power_allocator):
    """Test should_monitor method."""
    # Add two chargers with different can_charge states
    charger1 = MockCharger()
    charger1.set_can_charge(True)

    charger2 = MockCharger()
    charger2.set_can_charge(False)

    power_allocator.add_charger("charger1", charger1)
    power_allocator.add_charger("charger2", charger2)

    # With one charger that can charge, should_monitor should return True
    assert power_allocator.should_monitor() is True

    # If no charger can charge, should_monitor should return False
    charger1.set_can_charge(False)
    assert power_allocator.should_monitor() is False


def test_update_allocation_overcurrent(power_allocator):
    """Test update_allocation method with overcurrent situation."""
    # Create and add a charger
    charger = MockCharger(initial_current=10)
    charger.set_can_charge(True)
    power_allocator.add_charger("charger1", charger)

    # Simulate overcurrent
    available_currents = {
        Phase.L1: -8,
        Phase.L2: -2,
        Phase.L3: 2
    }

    result = power_allocator.update_allocation(available_currents)

    # Verify results
    assert "charger1" in result
    assert result["charger1"] == {
        Phase.L1: 2,   # 10 - 8 = 2
        Phase.L2: 8,   # 10 - 2 = 8
        Phase.L3: 10   # No change needed
    }


def test_update_allocation_recovery(power_allocator):
    """Test update_allocation method with recovery situation."""
    # Create and add a charger that's been reduced
    charger = MockCharger(initial_current=16)
    charger.set_can_charge(True)
    # Set current limit lower than the requested limit
    charger.set_current_limits({
        Phase.L1: 7,
        Phase.L2: 8,
        Phase.L3: 10
    })
    power_allocator.add_charger("charger1", charger)

    # Make sure the power allocator knows the requested current
    power_allocator._chargers["charger1"].requested_current = {
        Phase.L1: 16,
        Phase.L2: 16,
        Phase.L3: 16
    }

    # Simulate recovery with available capacity
    available_currents = {
        Phase.L1: 5,
        Phase.L2: 5,
        Phase.L3: 5
    }

    result = power_allocator.update_allocation(available_currents)

    # Verify results
    assert "charger1" in result
    assert result["charger1"] == {
        Phase.L1: 12,  # 7 + 5 = 12 (still below requested 16)
        Phase.L2: 13,  # 8 + 5 = 13 (still below requested 16)
        Phase.L3: 15   # 10 + 5 = 15 (still below requested 16)
    }


def test_manual_override_detection(power_allocator):
    """Test manual override detection."""
    # Create a charger
    charger = MockCharger(initial_current=10)
    power_allocator.add_charger("charger1", charger)

    # Simulate manual override by changing the limits outside the allocator
    charger.set_current_limits({
        Phase.L1: 16,
        Phase.L2: 16,
        Phase.L3: 16
    })

    # Check if the override is detected
    state = power_allocator._chargers["charger1"]
    assert state.detect_manual_override() is True
    assert state.manual_override_detected is True
    # The requested current should be updated to the new values
    assert state.requested_current == {
        Phase.L1: 16,
        Phase.L2: 16,
        Phase.L3: 16
    }


def test_multiple_chargers_allocation(power_allocator):
    """Test allocating current to multiple chargers."""
    # Create two chargers
    charger1 = MockCharger(initial_current=10)
    charger1.set_can_charge(True)

    charger2 = MockCharger(initial_current=16)
    charger2.set_can_charge(True)

    power_allocator.add_charger("charger1", charger1)
    power_allocator.add_charger("charger2", charger2)

    # Simulate overcurrent
    available_currents = {
        Phase.L1: -10,
        Phase.L2: -4,
        Phase.L3: 0
    }

    result = power_allocator.update_allocation(available_currents)

    # Verify results - both chargers should be reduced proportionally
    assert "charger1" in result
    assert "charger2" in result

    # charger1 uses 10A, charger2 uses 16A, total 26A
    # For Phase.L1: charger1 should get -10 * (10/26) = -3.85 ≈ -4
    # For Phase.L1: charger2 should get -10 * (16/26) = -6.15 ≈ -7
    assert result["charger1"][Phase.L1] == 6  # 10 - 4 = 6
    assert result["charger2"][Phase.L1] == 9  # 16 - 7 = 9
