"""Relay a TCP port byte for byte, from one address to another. #999

SparkGLM's Atlas server listens only on the head's loopback
(`127.0.0.1:8893`) and has no option to listen elsewhere, but the benchmark
client runs on another host. This relay listens on one named address (the
head's LAN address, at the same port) and copies bytes to the loopback server
and back, unchanged. Unlike `vllm_compat_proxy.py`, it never parses HTTP, so
it cannot alter a request or a reply.

It refuses to listen on every interface (`0.0.0.0` or `::`): the server has
no authentication, so the relay reaches only the address it names.

    uv run python scripts/tcp_relay.py --listen <LAN address>:8893 --upstream 127.0.0.1:8893

Standard library only (#235/#368).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger("tcp_relay")

_ANY = {"0.0.0.0", "::", ""}
_CHUNK = 1 << 16


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy until EOF, then half-close the other side."""
    try:
        while data := await reader.read(_CHUNK):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


async def _handle(
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    up_host: str,
    up_port: int,
) -> None:
    peer = client_w.get_extra_info("peername")
    try:
        up_r, up_w = await asyncio.open_connection(up_host, up_port)
    except OSError as exc:
        logger.warning("%s: upstream %s:%d refused: %s", peer, up_host, up_port, exc)
        client_w.close()
        return
    try:
        await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))
    finally:
        for w in (up_w, client_w):
            w.close()


async def serve(host: str, port: int, up_host: str, up_port: int) -> asyncio.Server:
    """Start relaying ``host:port`` to ``up_host:up_port``; returns the server."""

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await _handle(r, w, up_host, up_port)

    return await asyncio.start_server(handle, host, port)


def _address(text: str) -> tuple[str, int]:
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit():
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {text!r}")
    return host.strip("[]"), int(port)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--listen", type=_address, required=True, help="HOST:PORT to listen on"
    )
    p.add_argument(
        "--upstream", type=_address, required=True, help="HOST:PORT to relay to"
    )
    args = p.parse_args(argv)
    host, port = args.listen
    if host in _ANY:
        p.error("refusing to listen on every interface: name one address")
    logs.configure()
    up_host, up_port = args.upstream

    async def run() -> None:
        srv = await serve(host, port, up_host, up_port)
        logger.info("relaying %s:%d -> %s:%d", host, port, up_host, up_port)
        async with srv:
            await srv.serve_forever()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
