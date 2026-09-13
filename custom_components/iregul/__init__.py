"""Intégration i-regul (pompes à chaleur SAPAC et autres régulateurs i-regul)."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .api import IRegulClient
from .const import CONF_SCAN_INTERVAL, CONF_SERIAL, DEFAULT_SCAN_INTERVAL, PLATFORMS
from .coordinator import IRegulConfigEntry, IRegulCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: IRegulConfigEntry) -> bool:
    """Configure l'installation à partir d'une entrée de configuration."""
    client = IRegulClient(entry.data[CONF_SERIAL], entry.data[CONF_PASSWORD])
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = IRegulCoordinator(hass, entry, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: IRegulConfigEntry) -> None:
    """Recharge l'entrée quand les options changent."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IRegulConfigEntry) -> bool:
    """Décharge l'entrée."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
