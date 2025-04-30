# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""Utility functions that don't fit well elsewhere."""

import asyncio
import contextlib
import os
import socket
import stat
import typing
import weakref

from .protocol import _BLOCKING_ERRNOS, VarlinkProtocol, VarlinkTransport
from .types import FileDescriptor


_T = typing.TypeVar("_T")


@typing.overload
def completing_future() -> typing.ContextManager[asyncio.Future[None]]: ...


@typing.overload
def completing_future(
    value: _T,
) -> typing.ContextManager[asyncio.Future[_T]]: ...


@contextlib.contextmanager
def completing_future(
    value: typing.Any = None,
) -> typing.Iterator[asyncio.Future[typing.Any]]:
    """A context manager that returns a new asyncio.Future which will be done
    with the passed value on context exit unless the context exits with an
    exception in which case the future also raises the exception. Even though
    this is a synchronous context manager, it must be used in an asynchronous
    context.
    """
    future = asyncio.get_running_loop().create_future()
    done = False
    try:
        yield future
    except BaseException as exc:
        future.set_exception(exc)
        done = True
        raise
    finally:
        if not done:
            future.set_result(value)


async def connect_unix_varlink(
    protocol_factory: typing.Callable[[], VarlinkProtocol],
    path: os.PathLike[str] | str,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> tuple[VarlinkTransport, VarlinkProtocol]:
    """Connect to the unix domain socket at given path and return a varlink
    connection.
    """
    if loop is None:
        loop = asyncio.get_running_loop()
    sock = socket.socket(
        socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK
    )
    try:
        await loop.sock_connect(sock, os.fspath(path))
    except:
        sock.close()
        raise
    protocol = protocol_factory()
    transport = VarlinkTransport(loop, sock, sock, protocol)
    await asyncio.sleep(0)  # wait for all call_soon
    return transport, protocol


class VarlinkUnixServer(asyncio.AbstractServer):
    """An asyncio server class that will construct VarlinkTransports for
    accepted connections.
    """

    def __init__(
        self,
        sock: socket.socket,
        protocol_factory: typing.Callable[[], VarlinkProtocol],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self._loop = asyncio.get_running_loop() if loop is None else loop
        self._sock = sock
        self._protocol_factory = protocol_factory
        self._serving: asyncio.Future[None] | None = None
        self._transports: weakref.WeakSet[VarlinkTransport] = weakref.WeakSet()

    def close(self) -> None:
        if self._serving is not None:
            self._loop.remove_reader(self._sock)
            self._serving.set_result(None)
            self._serving = None

    def get_loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def is_serving(self) -> bool:
        return self._serving is not None

    async def start_serving(self) -> None:
        if self._serving is None:
            self._serving = self._loop.create_future()
            self._sock.listen(1)
            self._loop.add_reader(self._sock, self._handle_accept)

    def _handle_accept(self) -> None:
        try:
            conn, addr = self._sock.accept()
        except OSError as err:
            if err.errno in _BLOCKING_ERRNOS:
                return
            self.close()
            raise
        conn.setblocking(False)
        self._transports.add(
            VarlinkTransport(
                self._loop,
                conn,
                conn,
                self._protocol_factory(),
                {"peername": addr},
            )
        )

    async def serve_forever(self) -> None:
        await self.start_serving()
        await self.wait_closed()

    async def wait_closed(self) -> None:
        if self._serving is None:
            return
        await self._serving

    def close_clients(self) -> None:
        for transport in self._transports.copy():
            transport.close()

    def abort_clients(self) -> None:
        for transport in self._transports.copy():
            transport.abort()


async def create_unix_server(
    protocol_factory: typing.Callable[[], VarlinkProtocol],
    path: os.PathLike[str] | str | None = None,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    sock: socket.socket | None = None,
    start_serving: bool = True,
) -> VarlinkUnixServer:
    """In a similar spirit to asyncio.SelectorEventLoop.create_unix_server
    create a UNIX domain socket server except that the transport class being
    used will be VarlinkTransport for being capable of sending and receiving
    file descriptors.

    Either the socket (sock) or path must be provided.
    """
    if sock is None:
        if path is None:
            raise ValueError("neither path nor sock specified")
        pathstr = os.fspath(path)
        sock = socket.socket(
            socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        if not pathstr.startswith("\0"):
            try:
                st = os.stat(pathstr)
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISSOCK(st.st_mode):
                    os.unlink(pathstr)
        try:
            sock.bind(pathstr)
        except:
            sock.close()
            raise
    else:
        sock.setblocking(False)
    server = VarlinkUnixServer(sock, protocol_factory, loop=loop)
    if start_serving:
        await server.start_serving()
    return server


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
