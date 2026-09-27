"""Tests bout en bout contre un serveur i-regul simulé."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    SERVICE_SET_OPERATION_MODE,
)
from homeassistant.components.water_heater import (
    DOMAIN as WH_DOMAIN,
)
from homeassistant.components.water_heater import (
    SERVICE_SET_OPERATION_MODE as WH_SERVICE_SET_OPERATION_MODE,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.iregul.api import parse_frame
from custom_components.iregul.const import CONF_SERIAL, DOMAIN, TOLERATED_FAILURES
from custom_components.iregul.water_heater import pac_restart_threshold


async def _setup(hass: HomeAssistant, password: str = "secret") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="108944",
        data={CONF_SERIAL: "108944", CONF_PASSWORD: password},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_parse_status_frame(fake_server) -> None:
    """Le parser lit la trame {10#} réelle."""
    frame = parse_frame(fake_server.status)
    assert frame.header == ""
    assert frame.code == "10"
    assert frame.timestamp == "06/09/2026 19:45:13"
    assert frame.get_float("A", 3) == 23.7
    assert frame.get_bool("mem", 0, "alarme_flag") is False
    assert frame.get("Z", 11, "zone_nom") == "zone 1"
    assert len(frame.ids("Z")) == 15


def test_parse_discovery_frame(fake_server) -> None:
    """La trame {502#} est marquée OLD mais contient les libellés."""
    frame = parse_frame(fake_server.discover)
    assert frame.stale
    assert frame.get("A", 3, "francais") == "T° extérieure"
    assert frame.get("A", 21, "unit") == "l/m"
    assert frame.get("P", 76, "nom") == "T°max ECS"
    assert frame.get_float("Z", 11, "temperature_max") == 30


async def test_setup_and_entities(hass: HomeAssistant, fake_server) -> None:
    """L'intégration se charge et crée les entités attendues."""
    entry = await _setup(hass)
    assert entry.state.name == "LOADED"
    # Découverte puis état
    assert [c[1:4] for c in fake_server.commands[:2]] == ["502", "10#"]

    ext = hass.states.get("sensor.pompe_a_chaleur_108944_temperature_exterieure")
    assert ext is not None, [s.entity_id for s in hass.states.async_all("sensor")][:20]
    assert ext.state == "23.7"
    assert ext.attributes["unit_of_measurement"] == "°C"

    etat = hass.states.get("sensor.pompe_a_chaleur_108944_etat")
    assert etat.state == "pompe à chaleur en marche"

    energy = hass.states.get("sensor.pompe_a_chaleur_108944_conso_total_t1")
    assert energy.attributes["device_class"] == "energy"
    assert energy.attributes["state_class"] == "total_increasing"
    assert float(energy.state) == 6476.847

    comp = hass.states.get("binary_sensor.pompe_a_chaleur_108944_compresseur")
    assert comp.state == "on"

    alarm = hass.states.get("binary_sensor.pompe_a_chaleur_108944_alarme")
    assert alarm.state == "off"

    climate = hass.states.get("climate.pompe_a_chaleur_108944_zone_1")
    assert climate is not None
    assert climate.state == HVACMode.AUTO
    assert climate.attributes["preset_mode"] == "auto"
    assert climate.attributes["temperature"] == 20.0
    assert climate.attributes["min_temp"] == 5.0
    assert climate.attributes["max_temp"] == 30.0
    assert climate.attributes["hvac_action"] == "cooling"  # rafraîchissement autorisé, PAC en marche

    wh = hass.states.get("water_heater.pompe_a_chaleur_108944_ecs1")
    assert wh is not None
    assert wh.state == "auto"
    assert wh.attributes["current_temperature"] == 49.6
    assert wh.attributes["temperature"] == 55.0
    # Le plafond est celui du régulateur (57) : l'appli laisse saisir 60, mais
    # le régulateur n'en tient pas compte au-delà de son plafond.
    assert wh.attributes["max_temp"] == 57.0
    assert wh.attributes["min_temp"] == 5.0
    # Ce qui gouverne la relance de la PAC est visible sur l'entité.
    assert wh.attributes["temperature_max_pac"] == 55.0
    assert wh.attributes["hysteresis"] == 5.0
    assert wh.attributes["seuil_relance_estime"] == 50.0  # min(55, 55) - 5
    assert wh.attributes["chauffe_en_cours"] is True      # O@1 à 49,6 °C < 50
    assert "note" not in wh.attributes                    # consigne 55 <= T°max PAC

    # L'appoint électrique ECS est une entité pilotable à part entière.
    appoint = hass.states.get("water_heater.pompe_a_chaleur_108944_appoint_ecs")
    assert appoint is not None
    assert appoint.state == "auto"
    assert appoint.attributes["temperature"] == 60.0       # consigne normal Z@3
    assert appoint.attributes["max_temp"] == 70.0
    assert appoint.attributes["current_temperature"] == 49.6  # même ballon
    assert appoint.attributes["chauffe_en_cours"] is False   # O@7
    assert "seuil_relance_estime" not in appoint.attributes  # pas soumis à la PAC

    assert hass.states.get("switch.pompe_a_chaleur_108944_autorisation_chauffage").state == "off"
    assert hass.states.get("switch.pompe_a_chaleur_108944_autorisation_rafraichissement").state == "on"

    # Les zones ECS 2/3 et entrées TOR sont créées mais désactivées
    registry = er.async_get(hass)
    ecs2 = registry.async_get("water_heater.pompe_a_chaleur_108944_ecs2")
    assert ecs2 is not None and ecs2.disabled


async def test_set_temperature_and_modes(hass: HomeAssistant, fake_server) -> None:
    """Les commandes d'écriture ont le format de l'application officielle."""
    await _setup(hass)
    fake_server.commands.clear()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: "climate.pompe_a_chaleur_108944_zone_1", ATTR_TEMPERATURE: 21.5},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert fake_server.commands[0] == (
        "{11#DT_zones@11&consigne_normal[21.5]#DT_zones@11&consigne_reduit[19]"
        "#DT_zones@11&consigne_horsgel[10]#DT_zones@11&mode_select[0]}"
    )
    assert fake_server.commands[1] == "{10#}"  # relecture après écriture

    fake_server.commands.clear()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: "climate.pompe_a_chaleur_108944_zone_1", ATTR_PRESET_MODE: "eco"},
        blocking=True,
    )
    assert fake_server.commands[0].endswith("#DT_zones@11&mode_select[2]}")

    fake_server.commands.clear()
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: "climate.pompe_a_chaleur_108944_zone_1", ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    assert fake_server.commands[0].endswith("#DT_zones@11&mode_select[4]}")

    fake_server.commands.clear()
    await hass.services.async_call(
        WH_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: "water_heater.pompe_a_chaleur_108944_ecs1", ATTR_OPERATION_MODE: "eco"},
        blocking=True,
    )
    assert fake_server.commands[0] == (
        "{11#DT_zones@1&consigne_normal[55]#DT_zones@1&consigne_reduit[55]"
        "#DT_zones@1&consigne_horsgel[10]#DT_zones@1&mode_select[2]}"
    )

    fake_server.commands.clear()
    await hass.services.async_call(
        "switch", "turn_on",
        {ATTR_ENTITY_ID: "switch.pompe_a_chaleur_108944_autorisation_chauffage"},
        blocking=True,
    )
    assert fake_server.commands[0] == (
        "{12#DT_config@0&autorisation_chauffage[1]#DT_config@0&autorisation_rafraichissement[1]}"
    )

    fake_server.commands.clear()
    await hass.services.async_call(
        "button", "press",
        {ATTR_ENTITY_ID: "button.pompe_a_chaleur_108944_acquitter_l_alarme"},
        blocking=True,
    )
    assert fake_server.commands[0] == "{202#}"


