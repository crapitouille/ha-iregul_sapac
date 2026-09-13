"""Classe de base des entités i-regul."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IRegulCoordinator


class IRegulEntity(CoordinatorEntity[IRegulCoordinator]):
    """Entité rattachée au coordinator et à l'appareil unique."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: IRegulCoordinator, key: str) -> None:
        """Initialise l'entité avec un identifiant unique stable."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.client.serial}_{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Indisponible si le serveur ne renvoie que de vieilles données."""
        return super().available and not self.coordinator.data.status.stale
