import asyncio
import json
import logging
from unittest.mock import MagicMock, patch
import pytest
from homeassistant.components.mqtt.models import ReceiveMessage
from custom_components.evse_load_balancer.chargers.util.zigbee2mqtt import Zigbee2Mqtt


@pytest.fixture
def z2m(hass):
    """Return a Zigbee2Mqtt instance."""
    return Zigbee2Mqtt(
        hass=hass,
        z2m_name="test_device",
        state_cache={"power": None, "current": None, "is_connected": None},
    )


@pytest.mark.asyncio
async def test_async_unload_mqtt_not_setup(z2m, caplog):
    """Test async_unload_mqtt when MQTT is not set up."""
    caplog.set_level(logging.WARNING)
    z2m._mqtt_listener = None

    await z2m.async_unload_mqtt()

    assert "MQQT not set up, skipping unload" in caplog.text


@pytest.mark.asyncio
async def test_async_unload_mqtt_with_setup(z2m):
    """Test async_unload_mqtt when MQTT is set up."""
    mock_listener = MagicMock()
    z2m._mqtt_listener = mock_listener

    await z2m.async_unload_mqtt()

    mock_listener.assert_called_once()

    assert z2m._mqtt_listener is None
    assert len(z2m._pending_requests) == 0


@pytest.mark.asyncio
async def test_async_unload_mqtt_with_pending_requests(z2m):
    """Test async_unload_mqtt with pending requests."""
    mock_listener = MagicMock()
    z2m._mqtt_listener = mock_listener

    # Create a pending request that is not done
    pending_future = asyncio.Future()

    # Create a pending request that is done
    completed_future = asyncio.Future()
    completed_future.set_result("result")

    z2m._pending_requests = {
        "pending": pending_future,
        "completed": completed_future,
    }

    await z2m.async_unload_mqtt()

    # Verify listener was called
    mock_listener.assert_called_once()

    # Verify listener was set to None
    assert z2m._mqtt_listener is None

    # Verify pending requests were cleared
    assert len(z2m._pending_requests) == 0

    # Verify pending future was cancelled
    assert pending_future.cancelled()
    assert completed_future.done()
    assert not completed_future.cancelled()
    assert completed_future.result() == "result"


@pytest.mark.asyncio
async def test_message_received_updates_state_cache(z2m):
    """Test that received messages update the state cache correctly."""
    # Create a mock MQTT message
    message = ReceiveMessage(
        topic="zigbee2mqtt/test_device",
        payload=json.dumps({"power": 1000, "is_connected": "on", "unknown_prop": "value"}),
        qos=0,
        retain=False,
        subscribed_topic="zigbee2mqtt/test_device",
        timestamp=0,
    )

    # Process the message
    z2m.message_received(message)

    # Verify state cache was updated with the known properties
    assert z2m._state_cache["power"] == 1000
    assert z2m._state_cache["is_connected"] is True  # Should be converted by _serialize_value

    # Unknown properties should not be added
    assert "unknown_prop" not in z2m._state_cache


@pytest.mark.asyncio
async def test_message_received_resolves_pending_requests(z2m):
    """Test that received messages resolve pending property requests."""
    # Create pending futures
    power_future = asyncio.Future()
    state_future = asyncio.Future()
    z2m._pending_requests = {
        "power": power_future,
        "is_connected": state_future,
        "other_prop": asyncio.Future(),
    }

    # Create a mock MQTT message
    message = ReceiveMessage(
        topic="zigbee2mqtt/test_device",
        payload=json.dumps({"power": 1000, "is_connected": "off"}),
        qos=0,
        retain=False,
        subscribed_topic="zigbee2mqtt/test_device",
        timestamp=0,
    )

    # Process the message
    z2m.message_received(message)

    assert power_future.done()
    assert power_future.result() == 1000

    assert state_future.done()
    assert state_future.result() is False

    # Future for property not in the message should not be resolved
    assert not z2m._pending_requests["other_prop"].done()


def test_serialize_value(z2m):
    """Test the _serialize_value method for correct value conversion."""
    value_maps = {
        True: ["on", "true", "1"],
        False: ["off", "false", "0"],
    }
    for expected, values in value_maps.items():
        for value in values:
            assert z2m._serialize_value(value) is expected


