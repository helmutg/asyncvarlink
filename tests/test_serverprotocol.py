# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

import asyncio
import socket
import unittest

from asyncvarlink import (
    VarlinkInterface,
    VarlinkInterfaceRegistry,
    VarlinkInterfaceServerProtocol,
    VarlinkTransport,
    varlinkmethod,
)


class DemoInterface(VarlinkInterface, name="com.example.demo"):
    @varlinkmethod(return_parameter="result")
    def Answer(self) -> int:
        return 42


class ServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = VarlinkInterfaceRegistry()
        self.registry.register_interface(DemoInterface())

    async def test_smoke(self) -> None:
        loop = asyncio.get_running_loop()
        sock1, sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        try:
            VarlinkTransport(
                loop,
                sock2,
                sock2,
                VarlinkInterfaceServerProtocol(self.registry),
            )
            await loop.sock_sendall(
                sock1, b'{"method":"com.example.demo.Answer"}\0'
            )
            data = await loop.sock_recv(sock1, 1024)
            self.assertEqual(data, b'{"parameters": {"result": 42}}\0')
        finally:
            sock1.close()
            sock2.close()
