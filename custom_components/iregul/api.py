"""Client asynchrone pour le serveur cloud i-regul (protocole « cdram »).

Le régulateur i-regul (utilisé notamment par les pompes à chaleur SAPAC) est
piloté au travers du serveur i-regul.fr, port 443, en TCP brut **sans TLS**.
Chaque commande ouvre une connexion, envoie

    cdraminfo<SN><PASSWORD>{<code>#<param>#...}

et lit une réponse texte terminée par « } ». Voir protocole-i-regul.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "i-regul.fr"
DEFAULT_PORT = 443
DEFAULT_TIMEOUT = 60.0

# Le serveur i-regul refuse (RST immédiat) une connexion ouverte trop vite après
# la précédente. L'application officielle espace ses commandes et n'en garde
# qu'une en vol. On impose donc un intervalle minimal entre deux connexions et on
# réessaie les lectures en cas de reset transitoire.
MIN_REQUEST_INTERVAL = 2.0
MAX_ATTEMPTS = 4
RETRY_BACKOFF = 2.0

ITEM_RE = re.compile(r"#(\w+)@(\d+)&([^\[#]+)\[(.*?)\]")
HEADERS = ("PWD", "SNI", "OLD", "MES")

# Codes de commande
CMD_STATUS = "10"        # état courant (valeurs seules)
CMD_DISCOVER = "502"     # lecture complète avec libellés/unités/min/max
CMD_ALARMS = "98"        # journal des alarmes
CMD_SET_ZONE = "11"      # consignes + mode d'une zone
CMD_SET_AUTH = "12"      # autorisation chauffage / rafraîchissement
CMD_RESET_ALARM = "202"
CMD_DEFROST = "203"

# mode_select d'une zone
MODE_AUTO = 0
MODE_NORMAL = 1
MODE_REDUIT = 2
MODE_HORSGEL = 3
MODE_ARRET = 4


class IRegulError(Exception):
    """Erreur générique i-regul."""


class IRegulAuthError(IRegulError):
    """Mot de passe refusé (réponse PWD)."""


class IRegulUnknownSerialError(IRegulError):
    """Numéro de série inconnu (réponse SNI)."""


class IRegulConnectionError(IRegulError):
    """Impossible de joindre le serveur ou réponse incomplète."""


PointKey = tuple[str, int]


@dataclass
class IRegulFrame:
    """Une trame décodée."""

    header: str = ""          # "", "OLD", "MES"
    timestamp: str = ""       # "JJ/MM/AAAA HH:MM:SS" si présent
    code: str = ""            # code de commande écho
    points: dict[PointKey, dict[str, str]] = field(default_factory=dict)
    raw: str = ""

    @property
    def stale(self) -> bool:
        """Vrai si le serveur a renvoyé de vieilles données (régulateur hors ligne)."""
        return self.header == "OLD"

    def get(self, typ: str, ident: int, fld: str = "valeur") -> str | None:
        """Retourne un champ d'un point, ou None."""
        return self.points.get((typ, ident), {}).get(fld)

    def get_float(self, typ: str, ident: int, fld: str = "valeur") -> float | None:
        """Retourne un champ numérique, ou None."""
        val = self.get(typ, ident, fld)
        if val is None or val == "":
            return None
        try:
            return float(val.replace(",", "."))
        except ValueError:
            return None

    def get_bool(self, typ: str, ident: int, fld: str = "valeur") -> bool | None:
        """Retourne un champ booléen (0/1, True/False), ou None."""
        val = self.get(typ, ident, fld)
        if val is None:
            return None
        low = val.strip().lower()
        if low in ("true", "1", "1.0"):
            return True
        if low in ("false", "0", "0.0"):
            return False
        try:
            return float(low) != 0
        except ValueError:
            return None

    def ids(self, typ: str) -> list[int]:
        """Liste triée des identifiants d'un type."""
        return sorted(i for (t, i) in self.points if t == typ)


