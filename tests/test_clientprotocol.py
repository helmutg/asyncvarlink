# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

import asyncio
import socket
import typing
import unittest

from asyncvarlink import (
    ConversionError,
    VarlinkClientProtocol,
    VarlinkErrorReply,
    VarlinkInterface,
    VarlinkTransport,
    varlinkmethod,
)


class DemoInterface(VarlinkInterface, name="com.example.demo"):
    @varlinkmethod(return_parameter="result")
    def Method(self, argument: str) -> str: ...

    @varlinkmethod(return_parameter="result")
    def MoreMethod(self) -> typing.Iterator[str]: ...


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.loop = asyncio.get_running_loop()
        self.sock1, self.sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        self.proto = VarlinkClientProtocol()
        self.transport = VarlinkTransport(
            self.loop, self.sock2, self.sock2, self.proto
        )
        self.proxy = self.proto.make_proxy(DemoInterface)

    async def asyncTearDown(self) -> None:
        self.transport.close()
        await asyncio.sleep(0)
        self.assertLess(self.sock2.fileno(), 0)
        self.sock1.close()
        await super().asyncTearDown()

    async def expect_data(self, expected: bytes) -> None:
        data = await self.loop.sock_recv(self.sock1, len(expected) + 1)
        self.assertEqual(data, expected)

    async def send_data(self, data: bytes) -> None:
        await self.loop.sock_sendall(self.sock1, data)

    async def test_simple(self) -> None:
        fut = asyncio.ensure_future(self.proxy.Method(argument="spam"))
        await self.expect_data(
            b'{"method":"com.example.demo.Method","parameters":{"argument":"spam"}}\0'
        )
        self.assertFalse(fut.done())
        await self.send_data(b'{"parameters":{"result":"egg"}}\0')
        self.assertEqual(await fut, {"result": "egg"})

    async def test_more(self) -> None:
        gen = self.proxy.MoreMethod()
        fut = asyncio.ensure_future(anext(gen))
        await self.expect_data(
            b'{"method":"com.example.demo.MoreMethod","more":true}\0'
        )
        self.assertFalse(fut.done())
        await self.send_data(
            b'{"continues":true,"parameters":{"result":"spam"}}\0'
        )
        self.assertEqual(await fut, {"result": "spam"})
        fut = asyncio.ensure_future(anext(gen))
        await asyncio.sleep(0)
        self.assertFalse(fut.done())
        await self.send_data(b'{"parameters":{"result":"egg"}}\0')
        self.assertEqual(await fut, {"result": "egg"})
        with self.assertRaises(StopAsyncIteration):
            await anext(gen)

    async def test_invalid_argument(self) -> None:
        fut = asyncio.ensure_future(self.proxy.Method(invalid_argument=True))
        await asyncio.sleep(0)
        self.assertTrue(fut.done())
        self.assertRaises(ConversionError, fut.result)

    async def test_permission_denied(self) -> None:
        fut = asyncio.ensure_future(self.proxy.Method(argument="spam"))
        await self.expect_data(
            b'{"method":"com.example.demo.Method","parameters":{"argument":"spam"}}\0'
        )
        self.assertFalse(fut.done())
        await self.send_data(
            b'{"error":"org.varlink.service.PermissionDenied"}\0'
        )
        try:
            result = await fut
        except VarlinkErrorReply as err:
            self.assertEqual(err.name, "org.varlink.service.PermissionDenied")
        else:
            self.fail(
                f"expected a VarlinkErrorReply exception, got {result!r}"
            )
