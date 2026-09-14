"""Constantes de l'intégration i-regul."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "iregul"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.WATER_HEATER,
    Platform.SWITCH,
    Platform.BUTTON,
]

CONF_SERIAL = "serial"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 120
MIN_SCAN_INTERVAL = 30

MANUFACTURER = "i-regul / SAPAC"

# Identifiants de zones (index dans la table des zones du régulateur)
ZONE_ECS1 = 1
ZONE_PISCINE = 2
ZONE_APPOINT_ECS1 = 3
ZONE_APPOINT_CHAUFFAGE = 4
ZONE_RECIRCULATION_ECS = 5
ZONE_T_MINIMUM = 10
ZONE_HEATING_FIRST = 11
ZONE_HEATING_LAST = 30
ZONE_ECS2 = 31
ZONE_ECS3 = 32

# Consigne ECS maximale.
#
# Le régulateur publie un champ `Z@<id>&temperature_max` (57 sur une Mistral
# Compact), mais l'application officielle l'ignore complètement pour l'édition :
# elle ne s'en sert que pour l'affichage du tableau « pro ». Son écran ECS borne
# les consignes normal/réduit à une plage FIXE 30–60 °C
# (`_affichage_layout_zones`, cas 2 : `_b4xseekbar_normal._maxvalue = 60`).
# On reprend donc la même limite haute, élargie si le régulateur annonce plus.
ECS_SETPOINT_MAX = 60.0

# Sortie « circulateur zone n » = O@(100 + 10*n)
def heating_zone_circulator(zone_id: int) -> int:
    """Identifiant de la sortie circulateur d'une zone de chauffage (11 -> 110)."""
    return 100 + 10 * (zone_id - ZONE_HEATING_FIRST + 1)


# Sorties dont la valeur est analogique (et non 0/1)
ANALOG_OUTPUTS: dict[int, str] = {
    4: "steps",      # détendeur
    13: "%",         # vitesse capteur (ventilateur)
    52: "%",         # PWM a
    53: "%",         # PWM b
    54: "%",         # PWM c
    55: "%",         # PWM d
    72: "%",         # vitesse condenseur
    451: "",         # vitesse compresseur
}

# Codes d'état du régulateur (mem@0&etat), libellés FR de l'application
PAC_STATES: dict[int, str] = {
    0: "reset régulation",
    1: "démarrage en cours",
    2: "démarrage",
    3: "début de fonctionnement",
    4: "acquisition des paramètres",
    5: "pompe à chaleur en marche",
    6: "arrêt en cours",
    7: "arrêt du compresseur",
    8: "arrêt de l'installation",
    9: "temporisation après arrêt",
    10: "pompe à chaleur à l'arrêt",
    11: "temporisation redémarrage",
    12: "blocage cause défaut",
    20: "défaut connexion carte",
    21: "défaut sonde",
    22: "défaut démarreur",
    23: "verrouillage",
    24: "défaut débit capteur",
    25: "défaut niveau puits",
    26: "défaut basse pression",
    27: "pression maximum",
    28: "attente remontée pression",
    29: "défaut rafraîchissement",
    30: "température eau maximum",
    31: "montée HP rapide",
    32: "T° évaporateur trop basse",
    33: "T° condenseur minimum",
    34: "T° compresseur trop haute",
    35: "dégivrage infructueux",
    36: "défaut débit chauffage",
    37: "séchage chape",
    38: "défaut liaison esclave",
    39: "info driver",
    40: "manque pression eau",
    41: "no data",
    42: "vieilles données",
}

# Modes utilisateur d'une zone (mode_select)
ZONE_MODES: dict[int, str] = {
    0: "auto",
    1: "normal",
    2: "reduit",
    3: "horsgel",
    4: "arret",
}
