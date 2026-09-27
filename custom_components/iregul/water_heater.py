"""Entités water_heater : ballons ECS (Z@1, Z@31, Z@32) et appoint électrique ECS (Z@3).

Deux mécanismes chauffent le ballon, chacun avec sa zone et sa consigne :

- la **PAC** (zone ECS, Z@1), plafonnée par le paramètre installateur
  « T°max ECS » (P@76) et relancée seulement sous « consigne − hystérésis » ;
- l'**appoint électrique** (zone « appoint ecs1 », Z@3), qui prend le relais
  au-delà de ce que la PAC produit, selon son propre programme horaire.

Demander 60 °C à la zone ECS ne suffit donc pas à obtenir 60 °C : il faut que
l'appoint soit autorisé à y monter. Les deux sont exposés séparément.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_OFF,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    P_ECS_HYSTERESIS,
    P_ECS_TMAX_PAC,
    ZONE_APPOINT_ECS1,
    ZONE_ECS1,
    ZONE_ECS2,
    ZONE_ECS3,
    ZONE_MODES,
)
from .coordinator import IRegulConfigEntry, IRegulCoordinator, IRegulData
from .entity import IRegulEntity
from .zone import (
    MODE_ARRET,
    MODE_AUTO,
    MODE_HORSGEL,
    MODE_NORMAL,
    MODE_REDUIT,
    ZoneState,
    async_write_zone,
    zone_name,
)

STATE_AUTO = "auto"
STATE_FROST = "hors_gel"

_OP_TO_MODE = {
    STATE_AUTO: MODE_AUTO,
    STATE_PERFORMANCE: MODE_NORMAL,
    STATE_ECO: MODE_REDUIT,
    STATE_FROST: MODE_HORSGEL,
    STATE_OFF: MODE_ARRET,
}
_MODE_TO_OP = {v: k for k, v in _OP_TO_MODE.items()}


@dataclass(frozen=True)
class _EcsZone:
    """Description d'une zone ECS pilotable."""

    temp_sensor: int | None     # sonde A@ du ballon
    output: int | None          # sortie O@ qui indique que ça chauffe
    name: str | None            # nom imposé (sinon celui du régulateur)
    enabled: bool               # entité activée par défaut
    heated_by_pac: bool         # soumise à T°max ECS / hystérésis de la PAC
    fallback_max: float         # plafond si le régulateur n'en publie pas


