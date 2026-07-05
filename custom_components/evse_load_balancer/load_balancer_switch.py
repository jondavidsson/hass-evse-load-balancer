"""Load Balancer switch platform."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import EVSELoadBalancerCoordinator

_LOGGER = logging.getLogger(__name__)


class ManualOverrideSwitch(SwitchEntity):
    """
    Switch reflecting and controlling the charger's manual override lock.

    ON locks the charger at its current setting so the balancer won't
    auto-increase it (overcurrent cuts still apply). OFF releases the lock
    and restores the charger's full requested capacity.
    """

    def __init__(
        self,
        coordinator: EVSELoadBalancerCoordinator,
        entity_description: SwitchEntityDescription,
    ) -> None:
        """Initialize the ManualOverrideSwitch."""
        super().__init__()
        self.entity_description = entity_description
        self._coordinator = coordinator
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.config_entry.entry_id)},
            name="EVSE Load Balancer",
            manufacturer="EnergyLabs",
            configuration_url=(
                "https://github.com/dirkgroenen/hass-evse-load-balancer"
            ),
        )

        coordinator.register_sensor(self)

    @property
    def is_on(self) -> bool:
        """Return whether a manual override is currently active."""
        return self._coordinator.manual_override_active

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Lock the charger at its current setting."""
        self._coordinator.set_manual_override(active=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Release the manual override lock."""
        self._coordinator.set_manual_override(active=False)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the switch from the coordinator."""
        self._coordinator.unregister_sensor(self)