@pytest.mark.asyncio
async def test_message_received_handles_json_error(z2m, caplog):
    """Test that message_received handles JSON decode errors gracefully."""
    caplog.set_level(logging.ERROR)

    # Create a mock MQTT message with invalid JSON
    message = ReceiveMessage(
        topic="zigbee2mqtt/test_device",
        payload="this is not valid json",
        qos=0,
        retain=False,
        subscribed_topic="zigbee2mqtt/test_device",
        timestamp=0,
    )

    z2m.message_received(message)

    assert "Error decoding JSON" in caplog.text
    assert z2m._state_cache["power"] is None


@pytest.mark.asyncio
async def test_async_get_property_success(z2m):
    """Test successful property retrieval with async_get_property."""
    with patch.object(z2m, '_async_mqtt_publish') as mock_publish:
        z2m._mqtt_is_setup = MagicMock(return_value=True)

        # Create a background task that will simulate a response message
        async def simulate_response():
            await asyncio.sleep(0.1)
            message = ReceiveMessage(
                topic=z2m._topic_state,
                payload=json.dumps({"power": 1500}),
                qos=0,
                retain=False,
                subscribed_topic=z2m._topic_state,
                timestamp=0,
            )
            z2m.message_received(message)

        # Start the background task
        asyncio.create_task(simulate_response())

        # Call the function under test
        result = await z2m.async_get_property("power")

        # Verify the result
        assert result == 1500

        # Verify the MQTT publish was called with the right arguments
        mock_publish.assert_called_once_with(
            topic="zigbee2mqtt/test_device/get",
            payload={"power": ""},
            qos=1
        )

        # Verify the pending request was removed
        assert "power" not in z2m._pending_requests


@pytest.mark.asyncio
async def test_async_get_property_timeout(z2m):
    """Test property retrieval timeout with async_get_property."""
    # Mock the MQTT publish function and asyncio.wait_for
    with patch.object(z2m, '_async_mqtt_publish') as mock_publish:
        with patch('asyncio.wait_for', side_effect=TimeoutError):
            z2m._mqtt_is_setup = MagicMock(return_value=True)

            result = await z2m.async_get_property("power", timeout=0.1)
            assert result is None  # None due to timeout

            mock_publish.assert_called_once()

            # Verify the pending request was removed despite timeout
            assert "power" not in z2m._pending_requests


@pytest.mark.asyncio
async def test_initialize_state_cache(z2m):
    """Test initialize_state_cache requests all properties."""
    async def mock_get_property(property_name, timeout=5.0):
        values = {
            "power": 2000,
            "current": 8.5
        }
        return values.get(property_name)

    with patch.object(z2m, 'async_get_property', side_effect=mock_get_property):
        # Call initialize_state_cache
        await z2m.initialize_state_cache()

        # Verify state cache was updated with all values
        assert z2m._state_cache["power"] == 2000
        assert z2m._state_cache["current"] == 8.5


@pytest.mark.asyncio
async def test_initialize_state_cache_handles_timeouts(z2m, caplog):
    """Test initialize_state_cache handles timeouts gracefully."""
    caplog.set_level(logging.WARNING)

    # Mock async_get_property to simulate a timeout for one property
    async def mock_get_property(property_name, timeout=5.0):
        if property_name == "power":
            raise TimeoutError()
        values = {
            "current": 8.5
        }
        return values.get(property_name)

    with patch.object(z2m, 'async_get_property', side_effect=mock_get_property):
        await z2m.initialize_state_cache()
        assert z2m._state_cache["current"] == 8.5
        assert z2m._state_cache["power"] is None
        assert "Failed to receive initial value for 'power'" in caplog.text


@pytest.mark.asyncio
async def test_setup_mqtt_connection_and_message_handling(z2m):
    """Test setup_mqtt_connection subscribes to the correct topic and handles updates."""
    with patch("custom_components.evse_load_balancer.chargers.util.zigbee2mqtt.mqtt.async_subscribe") as mock_subscribe:
        callback_holder = {}

        async def fake_subscribe(hass, topic, callback, qos=0, encoding="utf-8"):
            callback_holder["callback"] = callback
            callback_holder["topic"] = topic
            return MagicMock()  # Simulate listener

        mock_subscribe.side_effect = fake_subscribe

        await z2m.setup_mqtt_connection()

        # Verify correct topic subscription
        assert callback_holder["topic"] == z2m._topic_state

        # Simulate receiving a message on the topic
        message = ReceiveMessage(
            topic=callback_holder["topic"],
            payload=json.dumps({"power": 1234}),
            qos=0,
            retain=False,
            subscribed_topic=callback_holder["topic"],
            timestamp=0,
        )
        callback_holder["callback"](message)

        # Check that the state_cache was updated
        assert z2m._state_cache["power"] == 1234
