"""Amina Charger implementation using direct MQTT communication."""

import asyncio
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt.models import PublishPayloadType, ReceiveMessage
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.core import callback as ha_core_callback

_LOGGER = logging.getLogger(__name__)

# Base MQTT topics for Zigbee2MQTT
Z2M_BASE_TOPIC_ROOT = "zigbee2mqtt"


class Zigbee2Mqtt:
    """Representation of an Amina S Charger using MQTT."""

    def __init__(
        self,
        hass: HomeAssistant,
        z2m_name: str,
        state_cache: dict[str, Any],
    ) -> None:
        """Init the base for chargers using Z2M."""
        self.hass = hass
        self._z2m_name = z2m_name

        self._topic_state: str = f"{Z2M_BASE_TOPIC_ROOT}/{self._z2m_name}"
        self._topic_set: str = f"{self._topic_state}/set"
        self._topic_get_base: str = f"{self._topic_state}/get"

        self._mqtt_listener: CALLBACK_TYPE | None = None
        self._pending_requests: dict[str, asyncio.Future[Any]] = {}

        """
        Property containing state cache.

        Each key represents a key included in the MQQT state message.
        The values are the latest known state received on the topic.
        """
        self._state_cache = dict(state_cache)

    async def async_setup_mqtt(self) -> None:
        """Subscribe to MQTT topics and request initial state. Called after __init__."""
        _LOGGER.debug("Setting up MQTT subscription for '%s'.", self._topic_state)
        if self._mqtt_listener is not None:
            _LOGGER.warning("MQTT setup already performed.")
            return

        await self.setup_mqtt_connection()
        await self.initialize_state_cache()

    async def setup_mqtt_connection(self) -> None:
        """Set up the MQTT connection and subscribe to the state topic."""
        _LOGGER.debug("Attempting to subscribe to '%s'", self._topic_state)
        mqtt_listener = await mqtt.async_subscribe(
            self.hass, self._topic_state, self.message_received, qos=0, encoding="utf-8"
        )
        self._mqtt_listener = mqtt_listener
        _LOGGER.debug("Successfully subscribed to MQTT topic '%s'.", self._topic_state)

    @ha_core_callback
    def message_received(self, msg: ReceiveMessage) -> None:
        """
        Handle new MQTT messages from the device's state topic.

        Listens for messages coming in and parses their body as JSON.
        For any key known to the state cache, updates the cache with the new value.
        """
        _LOGGER.debug(
            "Message received on topic '{%s}'. Payload: '%s'",
            msg.topic,
            msg.payload,
        )
        try:
            payload_json = json.loads(msg.payload)
            updated_properties: list[str] = []
            for key, value in payload_json.items():
                if key in self._state_cache:
                    processed_value = self._serialize_value(value)
                    self._state_cache[key] = processed_value
                    updated_properties.append(key)

            # Resolve pending_requests futures (async_get_property calls)
            for property_name in updated_properties:
                prop_future = self._pending_requests.get(property_name, None)
                if prop_future and not prop_future.done():
                    prop_future.set_result(self._state_cache[property_name])

        except json.JSONDecodeError:
            _LOGGER.exception(
                "Error decoding JSON from MQTT topic '%s': '%s'",
                msg.topic,
                msg.payload,
            )
        except Exception:
            _LOGGER.exception(
                "Error processing MQTT message from '%s'",
                msg.topic,
            )

    async def async_get_property(self, property_name: str, timeout: float = 5.0) -> Any:  # noqa: ASYNC109
        """Get a property value with proper request-response correlation."""
        response_future = self.hass.loop.create_future()
        self._pending_requests[property_name] = response_future

        try:
            await self._async_mqtt_publish(
                topic=self._topic_get_base, payload={property_name: ""}, qos=1
            )
            return await asyncio.wait_for(response_future, timeout)
        except TimeoutError:
            _LOGGER.warning("Timeout waiting for response to '%s'", property_name)
            return None
        finally:
            self._pending_requests.pop(property_name, None)

    async def initialize_state_cache(self) -> None:
        """Initialize the state cache by requesting initial values via MQTT."""
        for state_key in self._state_cache:
            _LOGGER.debug(
                "Requesting initial state for '%s'.",
                state_key,
            )
            try:
                self._state_cache[state_key] = await self.async_get_property(state_key)
                _LOGGER.debug(
                    'Update initial state for "%s": %s',
                    state_key,
                    self._state_cache[state_key],
                )
            except TimeoutError:
                _LOGGER.warning(
                    "Failed to receive initial value for '%s', timeout.'",
                    state_key,
                )

    def _mqtt_is_setup(self) -> bool:
        return self._mqtt_listener is not None

    async def _async_mqtt_publish(
        self,
        topic: str | dict,
        payload: dict | PublishPayloadType,
        qos: int = 0,
    ) -> None:
        if not self._mqtt_is_setup():
            _LOGGER.error("MQTT not set up, cannot publish to topic '%s'.", topic)
            return

        if isinstance(payload, dict):
            payload = json.dumps(payload)

        await mqtt.async_publish(self.hass, topic, payload=payload, qos=qos)

    def _serialize_value(self, value: Any) -> str:
        # Serialize possible boolean values
        if isinstance(value, str):
            if value.lower() in (
                "true",
                "on",
                "enable",
                "1",
            ):
                value = True
            elif value.lower() in (
                "false",
                "off",
                "disable",
                "0",
            ):
                value = False

        return value

    async def async_unload_mqtt(self) -> None:
        """Unsubscribe from MQTT topic."""
        if not self._mqtt_is_setup():
            _LOGGER.warning("MQQT not set up, skipping unload")
            return

        self._mqtt_listener()
        self._mqtt_listener = None

        for future in self._pending_requests.values():
            if not future.done():
                future.cancel("MQTT connection unloaded before response received")
        self._pending_requests.clear()
