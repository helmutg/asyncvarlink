# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""Python classes for the org.varlink.service interface."""

import typing

from .error import TypedVarlinkErrorReply
from .interface import VarlinkInterface, varlinkmethod


class VarlinkServiceInterface(VarlinkInterface):
    """Implementation of the basic varlink introspection interface."""

    name = "org.varlink.service"

    class _GetInfoResult(typing.TypedDict):
        vendor: str
        product: str
        version: str
        url: str
        interfaces: list[str]

    def __init__(self, vendor: str, product: str, version: str, url: str):
        """Construct an introspection interface object from the given
        metadata.
        """
        self._info: VarlinkServiceInterface._GetInfoResult = {
            "vendor": vendor,
            "product": product,
            "version": version,
            "url": url,
            "interfaces": [],
        }
        self._interfaces: dict[str, VarlinkInterface] = {}

    def register(self, interface: VarlinkInterface) -> None:
        """Register a VarlinkInterface instance with the introspection
        interface such that its GetInfo method will list the given
        interface and GetInterfaceDescription will provide a rendered
        description.
        """
        if interface.name in self._interfaces:
            raise ValueError(
                f"an interface named {interface.name} is already registered"
            )
        self._interfaces[interface.name] = interface

    @varlinkmethod
    def GetInfo(self) -> _GetInfoResult:
        """Refer to https://varlink.org/Service."""
        return self._info | {"interfaces": sorted(self._interfaces.keys())}

    @varlinkmethod(return_parameter="description")
    def GetInterfaceDescription(self, *, interface: str) -> str:
        """Refer to https://varlink.org/Service."""
        try:
            iface = self._interfaces[interface]
        except KeyError:
            raise InterfaceNotFound(interface=interface) from None
        return iface.render_interface_description()


class InterfaceNotFound(
    TypedVarlinkErrorReply, interface="org.varlink.service"
):
    """A method was called for an interface that is not provided or a
    method was called with an interface parameter and said interface is not
    provided.
    """

    class Parameters:
        interface: str


class MethodNotFound(TypedVarlinkErrorReply, interface="org.varlink.service"):
    """A method was called that is not provided on the named interface."""

    class Parameters:
        method: str


class InvalidParameter(
    TypedVarlinkErrorReply, interface="org.varlink.service"
):
    """A call parameter could not be validated to the expected type."""

    class Parameters:
        parameter: str


class ExpectedMore(TypedVarlinkErrorReply, interface="org.varlink.service"):
    """A method was expecting to be called with "more": true, but was called
    without.
    """

    class Parameters:
        pass
