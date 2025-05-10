"""EVSE Load Balancer Chargers."""

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..const import (
    CHARGER_DOMAIN_EASEE,
    CHARGER_DOMAIN_ZAPTEC,
    Z2M_DEVICE_IDENTIFIER_DOMAIN,
    MANUFACTURER_AMINA,
)
from .charger import Charger
from .easee_charger import EaseeCharger
from .amina_charger import AminaCharger

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceEntry

_LOGGER = logging.getLogger(__name__)


async def charger_factory(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry_id: str
) -> Charger:
    
    registry = dr.async_get(hass)
    device: DeviceEntry | None = registry.async_get(device_entry_id)

    if not device:
        msg = f"Device with ID {device_entry_id} not found in registry."
        _LOGGER.error(msg)
        raise ValueError(msg)

    _LOGGER.debug(
        f"Charger Factory: Device='{device.name}', Manufacturer='{device.manufacturer}', "
        f"Model='{device.model}', Identifiers='{device.identifiers}'"
    )

    if any(domain == CHARGER_DOMAIN_EASEE for domain, _ in device.identifiers):
        _LOGGER.info(f"Creating EaseeCharger for {device.name}")
        return EaseeCharger(hass, config_entry, device)

    if any(domain == CHARGER_DOMAIN_ZAPTEC for domain, _ in device.identifiers):
        _LOGGER.info(f"Attempting to create ZaptecCharger for {device.name}")
        _LOGGER.warning(f"ZaptecCharger class not implemented or instantiated for {device.name}")

    is_z2m_device_by_identifier = any(
        id_domain == Z2M_DEVICE_IDENTIFIER_DOMAIN for id_domain, _ in device.identifiers
    )

    if is_z2m_device_by_identifier:
        if device.manufacturer == MANUFACTURER_AMINA:
            _LOGGER.info(f"Creating AminaCharger for {device.name}")
            return AminaCharger(hass, config_entry, device)
        else:
            _LOGGER.warning(
                f"Z2M device '{device.name}' (Manuf: '{device.manufacturer}') not a supported Amina charger."
            )

    error_message_detail = (
        f"Identifiers: {device.identifiers}, Manufacturer: {device.manufacturer}, Model: {device.model}"
    )
    _LOGGER.error(
        f"Unsupported charger: No specific charger class matched for {device.name}. Details: {error_message_detail}"
    )
    raise ValueError(
        f"Unsupported charger: No specific charger class matched for {device.name}. Details: {error_message_detail}"
    )