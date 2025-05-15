asyncvarlink
============

This is a pure Python implementation of the [varlink](https://varlink.org) IPC
protocol based on `asyncio`. The main differences to he [reference
implementation](https://github.com/varlink/python) are:

 * Usage of `asyncio` instead of synchronous threading
 * Where the reference implementation parses a varlink interface description
   as a source of truth, this implementation derives a varlink interface
   description from a typed Python class to describe an interface.
 * Even though the [varlink faq](https://varlink.org/FAQ) explicitly renders
   passing file descriptors out of scope, `systemd` uses it and it is an
   important feature also implemented here.

Client usage
============

The `VarlinkServiceInterface` class represents the `org.varlink.service`
introspection interface. We can use it to introspect `systemd-hostnamed`.

    import asyncio
    from asyncvarlink import VarlinkClientProtocol, connect_unix_varlink
    from asyncvarlink.serviceinterface import VarlinkServiceInterface

    async def main() -> None:
        t, p = await connect_unix_varlink(
            VarlinkClientProtocol, "/run/systemd/io.systemd.Hostname"
        )
        i = p.make_proxy(VarlinkServiceInterface)
        print(await i.GetInfo())
        t.close()

    asyncio.run(main())

Server usage
============

Here is an example for defining an interface. If it is to be used by a server,
the methods need to be implemented of course, but on the client side, typed
stubs will do.

    import asyncio
    import enum
    import typing
    from asyncvarlink import VarlinkInterface, varlinkmethod

    class Direction(enum.Enum):
        left = "left"
        right = "right"

    class DemoInterface(VarlinkInterface, name="com.example.demo"):
        @varlinkmethod(return_parameter="direction")
        def Reverse(self, *, value: Direction) -> Direction:
            return Direction.left if value == Direction.right else Direction.right

        @varlinkmethod(return_parameter="value")
        def Range(self, *, count: int) -> typing.Iterable[int]:
            return range(count)

        @varlinkmethod
        async def Sleep(self, *, delay: float) -> None:
            await asyncio.sleep(delay)

Setting up a service is now a matter of plugging things together.

    import asyncio
    import sys
    from asyncvarlink import VarlinkInterfaceRegistry, VarlinkTransport
    from asyncvarlink.serviceinterface import VarlinkServiceInterface

    registry = VarlinkInterfaceRegistry()
    registry.register_interface(
        VarlinkServiceInterface(
            "ExampleVendor",
            "DemonstrationProduct",
            "1.0",
            "https://github.com/helmutg/asyncvarlink",
            registry,
        ),
    )
    registry.register_interface(DemoInterface())
    VarlinkTransport(
        asyncio.get_event_loop(),
        sys.stdin.fileno(),
        sys.stdout.fileno(),
        registry.protocol_factory(),
    )

When communicating via stdio, `varlinkctl` from `systemd` may be used to
interact with a service. Registering a `VarlinkServiceInterface` implementing
the `org.varlink.service` introspection interface is optional.

Collaboration
=============

The primary means of collaborating on this project is
[github](https://github.com/helmutg/asyncvarlink). If you prefer not to use a
centralized forge, sending inquiries and patches to
[Helmut](mailto:helmut@subdivi.de?Subject=asyncvarlink) is also welcome.

License
=======

GPL-2+
