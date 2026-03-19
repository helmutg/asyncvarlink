#!/usr/bin/python3
# Copyright 2026 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import argparse
import asyncio
import dataclasses
import math
import secrets
import socket
import sys
import typing
import unittest

if __file__.split("/")[-2:-1] == ["tests"]:
    sys.path.insert(0, "/".join(__file__.split("/")[:-2]))

from asyncvarlink import (
    connect_unix_varlink,
    create_unix_server,
    TypedVarlinkErrorReply,
    VarlinkClientProtocol,
    VarlinkInterface,
    VarlinkInterfaceRegistry,
    varlinkmethod,
    VarlinkMethodCall,
    VarlinkTransport,
)
from asyncvarlink.serviceinterface import VarlinkServiceInterface


class ResultTest05(typing.TypedDict):
    bool: bool
    int: int
    float: float
    string: str


class ResultTest06(typing.TypedDict):
    struct: ResultTest05


class Anon(typing.TypedDict):
    foo: bool
    bar: bool


@dataclasses.dataclass
class Interface:
    foo: list[dict[str, typing.Literal["foo", "bar", "baz"]] | None] | None
    anon: Anon


class FirstSecondStruct(typing.TypedDict):
    first: int
    second: str


@dataclasses.dataclass
class MyType:
    object: object
    enum: typing.Literal["one", "two", "three"]
    struct: FirstSecondStruct
    array: list[str]
    dictionary: dict[str, str]
    stringset: set[str]
    nullable: str | None
    nullable_array_struct: list[FirstSecondStruct] | None
    interface: Interface


class ClientIdError(
    TypedVarlinkErrorReply, interface="org.varlink.certification"
):
    class Parameters:
        pass


class CertificationError(
    TypedVarlinkErrorReply, interface="org.varlink.certification"
):
    class Parameters:
        wants: str
        got: str


class CertificationInterface(
    VarlinkInterface, name="org.varlink.certification"
):
    errors = (ClientIdError, CertificationError)

    def __init__(self) -> None:
        self.next_method: dict[str, str] = {}

    def check_equal(self, wants: typing.Any, got: typing.Any) -> None:
        if wants != got:
            if isinstance(wants, str) and isinstance(got, str):
                raise CertificationError(wants=wants, got=got)
            raise CertificationError(wants=repr(wants), got=repr(got))

    def check_method(
        self, client_id: str, cur_meth: str, next_meth: str
    ) -> None:
        try:
            exp_meth = self.next_method[client_id]
        except KeyError:
            raise CertificationError(
                wants="org.varlink.certification.Start",
                got="org.varlink.certification." + cur_meth,
            ) from None
        self.check_equal(
            "org.varlink.certification." + exp_meth,
            "org.varlink.certification." + cur_meth,
        )
        self.next_method[client_id] = next_meth

    @varlinkmethod(return_parameter="client_id")
    def Start(self) -> str:
        client_id = secrets.token_hex()
        self.next_method[client_id] = "Test01"
        return client_id

    @varlinkmethod(return_parameter="bool")
    def Test01(self, client_id: str) -> bool:
        self.check_method(client_id, "Test01", "Test02")
        return True

    @varlinkmethod(return_parameter="int")
    def Test02(self, client_id: str, bool: bool) -> int:
        self.check_method(client_id, "Test02", "Test03")
        self.check_equal(True, bool)
        return 1

    @varlinkmethod(return_parameter="float")
    def Test03(self, client_id: str, int: int) -> float:
        self.check_method(client_id, "Test03", "Test04")
        self.check_equal(1, int)
        return 1.0

    @varlinkmethod(return_parameter="string")
    def Test04(self, client_id: str, float: float) -> str:
        self.check_method(client_id, "Test04", "Test05")
        self.check_equal(1.0, float)
        return "ping"

    @varlinkmethod
    def Test05(self, client_id: str, string: str) -> ResultTest05:
        self.check_method(client_id, "Test05", "Test06")
        self.check_equal("ping", string)
        return {
            "bool": False,
            "int": 2,
            "float": math.pi,
            "string": "a lot of string",
        }

    @varlinkmethod
    def Test06(
        self, client_id: str, bool: bool, int: int, float: float, string: str
    ) -> ResultTest06:
        self.check_method(client_id, "Test06", "Test07")
        self.check_equal(False, bool)
        self.check_equal(2, int)
        self.check_equal(math.pi, float)
        self.check_equal("a lot of string", string)
        return {
            "struct": {
                "bool": False,
                "int": 2,
                "float": math.pi,
                "string": "a lot of string",
            },
        }

    @varlinkmethod(return_parameter="map")
    def Test07(self, client_id: str, struct: ResultTest05) -> dict[str, str]:
        self.check_method(client_id, "Test07", "Test08")
        self.check_equal(
            {
                "bool": False,
                "int": 2,
                "float": math.pi,
                "string": "a lot of string",
            },
            struct,
        )
        return {"foo": "Foo", "bar": "Bar"}

    @varlinkmethod(return_parameter="set")
    def Test08(self, client_id: str, map: dict[str, str]) -> set[str]:
        self.check_method(client_id, "Test08", "Test09")
        self.check_equal({"foo": "Foo", "bar": "Bar"}, map)
        return {"one", "two", "three"}

    @varlinkmethod(return_parameter="mytype")
    def Test09(self, client_id: str, set: set[str]) -> MyType:
        self.check_method(client_id, "Test09", "Test10")
        self.check_equal({"one", "two", "three"}, set)
        return MyType(
            object={
                "method": "org.varlink.certification.Test09",
                "parameters": {"map": {"foo": "Foo", "bar": "Bar"}},
            },
            enum="two",
            struct={"first": 1, "second": "2"},
            array=["one", "two", "three"],
            dictionary={"foo": "Foo", "bar": "Bar"},
            stringset={"one", "two", "three"},
            nullable=None,
            nullable_array_struct=None,
            interface=Interface(
                foo=[
                    None,
                    {"foo": "foo", "bar": "bar"},
                    None,
                    {"one": "foo", "two": "bar"},
                ],
                anon={"foo": True, "bar": False},
            ),
        )

    @varlinkmethod(return_parameter="string")
    def Test10(self, client_id: str, mytype: MyType) -> typing.Iterator[str]:
        self.check_equal(
            {
                "method": "org.varlink.certification.Test09",
                "parameters": {"map": {"foo": "Foo", "bar": "Bar"}},
            },
            mytype.object,
        )
        self.check_equal("two", mytype.enum)
        self.check_equal({"first": 1, "second": "2"}, mytype.struct)
        self.check_equal(["one", "two", "three"], mytype.array)
        self.check_equal({"foo": "Foo", "bar": "Bar"}, mytype.dictionary)
        self.check_equal({"one", "two", "three"}, mytype.stringset)
        self.check_equal(None, mytype.nullable)
        self.check_equal(None, mytype.nullable_array_struct)
        self.check_equal(
            Interface(
                foo=[
                    None,
                    {"foo": "foo", "bar": "bar"},
                    None,
                    {"one": "foo", "two": "bar"},
                ],
                anon={"foo": True, "bar": False},
            ),
            mytype.interface,
        )
        self.check_method(client_id, "Test10", "Test11")
        for i in range(1, 11):
            yield f"Reply number {i}"

    @varlinkmethod
    def Test11(self, client_id: str, last_more_replies: list[str]) -> None:
        '''must be called as "oneway"'''
        self.check_method(client_id, "Test11", "End")
        self.check_equal(
            [f"Reply number {i}" for i in range(1, 11)], last_more_replies
        )

    @varlinkmethod(return_parameter="all_ok")
    def End(self, client_id: str) -> bool:
        self.check_method(client_id, "End", "Start")
        del self.next_method[client_id]
        return True


