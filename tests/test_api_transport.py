"""Tests du transport : fragmentation et reset serveur (Errno 104)."""

from __future__ import annotations

import asyncio
import struct

import pytest

from custom_components.iregul.api import IRegulClient, IRegulConnectionError


async def _serve_once(payload: bytes, *, split_tail: bool, rst: bool) -> tuple[str, int, asyncio.AbstractServer]:
    """Serveur qui envoie payload (éventuellement en fragments) puis ferme/RST."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        if split_tail and payload.endswith(b"}\r"):
            # « } » et « \r » dans deux paquets séparés — le cas du bug.
            writer.write(payload[:-1])
            await writer.drain()
            await asyncio.sleep(0.02)
            writer.write(payload[-1:])
            await writer.drain()
        else:
            writer.write(payload)
            await writer.drain()
        if rst:
            # Force un RST plutôt qu'un FIN propre (SO_LINGER 0).
            sock = writer.get_extra_info("socket")
            sock.setsockopt(1, 7, struct.pack("ii", 1, 0))  # SOL_SOCKET, SO_LINGER
            writer.close()
        else:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return "127.0.0.1", port, server


@pytest.mark.parametrize("split_tail", [False, True])
@pytest.mark.parametrize("rst", [False, True])
async def test_read_handles_fragmentation_and_reset(socket_enabled, split_tail, rst) -> None:
    """La trame complète est lue même si le « } » final est fragmenté et suivi d'un RST."""
    payload = b"06/09/2026 19:45:13{10#A@3&valeur[23.7]#mem@0&etat[5]}\r"
    host, port, server = await _serve_once(payload, split_tail=split_tail, rst=rst)
    async with server:
        client = IRegulClient("108944", "secret", host=host, port=port, timeout=5)
        frame = await client.async_status()
    assert frame.get_float("A", 3) == 23.7
    assert frame.get("mem", 0, "etat") == "5"


async def test_retry_on_rate_limit_reset(socket_enabled, monkeypatch) -> None:
    """Un reset immédiat (rate-limit) sur une lecture est réessayé avec succès."""
    monkeypatch.setattr("custom_components.iregul.api.MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr("custom_components.iregul.api.RETRY_BACKOFF", 0.0)
    payload = b"{10#A@3&valeur[23.7]#mem@0&etat[5]}\r"
    calls = {"n": 0}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        calls["n"] += 1
        if calls["n"] == 1:
            # Première connexion : RST immédiat sans données (comme le vrai serveur).
            sock = writer.get_extra_info("socket")
            sock.setsockopt(1, 7, struct.pack("ii", 1, 0))
            writer.close()
            return
        writer.write(payload)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        client = IRegulClient("108944", "secret", host="127.0.0.1", port=port, timeout=5)
        frame = await client.async_status()
    assert calls["n"] >= 2
    assert frame.get_float("A", 3) == 23.7


async def test_write_not_retried_on_reset(socket_enabled, monkeypatch) -> None:
    """Une écriture (retry=False) n'est PAS rejouée en cas de reset."""
    monkeypatch.setattr("custom_components.iregul.api.MIN_REQUEST_INTERVAL", 0.0)
    calls = {"n": 0}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        calls["n"] += 1
        sock = writer.get_extra_info("socket")
        sock.setsockopt(1, 7, struct.pack("ii", 1, 0))
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        client = IRegulClient("108944", "secret", host="127.0.0.1", port=port, timeout=5)
        # Selon le timing, un reset donne soit une erreur soit une trame vide,
        # mais l'écriture ne doit jamais être rejouée.
        try:
            await client.async_reset_alarm()
        except IRegulConnectionError:
            pass
    assert calls["n"] == 1


async def test_reset_without_data_is_not_valid(socket_enabled, monkeypatch) -> None:
    """Un reset/fermeture sans données ne produit pas de trame exploitable.

    Le serveur ferme immédiatement : selon le timing, la lecture lève un reset
    (→ IRegulConnectionError) ou renvoie EOF (→ trame vide). Dans les deux cas
    l'installation ne doit pas se charger (config flow / coordinator rejettent
    une trame sans points).
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        sock = writer.get_extra_info("socket")
        sock.setsockopt(1, 7, struct.pack("ii", 1, 0))
        writer.close()

    monkeypatch.setattr("custom_components.iregul.api.MIN_REQUEST_INTERVAL", 0.0)
    monkeypatch.setattr("custom_components.iregul.api.RETRY_BACKOFF", 0.0)
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        client = IRegulClient("108944", "secret", host="127.0.0.1", port=port, timeout=5)
        try:
            frame = await client.async_status()
        except IRegulConnectionError:
            return
        assert not frame.points
