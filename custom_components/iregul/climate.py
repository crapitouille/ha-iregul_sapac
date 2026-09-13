"""Entité climate par zone de chauffage i-regul (Z@11..30)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ZONE_HEATING_FIRST,
    ZONE_HEATING_LAST,
    ZONE_MODES,
    heating_zone_circulator,
)
from .coordinator import IRegulConfigEntry, IRegulCoordinator
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

PRESET_AUTO = "auto"
_PRESET_TO_MODE = {
    PRESET_AUTO: MODE_AUTO,
    PRESET_COMFORT: MODE_NORMAL,
    PRESET_ECO: MODE_REDUIT,
    PRESET_AWAY: MODE_HORSGEL,
}
_MODE_TO_PRESET = {v: k for k, v in _PRESET_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRegulConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée une entité climate par zone de chauffage active."""
    coordinator = entry.runtime_data
    data = coordinator.data
    entities = []
    for zone_id in range(ZONE_HEATING_FIRST, ZONE_HEATING_LAST + 1):
        if ZoneState.from_data(data, zone_id) is None:
            continue
        active = data.value_bool("Z", zone_id, "zone_active")
        if active is False:
            continue
        entities.append(IRegulZoneClimate(coordinator, zone_id))
    async_add_entities(entities)


class IRegulZoneClimate(IRegulEntity, ClimateEntity):
    """Zone de chauffage / rafraîchissement pilotée par courbe de chauffe."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.OFF]
    _attr_preset_modes = [PRESET_AUTO, PRESET_COMFORT, PRESET_ECO, PRESET_AWAY]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: IRegulCoordinator, zone_id: int) -> None:
        """Initialise la zone."""
        super().__init__(coordinator, f"climate_Z_{zone_id}")
        self._zone_id = zone_id
        self._circulator = heating_zone_circulator(zone_id)
        data = coordinator.data
        self._attr_name = zone_name(data, zone_id, f"Zone {zone_id - ZONE_HEATING_FIRST + 1}")
        self._attr_min_temp = data.meta.get_float("Z", zone_id, "temperature_min") or 5.0
        self._attr_max_temp = data.meta.get_float("Z", zone_id, "temperature_max") or 30.0

    # ---- lecture -------------------------------------------------------

    @property
    def _state(self) -> ZoneState | None:
        return ZoneState.from_data(self.coordinator.data, self._zone_id)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """AUTO sauf si la zone est à l'arrêt."""
        state = self._state
        if state is None:
            return None
        return HVACMode.OFF if state.mode_select == MODE_ARRET else HVACMode.AUTO

    @property
    def preset_mode(self) -> str | None:
        """Preset dérivé de mode_select."""
        state = self._state
        if state is None or state.mode_select == MODE_ARRET:
            return None
        return _MODE_TO_PRESET.get(state.mode_select)

    @property
    def target_temperature(self) -> float | None:
        """Consigne du mode actif."""
        state = self._state
        return state.active_setpoint if state else None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Action déduite du circulateur de zone et des autorisations."""
        data = self.coordinator.data
        state = self._state
        if state is None:
            return None
        if state.mode_select == MODE_ARRET:
            return HVACAction.OFF
        circ = data.value_bool("O", self._circulator)
        if not circ:
            return HVACAction.IDLE
        heating = data.value_bool("C", 0, "autorisation_chauffage")
        cooling = data.value_bool("C", 0, "autorisation_rafraichissement")
        producing = (data.value_float("mem", 0, "etat") or 0) == 5
        if heating and producing:
            return HVACAction.HEATING
        if cooling and producing:
            return HVACAction.COOLING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Toutes les consignes et la pente."""
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
        mode_eff = data.value("Z", self._zone_id, "mode")
        if mode_eff is not None:
            attrs["mode_effectif"] = mode_eff
        for fld in ("temperature_pente_chaud", "temperature_pente_froid"):
            val = data.value_float("Z", self._zone_id, fld)
            if val is not None:
                attrs[fld] = val
        calc = data.value_float("mem", self._zone_id, "CZ")
        if calc is not None:
            attrs["consigne_calculee"] = calc
        return attrs

    # ---- écriture ------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Modifie la consigne du mode actif."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        state = self._state
        if temp is None or state is None:
            return
        await async_write_zone(self.coordinator, self._zone_id, state.with_setpoint(float(temp)))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Change mode_select."""
        state = self._state
        if state is None:
            return
        await async_write_zone(self.coordinator, self._zone_id, state.with_mode(_PRESET_TO_MODE[preset_mode]))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """OFF -> arrêt ; AUTO -> mode automatique."""
        state = self._state
        if state is None:
            return
        mode = MODE_ARRET if hvac_mode == HVACMode.OFF else MODE_AUTO
        await async_write_zone(self.coordinator, self._zone_id, state.with_mode(mode))

    async def async_turn_on(self) -> None:
        """Remet la zone en automatique."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Arrête la zone."""
        await self.async_set_hvac_mode(HVACMode.OFF)
