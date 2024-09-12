# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""asncio and protocol level varlink functionality."""

import asyncio
import collections
import errno
import functools
import json
import logging
import os
import socket
import typing

from .types import FileDescriptor, HasFileno, JSONObject

logger = logging.getLogger("asyncvarlink.protocol")


def _close(thing: HasFileno) -> None:
    """Close something that has a fileno. Use .close() if available to improve
    behaviour on sockets and buffered files.
    """
    try:
        # Silence the type checker: HasFileno doesn't have close, but we're
        # handling the AttributeError.
        closemeth = thing.close  # type: ignore[attr-defined]
    except AttributeError:
        os.close(thing.fileno())
    else:
        closemeth()


def _check_socket(thing: socket.socket | int | HasFileno) -> HasFileno:
    """Attempt to upgrade a file descriptor into a socket object if it happens
    to be a socket.
    """
    if isinstance(thing, socket.socket):
        return thing
    if not hasattr(thing, "fileno"):
        if not isinstance(thing, int):
            raise TypeError("not a file descriptor")
        thing = FileDescriptor(thing)
    assert hasattr(thing, "fileno")  # mypy is unable to notice
    try:
        sock = socket.socket(fileno=thing.fileno())
    except OSError as err:
        if err.errno == errno.ENOTSOCK:
            return thing
        raise
    if sock.type != socket.SOCK_STREAM:
        raise ValueError("the given socket is not SOCK_STREAM")
    return sock


_BLOCKING_ERRNOS = frozenset((errno.EWOULDBLOCK, errno.EAGAIN))