async def test_ecs_above_pac_max_explains_itself(hass: HomeAssistant, fake_server) -> None:
    """Une consigne ECS au-delà de T°max PAC est signalée, seuil de relance à l'appui.

    Cas réel : ballon à 53 °C, consigne 57 → la PAC vise au mieux 55 et ne
    relance que sous 50 °C. Sans explication, on croit à une panne.
    """
    await _setup(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    fake_server.status = fake_server.status.replace(
        "Z@1&consigne_normal[55]", "Z@1&consigne_normal[57]"
    ).replace("Z@1&mode_select[0]", "Z@1&mode_select[1]")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    wh = hass.states.get("water_heater.pompe_a_chaleur_108944_ecs1")
    assert wh.attributes["temperature"] == 57.0
    assert wh.attributes["seuil_relance_estime"] == 50.0  # min(57, 55) - 5
    assert "55" in wh.attributes["note"]


async def test_appoint_setpoint_60(hass: HomeAssistant, fake_server) -> None:
    """Viser 60 °C passe par l'appoint électrique (Z@3), pas par la zone ECS."""
    await _setup(hass)
    fake_server.commands.clear()
    await hass.services.async_call(
        WH_DOMAIN,
        WH_SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: "water_heater.pompe_a_chaleur_108944_appoint_ecs", ATTR_OPERATION_MODE: "performance"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert fake_server.commands[0] == (
        "{11#DT_zones@3&consigne_normal[60]#DT_zones@3&consigne_reduit[35]"
        "#DT_zones@3&consigne_horsgel[10]#DT_zones@3&mode_select[1]}"
    )


def test_pac_restart_threshold() -> None:
    """Seuil de relance : consigne plafonnée par T°max PAC, moins l'hystérésis."""
    assert pac_restart_threshold(55, 55, 5) == 50
    assert pac_restart_threshold(60, 55, 5) == 50   # le cas qui surprend
    assert pac_restart_threshold(50, 55, 5) == 45
    assert pac_restart_threshold(60, None, 5) == 55
    assert pac_restart_threshold(None, 55, 5) is None


async def test_bad_password(hass: HomeAssistant, fake_server) -> None:
    """Un mauvais mot de passe déclenche la ré-authentification."""
    entry = await _setup(hass, password="wrong")
    assert entry.state.name == "SETUP_ERROR"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_config_flow(hass: HomeAssistant, fake_server) -> None:
    """Le config flow valide les identifiants."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SERIAL: "108944", CONF_PASSWORD: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SERIAL: "108944", CONF_PASSWORD: "secret"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "WattKeeper - i-regul 108944"


async def test_transient_failure_keeps_last_values(hass: HomeAssistant, fake_server) -> None:
    """Un échec isolé conserve les dernières valeurs au lieu de tout rendre indisponible.

    Le serveur i-regul a des absences ; faire clignoter toutes les entités à
    chaque trou rendrait le dashboard et l'historique inutilisables.
    """
    await _setup(hass)
    entity = "sensor.pompe_a_chaleur_108944_temperature_exterieure"
    assert hass.states.get(entity).state == "23.7"

    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    fake_server.fail = True

    # Échecs tolérés : la valeur reste affichée.
    for expected in range(1, TOLERATED_FAILURES + 1):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.consecutive_failures == expected
        assert coordinator.last_update_success is True
        assert hass.states.get(entity).state == "23.7"

    # Au-delà, on devient honnête : les entités passent indisponibles.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    assert hass.states.get(entity).state == "unavailable"

    # Le serveur revient : tout se rétablit et la cadence nominale reprend.
    fake_server.fail = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.consecutive_failures == 0
    assert hass.states.get(entity).state == "23.7"


async def test_backoff_slows_polling_while_failing(hass: HomeAssistant, fake_server) -> None:
    """Tant que ça échoue, on espace les interrogations au lieu d'insister.

    Le premier échec garde la cadence nominale : un trou isolé est fréquent et
    ralentir tout de suite retarderait la détection du retour du serveur.
    L'espacement ne démarre qu'au deuxième échec consécutif.
    """
    await _setup(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    base = coordinator.update_interval
    fake_server.fail = True

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.update_interval == base  # premier échec : on n'insiste ni ne ralentit

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.update_interval > base  # ça dure : on espace

    fake_server.fail = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.update_interval == base  # retour à la normale
