# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import asyncio
import contextlib
import json
import socket
import typing
import unittest

from asyncvarlink import (
    TypedVarlinkErrorReply,
    VarlinkInterface,
    VarlinkInterfaceRegistry,
    VarlinkInterfaceServerProtocol,
    VarlinkTransport,
    varlinkmethod,
)


class DemoError(TypedVarlinkErrorReply, interface="com.example.demo"):
    class Parameters:
        pass


class DemoInterface(VarlinkInterface, name="com.example.demo"):
    @varlinkmethod(return_parameter="result")
    def Answer(self) -> int:
        return 42

    @varlinkmethod
    def Error(self) -> None:
        raise DemoError()

    @varlinkmethod(return_parameter="result")
    async def AsyncAnswer(self) -> int:
        await asyncio.sleep(0)
        return 42

    @varlinkmethod(return_parameter="result")
    def SyncMore(self) -> typing.Iterator[int]:
        yield 1
        yield 2

    @varlinkmethod(return_parameter="result")
    def SyncMoreError(self) -> typing.Iterator[int]:
        yield 1
        raise DemoError()

    @varlinkmethod(return_parameter="result")
    async def AsyncMore(self) -> typing.AsyncIterator[int]:
        yield 1
        yield 2


class ServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = VarlinkInterfaceRegistry()
        self.registry.register_interface(DemoInterface())

    @contextlib.asynccontextmanager
    async def connected_server(
        self,
    ) -> typing.AsyncIterator[tuple[socket.socket, socket.socket]]:
        loop = asyncio.get_running_loop()
        sock1, sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        transport: VarlinkTransport | None = None
        try:
            transport = VarlinkTransport(
                loop,
                sock2,
                sock2,
                VarlinkInterfaceServerProtocol(self.registry),
            )
            yield (sock1, sock2)
        finally:
            if transport:
                transport.close()
                await asyncio.sleep(0)
                self.assertLess(sock2.fileno(), 0)
            else:
                sock2.close()
            sock1.close()

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

    async def test_more_error(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.SyncMoreError","more":true}',
            b'{"continues":true,"parameters":{"result":1}}\0'
            b'{"error":"com.example.demo.DemoError"}',
        )

    async def test_async(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.AsyncAnswer"}',
            b'{"parameters":{"result":42}}',
        )

    async def test_protocol_violation(self) -> None:
        await self.invoke(
            b"{}",
            b'{"error":"invalid.asyncvarlink.ProtocolViolation"}',
        )

    async def test_broken_pipe(self) -> None:
        loop = asyncio.get_running_loop()
        async with self.connected_server() as (sock1, sock2):
            await loop.sock_sendall(
                sock1, b'{"method":"com.example.demo.AsyncAnswer"}\0'
            )
            sock1.close()
            self.assertGreaterEqual(sock2.fileno(), 0)
            # A single sleep(0) does not seem to suffice here.
            for _ in range(99):
                if sock2.fileno() < 0:
                    break
                await asyncio.sleep(0)
            self.assertLess(sock2.fileno(), 0)
