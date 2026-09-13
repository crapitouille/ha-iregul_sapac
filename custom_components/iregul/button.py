"""Boutons i-regul : acquittement d'alarme (202), dégivrage forcé (203)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import IRegulConfigEntry, IRegulCoordinator
from .entity import IRegulEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRegulConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les boutons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            IRegulButton(coordinator, "reset_alarme", "Acquitter l'alarme", "mdi:alarm-light-off", "reset_alarm"),
            IRegulButton(coordinator, "degivrage", "Forcer un dégivrage", "mdi:snowflake-melt", "defrost"),
        ]
    )


class IRegulButton(IRegulEntity, ButtonEntity):
    """Bouton envoyant une commande sans paramètre."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IRegulCoordinator, key: str, name: str, icon: str, action: str) -> None:
        """Initialise le bouton."""
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_icon = icon
        self._action = action

    async def async_press(self) -> None:
        """Envoie la commande."""
        client = self.coordinator.client
        coro = client.async_reset_alarm() if self._action == "reset_alarm" else client.async_defrost()
        await self.coordinator.async_write(coro)
