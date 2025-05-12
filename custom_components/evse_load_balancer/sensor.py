"""EVSE Load Balancer sensor platform."""

from collections.abc import Callable

from homeassistant import config_entries, core
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)

from .const import (
    COORDINATOR_STATES,
    DOMAIN,
    POSSIBLE_CHARGER_EV_STATUSES,
)
from .coordinator import EVSELoadBalancerCoordinator
from .load_balancer_phase_sensor import (
    SENSOR_KEY_AVAILABLE_CURRENT_L1,
    SENSOR_KEY_AVAILABLE_CURRENT_L2,
    SENSOR_KEY_AVAILABLE_CURRENT_L3,
    LoadBalancerPhaseSensor,
)
from .load_balancer_sensor import LoadBalancerSensor
from .utils import get_callable_name


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: Callable,
) -> None:
    """Set up sensors based on config entry."""
    coordinator: EVSELoadBalancerCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    sensors = [
        SensorCls(coordinator, entity_description)
        for SensorCls, entity_description in SENSORS
    ]
    async_add_entities(sensors, update_before_add=False)


SENSORS: tuple[tuple[SensorEntity, SensorEntityDescription], ...] = (
    (
        LoadBalancerSensor,
        SensorEntityDescription(
            key=get_callable_name(EVSELoadBalancerCoordinator.get_load_balancing_state),
            name="Load Balancing State",
            options=list(COORDINATOR_STATES),
            device_class=SensorDeviceClass.ENUM,
            entity_registry_enabled_default=True,
        ),
    ),
    (
        LoadBalancerSensor,
        SensorEntityDescription(
            key=get_callable_name(EVSELoadBalancerCoordinator.get_last_check_timestamp),
            name="Last Check",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_registry_enabled_default=False,
        ),
    ),
    (
        LoadBalancerPhaseSensor,
        SensorEntityDescription(
            key=SENSOR_KEY_AVAILABLE_CURRENT_L1,
            device_class=SensorDeviceClass.CURRENT,
            suggested_display_precision=0,
            entity_registry_enabled_default=False,
        ),
    ),
    (
        LoadBalancerPhaseSensor,
        SensorEntityDescription(
            key=SENSOR_KEY_AVAILABLE_CURRENT_L2,
            device_class=SensorDeviceClass.CURRENT,
            suggested_display_precision=0,
            entity_registry_enabled_default=False,
        ),
    ),
    (
        LoadBalancerPhaseSensor,
        SensorEntityDescription(
            key=SENSOR_KEY_AVAILABLE_CURRENT_L3,
            device_class=SensorDeviceClass.CURRENT,
            suggested_display_precision=0,
            entity_registry_enabled_default=False,
        ),
    ),
    (
        LoadBalancerSensor,
        SensorEntityDescription(
            key="charger_is_car_connected",  # This key must match the property name in the coordinator
            name="Car Connected",     # User-friendly name for this sensor
            icon="mdi:power-plug-outline",   # Suggests an icon related to connectivity
            entity_registry_enabled_default=False,
        ),
    ),
    (
        LoadBalancerSensor,
        SensorEntityDescription(
            key="charger_ev_status",
            name="Charger EV Status",
            device_class=SensorDeviceClass.ENUM,
            options=POSSIBLE_CHARGER_EV_STATUSES,
            icon="mdi:ev-station",
            entity_registry_enabled_default=False,
        ),
    ),
)
