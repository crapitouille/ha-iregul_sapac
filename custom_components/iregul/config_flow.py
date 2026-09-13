"""Config flow i-regul."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback

from .api import (
    IRegulAuthError,
    IRegulClient,
    IRegulConnectionError,
    IRegulError,
    IRegulUnknownSerialError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SERIAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERIAL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _validate(serial: str, password: str) -> dict[str, str]:
    """Teste les identifiants avec une lecture d'état (commande 10)."""
    client = IRegulClient(serial, password)
    frame = await client.async_status()
    if not frame.points:
        raise IRegulConnectionError("Trame vide")
    return {"version": frame.get("mem", 0, "version") or ""}


class IRegulConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flux de configuration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Étape initiale : SN + mot de passe."""
        errors: dict[str, str] = {}
        if user_input is not None:
            serial = user_input[CONF_SERIAL].strip()
            await self.async_set_unique_id(serial)
            self._abort_if_unique_id_configured()
            try:
                await _validate(serial, user_input[CONF_PASSWORD])
            except IRegulAuthError:
                errors["base"] = "invalid_auth"
            except IRegulUnknownSerialError:
                errors["base"] = "unknown_serial"
            except IRegulConnectionError:
                errors["base"] = "cannot_connect"
            except IRegulError:
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"i-regul {serial}",
                    data={CONF_SERIAL: serial, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Ré-authentification."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nouveau mot de passe."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await _validate(entry.data[CONF_SERIAL], user_input[CONF_PASSWORD])
            except IRegulAuthError:
                errors["base"] = "invalid_auth"
            except IRegulError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options."""
        return IRegulOptionsFlow()


class IRegulOptionsFlow(OptionsFlow):
    """Options : intervalle de scrutation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Formulaire d'options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)
                    )
                }
            ),
        )
