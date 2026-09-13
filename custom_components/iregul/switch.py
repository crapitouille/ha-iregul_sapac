"""Interrupteurs i-regul : autorisation chauffage / rafraîchissement (commande 12)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import IRegulConfigEntry, IRegulCoordinator
from .entity import IRegulEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRegulConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les deux interrupteurs d'autorisation."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            IRegulAuthSwitch(coordinator, "autorisation_chauffage", "Autorisation chauffage", "mdi:radiator"),
            IRegulAuthSwitch(coordinator, "autorisation_rafraichissement", "Autorisation rafraîchissement", "mdi:snowflake"),
        ]
    )


class IRegulAuthSwitch(IRegulEntity, SwitchEntity):
    """Autorisation globale (C@0&autorisation_*)."""

    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: IRegulCoordinator, field: str, name: str, icon: str) -> None:
        """Initialise l'interrupteur."""
        super().__init__(coordinator, field)
        self._field = field
        self._attr_name = name
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        """État de l'autorisation."""
        return self.coordinator.data.value_bool("C", 0, self._field)

    async def _async_write(self, value: bool) -> None:
        data = self.coordinator.data
        heating = bool(data.value_bool("C", 0, "autorisation_chauffage"))
        cooling = bool(data.value_bool("C", 0, "autorisation_rafraichissement"))
        if self._field == "autorisation_chauffage":
            heating = value
        else:
            cooling = value
        await self.coordinator.async_write(
            self.coordinator.client.async_set_authorizations(heating, cooling)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Autorise."""
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Interdit."""
        await self._async_write(False)
