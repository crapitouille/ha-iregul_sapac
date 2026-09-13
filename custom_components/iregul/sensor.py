"""Capteurs i-regul : sondes (A), mesures (M), sorties analogiques (O), état (mem)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ANALOG_OUTPUTS, PAC_STATES, ZONE_MODES
from .coordinator import IRegulConfigEntry, IRegulCoordinator, IRegulData
from .entity import IRegulEntity
from .zone import zone_name as _zone_name


@dataclass(frozen=True, kw_only=True)
class IRegulSensorDescription(SensorEntityDescription):
    """Description d'un capteur i-regul."""

    value_fn: Callable[[IRegulData], float | str | None]
    attributes_fn: Callable[[IRegulData], dict] | None = None


# Unité serveur -> (unité HA, device_class, state_class)
_UNIT_MAP: dict[str, tuple[str | None, SensorDeviceClass | None, SensorStateClass | None]] = {
    "°": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
    "°C": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
    "bar": (UnitOfPressure.BAR, SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT),
    "l/m": (UnitOfVolumeFlowRate.LITERS_PER_MINUTE, SensorDeviceClass.VOLUME_FLOW_RATE, SensorStateClass.MEASUREMENT),
    "W": (UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "kW": (UnitOfPower.KILO_WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "kWh": (UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    "MWh": (UnitOfEnergy.MEGA_WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    "h": (UnitOfTime.HOURS, SensorDeviceClass.DURATION, SensorStateClass.TOTAL_INCREASING),
    "%": (PERCENTAGE, None, SensorStateClass.MEASUREMENT),
}

# Mesures qui ne sont pas des compteurs cumulés malgré leur unité
_MEASUREMENT_OVERRIDE = {("M", 5), ("M", 6), ("M", 7), ("M", 15)}


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def _point_description(data: IRegulData, typ: str, ident: int) -> IRegulSensorDescription:
    """Construit la description d'un point A/M/O à partir des métadonnées serveur."""
    label = data.label(typ, ident)
    unit = data.unit(typ, ident)
    if typ == "O" and not unit:
        unit = ANALOG_OUTPUTS.get(ident, "")
    ha_unit, device_class, state_class = _UNIT_MAP.get(unit or "", (None, None, SensorStateClass.MEASUREMENT))
    if (typ, ident) in _MEASUREMENT_OVERRIDE:
        state_class = SensorStateClass.MEASUREMENT
    if unit and ha_unit is None:
        ha_unit = unit  # unité inconnue : on la garde telle quelle
    # Entités secondaires désactivées par défaut : tarifs T2/T3, compteurs à zéro, diag
    enabled = True
    if typ == "M" and (" T2" in label or " T3" in label or label in ("boot", "u(t)")):
        enabled = False
    if typ == "O" and ident in (52, 53, 54, 55):
        enabled = False
    category = EntityCategory.DIAGNOSTIC if typ == "O" or not enabled else None
    return IRegulSensorDescription(
        key=f"{typ}_{ident}",
        name=label,
        translation_key=None,
        native_unit_of_measurement=ha_unit,
        device_class=device_class,
        state_class=state_class,
        entity_category=category,
        entity_registry_enabled_default=enabled,
        suggested_display_precision=1 if device_class in (SensorDeviceClass.TEMPERATURE, SensorDeviceClass.PRESSURE, SensorDeviceClass.DURATION) else None,
        value_fn=lambda d, t=typ, i=ident: d.value_float(t, i),
    )


def _state_text(data: IRegulData) -> str | None:
    code = data.value_float("mem", 0, "etat")
    if code is None:
        return None
    return PAC_STATES.get(int(code), f"état {int(code)}")


def _state_attrs(data: IRegulData) -> dict:
    return {
        "code": data.value("mem", 0, "etat"),
        "sous_etat": data.value("mem", 0, "sous_etat"),
        "alarme": data.value("mem", 0, "alarme"),
        "alarme_flag": data.value_bool("mem", 0, "alarme_flag"),
        "alarme_num_sonde": data.value("mem", 0, "alarme_num_sonde"),
        "version": data.value("mem", 0, "version"),
        "date_time_regulateur": data.value("mem", 0, "date_time"),
        "horodatage_trame": data.status.timestamp,
        "donnees_anciennes": data.status.stale,
    }


def _alarm_text(data: IRegulData) -> str | None:
    code = data.value_float("mem", 0, "alarme")
    if code is None:
        return None
    return PAC_STATES.get(int(code), f"alarme {int(code)}")


STATIC_SENSORS: tuple[IRegulSensorDescription, ...] = (
    IRegulSensorDescription(
        key="etat",
        name="État",
        icon="mdi:heat-pump",
        value_fn=_state_text,
        attributes_fn=_state_attrs,
    ),
    IRegulSensorDescription(
        key="alarme",
        name="Dernière alarme",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_alarm_text,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRegulConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les capteurs à partir de la découverte."""
    coordinator = entry.runtime_data
    data = coordinator.data
    entities: list[IRegulSensor] = [
        IRegulSensor(coordinator, desc) for desc in STATIC_SENSORS
    ]
    for typ in ("A", "M"):
        for ident in sorted(set(data.meta.ids(typ)) | set(data.status.ids(typ))):
            entities.append(IRegulSensor(coordinator, _point_description(data, typ, ident)))
    for ident in sorted(set(data.meta.ids("O")) | set(data.status.ids("O"))):
        if ident in ANALOG_OUTPUTS:
            entities.append(IRegulSensor(coordinator, _point_description(data, "O", ident)))
    # Consignes de zones (diagnostic, désactivées par défaut)
    for zone_id in sorted(set(data.meta.ids("Z")) | set(data.status.ids("Z"))):
        zone_name = _zone_name(data, zone_id, f"Zone {zone_id}")
        for fld, suffix in (("consigne_normal", "consigne normal"), ("consigne_reduit", "consigne réduit")):
            entities.append(
                IRegulSensor(
                    coordinator,
                    IRegulSensorDescription(
                        key=f"Z_{zone_id}_{fld}",
                        name=f"{zone_name} {suffix}",
                        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                        device_class=SensorDeviceClass.TEMPERATURE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        entity_registry_enabled_default=False,
                        value_fn=lambda d, z=zone_id, f=fld: d.value_float("Z", z, f),
                    ),
                )
            )
        entities.append(
            IRegulSensor(
                coordinator,
                IRegulSensorDescription(
                    key=f"Z_{zone_id}_mode",
                    name=f"{zone_name} mode",
                    icon="mdi:tune-variant",
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    value_fn=lambda d, z=zone_id: _zone_mode(d, z),
                ),
            )
        )
    # Consignes calculées par zone (mem@n&CZ)
    for ident in sorted(i for (t, i) in data.status.points if t == "mem" and "CZ" in data.status.points[(t, i)]):
        entities.append(
            IRegulSensor(
                coordinator,
                IRegulSensorDescription(
                    key=f"mem_{ident}_CZ",
                    name=f"Consigne calculée zone {ident}",
                    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                    device_class=SensorDeviceClass.TEMPERATURE,
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    value_fn=lambda d, i=ident: d.value_float("mem", i, "CZ"),
                ),
            )
        )
    async_add_entities(entities)


def _zone_mode(data: IRegulData, zone_id: int) -> str | None:
    sel = data.value_float("Z", zone_id, "mode_select")
    if sel is None:
        return None
    return ZONE_MODES.get(int(sel), str(int(sel)))


class IRegulSensor(IRegulEntity, SensorEntity):
    """Capteur générique."""

    entity_description: IRegulSensorDescription

    def __init__(self, coordinator: IRegulCoordinator, description: IRegulSensorDescription) -> None:
        """Initialise le capteur."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Valeur courante."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Attributs supplémentaires."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
