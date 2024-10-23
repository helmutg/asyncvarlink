# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""Python classes for the org.varlink.service interface."""

import typing

from .error import TypedVarlinkErrorReply
from .message import VarlinkMethodCall
from .interface import (
    VarlinkInterface,
    VarlinkMethodSignature,
    varlinkmethod,
    varlinksignature,
)
from .serviceerrors import *


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


class VarlinkServiceInterface(VarlinkInterface, name="org.varlink.service"):
    """Implementation of the basic varlink introspection interface."""

    class _GetInfoResult(typing.TypedDict):
        vendor: str
        product: str
        version: str
        url: str
        interfaces: list[str]

    def __init__(
        self,
        vendor: str,
        product: str,
        version: str,
        url: str,
        registry: VarlinkInterfaceRegistry,
    ):
        """Construct an introspection interface object from the given
        metadata and a VarlinkInterfaceRegistry for introspection.
        """
        self._info: VarlinkServiceInterface._GetInfoResult = {
            "vendor": vendor,
            "product": product,
            "version": version,
            "url": url,
            "interfaces": [],
        }
        self._registry = registry

    @varlinkmethod
    def GetInfo(self) -> _GetInfoResult:
        """Refer to https://varlink.org/Service."""
        return self._info | {
            "interfaces": sorted(iface.name for iface in self._registry)
        }

    @varlinkmethod(return_parameter="description")
    def GetInterfaceDescription(self, *, interface: str) -> str:
        """Refer to https://varlink.org/Service."""
        try:
            iface = self._registry[interface]
        except KeyError:
            raise InterfaceNotFound(interface=interface) from None
        return iface.render_interface_description()
