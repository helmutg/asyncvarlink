# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""asyncio varlink server protocol implementation"""

import asyncio
import typing

from .conversion import ConversionError, FileDescriptorVarlinkType
from .error import VarlinkErrorReply, GenericVarlinkErrorReply
from .interface import (
    AnnotatedResult,
    VarlinkInterface,
    VarlinkMethodSignature,
    varlinksignature,
)
from .message import VarlinkMethodCall, VarlinkMethodReply
from .protocol import VarlinkProtocol
from .serviceerrors import (
    ExpectedMore,
    InterfaceNotFound,
    InvalidParameter,
    MethodNotFound,
)
from .types import FileDescriptorArray, JSONObject


class VarlinkInterfaceRegistry:
    """Collection of VarlinkInterface instances."""

    def __init__(self) -> None:
        self.interfaces: dict[str, VarlinkInterface] = {}

    def register_interface(self, interface: VarlinkInterface) -> None:
        """Register an interface instance. Its name must be unique to the
        registry.
        """
        if interface.name in self.interfaces:
            raise ValueError(
                f"an interface named {interface.name} is already registered"
            )
        self.interfaces[interface.name] = interface

    def lookup_method(
        self, call: VarlinkMethodCall
    ) -> tuple[typing.Callable[..., typing.Any], VarlinkMethodSignature]:
        """Look up a method. Return the Python callable responsible for the
        method referenced by the call and its VarlinkMethodSignature used
        for introspection and type conversion. This raises a number of
        subclasses of VarlinkErrorReply.
        """
        try:
            interface = self.interfaces[call.method_interface]
        except KeyError:
            raise InterfaceNotFound(interface=call.method_interface) from None
        try:
            method = getattr(interface, call.method_name)
        except AttributeError:
            raise MethodNotFound(method=call.method_name) from None
        if (signature := varlinksignature(method)) is None:
            # Reject any method that has not been marked with varlinkmethod.
            raise MethodNotFound(method=call.method_name)
        if signature.more and not call.more:
            raise ExpectedMore()
        return (method, signature)

    def __iter__(self) -> typing.Iterator[VarlinkInterface]:
        """Iterate over the registered VarlinkInterface instances."""
        return iter(self.interfaces.values())

    def __getitem__(self, interface: str) -> VarlinkInterface:
        """Look up a VarlinkInterface by its name. Raises KeyError."""
        return self.interfaces[interface]

    def protocol_factory(self) -> VarlinkProtocol:
        """Factory method for generating protocol instances.
        Example:

            create_unix_server(registry.protocol_factory, ...)
        """
        return VarlinkInterfaceServerProtocol(self)


class VarlinkServerProtocol(VarlinkProtocol):
    """Protocol class for a varlink service. It receives calls as
    VarlinkMethodCall objects and issues replies as VarlinkMethodReply or
    VarlinkErrorReply objects. A derived class should implement call_received.
    """

    def send_reply(
        self,
        reply: VarlinkMethodReply | VarlinkErrorReply,
        fds: list[int] | None = None,
        autoclose: bool = True,
    ) -> asyncio.Future[None]:
        """Enqueue the given reply and file descriptors for sending. For the
        semantics regarding fds, please refer to the documentation of
        send_message.
        """
        return self.send_message(reply.tojson(), fds, autoclose)

    def request_received(
        self, obj: JSONObject, fds: FileDescriptorArray | None
    ) -> asyncio.Future[None] | None:
        try:
            try:
                call = VarlinkMethodCall.fromjson(obj)
            except (TypeError, ValueError):
                raise GenericVarlinkErrorReply("ProtocolViolation") from None
            return self.call_received(call, fds)
        except VarlinkErrorReply as err:
            if not obj.get("oneway", False):
                self.send_reply(err)
            return None

    def call_received(
        self, call: VarlinkMethodCall, fds: FileDescriptorArray | None
    ) -> asyncio.Future[None] | None:
        """Handle a received varlink parsed as a call object and associated
        file descriptors. The descriptors are valid until the function returns.
        Their life time can be extended by adding a referee before returning.
        The function should call the send_reply method as needed or raise a
        VarlinkErrorReply to be sent by the caller.
        """
        raise NotImplementedError