# pylint: disable=too-many-instance-attributes  # Yes, we need them all
class VarlinkTransport(asyncio.BaseTransport):
    """A specialized asyncio Transport class for use with varlink and file
    descriptor passing. As such, it provides send_message rather than write
    and expects the protocol class to provide message_received rather than
    read. It also allows sending and receiving to happen on different file
    descriptors to facilitate use with stdin and stdout pipes.
    """

    MAX_RECV_FDS = 1024
    """The maximum number of file descriptors that will be expected in a single
    varlink message.
    """

    # pylint: disable=too-many-arguments  # Yes, we need five arguments.
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        recvfd: socket.socket | int | HasFileno,
        sendfd: socket.socket | int | HasFileno,
        protocol: "VarlinkProtocol",
        extra: typing.Mapping[str, typing.Any] | None = None,
    ):
        super().__init__(extra)
        self._loop = loop
        self._recvfd: HasFileno | None = _check_socket(recvfd)
        self._paused = True
        os.set_blocking(self._recvfd.fileno(), False)
        self._sendfd: HasFileno | None
        if recvfd is sendfd:
            self._sendfd = self._recvfd
        else:
            self._sendfd = _check_socket(sendfd)
            os.set_blocking(self._sendfd.fileno(), False)
        if isinstance(self._sendfd, socket.socket):
            self._do_write = self._do_write_socket
        else:
            self._do_write = self._do_write_fd
        # Using a deque as we only do end operations and those become O(1).
        self._sendqueue: collections.deque[
            tuple[
                list[bytes],  # data to be sent
                list[int],  # file descriptors to be sent
                asyncio.Future[None],  # completion notification
            ]
        ]
        self._sendqueue = collections.deque()
        self._closing = False
        self.set_protocol(protocol)
        self._loop.call_soon(self._protocol.connection_made, self)
        self._loop.call_soon(self.resume_receiving)

    def set_protocol(self, protocol: asyncio.BaseProtocol) -> None:
        assert isinstance(protocol, VarlinkProtocol)
        self._protocol = protocol

    def get_protocol(self) -> "VarlinkProtocol":
        return self._protocol

    def _close_receiver(self) -> None:
        if self._recvfd is None:
            return
        if self._sendfd is None:
            self._closing = True
        elif self._recvfd.fileno() != self._sendfd.fileno():
            _close(self._recvfd)
        self._recvfd = None

    def _handle_read_socket(self) -> None:
        assert isinstance(self._recvfd, socket.socket)
        try:
            msg, fds, _flags, _addr = socket.recv_fds(
                self._recvfd, 4096, self.MAX_RECV_FDS
            )
        except OSError as err:
            if err.errno in _BLOCKING_ERRNOS:
                return
            logger.debug("%r: reading from socket failed", self, exc_info=True)
            self._loop.remove_reader(self._recvfd)
            self._close_receiver()
            return
        if msg:
            self._protocol.message_received(msg, fds)
        else:
            self._loop.remove_reader(self._recvfd)
            try:
                self._protocol.eof_received()
            finally:
                self._close_receiver()

    def _handle_read_fd(self) -> None:
        assert self._recvfd is not None
        try:
            data = os.read(self._recvfd.fileno(), 4096)
        except OSError as err:
            if err.errno in _BLOCKING_ERRNOS:
                return
            logger.debug("%r: reading from socket failed", self, exc_info=True)
            self._loop.remove_reader(self._recvfd)
            self._close_receiver()
            return
        if data:
            self._protocol.message_received(data, [])
        else:
            self._loop.remove_reader(self._recvfd)
            try:
                self._protocol.eof_received()
            finally:
                self._close_receiver()

    def pause_receiving(self) -> None:
        """Pause receiving messages. No data will be passed to the protocol's
        message_received() method until resume_receiving is called.
        """
        if self._closing or self._recvfd is None or self._paused:
            return
        self._paused = True
        self._loop.remove_reader(self._recvfd)

    def resume_receiving(self) -> None:
        """Resume receiving messages. Received messages will be passed to the
        protocol's message_received method again.
        """
        if self._closing or self._recvfd is None or not self._paused:
            return
        self._paused = False
        if isinstance(self._recvfd, socket.socket):
            self._loop.call_soon(
                self._loop.add_reader, self._recvfd, self._handle_read_socket
            )
        else:
            self._loop.call_soon(
                self._loop.add_reader, self._recvfd, self._handle_read_fd
            )

    def send_message(
        self, data: bytes, fds: list[int] | None = None
    ) -> asyncio.Future[None]:
        """Enqueue the given data and file descriptors for sending. In case
        file descriptors are provided, they will be delivered combined using
        sendmsg. Otherwise, messages may be concatenated. The returned future
        will be done when the message has been sent. The given file descriptors
        should remain open until then.
        """
        if self._do_write is self._do_write_fd and fds:
            raise ValueError("cannot send fds on non-socket transport")
        if fds is None:
            fds = []
        if self._closing:
            logger.warning("%r: attempt to write to closed transport", self)
            fut = self._loop.create_future()
            fut.set_exception(OSError(errno.EPIPE, "Broken pipe"))
            return fut
        assert self._sendfd is not None
        if self._sendqueue:
            lastitem = self._sendqueue[-1]
            if lastitem[1] or fds:
                fut = self._loop.create_future()
                self._sendqueue.append(([data], fds, fut))
            else:
                fut = lastitem[2]
                lastitem[0].append(data)
        else:
            fut = self._loop.create_future()
            self._sendqueue.append(([data], fds, fut))
            self._loop.call_soon(
                self._loop.add_writer, self._sendfd, self._handle_write
            )
        return fut

    def _close_sender(self) -> None:
        if self._sendfd is None:
            return
        self._loop.remove_writer(self._sendfd)
        if self._recvfd is None:
            self._closing = True
        elif self._recvfd.fileno() != self._sendfd.fileno():
            _close(self._sendfd)
        self._sendfd = None
        while self._sendqueue:
            _, _, fut = self._sendqueue.popleft()
            fut.set_exception(OSError(errno.EPIPE, "Broken pipe"))

    def _handle_write(self) -> None:
        assert self._sendfd is not None
        while self._sendqueue:
            data, fds, fut = self._sendqueue.popleft()
            try:
                sent = self._do_write(data, fds)
            except OSError as err:
                if err.errno in _BLOCKING_ERRNOS:
                    self._sendqueue.appendleft((data, fds, fut))
                else:
                    logger.debug("%r: sending failed", self, exc_info=True)
                    self._close_sender()
                    fut.set_exception(err)
                return
            while sent > 0:
                assert data
                if sent >= len(data[0]):
                    sent -= len(data.pop(0))
                else:
                    data[0] = data[0][:sent]
                    sent = 0
            if data:
                self._sendqueue.appendleft((data, [], fut))
            else:
                fut.set_result(None)
        if not self._sendqueue:
            if self._closing:
                self._close_sender()
            else:
                self._loop.remove_writer(self._sendfd)

    def _do_write_socket(self, data: list[bytes], fds: list[int]) -> int:
        assert isinstance(self._sendfd, socket.socket)
        if fds:
            return socket.send_fds(self._sendfd, data, fds)
        return self._sendfd.sendmsg(data)

    def _do_write_fd(self, data: list[bytes], fds: list[int]) -> int:
        assert not fds
        assert self._sendfd is not None
        return os.writev(self._sendfd.fileno(), data)

    def _connection_lost(self) -> None:
        try:
            self._protocol.connection_lost(None)
        finally:
            self._close_receiver()
            if not self._sendqueue:
                self._close_sender()

    def close(self) -> None:
        if not self._closing:
            self._closing = True
            self._loop.call_soon(self._connection_lost)

    def is_closing(self) -> bool:
        return self._closing


