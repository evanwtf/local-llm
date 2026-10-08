"""The byte-for-byte contract of the TCP relay (#999)."""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib

import pytest

_spec = importlib.util.spec_from_file_location(
    "tcp_relay",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "tcp_relay.py",
)
assert _spec is not None and _spec.loader is not None
relay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay)


async def _upstream(received: list[bytes]) -> asyncio.Server:
    """An upstream that records what it got, then answers and closes."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        data = await reader.read(1 << 20)
        received.append(data)
        writer.write(b"\x00reply\xff" + data[::-1])
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


def test_relays_bytes_unchanged_both_ways() -> None:
    async def run() -> None:
        received: list[bytes] = []
        up = await _upstream(received)
        up_port = up.sockets[0].getsockname()[1]
        srv = await relay.serve("127.0.0.1", 0, "127.0.0.1", up_port)
        port = srv.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        payload = b"POST /v1/x HTTP/1.1\r\n\r\n\x00\xff" * 100
        writer.write(payload)
        writer.write_eof()
        got = await reader.read()
        writer.close()
        srv.close()
        up.close()
        assert received == [payload]
        assert got == b"\x00reply\xff" + payload[::-1]

    asyncio.run(run())


def test_a_refused_upstream_closes_the_client_cleanly() -> None:
    async def run() -> None:
        # A port nothing listens on: bind, read the port, close.
        dead = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = dead.sockets[0].getsockname()[1]
        dead.close()
        await dead.wait_closed()
        srv = await relay.serve("127.0.0.1", 0, "127.0.0.1", dead_port)
        port = srv.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await asyncio.wait_for(reader.read(), 5) == b""
        writer.close()
        srv.close()

    asyncio.run(run())


def test_refuses_to_listen_on_every_interface() -> None:
    with pytest.raises(SystemExit):
        relay.main(["--listen", "0.0.0.0:8893", "--upstream", "127.0.0.1:8893"])
