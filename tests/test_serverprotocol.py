# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

import asyncio
import socket
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


class ServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = VarlinkInterfaceRegistry()
        self.registry.register_interface(DemoInterface())

    async def invoke(self, request: bytes, expected_response: bytes) -> None:
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
            await loop.sock_sendall(sock1, request + b"\0")
            data = await loop.sock_recv(sock1, 1024)
            self.assertEqual(data, expected_response + b"\0")
        finally:
            if transport:
                transport.close()
                await asyncio.sleep(0)
                self.assertLess(sock2.fileno(), 0)
            else:
                sock2.close()
            sock1.close()

    async def test_smoke(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.Answer"}',
            b'{"parameters":{"result":42}}',
        )

    async def test_error(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.Error"}',
            b'{"error":"com.example.demo.DemoError"}',
        )

    async def test_async(self) -> None:
        await self.invoke(
            b'{"method":"com.example.demo.AsyncAnswer"}',
            b'{"parameters":{"result":42}}',
        )
