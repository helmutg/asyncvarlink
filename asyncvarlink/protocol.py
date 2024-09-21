# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""asncio and protocol level varlink functionality."""

import asyncio
import collections
import dataclasses
import errno
import functools
import json
import logging
import os
import socket
import typing

from .types import (
    FileDescriptor,
    HasFileno,
    JSONObject,
    JSONValue,
    OwnedFileDescriptors,
    close_fileno,
    validate_interface,
    validate_name,
)

logger = logging.getLogger("asyncvarlink.protocol")


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
            close_fileno(self._recvfd)
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
        ownedfds = OwnedFileDescriptors(fds)
        if msg:
            try:
                maybefut = self._protocol.message_received(msg, ownedfds)
            except:
                ownedfds.close()
                raise
            if maybefut:
                maybefut.add_done_callback(lambda _fut: ownedfds.close())
            else:
                ownedfds.close()
        else:
            ownedfds.close()
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
            self._protocol.message_received(data, OwnedFileDescriptors())
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
            close_fileno(self._sendfd)
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
            tuple[
                # A closure for invoking the next consumer
                typing.Callable[[], asyncio.Future[None] | None],
                # An optional future that should be notified when the consumer
                # is done consuming (i.e. the future it returned is completed
                # or it returned None or raised an exception).
                asyncio.Future[None] | None,
            ]
        ] = collections.deque()
        self._transport: VarlinkTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, VarlinkTransport)
        self._transport = transport

    def message_received(
        self, data: bytes, fds: OwnedFileDescriptors
    ) -> asyncio.Future[None] | None:
        """Called when the transport received new data. The data can be
        accompanied by open file descriptors. The caller will close all
        passed file descriptors that have not been taken
        (OwnedFileDescriptors.take) once message_received returns None or the
        returned future completes.
        """
        parts = data.split(b"\0")
        if self._recv_buffer:
            parts[0] = self._recv_buffer + parts[0]
        self._recv_buffer = parts.pop()
        loop = asyncio.get_running_loop()
        processing = bool(self._consumer_queue)
        result = None
        for reqdata in parts:
            fut = loop.create_future() if fds else None
            if result is None:
                result = fut
            else:
                assert fut is None
            try:
                obj = json.loads(reqdata)
            except json.decoder.JSONDecodeError as err:
                self._consumer_queue.append(
                    (
                        functools.partial(
                            self.error_received, err, reqdata, fds
                        ),
                        fut,
                    ),
                )
            else:
                self._consumer_queue.append(
                    (functools.partial(self.request_received, obj, fds), fut)
                )
            if not processing:
                loop.call_soon(self._process_queue, None)
                processing = True
            fds = OwnedFileDescriptors()
        return result

    def _process_queue(self, _: asyncio.Future[None] | None) -> None:
        assert self._transport is not None
        if not self._consumer_queue:
            self._transport.resume_receiving()
            return
        consume, notify = self._consumer_queue.popleft()
        fut = None
        try:
            fut = consume()
        finally:
            if fut is None or fut.done():
                if notify is not None:
                    notify.set_result(None)
                # If the consumer finishes immediately, skip back pressure
                # via pause_receiving as that typically incurs two syscalls.
                if self._consumer_queue:
                    asyncio.get_running_loop().call_soon(
                        self._process_queue, None
                    )
                else:
                    self._transport.resume_receiving()
            else:
                if notify is not None:
                    fut.add_done_callback(lambda _fut: notify.set_result(None))
                fut.add_done_callback(self._process_queue)
                self._transport.pause_receiving()

    def eof_received(self) -> None:
        """Callback for signalling the end of messages on the receiving side.
        The default implementation does nothing.
        """

    def request_received(
        self, obj: JSONObject, fds: OwnedFileDescriptors
    ) -> asyncio.Future[None] | None:
        """Handle an incoming varlink request or response object together with
        associated file descriptors. If the handler returns a future, further
        processing will be delayed until the future is done. Once the function
        returns None or the returned future completes all remaining fds that
        have not been taken (OwnedFileDescriptors.take) will be closed.
        """
        raise NotImplementedError

    # pylint: disable=unused-argument  # Arguments provided for inheritance
    def error_received(
        self, err: Exception, data: bytes, fds: OwnedFileDescriptors
    ) -> None:
        """Handle an incoming protocol violation such as wrongly encoded JSON.
        The default handler does nothing.
        """

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


