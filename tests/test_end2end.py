# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

import asyncio
import contextlib
import functools
import tempfile
import unittest

from asyncvarlink import (
    VarlinkClientProtocol,
    VarlinkInterface,
    VarlinkInterfaceProxy,
    VarlinkInterfaceRegistry,
    VarlinkInterfaceServerProtocol,
    connect_unix_varlink,
    create_unix_server,
    varlinkmethod,
)


class DummyInterface(VarlinkInterface, name="com.example.Dummy"):
    def __init__(self) -> None:
        self.argument = "unset"

    @varlinkmethod(return_parameter="result")
    def Method(self, argument: str) -> str:
        self.argument = argument
        return "returnvalue"


class End2EndTests(unittest.IsolatedAsyncioTestCase):
    async def test_end2end(self) -> None:
        registry = VarlinkInterfaceRegistry()
        interface = DummyInterface()
        registry.register_interface(interface)
        with tempfile.TemporaryDirectory() as tdir:
            sockpath = tdir + "/sock"
            async with await create_unix_server(
                functools.partial(VarlinkInterfaceServerProtocol, registry),
                sockpath,
            ) as server:
                with contextlib.closing(server):
                    transport, protocol = await connect_unix_varlink(
                        VarlinkClientProtocol, sockpath
                    )
                    assert isinstance(protocol, VarlinkClientProtocol)
                    with contextlib.closing(transport):
                        proxy = VarlinkInterfaceProxy(protocol, DummyInterface)
                        self.assertEqual(
                            await proxy.Method(argument="argument"),
                            {"result": "returnvalue"},
                        )
                        self.assertEqual(interface.argument, "argument")
