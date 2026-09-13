"""Logique commune aux zones (chauffage, ECS) : consignes et mode_select."""

from __future__ import annotations

from dataclasses import dataclass

from .api import MODE_ARRET, MODE_AUTO, MODE_HORSGEL, MODE_NORMAL, MODE_REDUIT
from .coordinator import IRegulCoordinator, IRegulData, normalize_label


@dataclass
class ZoneState:
    """Consignes et mode d'une zone tels que connus du régulateur."""

    consigne_normal: float
    consigne_reduit: float
    consigne_horsgel: float
    mode_select: int

    @classmethod
    def from_data(cls, data: IRegulData, zone_id: int) -> ZoneState | None:
        """Construit l'état depuis les trames ; None si la zone n'existe pas."""
        normal = data.value_float("Z", zone_id, "consigne_normal")
        reduit = data.value_float("Z", zone_id, "consigne_reduit")
        horsgel = data.value_float("Z", zone_id, "consigne_horsgel")
        mode = data.value_float("Z", zone_id, "mode_select")
        if normal is None or reduit is None or horsgel is None or mode is None:
            return None
        return cls(normal, reduit, horsgel, int(mode))

    @property
    def active_setpoint(self) -> float:
        """Consigne correspondant au mode sélectionné (auto -> normal)."""
        if self.mode_select == MODE_REDUIT:
            return self.consigne_reduit
        if self.mode_select == MODE_HORSGEL:
            return self.consigne_horsgel
        return self.consigne_normal

    def with_setpoint(self, value: float) -> ZoneState:
        """Nouvel état avec la consigne du mode actif modifiée."""
        if self.mode_select == MODE_REDUIT:
            return ZoneState(self.consigne_normal, value, self.consigne_horsgel, self.mode_select)
        if self.mode_select == MODE_HORSGEL:
            return ZoneState(self.consigne_normal, self.consigne_reduit, value, self.mode_select)
        return ZoneState(value, self.consigne_reduit, self.consigne_horsgel, self.mode_select)

    def with_mode(self, mode: int) -> ZoneState:
        """Nouvel état avec un autre mode_select."""
        return ZoneState(self.consigne_normal, self.consigne_reduit, self.consigne_horsgel, mode)


async def async_write_zone(coordinator: IRegulCoordinator, zone_id: int, state: ZoneState) -> None:
    """Envoie la commande 11 pour une zone puis rafraîchit."""
    await coordinator.async_write(
        coordinator.client.async_set_zone(
            zone_id,
            consigne_normal=state.consigne_normal,
            consigne_reduit=state.consigne_reduit,
            consigne_horsgel=state.consigne_horsgel,
            mode_select=state.mode_select,
        )
    )


def zone_name(data: IRegulData, zone_id: int, default: str) -> str:
    """Nom lisible de la zone (« (ecs1) » -> « ecs1 »)."""
    raw = (data.value("Z", zone_id, "zone_nom") or default).strip("() ").strip()
    if not raw or raw.startswith("---"):
        # « ----> 1 » : circuits sans nom
        return default
    return normalize_label(raw)


__all__ = [
    "MODE_ARRET",
    "MODE_AUTO",
    "MODE_HORSGEL",
    "MODE_NORMAL",
    "MODE_REDUIT",
    "ZoneState",
    "async_write_zone",
    "zone_name",
]