class VarlinkInterfaceServerProtocol(VarlinkServerProtocol):
    """Serve the interfaces registered with a registry via varlink."""

    def __init__(self, registry: VarlinkInterfaceRegistry) -> None:
        """Defer method lookup to the given registry."""
        super().__init__()
        self._registry = registry

    def call_received(
        self, call: VarlinkMethodCall, fds: FileDescriptorArray | None
    ) -> asyncio.Future[None] | None:
        method, signature = self._registry.lookup_method(call)
        try:
            pyparams = signature.parameter_type.fromjson(
                call.parameters, {FileDescriptorVarlinkType: fds}
            )
        except ConversionError as err:
            raise InvalidParameter(parameter=err.location[0]) from err
        if not signature.asynchronous:
            if signature.more:
                return asyncio.ensure_future(
                    self._call_sync_method_more(method, signature, pyparams)
                )
            self._call_sync_method_single(
                method, signature, pyparams, call.oneway
            )
            return None
        if signature.more:
            return asyncio.ensure_future(
                self._call_async_method_more(method, signature, pyparams)
            )
        return asyncio.ensure_future(
            self._call_async_method_single(
                method, signature, pyparams, call.oneway
            ),
        )

    def _call_sync_method_single(
        self,
        method: typing.Callable[..., typing.Any],
        signature: VarlinkMethodSignature,
        pyparams: dict[str, typing.Any],
        oneway: bool,
    ) -> asyncio.Future[None] | None:
        result = method(**pyparams)
        assert isinstance(result, AnnotatedResult)
        assert not result.continues
        if oneway:
            return None
        fds: list[int] = []  # modified by tojson
        jsonparams = signature.return_type.tojson(
            result.parameters, {FileDescriptorVarlinkType: fds}
        )
        return self.send_reply(
            VarlinkMethodReply(jsonparams, extensions=result.extensions), fds
        )

    async def _call_sync_method_more(
        self,
        method: typing.Callable[..., typing.Any],
        signature: VarlinkMethodSignature,
        pyparams: dict[str, typing.Any],
    ) -> None:
        continues = True
        for result in method(**pyparams):
            assert continues
            assert isinstance(result, AnnotatedResult)
            fds: list[int] = []  # modified by tojson
            jsonparams = signature.return_type.tojson(
                result.parameters, {FileDescriptorVarlinkType: fds}
            )
            await self.send_reply(
                VarlinkMethodReply(
                    jsonparams,
                    continues=result.continues,
                    extensions=result.extensions,
                ),
                fds,
            )
            continues = result.continues
        assert not continues

    async def _call_async_method_single(
        self,
        method: typing.Callable[..., typing.Any],
        signature: VarlinkMethodSignature,
        pyparams: dict[str, typing.Any],
        oneway: bool,
    ) -> None:
        result = await method(**pyparams)
        assert isinstance(result, AnnotatedResult)
        assert not result.continues
        if oneway:
            return
        fds: list[int] = []  # modified by tojson
        jsonparams = signature.return_type.tojson(
            result.parameters, {FileDescriptorVarlinkType: fds}
        )
        await self.send_reply(
            VarlinkMethodReply(jsonparams, extensions=result.extensions), fds
        )

    async def _call_async_method_more(
        self,
        method: typing.Callable[..., typing.Any],
        signature: VarlinkMethodSignature,
        pyparams: dict[str, typing.Any],
    ) -> None:
        continues = True
        async for result in method(**pyparams):
            assert continues
            assert isinstance(result, AnnotatedResult)
            fds: list[int] = []  # modified by tojson
            jsonparams = signature.return_type.tojson(
                result.parameters, {FileDescriptorVarlinkType: fds}
            )
            await self.send_reply(
                VarlinkMethodReply(
                    jsonparams,
                    continues=result.continues,
                    extensions=result.extensions,
                ),
                fds,
            )
            continues = result.continues
        assert not continues
