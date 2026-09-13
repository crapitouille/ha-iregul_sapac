"""Fixtures : serveur i-regul simulé rejouant des trames réelles anonymisées."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from custom_components.iregul import api as iregul_api

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Active les custom components dans les tests."""
    yield


class FakeIRegulServer:
    """Serveur TCP rejouant les captures et enregistrant les commandes reçues."""

    def __init__(self, serial: str, password: str) -> None:
        self.serial = serial
        self.password = password
        self.commands: list[str] = []
        self.status = (FIXTURES / "frame_10.txt").read_text(encoding="utf-8")
        self.discover = (FIXTURES / "frame_502.txt").read_text(encoding="utf-8")
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(4096)
        msg = data.decode()
        prefix = f"cdraminfo{self.serial}"
        if not msg.startswith(prefix):
            writer.write(b"SNI}\r")
        elif not msg[len(prefix):].startswith(self.password):
            writer.write(b"PWD}\r")
        else:
            cmd = msg[len(prefix) + len(self.password):]
            self.commands.append(cmd)
            code = cmd[1:].split("#", 1)[0]
            if code == "502":
                writer.write(self.discover.encode("utf-8"))
            elif code in ("11", "12", "202", "203"):
                # Le serveur répond par un écho vide du code, puis on rafraîchit avec {10#}
                writer.write(f"{{{code}#}}\r".encode())
            else:
                writer.write(self.status.encode("utf-8"))
        await writer.drain()
        writer.close()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self.server
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
async def fake_server(monkeypatch, socket_enabled) -> AsyncIterator[FakeIRegulServer]:
    """Démarre le faux serveur et y redirige le client."""
    server = FakeIRegulServer("108944", "secret")
    await server.start()
    monkeypatch.setattr(iregul_api, "DEFAULT_HOST", "127.0.0.1")
    monkeypatch.setattr(iregul_api, "DEFAULT_PORT", server.port)
    # Pas d'attente entre commandes ni de backoff dans les tests.
    monkeypatch.setattr(iregul_api, "MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr(iregul_api, "RETRY_BACKOFF", 0.0)
    orig_init = iregul_api.IRegulClient.__init__

    def patched_init(self, serial, password, host=None, port=None, timeout=iregul_api.DEFAULT_TIMEOUT):
        orig_init(self, serial, password, host="127.0.0.1", port=server.port, timeout=timeout)

    monkeypatch.setattr(iregul_api.IRegulClient, "__init__", patched_init)
    yield server
    await server.stop()
