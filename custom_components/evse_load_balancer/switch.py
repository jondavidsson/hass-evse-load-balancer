"""EVSE Load Balancer switch platform."""

from typing import TYPE_CHECKING

from homeassistant import config_entries, core
from homeassistant.components.switch import SwitchEntityDescription

from .const import DOMAIN
from .load_balancer_switch import ManualOverrideSwitch

if TYPE_CHECKING:
    from collections.abc import Callable

    from .coordinator import EVSELoadBalancerCoordinator


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: "Callable",
) -> None:
    """Set up switches based on config entry."""
    coordinator: EVSELoadBalancerCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    switches = [
        SwitchCls(coordinator, entity_description)
        for SwitchCls, entity_description in SWITCHES
    ]
    async_add_entities(switches, update_before_add=False)


SWITCHES: tuple[tuple[type[ManualOverrideSwitch], SwitchEntityDescription], ...] = (
    (
        ManualOverrideSwitch,
        SwitchEntityDescription(
            key="manual_override",
            translation_key="evse_manual_override",
            entity_registry_enabled_default=True,
        ),
    ),
)