def make_registry() -> VarlinkInterfaceRegistry:
    registry = VarlinkInterfaceRegistry()
    registry.register_interface(
        VarlinkServiceInterface(
            "asyncvarlink",
            "certification_server",
            "0",
            "https://github.com/helmutg/asyncvarlink",
            registry,
        ),
    )
    registry.register_interface(CertificationInterface())
    return registry


async def run_test(protocol: VarlinkClientProtocol) -> None:
    proxy = protocol.make_proxy(CertificationInterface)
    res = await proxy.Start()
    cid = res["client_id"]
    res = await proxy.Test01(client_id=cid)
    res = await proxy.Test02(client_id=cid, **res)
    res = await proxy.Test03(client_id=cid, **res)
    res = await proxy.Test04(client_id=cid, **res)
    res = await proxy.Test05(client_id=cid, **res)
    res = await proxy.Test06(client_id=cid, **res)
    res = await proxy.Test07(client_id=cid, **res)
    res = await proxy.Test08(client_id=cid, **res)
    res = await proxy.Test09(client_id=cid, **res)
    array = []
    async for res in proxy.Test10(client_id=cid, **res):
        array.append(res["string"])
    await proxy.Test11.oneway(client_id=cid, last_more_replies=array)
    if not await proxy.End(client_id=cid):
        raise RuntimeError("certification failed")


async def selftest() -> None:
    loop = asyncio.get_running_loop()
    sock1, sock2 = socket.socketpair()
    sp = make_registry().protocol_factory()
    st = VarlinkTransport(loop, sock1, sock1, sp)
    cp = VarlinkClientProtocol()
    ct = VarlinkTransport(loop, sock2, sock2, cp)
    await asyncio.sleep(0)
    await run_test(cp)
    ct.close()
    st.close()


class CertificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_self(self) -> None:
        await selftest()


async def client_main(sock: str) -> None:
    _, p = await connect_unix_varlink(VarlinkClientProtocol, sock)
    assert isinstance(p, VarlinkClientProtocol)
    await run_test(p)
    print("Certification passed")


async def server_main(sock: str) -> None:
    await (
        await create_unix_server(
            make_registry().protocol_factory,
            sock,
            loop=asyncio.get_running_loop(),
        )
    ).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--socket", action="store")
    args = parser.parse_args()
    if not args.socket:
        asyncio.run(selftest())
        print("Certification passed")
    elif args.client:
        asyncio.run(client_main(args.socket))
    else:
        asyncio.run(server_main(args.socket))


if __name__ == "__main__":
    main()
