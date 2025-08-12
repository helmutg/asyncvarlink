# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

"""asyncio varlink server protocol implementation"""

import asyncio
import functools
import logging
import os
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
from .protocol import VarlinkBaseProtocol, VarlinkProtocol
from .serviceerrors import (
    ExpectedMore,
    InterfaceNotFound,
    InvalidParameter,
    MethodNotFound,
)
from .types import FileDescriptorArray, JSONObject


_logger = logging.getLogger("asyncvarlink.serverprotocol")


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

    def protocol_factory(self) -> VarlinkBaseProtocol:
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
        try:
            json = reply.tojson()
        except ConversionError as err:
            json = InvalidParameter(parameter=err.location[0]).tojson()
            if fds and autoclose:
                for fd in fds:
                    os.close(fd)
            fds = []
        return self.send_message(json, fds, autoclose)

    def _on_receiver_completes(
        self,
        backpressure_fut: asyncio.Future[None],
        oneway: bool,
        call_fut: asyncio.Future[None],
    ) -> None:
        exc = call_fut.exception()
        if exc is not None:
            if isinstance(exc, VarlinkErrorReply):
                if not oneway:
                    self.send_reply(exc)
            elif isinstance(exc, (SystemExit, KeyboardInterrupt)):
                raise exc
            else:
                try:
                    self.handle_call_exception(exc, oneway)
                except VarlinkErrorReply as err:
                    if not oneway:
                        self.send_reply(err)
        backpressure_fut.set_result(None)

    def handle_call_exception(self, exc: BaseException, oneway: bool) -> None:
        """React to call_received emitting an exception not inheriting from
        VarlinkErrorReply. The caught exception is provided. The default
        implementation logs the exception and sends an error reply unless
        oneway is True. The function may raise a VarlinkErrorReply and have it
        sent when needed.
        """
        _logger.exception(
            "unhandled exception from call_received future", exc_info=exc
        )
        if not oneway:
            self.send_reply(
                GenericVarlinkErrorReply(
                    "invalid.asyncvarlink.InternalServerError"
                )
            )

    def request_received(
        self, obj: JSONObject, fds: FileDescriptorArray | None
    ) -> asyncio.Future[None] | None:
        try:
            try:
                call = VarlinkMethodCall.fromjson(obj)
            except (TypeError, ValueError):
                raise GenericVarlinkErrorReply(
                    "invalid.asyncvarlink.ProtocolViolation"
                ) from None
            fut = self.call_received(call, fds)
            if fut is None:
                return None
            bpfut = asyncio.get_running_loop().create_future()
            fut.add_done_callback(
                functools.partial(
                    self._on_receiver_completes, bpfut, call.oneway
                ),
            )
            return bpfut
        except VarlinkErrorReply as err:
            if not obj.get("oneway", False):
                self.send_reply(err)
            return None
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException as exc:
            oneway = bool(obj.get("oneway", False))
            try:
                self.handle_call_exception(exc, oneway)
            except VarlinkErrorReply as err:
                if not oneway:
                    self.send_reply(err)
            return None

    def call_received(
        self, call: VarlinkMethodCall, fds: FileDescriptorArray | None
    ) -> asyncio.Future[None] | None:
        """Handle a received varlink parsed as a call object and associated
        file descriptors. The descriptors are valid until the function returns.
        Their life time can be extended by adding a referee before returning.
        If the function raises an exception or the returned future (if any)
        completes with an exception a reply will be generated. Otherwise, the
        function is expected to call send_reply as needed before returning None
        or completing the returned future.
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
                fut = asyncio.ensure_future(
                    self._call_sync_method_more(method, signature, pyparams)
                )
            else:
                self._call_sync_method_single(
                    method, signature, pyparams, call.oneway
                )
                return None
        elif signature.more:
            fut = asyncio.ensure_future(
                self._call_async_method_more(method, signature, pyparams)
            )
        else:
            fut = asyncio.ensure_future(
                self._call_async_method_single(
                    method, signature, pyparams, call.oneway
                ),
            )
        if fds is not None:
            fds.reference_until_done(fut)
        return fut

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
        try:
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
        except VarlinkErrorReply as err:
            self.send_reply(err)

    async def _call_async_method_single(
        self,
        method: typing.Callable[..., typing.Any],
        signature: VarlinkMethodSignature,
        pyparams: dict[str, typing.Any],
        oneway: bool,
    ) -> None:
        try:
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
                VarlinkMethodReply(jsonparams, extensions=result.extensions),
                fds,
            )
        except VarlinkErrorReply as err:
            if not oneway:
                self.send_reply(err)

    async def _call_async_method_more(
        self,
        method: typing.Callable[..., typing.Any],
        signature: VarlinkMethodSignature,
        pyparams: dict[str, typing.Any],
    ) -> None:
        try:
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
        except VarlinkErrorReply as err:
            self.send_reply(err)
