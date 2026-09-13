"""Entité water_heater pour les ballons d'eau chaude sanitaire (Z@1, Z@31, Z@32)."""

from __future__ import annotations

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

from .const import ZONE_ECS1, ZONE_ECS2, ZONE_ECS3, ZONE_MODES
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

# zone ECS -> (sonde de température, sortie « production ECS », sortie appoint)
_ECS_POINTS: dict[int, tuple[int | None, int | None, int | None]] = {
    ZONE_ECS1: (1, 1, 7),
    ZONE_ECS2: (None, None, None),
    ZONE_ECS3: (None, None, None),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRegulConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les entités ECS présentes."""
    coordinator = entry.runtime_data
    data = coordinator.data
    entities = []
    for zone_id in _ECS_POINTS:
        if ZoneState.from_data(data, zone_id) is None:
            continue
        entities.append(IRegulWaterHeater(coordinator, zone_id, enabled=zone_id == ZONE_ECS1))
    async_add_entities(entities)


def _find_temp_sensor(data: IRegulData, zone_id: int) -> int | None:
    """Sonde de température associée au ballon, par libellé si besoin."""
    fixed = _ECS_POINTS[zone_id][0]
    if fixed is not None and data.has(("A", fixed)):
        return fixed
    wanted = {ZONE_ECS2: "ecs2", ZONE_ECS3: "ecs3"}.get(zone_id, "ecs")
    for ident in data.meta.ids("A"):
        if wanted in data.label("A", ident).lower():
            return ident
    return None


class IRegulWaterHeater(IRegulEntity, WaterHeaterEntity):
    """Ballon ECS piloté par le régulateur."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_operation_list = [STATE_AUTO, STATE_PERFORMANCE, STATE_ECO, STATE_FROST, STATE_OFF]
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )

    def __init__(self, coordinator: IRegulCoordinator, zone_id: int, *, enabled: bool) -> None:
        """Initialise le ballon."""
        super().__init__(coordinator, f"water_heater_Z_{zone_id}")
        self._zone_id = zone_id
        data = coordinator.data
        self._attr_name = zone_name(data, zone_id, f"ECS {zone_id}").upper() if zone_id == ZONE_ECS1 else zone_name(data, zone_id, f"ECS {zone_id}")
        self._attr_entity_registry_enabled_default = enabled
        self._temp_sensor = _find_temp_sensor(data, zone_id)
        _, self._prod_output, self._boost_output = _ECS_POINTS[zone_id]
        self._attr_min_temp = data.meta.get_float("Z", zone_id, "temperature_min") or 10.0
        self._attr_max_temp = data.meta.get_float("Z", zone_id, "temperature_max") or 65.0

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
        """Consignes, production et appoint."""
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
        if self._prod_output is not None:
            attrs["production_en_cours"] = data.value_bool("O", self._prod_output)
        if self._boost_output is not None:
            attrs["appoint_en_cours"] = data.value_bool("O", self._boost_output)
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
        await async_write_zone(self.coordinator, self._zone_id, state.with_mode(_OP_TO_MODE[operation_mode]))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Remet en automatique."""
        await self.async_set_operation_mode(STATE_AUTO)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Arrêt de la production ECS."""
        await self.async_set_operation_mode(STATE_OFF)

    async def async_turn_away_mode_on(self) -> None:
        """Hors-gel."""
        await self.async_set_operation_mode(STATE_FROST)

    async def async_turn_away_mode_off(self) -> None:
        """Retour en automatique."""
        await self.async_set_operation_mode(STATE_AUTO)
