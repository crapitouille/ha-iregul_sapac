"""Capteurs binaires i-regul : entrées TOR (I), sorties TOR (O), alarme."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ANALOG_OUTPUTS
from .coordinator import IRegulConfigEntry, IRegulCoordinator
from .entity import IRegulEntity

# Sorties « principales » visibles par défaut ; les autres sont en diagnostic
_PRIMARY_OUTPUTS = {1, 3, 7, 10, 12, 26, 60, 100, 101}
_RUNNING_OUTPUTS = {3, 60, 100}  # compresseur, circulateurs
_HEAT_OUTPUTS = {7, 12, 101}     # appoints, cordon chauffant


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRegulConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les capteurs binaires."""
    coordinator = entry.runtime_data
    data = coordinator.data
    entities: list[BinarySensorEntity] = [IRegulAlarmSensor(coordinator)]

    for ident in sorted(set(data.meta.ids("O")) | set(data.status.ids("O"))):
        if ident in ANALOG_OUTPUTS:
            continue
        label = data.label("O", ident)
        primary = ident in _PRIMARY_OUTPUTS or ident >= 110 and ident < 400 and ident % 10 == 0
        device_class = None
        if ident in _RUNNING_OUTPUTS or (110 <= ident < 400 and ident % 10 == 0):
            device_class = BinarySensorDeviceClass.RUNNING
        elif ident in _HEAT_OUTPUTS:
            device_class = BinarySensorDeviceClass.HEAT
        elif ident == 50:  # « Pas d'alarme »
            device_class = None
        entities.append(
            IRegulPointBinarySensor(
                coordinator, "O", ident, label, device_class,
                category=None if primary else EntityCategory.DIAGNOSTIC,
                enabled=primary or ident < 100,
            )
        )

    for ident in sorted(set(data.meta.ids("I")) | set(data.status.ids("I"))):
        label = f"Entrée {data.label('I', ident)}"
        entities.append(
            IRegulPointBinarySensor(
                coordinator, "I", ident, label, None,
                category=EntityCategory.DIAGNOSTIC, enabled=False,
            )
        )
    async_add_entities(entities)


class IRegulPointBinarySensor(IRegulEntity, BinarySensorEntity):
    """Un point TOR (entrée ou sortie)."""

    def __init__(
        self,
        coordinator: IRegulCoordinator,
        typ: str,
        ident: int,
        label: str,
        device_class: BinarySensorDeviceClass | None,
        *,
        category: EntityCategory | None,
        enabled: bool,
    ) -> None:
        """Initialise le capteur."""
        super().__init__(coordinator, f"{typ}_{ident}")
        self._typ = typ
        self._ident = ident
        self._attr_name = label
        self._attr_device_class = device_class
        self._attr_entity_category = category
        self._attr_entity_registry_enabled_default = enabled

    @property
    def is_on(self) -> bool | None:
        """État du point."""
        return self.coordinator.data.value_bool(self._typ, self._ident)


class IRegulAlarmSensor(IRegulEntity, BinarySensorEntity):
    """Alarme active sur le régulateur (mem@0&alarme_flag)."""

    _attr_name = "Alarme"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: IRegulCoordinator) -> None:
        """Initialise le capteur d'alarme."""
        super().__init__(coordinator, "alarme_flag")

    @property
    def is_on(self) -> bool | None:
        """Vrai si une alarme est active."""
        return self.coordinator.data.value_bool("mem", 0, "alarme_flag")

    @property
    def extra_state_attributes(self) -> dict:
        """Code d'alarme et sonde concernée."""
        data = self.coordinator.data
        return {
            "alarme": data.value("mem", 0, "alarme"),
            "alarme_num_sonde": data.value("mem", 0, "alarme_num_sonde"),
        }
