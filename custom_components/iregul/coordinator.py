"""Coordinator i-regul : découverte initiale puis scrutation de l'état."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    IRegulAuthError,
    IRegulClient,
    IRegulConnectionError,
    IRegulError,
    IRegulFrame,
    IRegulUnknownSerialError,
    PointKey,
)
from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)


# Libellés serveur ambigus -> libellés corrigés
_LABEL_OVERRIDES: dict[tuple[str, int], str] = {
    ("M", 16): "Énergie absorbée (kWh)",   # libellé serveur « Puissance absorbée », unité kWh
    ("M", 17): "Énergie absorbée (MWh)",   # idem, unité MWh
}


def normalize_label(label: str, typ: str | None = None, ident: int | None = None) -> str:
    """« T° ECS » -> « Température ECS » ; corrige les libellés ambigus connus."""
    if typ is not None and ident is not None and (typ, ident) in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[(typ, ident)]
    return re.sub(r"\bT°\s*", "Température ", label).strip()


@dataclass
class IRegulData:
    """Données exposées aux entités."""

    meta: IRegulFrame      # trame de découverte (libellés, unités, min/max) — valeurs anciennes
    status: IRegulFrame    # dernière trame d'état (valeurs courantes)

    def label(self, typ: str, ident: int, default: str | None = None) -> str:
        """Libellé français d'un point (ou alias, ou défaut)."""
        point = self.meta.points.get((typ, ident), {})
        label = point.get("francais") or point.get("alias") or point.get("nom")
        if not label:
            return default or f"{typ}@{ident}"
        return normalize_label(label, typ, ident)

    def unit(self, typ: str, ident: int) -> str | None:
        """Unité d'un point telle qu'envoyée par le serveur."""
        unit = self.meta.points.get((typ, ident), {}).get("unit")
        return unit or None

    def value(self, typ: str, ident: int, fld: str = "valeur") -> str | None:
        """Valeur courante (trame d'état), repli sur la découverte."""
        val = self.status.get(typ, ident, fld)
        if val is None:
            val = self.meta.get(typ, ident, fld)
        return val

    def value_float(self, typ: str, ident: int, fld: str = "valeur") -> float | None:
        """Valeur numérique courante."""
        val = self.status.get_float(typ, ident, fld)
        if val is None:
            val = self.meta.get_float(typ, ident, fld)
        return val

    def value_bool(self, typ: str, ident: int, fld: str = "valeur") -> bool | None:
        """Valeur booléenne courante."""
        val = self.status.get_bool(typ, ident, fld)
        if val is None:
            val = self.meta.get_bool(typ, ident, fld)
        return val

    def has(self, key: PointKey) -> bool:
        """Le point existe-t-il dans l'une des deux trames ?"""
        return key in self.status.points or key in self.meta.points


type IRegulConfigEntry = ConfigEntry[IRegulCoordinator]


class IRegulCoordinator(DataUpdateCoordinator[IRegulData]):
    """Scrute le serveur i-regul."""

    config_entry: IRegulConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IRegulConfigEntry,
        client: IRegulClient,
        scan_interval: int,
    ) -> None:
        """Initialise le coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{client.serial}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self._meta: IRegulFrame | None = None

    async def _async_setup(self) -> None:
        """Découverte initiale (commande 502) — libellés, unités, bornes."""
        try:
            self._meta = await self.client.async_discover()
        except IRegulAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except IRegulUnknownSerialError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except IRegulError as err:
            raise UpdateFailed(str(err)) from err
        _LOGGER.debug(
            "Découverte i-regul %s : %d points, version régulateur %s",
            self.client.serial,
            len(self._meta.points),
            self._meta.get("mem", 0, "version"),
        )

    async def _async_update_data(self) -> IRegulData:
        """Lecture de l'état courant (commande 10)."""
        assert self._meta is not None
        try:
            status = await self.client.async_status()
        except (IRegulAuthError, IRegulUnknownSerialError) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except IRegulConnectionError as err:
            raise UpdateFailed(str(err)) from err
        if status.stale:
            _LOGGER.warning(
                "i-regul %s : le serveur renvoie de vieilles données (régulateur hors ligne ?)",
                self.client.serial,
            )
        if not status.points:
            raise UpdateFailed("Trame d'état vide")
        return IRegulData(meta=self._meta, status=status)

    async def async_write(self, coro) -> None:
        """Exécute une commande d'écriture puis rafraîchit l'état."""
        try:
            await coro
        except (IRegulAuthError, IRegulUnknownSerialError) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except IRegulError as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()

    @property
    def device_info(self) -> DeviceInfo:
        """Appareil unique pour l'installation."""
        meta = self._meta
        config = meta.points.get(("C", 0), {}) if meta else {}
        model = "Régulateur i-regul"
        if config.get("cdram_version"):
            model = f"i-regul v{config['cdram_version']}"
            if config.get("cdram_addon"):
                model += f" addon {config['cdram_addon']}"
        return DeviceInfo(
            identifiers={(DOMAIN, self.client.serial)},
            name=f"Pompe à chaleur {self.client.serial}",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=meta.get("mem", 0, "version") if meta else None,
            serial_number=self.client.serial,
        )