_ECS_ZONES: dict[int, _EcsZone] = {
    ZONE_ECS1: _EcsZone(1, 1, "ECS1", True, True, 57.0),
    ZONE_APPOINT_ECS1: _EcsZone(1, 7, "Appoint ECS", True, False, 70.0),
    ZONE_ECS2: _EcsZone(None, None, None, False, True, 57.0),
    ZONE_ECS3: _EcsZone(None, None, None, False, True, 57.0),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRegulConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les entités ECS présentes."""
    coordinator = entry.runtime_data
    data = coordinator.data
    async_add_entities(
        IRegulWaterHeater(coordinator, zone_id, spec)
        for zone_id, spec in _ECS_ZONES.items()
        if ZoneState.from_data(data, zone_id) is not None
    )


def _find_temp_sensor(data: IRegulData, zone_id: int, fixed: int | None) -> int | None:
    """Sonde de température associée au ballon, par libellé si besoin."""
    if fixed is not None and data.has(("A", fixed)):
        return fixed
    wanted = {ZONE_ECS2: "ecs2", ZONE_ECS3: "ecs3"}.get(zone_id, "ecs")
    for ident in data.meta.ids("A"):
        if wanted in data.label("A", ident).lower():
            return ident
    return None


def pac_restart_threshold(
    setpoint: float | None, tmax_pac: float | None, hysteresis: float | None
) -> float | None:
    """Seuil sous lequel la PAC relance la production ECS (estimation).

    La consigne effective de la PAC est plafonnée par T°max ECS ; la production
    ne reprend qu'une fois le ballon descendu d'une hystérésis sous ce plafond.
    Formule déduite du comportement observé, pas lue dans le firmware.
    """
    if setpoint is None or hysteresis is None:
        return None
    effective = min(setpoint, tmax_pac) if tmax_pac is not None else setpoint
    return effective - hysteresis


class IRegulWaterHeater(IRegulEntity, WaterHeaterEntity):
    """Zone ECS pilotée par le régulateur (PAC ou appoint électrique)."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_operation_list = [STATE_AUTO, STATE_PERFORMANCE, STATE_ECO, STATE_FROST, STATE_OFF]
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.AWAY_MODE
    )

    def __init__(self, coordinator: IRegulCoordinator, zone_id: int, spec: _EcsZone) -> None:
        """Initialise la zone ECS."""
        super().__init__(coordinator, f"water_heater_Z_{zone_id}")
        self._zone_id = zone_id
        self._spec = spec
        data = coordinator.data
        self._attr_name = spec.name or zone_name(data, zone_id, f"ECS {zone_id}")
        self._attr_entity_registry_enabled_default = spec.enabled
        self._attr_icon = "mdi:water-boiler" if spec.heated_by_pac else "mdi:water-boiler-alert"
        self._temp_sensor = _find_temp_sensor(data, zone_id, spec.temp_sensor)
        # Le minimum doit couvrir la consigne hors-gel, exposée en mode absence.
        self._attr_min_temp = data.meta.get_float("Z", zone_id, "temperature_min") or 5.0
        # Le plafond est celui que publie le régulateur. L'application officielle
        # laisse saisir jusqu'à 60 °C, mais le régulateur n'en tient pas compte
        # au-delà de son plafond : proposer plus serait un réglage sans effet.
        self._attr_max_temp = (
            data.meta.get_float("Z", zone_id, "temperature_max") or spec.fallback_max
        )

    @property
    def _state(self) -> ZoneState | None:
        return ZoneState.from_data(self.coordinator.data, self._zone_id)

    @property
    def current_temperature(self) -> float | None:
        """Température du ballon."""
        if self._temp_sensor is None:
            return None
        return self.coordinator.data.value_float("A", self._temp_sensor)

    @property
    def target_temperature(self) -> float | None:
        """Consigne du mode actif."""
        state = self._state
        return state.active_setpoint if state else None

    @property
    def current_operation(self) -> str | None:
        """Mode sélectionné."""
        state = self._state
        return _MODE_TO_OP.get(state.mode_select) if state else None

    @property
    def is_away_mode_on(self) -> bool | None:
        """Mode hors-gel = absence."""
        state = self._state
        return state.mode_select == MODE_HORSGEL if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Consignes, état de chauffe et, pour la PAC, ce qui gouverne la relance."""
        data = self.coordinator.data
        state = self._state
        attrs: dict[str, Any] = {"zone_id": self._zone_id}
        if state:
            attrs.update(
                consigne_normal=state.consigne_normal,
                consigne_reduit=state.consigne_reduit,
                consigne_horsgel=state.consigne_horsgel,
                mode_select=ZONE_MODES.get(state.mode_select, state.mode_select),
            )
        if self._spec.output is not None:
            attrs["chauffe_en_cours"] = data.value_bool("O", self._spec.output)

        if self._spec.heated_by_pac:
            tmax = data.value_float("P", P_ECS_TMAX_PAC)
            hyst = data.value_float("P", P_ECS_HYSTERESIS)
            setpoint = state.active_setpoint if state else None
            if tmax is not None:
                attrs["temperature_max_pac"] = tmax
            if hyst is not None:
                attrs["hysteresis"] = hyst
            threshold = pac_restart_threshold(setpoint, tmax, hyst)
            if threshold is not None:
                attrs["seuil_relance_estime"] = threshold
            if setpoint is not None and tmax is not None and setpoint > tmax:
                attrs["note"] = (
                    f"La PAC ne chauffe l'ECS que jusqu'à {tmax:g} °C ; "
                    "au-delà, c'est l'appoint électrique qui doit prendre le relais."
                )
            # Appoint associé : utile pour comprendre qui chauffe.
            if self._zone_id == ZONE_ECS1:
                attrs["appoint_en_cours"] = data.value_bool("O", 7)
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Modifie la consigne du mode actif."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        state = self._state
        if temp is None or state is None:
            return
        await async_write_zone(self.coordinator, self._zone_id, state.with_setpoint(float(temp)))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Change mode_select."""
        state = self._state
        if state is None:
            return
        await async_write_zone(
            self.coordinator, self._zone_id, state.with_mode(_OP_TO_MODE[operation_mode])
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Remet en automatique."""
        await self.async_set_operation_mode(STATE_AUTO)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Arrêt de la zone."""
        await self.async_set_operation_mode(STATE_OFF)

    async def async_turn_away_mode_on(self) -> None:
        """Hors-gel."""
        await self.async_set_operation_mode(STATE_FROST)

    async def async_turn_away_mode_off(self) -> None:
        """Retour en automatique."""
        await self.async_set_operation_mode(STATE_AUTO)
