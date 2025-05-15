Usage
=====

Installation
------------

At present, the project is not on PyPi, but you can install it from git.

.. code-block:: console

   (.venv) $ pip install git+https://github.com/helmutg/asyncvarlink

Client
------

In order to use the high-level interface, a varlink interface definition needs a corresponding Python class.
For the ``org.varlink.service`` introspection interface, there is a ``VarlinkServiceInterface`` Python class available.
We can use it to introspect ``systemd-hostnamed`` for example.

.. code-block:: python

   import asyncio
   from asyncvarlink import VarlinkClientProtocol, connect_unix_varlink
   from asyncvarlink.serviceinterface import VarlinkServiceInterface

   async def main() -> None:
       transport, protocol = await connect_unix_varlink(
           VarlinkClientProtocol, "/run/systemd/io.systemd.Hostname"
       )
       interfaceproxy = protocol.make_proxy(VarlinkServiceInterface)
       print(await interfaceproxy.GetInfo())
       transport.close()

   asyncio.run(main())

Defining an interface
---------------------

When using ``asyncvarlink`` a varlink interface definition has to be manually translated to a Python class in order to use the interface with type automatic type conversion.
Definitions have to inherit from ``asyncvarlink.VarlinkInterface`` and they have to define their interface name (e.g. via a class inheritance argument ``name``).
Methods available to varlink have to be decorated with ``asyncvarlink.varlinkmethod`` and they must be precisely typed.
Let's go through the following example.

.. code-block:: python

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

A varlink enum may be represented in Python as an ``enum.Enum`` subclass or be typed as a ``typing.Literal``.
In this example, we use the former and define a ``Direction`` type.
When looking into methods, note that every call parameter and return parameter must have a name.
In case of the ``Reverse`` method, a single call parameter named ``value`` is accepted.
On the JSON level, the ``Direction`` type is represented as a string, but ``asyncvarlink`` takes care of converting it from and to the ``Direction`` instances.
The ``Reverse`` method returns a single value only and therefore its name may be given via the ``return_parameter`` keyword argument to the decorator.

The ``Range`` method demonstrates returning multiple values.
A caller of this method must supply the ``more`` keyword.
Each integer from the requested range is returned individually as a return parameter named ``value``.

The last example demonstrates that method definitions may be asynchronous and delay execution.
The special case of returning ``None`` indicates that there are no return parameters and an empty object is returned on the JSON level.

We may turn this Python class into a textual varlink interface definition suitable for consumption by other varlink implementations or the introspection interface.

.. code-block:: python

   print(DemoInterface.render_interface_description())

It will result in the following interface definition.

.. code-block:: text

   interface com.example.demo

   method Range(count: int) -> (value: object)
   method Reverse(value: (left, right)) -> (direction: (left, right))
   method Sleep(delay: float) -> ()

When using ``VarlinkInterface`` subclasses with a client, they are not instantiated and their methods are never called.
Therefore, methods may be stubbed using dots (``...``).
For use in a server, instances need to be supplied as their methods will be invoked upon client request.

Server
------

Let's build a simple server that provides the previously defined demonstration interface as well as the generic introspection interface on stdin/stdout.
The relevant protocol class does not consume individual interface instances, but an ``asyncvarlink.VarlinkInterfaceRegistry``.
It forms a cycle with the introspection interface as the latter wants to know about all available interfaces, but it also is a registered interface.

.. code-block:: python

   from asyncvarlink import VarlinkInterfaceRegistry
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

Once the registry is constructed, the communication channel may be established.
We attempt to look up an inherited file descriptor named ``varlink`` via systemd's socket activation protocol falling back to communication via stdin and stdout.
This is how `varlinkctl`_ communicates with executed servers.

.. code-block:: python

   import sys
   from asyncvarlink import VarlinkTransport, VarlinkInterfaceServerProtocol, get_listen_fd

   async def main():
       fut = asyncio.get_running_loop().create_future()
       protocol = VarlinkInterfaceServerProtocol(registry)
       protocol.connection_lost = fut.set_result
       listenfd = get_listen_fd("varlink")
       VarlinkTransport(
           asyncio.get_running_loop(),
           listenfd or sys.stdin.fileno(),
           listenfd or sys.stdout.fileno(),
           protocol,
       )
       await fut

   asyncio.run(main())

This allows us to interact with our toy service.

.. code-block:: text

   $ varlinkctl info ./toy_server.py
       Vendor: ExampleVendor
      Product: DemonstrationProduct
      Version: 1.0
          URL: https://github.com/helmutg/asyncvarlink
   Interfaces: com.example.demo
               org.varlink.service
   $

.. _varlinkctl: https://www.freedesktop.org/software/systemd/man/latest/varlinkctl.html
