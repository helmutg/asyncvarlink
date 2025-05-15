Structure
=========

On the ``asyncio`` side the main classes are ``VarlinkTransport`` and ``VarlinkProtocol``.
The use of a dedicated transport class is unusual and rooted in two aspects.
For one thing, varlink can communicate over pipes where sending and receiving happens on different file descriptors.
For another, this library implements file descriptor passing (over sockets) and most transport classes don't do that.
As a result, the ``VarlinkProtocol`` hierarchy only works with ``VarlinkTransport`` transport objects.
Where usual transports use ``write`` and usual protocols use ``data_received``, these classes use ``VarlinkTransport.send_message`` and ``VarlinkProtocol.message_received`` instead.
As a result transport methods ``pause_reading`` and ``resume_reading`` are called ``pause_receiving`` and ``resume_receiving``.
Other methods such as ``close`` and ``is_closing`` work as with a more common transport.
On the protocol side, ``connection_made`` and ``eof_received`` and ``connection_lost`` have the usual protocol semantics.
The ``VarlinkProtocol`` implements the lowest level of parsing and consumes and produces arbitrary JSON objects combined with file descriptors.

On top of the lowest level, there are ``VarlinkClientProtocol`` and ``VarlinkServerProtocol``.
These classes perform basic structural validation and turn the JSON objects into ``VarlinkMethodCall`` and ``VarlinkMethodReply`` objects or ``VarlinkErrorReply`` exceptions.
At this level, any file descriptors are simply passed through.
The call and reply parameters still are bare Python objects representing the JSON data with no validation.

To move to yet higher level, the ``VarlinkInterface`` base class can be used for representing what varlink calls an `interface`_.
While the approach taken by many other implementations is consuming a ``.varlink`` interface description file, this library goes the other way round and one describes an interface as Python code with type hints.
An interface definition in the specified syntax can then be derived (e.g. for use with introspection) automatically.
To craft your own interface, inherit from ``VarlinkInterface`` and define an interface name.
Then use the ``@varlinkmethod`` decorator for all varlink exposed methods, which must be fully type annotated.
This decorator derives a varlink-specific type representation from the Python type annotations.
If an interface is only meant for use with clients, there is no need for implementing its methods.

Instances of ``VarlinkInterface`` subclasses may be collected in a ``VarlinkInterfaceRegistry`` and then passed to a ``VarlinkInterfaceServerProtocol`` to run an IPC server with the given instances.
On the client side, a ``VarlinkInterface`` subclass (not instance) can be glued to a ``VarlinkClientProtocol`` instance using a ``VarlinkInterfaceProxy``.
It allows accessing varlink methods as if they were regular, asynchronous Python methods.

The passing of file descriptors is a bit special.
There are two main ways in which file descriptors are conveyed.
One is as a ``list[int]``.
In those cases, they are typically passed as function arguments and the called function is supposed to observe, but not close them.
The file descriptors are supposed to remain valid until the called function returns.
The other way uses the ``FileDescriptorArray`` class.
When this class is in use, the object actually owns the file descriptors and is responsible for closing them eventually.
Typically, the life time of such an array is fairly limited, but it can be extended by passing an ``asyncio.Future`` to its ``reference_until_done`` method to defer closing.
Additionally, individual file descriptors may be removed from the array using the ``take`` method.
When doing so, responsibility for closing a taken file descriptor is transferred to the caller.

The type conversion between JSON and Python objects is mostly straight forward.
Basic types such as ``bool``, ``int``, ``float`` and ``str`` map trivially.
The ``list`` type must be used homogeneously and is traversed while ``dict`` may be used homogeneously or inhomogeneous (as a ``typing.TypedDict``) with ``str`` keys in all cases.
Values can be made optional in most cases and Python ``set`` is represented as a mapping from strings to empty objects.
Subclasses of ``enum.Enum`` are turned into strings and the introspection reports them as enums.
File descriptors tagged with the ``FileDescriptor`` wrapper are represented as integer indices into a separately passed array of file descriptors on the transport layer.

For details of any of the mentioned classes, please refer to the docstrings e.g. by using ``pydoc``.

.. _interface: https://varlink.org/Interface-Definition
