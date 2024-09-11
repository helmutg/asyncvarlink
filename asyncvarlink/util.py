# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""Utility functions that don't fit well elsewhere."""

import asyncio
import os
import socket
import typing

from .protocol import VarlinkProtocol, VarlinkTransport
from .types import FileDescriptor


async def connect_unix_varlink(
    loop: asyncio.AbstractEventLoop,
    protocol_factory: type[VarlinkProtocol],
    path: os.PathLike[str] | str,
) -> tuple[VarlinkTransport, VarlinkProtocol]:
    """Connect to the unix domain socket at given path and return a varlink
    connection.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0)
    try:
        sock.setblocking(False)
        await loop.sock_connect(sock, os.fspath(path))
    except:
        sock.close()
        raise
    protocol = protocol_factory()
    transport = VarlinkTransport(loop, sock, sock, protocol)
    await asyncio.sleep(0)  # wait for all call_soon
    return transport, protocol


def get_listen_fd(name: str) -> typing.Optional[FileDescriptor]:
    """Obtain a file descriptor using the systemd socket activation
    protocol.
    """
    try:
        pid = int(os.environ["LISTEN_PID"])
        fds = int(os.environ["LISTEN_FDS"])
    except (KeyError, ValueError):
        return None
    if fds < 1 or pid != os.getpid():
        return None
    if fds == 1:
        if os.environ.get("LISTEN_FDNAMES", name) != name:
            return None
        return FileDescriptor(3)
    try:
        names = os.environ["LISTEN_FDNAMES"].split(":")
    except KeyError:
        return None
    if len(names) != fds:
        return None
    try:
        return FileDescriptor(3 + names.index(name))
    except ValueError:
        return None
