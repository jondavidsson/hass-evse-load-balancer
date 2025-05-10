"""Amina Charger implementation using direct MQTT communication."""
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.core import callback as ha_core_callback
from homeassistant.helpers.device_registry import DeviceEntry

# Importera från dina befintliga filer
from ..const import Phase
from .charger import Charger, PhaseMode

_LOGGER = logging.getLogger(__name__)

# Zigbee2MQTT expose property names (nycklar i JSON-payloads)
Z2M_PROPERTY_CHARGE_LIMIT = "charge_limit"
Z2M_PROPERTY_SINGLE_PHASE = "single_phase"
Z2M_PROPERTY_EV_CONNECTED = "ev_connected"
Z2M_PROPERTY_EV_STATUS = "ev_status"
Z2M_PROPERTY_CHARGING = "charging" # Används ofta tillsammans med ev_status

# Hårdvarugränser för Amina S (enligt Z2M-dokumentationen)
AMINA_HW_MAX_CURRENT = 32  # Ampere
AMINA_HW_MIN_CURRENT = 6   # Ampere

# Bas-MQTT-ämnen för Zigbee2MQTT
Z2M_BASE_TOPIC_ROOT = "zigbee2mqtt"


class AminaCharger(Charger):
    """Representation of an Amina S Charger using MQTT."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        device: DeviceEntry,
    ) -> None:
        """Initialize the Amina Charger instance."""
        super().__init__(hass, config_entry, device)
        
        # Antag att device.name är Z2M Friendly Name.
        # Detta är ett viktigt antagande. Om friendly_name är annorlunda,
        # måste det hämtas på ett tillförlitligt sätt.
        self._z2m_friendly_name: str = device.name 
        if not self._z2m_friendly_name:
            # Fallback om device.name är tomt, men detta bör inte hända för en giltig DeviceEntry
            _LOGGER.error("Zigbee2MQTT friendly name could not be determined from device entry.")
            # Du kan behöva hämta det från config_entry.title eller annan källa
            # beroende på hur din config_flow sparar information.
            # För nu, kasta ett fel eller sätt ett placeholder som kommer misslyckas.
            raise ValueError("Cannot initialize AminaCharger: Zigbee2MQTT friendly name is missing.")

        self._topic_state: str = f"{Z2M_BASE_TOPIC_ROOT}/{self._z2m_friendly_name}"
        self._topic_set: str = f"{self._topic_state}/set"
        self._topic_get_base: str = f"{self._topic_state}/get"

        self._mqtt_listeners: list[callable] = []
        self._state_cache: dict[str, Any] = {
            Z2M_PROPERTY_CHARGE_LIMIT: AMINA_HW_MIN_CURRENT, # Default till min
            Z2M_PROPERTY_SINGLE_PHASE: False, # Default till multi-phase (False)
            Z2M_PROPERTY_EV_CONNECTED: False,
            Z2M_PROPERTY_EV_STATUS: "unknown", # Eller ett känt startvärde från Z2M som "disconnected"
            Z2M_PROPERTY_CHARGING: False,
        }
        self._is_mqtt_setup_done: bool = False

    async def async_setup_mqtt(self) -> None:
        """Subscribe to MQTT topics and request initial state. Should be called after __init__."""
        if self._is_mqtt_setup_done:
            _LOGGER.warning(f"AminaCharger {self._z2m_friendly_name}: MQTT setup already performed.")
            return

        @ha_core_callback
        def message_received(msg) -> None:
            """Handle new MQTT messages from the device's state topic."""
            try:
                payload_json = json.loads(msg.payload)
                _LOGGER.debug(f"MQTT message received on {msg.topic} for {self._z2m_friendly_name}: {payload_json}")
                
                # Uppdatera cacheminnet med alla relevanta nycklar från payloaden
                for key, value in payload_json.items():
                    if key in self._state_cache: # Vi bryr oss bara om nycklar vi spårar
                        # Särskild hantering för boolean-värden från Z2M som kan vara strängar
                        if key == Z2M_PROPERTY_SINGLE_PHASE or \
                           key == Z2M_PROPERTY_EV_CONNECTED or \
                           key == Z2M_PROPERTY_CHARGING:
                            if isinstance(value, str):
                                self._state_cache[key] = value.lower() == 'true' or value.lower() == 'on'
                            else:
                                self._state_cache[key] = bool(value)
                        else:
                            self._state_cache[key] = value
                        _LOGGER.debug(f"AminaCharger {self._z2m_friendly_name}: Cache updated for '{key}': {self._state_cache[key]}")
            
            except json.JSONDecodeError:
                _LOGGER.error(f"AminaCharger {self._z2m_friendly_name}: Error decoding JSON from MQTT topic {msg.topic}: {msg.payload}")
            except Exception as e:
                _LOGGER.error(f"AminaCharger {self._z2m_friendly_name}: Error processing MQTT message from {msg.topic}: {e}")

        # Prenumerera på huvudämnet för statusuppdateringar
        try:
            unsubscribe_state = await mqtt.async_subscribe(
                self.hass, self._topic_state, message_received, qos=0, encoding="utf-8"
            )
            self._mqtt_listeners.append(unsubscribe_state)
            self._is_mqtt_setup_done = True
            _LOGGER.info(f"AminaCharger '{self._z2m_friendly_name}': Subscribed to MQTT topic '{self._topic_state}'")

            # Begär initial status för nyckelegenskaper genom att publicera till deras /get-ämnen
            # Enligt Amina S Z2M-doku skickar man inget payload till dessa /get-ämnen.
            properties_to_get = [
                Z2M_PROPERTY_CHARGE_LIMIT,
                Z2M_PROPERTY_SINGLE_PHASE,
                Z2M_PROPERTY_EV_CONNECTED,
                Z2M_PROPERTY_EV_STATUS,
                Z2M_PROPERTY_CHARGING,
            ]
            for prop in properties_to_get:
                get_topic = f"{self._topic_get_base}/{prop}"
                _LOGGER.debug(f"AminaCharger '{self._z2m_friendly_name}': Requesting initial state for '{prop}' on topic '{get_topic}'")
                await mqtt.async_publish(self.hass, get_topic, payload="", qos=0, retain=False)
        
        except Exception as e:
            _LOGGER.error(f"AminaCharger '{self._z2m_friendly_name}': Failed to setup MQTT subscriptions or publish get requests: {e}")
            self._is_mqtt_setup_done = False # Återställ om setup misslyckades


    async def async_unload_mqtt(self) -> None:
        """Unsubscribe from MQTT topics when the integration is unloaded."""
        _LOGGER.info(f"AminaCharger '{self._z2m_friendly_name}': Unsubscribing from MQTT topics.")
        for unsubscribe in self._mqtt_listeners:
            try:
                unsubscribe()
            except Exception as e:
                _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': Error during MQTT unsubscribe: {e}")
        self._mqtt_listeners.clear()
        self._is_mqtt_setup_done = False

    def _publish_to_set_topic(self, payload_dict: dict[str, Any]) -> None:
        """Internal helper to publish a command to the Zigbee2MQTT /set topic."""
        # Denna metod är synkron och anropar en asynkron HA-tjänst.
        # hass.services.call hanterar detta genom att schemalägga anropet.
        payload_str = json.dumps(payload_dict)
        _LOGGER.debug(f"AminaCharger '{self._z2m_friendly_name}': Publishing to '{self._topic_set}': {payload_str}")
        self.hass.services.call(
            mqtt.DOMAIN,
            mqtt.SERVICE_PUBLISH,
            {
                mqtt.ATTR_TOPIC: self._topic_set,
                mqtt.ATTR_PAYLOAD: payload_str,
                mqtt.ATTR_QOS: 1, # Använd QoS 1 för kommandon för att öka chansen att de når fram
                mqtt.ATTR_RETAIN: False,
            },
            blocking=False, # Blockera inte HA event loop från en synkron metod
        )

    async def _async_publish_to_set_topic(self, payload_dict: dict[str, Any]) -> None:
        """Internal async helper to publish a command to the Zigbee2MQTT /set topic."""
        payload_str = json.dumps(payload_dict)
        _LOGGER.debug(f"AminaCharger '{self._z2m_friendly_name}': Async publishing to '{self._topic_set}': {payload_str}")
        await mqtt.async_publish(
            self.hass,
            self._topic_set,
            payload_str,
            qos=1,
            retain=False,
        )

    # --- Implementation av Charger basklassens abstrakta metoder ---

    def set_phase_mode(self, mode: PhaseMode, phase: Phase) -> None:
        """Set the phase mode of the charger via MQTT."""
        # Amina S 'single_phase' är en boolean: true för enfas, false för trefas.
        # 'phase'-argumentet (L1, L2, L3) verkar irrelevant för Amina S:s globala inställning.
        if not self._is_mqtt_setup_done:
            _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': MQTT not set up, cannot set phase mode.")
            return

        value_to_set: bool | None = None
        if mode == PhaseMode.SINGLE:
            value_to_set = True
        elif mode == PhaseMode.MULTI:
            value_to_set = False
        
        if value_to_set is not None:
            _LOGGER.info(f"AminaCharger '{self._z2m_friendly_name}': Setting single_phase to {value_to_set} (Mode: {mode}) via MQTT.")
            self._publish_to_set_topic({Z2M_PROPERTY_SINGLE_PHASE: value_to_set})
        else:
            _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': Unknown PhaseMode requested for set_phase_mode: {mode}")

    async def set_current_limit(self, limit: dict[Phase, int]) -> None:
        """Set the charger limit in amps via MQTT."""
        if not self._is_mqtt_setup_done:
            _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': MQTT not set up, cannot set current limit.")
            return

        target_current = None
        # Amina har en global strömgräns, vi tar L1 som referens eller första giltiga.
        if Phase.L1 in limit and limit[Phase.L1] is not None:
            target_current = limit[Phase.L1]
        elif limit: 
            for phase_val in limit.values():
                if phase_val is not None:
                    target_current = phase_val
                    break
        
        if target_current is None:
            _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': No valid current limit provided in limit dictionary: {limit}")
            return

        # Begränsa till hårdvarans min/max
        clamped_current = max(AMINA_HW_MIN_CURRENT, min(AMINA_HW_MAX_CURRENT, int(target_current)))
        if clamped_current != target_current:
            _LOGGER.warning(
                f"AminaCharger '{self._z2m_friendly_name}': Requested current {target_current}A is outside hardware limits "
                f"({AMINA_HW_MIN_CURRENT}A-{AMINA_HW_MAX_CURRENT}A). Clamping to {clamped_current}A."
            )
        
        _LOGGER.info(f"AminaCharger '{self._z2m_friendly_name}': Setting {Z2M_PROPERTY_CHARGE_LIMIT} to {clamped_current}A via MQTT.")
        await self._async_publish_to_set_topic({Z2M_PROPERTY_CHARGE_LIMIT: clamped_current})

    def get_current_limit(self) -> dict[Phase, int] | None:
        """Get the current limit of the charger in amps from internal cache."""
        if not self._is_mqtt_setup_done:
            _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': MQTT not set up, cannot get current limit from cache (cache may be stale).")
            # Returnera None eller defaultvärde om MQTT inte är igång? För nu, fortsätt med cache.
            
        current_limit_val = self._state_cache.get(Z2M_PROPERTY_CHARGE_LIMIT)
        is_single_phase = self._state_cache.get(Z2M_PROPERTY_SINGLE_PHASE, False)

        if current_limit_val is None:
            _LOGGER.debug(f"AminaCharger '{self._z2m_friendly_name}': {Z2M_PROPERTY_CHARGE_LIMIT} not found in cache.")
            return None
        
        try:
            current_limit_val = int(float(current_limit_val)) # Säkerställ int
        except (ValueError, TypeError):
            _LOGGER.error(f"AminaCharger '{self._z2m_friendly_name}': Invalid value for {Z2M_PROPERTY_CHARGE_LIMIT} in cache: {current_limit_val}")
            return None

        if is_single_phase:
            return {Phase.L1: current_limit_val, Phase.L2: 0, Phase.L3: 0}
        else:
            return {
                Phase.L1: current_limit_val,
                Phase.L2: current_limit_val,
                Phase.L3: current_limit_val,
            }

    def get_max_current_limit(self) -> dict[Phase, int] | None:
        """Get the configured maximum current limit of the charger in amps."""
        # Detta är hårdvarumax enligt Z2M-definitionen för Amina S.
        max_val = AMINA_HW_MAX_CURRENT
        return {
            Phase.L1: max_val,
            Phase.L2: max_val,
            Phase.L3: max_val,
        }

    def car_connected(self) -> bool:
        """Return whether the car is connected to the charger from internal cache."""
        if not self._is_mqtt_setup_done:
            _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': MQTT not set up, car_connected from cache may be stale.")

        connected = self._state_cache.get(Z2M_PROPERTY_EV_CONNECTED, False)
        return bool(connected)

    def can_charge(self) -> bool:
        """Return whether the car is connected and charging or accepting charge from internal cache."""
        if not self._is_mqtt_setup_done:
            _LOGGER.warning(f"AminaCharger '{self._z2m_friendly_name}': MQTT not set up, can_charge from cache may be stale.")

        if not self.car_connected(): # Använder vår egen car_connected som läser från cache
            return False

        ev_status = str(self._state_cache.get(Z2M_PROPERTY_EV_STATUS, "unknown"))
        _LOGGER.debug(f"AminaCharger '{self._z2m_friendly_name}': can_charge check -> ev_status from cache: '{ev_status}'")
        
        # Exakta strängvärden från Z2M för Amina S ev_status är viktiga här.
        # De som nämnts i Z2M-doku är: "disconnected", "connected", "charging", "error", 
        # "ready_to_charge", "paused", "resuming_charge".
        # "Not Connected" såg vi i en tidigare dump, vilket troligen motsvarar "disconnected".
        return ev_status.lower() in ("charging", "ready_to_charge", "resuming_charge")
    
    def get_ev_status(self) -> str | None:
        """
        Get the raw EV status string from the internal cache.
        This method will be called by the coordinator to update a sensor.
        """
        if not self._is_mqtt_setup_done:
            _LOGGER.warning(
                f"AminaCharger '{self._z2m_friendly_name}': MQTT not set up, "
                f"get_ev_status from cache may be stale."
            )
            # Return the cached value anyway, or None, or the default "unknown"
            # Depending on how you want sensors to behave if MQTT is down.
            # Returning the cached value (even if potentially stale with a warning) is often acceptable.
        current_status = self._state_cache.get(Z2M_PROPERTY_EV_STATUS)
        _LOGGER.debug(f"AminaCharger '{self._z2m_friendly_name}': get_ev_status returning: {current_status}")
        return current_status