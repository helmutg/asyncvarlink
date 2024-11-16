# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

import asyncio
import socket
import unittest

from asyncvarlink import (
    VarlinkClientProtocol,
    VarlinkInterface,
    VarlinkTransport,
    varlinkmethod,
)


class DemoInterface(VarlinkInterface, name="com.example.demo"):
    @varlinkmethod(return_parameter="result")
    def Method(self, argument: str) -> str: ...


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_smoke(self) -> None:
        loop = asyncio.get_running_loop()
        sock1, sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        transport = None
        try:
            proto = VarlinkClientProtocol()
            transport = VarlinkTransport(loop, sock2, sock2, proto)
            proxy = proto.make_proxy(DemoInterface)
            fut = asyncio.ensure_future(proxy.Method(argument="spam"))
            data = await loop.sock_recv(sock1, 1024)
            self.assertEqual(
                data,
                b'{"method":"com.example.demo.Method","parameters":{"argument":"spam"}}\0',
            )
            self.assertFalse(fut.done())
            await loop.sock_sendall(
                sock1, b'{"parameters":{"result":"egg"}}\0'
            )
            self.assertEqual(await fut, {"result": "egg"})
        finally:
            if transport:
                transport.close()
                await asyncio.sleep(0)
                self.assertLess(sock2.fileno(), 0)
            else:
                sock2.close()
            sock1.close()
