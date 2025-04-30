# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""Python classes for the org.varlink.service interface."""

import typing

from .interface import VarlinkInterface, varlinkmethod
from .serverprotocol import VarlinkInterfaceRegistry

# The serviceerrors module is split off to avoid circular imports, users should
# import its exceptions from this module.
from .serviceerrors import (
    ExpectedMore,
    InterfaceNotFound,
    InvalidParameter,
    MethodNotFound,
)


__all__ = [
    "ExpectedMore",
    "InterfaceNotFound",
    "InvalidParameter",
    "MethodNotFound",
    "VarlinkServiceInterface",
]


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
