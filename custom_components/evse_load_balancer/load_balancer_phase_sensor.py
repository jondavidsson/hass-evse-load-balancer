"""EVSE Load Balancer sensor platform."""

import logging
from functools import cached_property

from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription
from homeassistant.components.sensor.const import UnitOfElectricCurrent
from homeassistant.helpers.entity import (
    EntityCategory,
)

from .const import (
    Phase,
)
from .coordinator import EVSELoadBalancerCoordinator
from .load_balancer_sensor import LoadBalancerSensor

_LOGGER = logging.getLogger(__name__)

SENSOR_KEY_AVAILABLE_CURRENT_L1 = "available_current_l1"
SENSOR_KEY_AVAILABLE_CURRENT_L2 = "available_current_l2"
SENSOR_KEY_AVAILABLE_CURRENT_L3 = "available_current_l3"


class LoadBalancerPhaseSensor(LoadBalancerSensor):
    """Representation of a EVSE Load Balancer sensor."""

    def __init__(
        self,
        coordinator: EVSELoadBalancerCoordinator,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Initialize the LoadBalancerPhaseSensor."""
        super().__init__(coordinator, entity_description)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

        coordinator.register_sensor(self)

    @property
    def native_value(self) -> int | None:
        """Return the available current from the coordinator."""
        if self.entity_description.device_class == SensorDeviceClass.CURRENT:
            return self._coordinator.get_available_current_for_phase(self._phase)

        _LOGGER.error(
            (
                f"Cant get sensor value. Sensor {self.entity_description.key}",
                "has an invalid device class: {self.entity_description.device_class}.",
            )
        )

        return None

    @cached_property
    def _phase(self) -> Phase:
        """Return the phase for the sensor."""
        key = self.entity_description.key
        if key in [SENSOR_KEY_AVAILABLE_CURRENT_L1]:
            return Phase.L1
        if key in [SENSOR_KEY_AVAILABLE_CURRENT_L2]:
            return Phase.L2
        if key in [SENSOR_KEY_AVAILABLE_CURRENT_L3]:
            return Phase.L3
        msg = f"No phase for invalid sensor key: {key}"
        raise ValueError(msg)