@dataclasses.dataclass
class VarlinkMethodCall:
    """Represent a parsed and roughly validated varlink method call."""

    method: str
    parameters: JSONObject
    oneway: bool = False
    more: bool = False
    upgrade: bool = False
    extensions: JSONObject = dataclasses.field(default_factory=dict)

    @property
    def method_interface(self) -> str:
        """Return the interface portion of the method string. May raise a
        ValueError.
        """
        interface, dot, _ = self.method.rpartition(".")
        if dot != ".":
            raise ValueError("unqualified method string")
        return interface

    @property
    def method_name(self) -> str:
        """Return the unqualified name portion of the method string."""
        return self.method.rpartition(".")[2]

    def __post_init__(self) -> None:
        validate_interface(self.method_interface)
        validate_name(self.method_name)

    @classmethod
    def fromjson(cls, obj: JSONValue) -> "VarlinkMethodCall":
        """Parse a JSON value into a validated VarlinkMethodCall. May raise
        TypeError and ValueError.
        """
        if not isinstance(obj, dict):
            raise TypeError(
                f"call object must be a map, is {obj.__class__.__name__}"
            )
        extensions = obj.copy()
        try:
            method = extensions.pop("method")
        except KeyError:
            raise ValueError("call object must have a method") from None
        if not isinstance(method, str):
            raise TypeError(
                f"method field of call object must be a str, is "
                f"{method.__class__.__name__}"
            )
        parameters = extensions.pop("parameters", {})
        if not isinstance(parameters, dict):
            raise TypeError(
                f"call parameters must be map, are "
                f"{parameters.__class__.__name__}"
            )
        oneway = extensions.pop("oneway", False)
        if not isinstance(oneway, bool):
            raise TypeError(
                f"call property oneay must be bool, is "
                f"{oneway.__class__.__name__}"
            )
        more = extensions.pop("more", False)
        if not isinstance(more, bool):
            raise TypeError(
                f"call property more must be bool, is "
                f"{oneway.__class__.__name__}"
            )
        upgrade = extensions.pop("upgrade", False)
        if not isinstance(upgrade, bool):
            raise TypeError(
                f"call property upgrade must be bool, is "
                f"{oneway.__class__.__name__}"
            )
        if sum((oneway, more, upgrade)) > 1:
            raise ValueError("cannot combine oneway, more or upgrade")
        return cls(
            method,
            parameters,
            oneway,
            more,
            upgrade,
            extensions,
        )

    def tojson(self) -> JSONObject:
        """Export as a JSONObject suitable for json.dumps."""
        result: JSONObject = {"method": self.method}
        if self.parameters:
            result["parameters"] = self.parameters
        if self.oneway:
            result["oneway"] = True
        if self.more:
            result["more"] = True
        if self.upgrade:
            result["upgrade"] = True
        result.update(self.extensions)
        return result


@dataclasses.dataclass
class VarlinkMethodReply:
    """Represent a parsed and roughly validated varlink method reply."""

    parameters: JSONObject
    continues: bool = False
    error: str | None = None
    extensions: JSONObject = dataclasses.field(default_factory=dict)

    @property
    def error_interface(self) -> str:
        """Return the interface portion of the error string if any. May raise a
        ValueError.
        """
        if self.error is None:
            raise ValueError("not an error")
        interface, dot, _ = self.error.rpartition(".")
        if dot != ".":
            raise ValueError("unqualified error string")
        return interface

    @property
    def error_name(self) -> str:
        """Return the unqualified name portion of the error string if any. May
        raise a ValueError.
        """
        if self.error is None:
            raise ValueError("not an error")
        return self.error.rpartition(".")[2]

    def __post_init__(self) -> None:
        if self.error is not None:
            validate_interface(self.error_interface)
            validate_name(self.error_name)

    @classmethod
    def fromjson(cls, obj: JSONValue) -> "VarlinkMethodReply":
        """Parse a JSON value into a validated VarlinkMethodReply. May raise
        TypeError and ValueError.
        """
        if not isinstance(obj, dict):
            raise TypeError(
                f"call object must be a map, is {obj.__class__.__name__}"
            )
        extensions = obj.copy()
        parameters = extensions.pop("parameters", {})
        if not isinstance(parameters, dict):
            raise TypeError(
                f"reply parameters must be map, are "
                f"{parameters.__class__.__name__}"
            )
        continues = extensions.pop("continues", False)
        if not isinstance(continues, bool):
            raise TypeError(
                f"reply property continues must be bool, is "
                f"{continues.__class__.__name__}"
            )
        error = extensions.pop("error", None)
        if error is not None and not isinstance(error, str):
            raise TypeError(
                f"reply property error must be str, is "
                f"{error.__class__.__name__}"
            )
        return VarlinkMethodReply(parameters, continues, error, extensions)

    def tojson(self) -> JSONObject:
        """Export as a JSONObject suitable for json.dumps."""
        result: JSONObject = {}
        if self.continues:
            result["continues"] = True
        if self.error:
            result["error"] = self.error
        if self.parameters:
            result["parameters"] = self.parameters
        result.update(self.extensions)
        return result
