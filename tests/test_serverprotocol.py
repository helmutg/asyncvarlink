# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import asyncio
import collections.abc
import contextlib
import json
import os
import socket
import typing
import unittest
from unittest.mock import Mock

from asyncvarlink import (
    FileDescriptor,
    override,
    TypedVarlinkErrorReply,
    VarlinkInterface,
    VarlinkInterfaceRegistry,
    VarlinkInterfaceServerProtocol,
    VarlinkTransport,
    varlinkmethod,
)

from helpers import async_read_fd


class DemoError(TypedVarlinkErrorReply, interface="com.example.demo"):
    class Parameters:
        pass


class DemoInterface(VarlinkInterface, name="com.example.demo"):
    def __init__(self, fut: asyncio.Future[int]):
        super().__init__()
        self.fut = fut

    @varlinkmethod(return_parameter="result")
    def Answer(self) -> int:
        return 42

    @varlinkmethod
    def Error(self) -> None:
        raise DemoError()

    @varlinkmethod
    async def AsyncError(self) -> None:
        await asyncio.sleep(0)
        raise DemoError()

    @varlinkmethod(return_parameter="result")
    async def FutureAnswer(self) -> int:
        return await self.fut

    @varlinkmethod(return_parameter="result")
    def SyncMore(self) -> collections.abc.Iterator[int]:
        yield 1
        yield 2

    @varlinkmethod(return_parameter="result")
    def SyncMoreError(self) -> collections.abc.Iterator[int]:
        yield 1
        raise DemoError()

    @varlinkmethod(return_parameter="result")
    async def AsyncMore(self) -> collections.abc.AsyncIterator[int]:
        yield 1
        yield 2

    @varlinkmethod(return_parameter="fd")
    async def CreateFd(
        self, kind: typing.Literal["pipe", "socket"]
    ) -> FileDescriptor:
        if kind == "pipe":
            rend, wend = os.pipe()
            os.write(wend, b"needle")
            os.close(wend)
            return FileDescriptor(rend, True)
        assert kind == "socket"
        sock1, sock2 = socket.socketpair()
        sock1.send(b"needle")
        sock1.close()
        return FileDescriptor(sock2, True)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.registry = VarlinkInterfaceRegistry()
        self.fut = asyncio.get_running_loop().create_future()
        self.registry.register_interface(DemoInterface(self.fut))

    @contextlib.asynccontextmanager
    async def connected_server(
        self,
    ) -> collections.abc.AsyncIterator[tuple[socket.socket, socket.socket]]:
        loop = asyncio.get_running_loop()
        sock1, sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        self.protocol = VarlinkInterfaceServerProtocol(self.registry)
        transport: VarlinkTransport | None = None
        try:
            transport = VarlinkTransport(loop, sock2, sock2, self.protocol)
            yield (sock1, sock2)
        finally:
            if transport:
                transport.close()
                await asyncio.sleep(0)
                self.assertLess(sock2.fileno(), 0)
            else:
                sock2.close()
            sock1.close()

    @contextlib.asynccontextmanager
    async def piped_server(
        self,
    ) -> collections.abc.AsyncIterator[tuple[FileDescriptor, FileDescriptor]]:
        loop = asyncio.get_running_loop()
        self.protocol = VarlinkInterfaceServerProtocol(self.registry)
        pipe1r, pipe1w = FileDescriptor.make_pipe()
        pipe2r, pipe2w = FileDescriptor.make_pipe()
        # FileDescriptor makes close idempotent
        with (
            contextlib.closing(pipe1r),
            contextlib.closing(pipe1w),
            contextlib.closing(pipe2r),
            contextlib.closing(pipe2w),
        ):
            transport: VarlinkTransport | None = None
            try:
                transport = VarlinkTransport(
                    loop, pipe2r, pipe1w, self.protocol
                )
                yield (pipe1r, pipe2w)
            finally:
                if transport:
                    transport.close()
                    await asyncio.sleep(0)
                    self.assertFalse(pipe2r)
                    self.assertFalse(pipe1w)

    def sock_recv_fds(
        self, sock: socket.socket
    ) -> asyncio.Future[tuple[bytes, list[int]]]:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        loop.add_reader(sock, self._sock_recv_fds, sock, fut)
        return fut

    def _sock_recv_fds(
        self, sock: socket.socket, fut: asyncio.Future[tuple[bytes, list[int]]]
    ) -> None:
        asyncio.get_running_loop().remove_reader(sock)
        try:
            data, fds, _flags, _addr = socket.recv_fds(sock, 1024, 32)
        except Exception as exc:
            fut.set_exception(exc)
        else:
            fut.set_result((data, fds))

    async def invoke(self, request: bytes, expected_response: bytes) -> None:
        loop = asyncio.get_running_loop()
        async with self.connected_server() as (sock1, _):
            await loop.sock_sendall(sock1, request + b"\0")
            data = await loop.sock_recv(sock1, 1024)
            if json.loads(data.split(b"\0", 1)[0]).get("continues"):
                data += await loop.sock_recv(sock1, 1024)
            self.assertEqual(data, expected_response + b"\0")

    async def test_sync_single(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.Answer"}',
            b'{"parameters":{"result":42}}',
        )

    async def test_more(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.SyncMore","more":true}',
            b'{"continues":true,"parameters":{"result":1}}\0'
            b'{"parameters":{"result":2}}',
        )
        await self.invoke(
            b'{"method":"com.example.demo.AsyncMore","more":true}',
            b'{"continues":true,"parameters":{"result":1}}\0'
            b'{"parameters":{"result":2}}',
        )

    async def test_error(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.Error"}',
            b'{"error":"com.example.demo.DemoError"}',
        )
        await self.invoke(
            b'{"method":"com.example.demo.SyncMore"}',
            b'{"error":"org.varlink.service.ExpectedMore"}',
        )

    async def test_async_error(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.AsyncError"}',
            b'{"error":"com.example.demo.DemoError"}',
        )

    async def test_more_error(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.SyncMoreError","more":true}',
            b'{"continues":true,"parameters":{"result":1}}\0'
            b'{"error":"com.example.demo.DemoError"}',
        )

    async def test_invalid_interface(self) -> None:
        await self.invoke(
            b'{"method":"com.example.doesnotexist.Anything"}',
            b'{"error":"org.varlink.service.InterfaceNotFound","parameters":{"interface":"com.example.doesnotexist"}}',
        )

    async def test_invalid_method(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.DoesNotExist"}',
            b'{"error":"org.varlink.service.MethodNotFound","parameters":{"method":"DoesNotExist"}}',
        )

    async def test_invalid_parameters(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.Answer","parameters":{"unexpected":1}}',
            b'{"error":"org.varlink.service.InvalidParameter","parameters":{"parameter":"unexpected"}}',
        )

    async def test_async(self) -> None:
        self.fut.set_result(42)
        await self.invoke(
            b'{"method":"com.example.demo.FutureAnswer"}',
            b'{"parameters":{"result":42}}',
        )

    async def test_return_fd(self) -> None:
        loop = asyncio.get_running_loop()
        async with self.connected_server() as (sock1, sock2):
            for kind in ("pipe", "socket"):
                with self.subTest(kind=kind):
                    await loop.sock_sendall(
                        sock1,
                        b'{"method":"com.example.demo.CreateFd","parameters":{"kind":"%s"}}\0'
                        % kind.encode("ascii"),
                    )
                    data, fds = await self.sock_recv_fds(sock1)
                    self.assertEqual(data, b'{"parameters":{"fd":0}}\0')
                    self.assertEqual(len(fds), 1)
                    data = await async_read_fd(fds[0], 1024)
                    os.close(fds[0])
                    self.assertEqual(data, b"needle")

    async def test_protocol_violation(self) -> None:
        await self.invoke(
            b"{}",
            b'{"error":"invalid.asyncvarlink.ProtocolViolation"}',
        )

    async def test_broken_socket(self) -> None:
        loop = asyncio.get_running_loop()
        async with self.connected_server() as (sock1, sock2):
            self.protocol.connection_lost = Mock(return_value=None)
            await loop.sock_sendall(
                sock1, b'{"method":"com.example.demo.FutureAnswer"}\0'
            )
            sock1.close()
            for _ in range(10):
                await asyncio.sleep(0)
            # Since the future is still pending, the write end is not being
            # closed yet.
            self.assertFalse(self.protocol.connection_lost.called)
            self.fut.set_result(42)
            for delay in range(100):
                if self.protocol.connection_lost.called:
                    break
                await asyncio.sleep(0.01 * delay)
            self.protocol.connection_lost.assert_called_once_with(None)
            self.assertLess(sock2.fileno(), 0)

    async def test_pipe_closed(self) -> None:
        loop = asyncio.get_running_loop()
        async with self.piped_server() as (rpipe, wpipe):
            self.protocol.connection_lost = Mock(return_value=None)
            os.write(
                wpipe.fileno(), b'{"method":"com.example.demo.FutureAnswer"}\0'
            )
            wpipe.close()
            for _ in range(10):
                await asyncio.sleep(0)
            # Since the future is still pending, the write end is not being
            # closed yet.
            self.assertFalse(self.protocol.connection_lost.called)
            self.fut.set_result(42)
            data = await async_read_fd(rpipe.fileno(), 1024)
            self.assertEqual(data, b'{"parameters":{"result":42}}\0')
            for _ in range(10):
                if self.protocol.connection_lost.called:
                    break
                await asyncio.sleep(0)
            self.protocol.connection_lost.assert_called_once_with(None)

    async def test_broken_pipe(self) -> None:
        loop = asyncio.get_running_loop()
        async with self.piped_server() as (rpipe, wpipe):
            self.protocol.connection_lost = Mock(return_value=None)
            rpipe.close()
            os.write(
                wpipe.fileno(), b'{"method":"com.example.demo.FutureAnswer"}\0'
            )
            wpipe.close()
            self.fut.set_result(42)
            for _ in range(20):
                if self.protocol.connection_lost.called:
                    break
                await asyncio.sleep(0)
            self.protocol.connection_lost.assert_called_once_with(None)