def parse_frame(text: str) -> IRegulFrame:
    """Décode une réponse brute du serveur."""
    frame = IRegulFrame(raw=text)
    body = text.strip()
    if body[:3] in HEADERS:
        frame.header = body[:3]
        body = body[3:]
    if frame.header == "PWD":
        raise IRegulAuthError("Mot de passe refusé par i-regul")
    if frame.header == "SNI":
        raise IRegulUnknownSerialError("Numéro de série inconnu d'i-regul")

    brace = body.find("{")
    if brace < 0:
        return frame
    prefix = body[:brace].strip()
    if len(prefix) >= 19:
        frame.timestamp = prefix[:19]
    body = body[brace:]
    hash_pos = body.find("#")
    if hash_pos > 1:
        frame.code = body[1:hash_pos]

    for typ, ident, fld, value in ITEM_RE.findall(body):
        frame.points.setdefault((typ, int(ident)), {})[fld] = value
    return frame


class IRegulClient:
    """Client TCP minimal, une commande à la fois."""

    def __init__(
        self,
        serial: str,
        password: str,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise le client."""
        self._serial = serial.strip()
        self._password = password.strip()
        self._host = host
        self._port = port
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    @property
    def serial(self) -> str:
        """Numéro de série de l'installation."""
        return self._serial

    async def _throttle(self) -> None:
        """Impose un intervalle minimal depuis la dernière connexion."""
        wait = MIN_REQUEST_INTERVAL - (asyncio.get_running_loop().time() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)

    async def _attempt(self, message: bytes) -> str:
        """Une tentative : ouvre, envoie, lit jusqu'à « } », ferme."""
        writer = None
        chunks: list[bytes] = []
        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await asyncio.open_connection(self._host, self._port)
                writer.write(message)
                await writer.drain()
                while True:
                    try:
                        chunk = await reader.read(65536)
                    except ConnectionResetError:
                        # RST après la trame : fin normale si des données sont
                        # déjà arrivées, sinon on laisse remonter pour un réessai.
                        if not chunks:
                            raise
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                    # Fin de trame testée sur le buffer complet : le « } » final et
                    # un éventuel « \r » peuvent arriver dans des paquets séparés.
                    if b"".join(chunks).rstrip(b"\r\n").endswith(b"}"):
                        break
        except (OSError, asyncio.TimeoutError) as err:
            # Trame déjà complète malgré un reset tardif : on l'accepte.
            if chunks and b"".join(chunks).rstrip(b"\r\n").endswith(b"}"):
                pass
            else:
                raise IRegulConnectionError(
                    f"Connexion i-regul impossible : {err}"
                ) from err
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        return b"".join(chunks).decode("utf-8", errors="replace")

    async def async_raw(
        self, command: str, *, retry: bool = True, require_frame: bool = True
    ) -> str:
        """Envoie « {code#...} » et retourne la réponse brute.

        Sérialise les commandes, les espace (le serveur refuse une connexion
        trop rapprochée) et réessaie les lectures en cas d'échec transitoire
        (reset, ou réponse incomplète). `retry=False` pour une écriture (elle
        peut avoir été appliquée malgré le reset : on ne la rejoue pas) ;
        `require_frame=False` accepte une réponse vide (écritures).
        """
        if not (command.startswith("{") and command.endswith("}")):
            raise ValueError("La commande doit être de la forme {code#...}")
        message = f"cdraminfo{self._serial}{self._password}{command}".encode()
        attempts = MAX_ATTEMPTS if retry else 1
        async with self._lock:
            last_err: IRegulConnectionError | None = None
            for attempt in range(1, attempts + 1):
                await self._throttle()
                _LOGGER.debug("i-regul -> %s (tentative %d/%d)", command, attempt, attempts)
                try:
                    data = await self._attempt(message)
                    # Une réponse sans « } » final = trame refusée/incomplète
                    # (le rate-limit se manifeste parfois par une fermeture nette).
                    if require_frame and not data.rstrip("\r\n").endswith("}"):
                        raise IRegulConnectionError(
                            "Réponse i-regul incomplète (connexion refusée ?)"
                        )
                except IRegulConnectionError as err:
                    last_err = err
                    self._last_request = asyncio.get_running_loop().time()
                    if attempt < attempts:
                        backoff = RETRY_BACKOFF * attempt
                        _LOGGER.debug(
                            "i-regul : %s — nouvel essai dans %.0f s", err, backoff
                        )
                        await asyncio.sleep(backoff)
                        continue
                    raise
                else:
                    self._last_request = asyncio.get_running_loop().time()
                    _LOGGER.debug("i-regul <- %d octets", len(data))
                    return data
            assert last_err is not None
            raise last_err

    async def async_command(
        self, code: str, *params: str, retry: bool = True, require_frame: bool = True
    ) -> IRegulFrame:
        """Envoie « {code#p1#p2…} » et décode la réponse."""
        body = "#".join([code, *params]) if params else f"{code}#"
        return parse_frame(
            await self.async_raw("{" + body + "}", retry=retry, require_frame=require_frame)
        )

    # ---- lecture -------------------------------------------------------

    async def async_status(self) -> IRegulFrame:
        """État courant (commande 10)."""
        return await self.async_command(CMD_STATUS)

    async def async_discover(self) -> IRegulFrame:
        """Lecture complète avec libellés (commande 502, ~30 ko)."""
        return await self.async_command(CMD_DISCOVER)

    async def async_alarms(self) -> IRegulFrame:
        """Journal des alarmes (commande 98)."""
        return await self.async_command(CMD_ALARMS)

    # ---- écriture ------------------------------------------------------

    async def async_set_zone(
        self,
        zone_id: int,
        *,
        consigne_normal: float,
        consigne_reduit: float,
        consigne_horsgel: float,
        mode_select: int,
    ) -> IRegulFrame:
        """Écrit les consignes et le mode d'une zone (commande 11).

        L'application officielle envoie toujours les quatre champs ensemble.
        """
        if mode_select not in (MODE_AUTO, MODE_NORMAL, MODE_REDUIT, MODE_HORSGEL, MODE_ARRET):
            raise ValueError(f"mode_select invalide : {mode_select}")
        params = [
            f"DT_zones@{zone_id}&consigne_normal[{_fmt(consigne_normal)}]",
            f"DT_zones@{zone_id}&consigne_reduit[{_fmt(consigne_reduit)}]",
            f"DT_zones@{zone_id}&consigne_horsgel[{_fmt(consigne_horsgel)}]",
            f"DT_zones@{zone_id}&mode_select[{int(mode_select)}]",
        ]
        return await self.async_command(CMD_SET_ZONE, *params, retry=False, require_frame=False)

    async def async_set_authorizations(self, heating: bool, cooling: bool) -> IRegulFrame:
        """Autorise/interdit le chauffage et le rafraîchissement (commande 12)."""
        return await self.async_command(
            CMD_SET_AUTH,
            f"DT_config@0&autorisation_chauffage[{int(heating)}]",
            f"DT_config@0&autorisation_rafraichissement[{int(cooling)}]",
            retry=False,
            require_frame=False,
        )

    async def async_reset_alarm(self) -> IRegulFrame:
        """Acquitte l'alarme (commande 202)."""
        return await self.async_command(CMD_RESET_ALARM, retry=False, require_frame=False)

    async def async_defrost(self) -> IRegulFrame:
        """Force un dégivrage (commande 203)."""
        return await self.async_command(CMD_DEFROST, retry=False, require_frame=False)


def _fmt(value: float) -> str:
    """Formate une consigne comme l'appli (entier si possible, sinon 1 décimale)."""
    value = round(float(value), 1)
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"
