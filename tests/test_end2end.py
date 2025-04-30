# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

import contextlib
import functools
import tempfile
import unittest

from asyncvarlink import (
    VarlinkClientProtocol,
    VarlinkInterface,
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
        async with contextlib.AsyncExitStack() as stack:
            tdir = stack.enter_context(tempfile.TemporaryDirectory())
            sockpath = tdir + "/sock"
            server = stack.enter_async_context(
                await create_unix_server(registry.protocol_factory, sockpath)
            )
            stack.callback(server.close)
            transport, protocol = await connect_unix_varlink(
                VarlinkClientProtocol, sockpath
            )
            stack.callback(transport.close)
            assert isinstance(protocol, VarlinkClientProtocol)
            proxy = protocol.make_proxy(DummyInterface)
            self.assertEqual(
                await proxy.Method(argument="argument"),
                {"result": "returnvalue"},
            )
            self.assertEqual(interface.argument, "argument")