class VarlinkProtocol(asyncio.BaseProtocol):
    """An asyncio protocol that provides message_received() rather than
    data_received() to accommodate passed file descriptors.
    """

    def __init__(self) -> None:
        self._recv_buffer = b""
        self._consumer_queue: collections.deque[
            typing.Callable[[], asyncio.Future[None] | None]
        ] = collections.deque()
        self._transport: VarlinkTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, VarlinkTransport)
        self._transport = transport

    def message_received(self, data: bytes, fds: list[int]) -> None:
        """Called when the transport received new data. The data can be
        accompanied by open file descriptors. It is the responsibility of the
        method to eventually close them.
        """
        parts = data.split(b"\0")
        if self._recv_buffer:
            parts[0] = self._recv_buffer + parts[0]
        self._recv_buffer = parts.pop()
        loop = asyncio.get_running_loop()
        processing = bool(self._consumer_queue)
        for reqdata in parts:
            try:
                obj = json.loads(reqdata)
            except json.decoder.JSONDecodeError as err:
                self._consumer_queue.append(
                    functools.partial(self.error_received, err, reqdata, fds)
                )
            else:
                self._consumer_queue.append(
                    functools.partial(self.request_received, obj, fds)
                )
            if not processing:
                loop.call_soon(self._process_queue, None)
                processing = True
            fds = []

    def _process_queue(self, _: asyncio.Future[None] | None) -> None:
        assert self._transport is not None
        if not self._consumer_queue:
            self._transport.resume_receiving()
            return
        consume = self._consumer_queue.popleft()
        fut = None
        try:
            fut = consume()
        finally:
            if fut is None or fut.done():
                if self._consumer_queue:
                    asyncio.get_running_loop().call_soon(
                        self._process_queue, None
                    )
                else:
                    self._transport.resume_receiving()
            else:
                fut.add_done_callback(self._process_queue)
                self._transport.pause_receiving()

    def eof_received(self) -> None:
        """Callback for signalling the end of messages on the receiving side.
        The default implementation does nothing.
        """

    def request_received(
        self, obj: JSONObject, fds: list[int]
    ) -> asyncio.Future[None] | None:
        """Handle an incoming varlink request or response object together with
        associated file descriptors. If the handler returns a future, further
        processing will be delayed until the future is done. The handler is
        responsible for the disposal of passed file descriptors.
        """
        raise NotImplementedError

    # pylint: disable=unused-argument  # Arguments provided for inheritance
    def error_received(
        self, err: Exception, data: bytes, fds: list[int]
    ) -> None:
        """Handle an incoming protocol violation such as wrongly encoded JSON.
        The handler is responsible for disposing any received file descriptors
        and this is all that the default implementation does.
        """
        for fd in fds:
            os.close(fd)

    def send_message(
        self,
        obj: JSONObject,
        fds: list[int] | None = None,
        autoclose: bool = True,
    ) -> asyncio.Future[None]:
        """Send a varlink request or response together with associated file
        descriptors. The returned future is done once the message has actaullly
        been sent or terminally failed sending. In the latter case, the future
        raises an exception. If autoclose is True, the file descriptors are
        closed after transmission. Otherwise, the caller is responsible for
        closing them after completion of the returned future.
        """
        assert self._transport is not None
        fut = self._transport.send_message(
            json.dumps(obj).encode("utf8") + b"\0", fds
        )
        if fds is not None and autoclose:

            @fut.add_done_callback
            def close_fds(_: asyncio.Future[None]) -> None:
                for fd in fds:
                    os.close(fd)

        return fut

    def connection_lost(self, exc: Exception | None) -> None:
        pass