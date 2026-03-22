# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

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
    MethodNotImplemented,
    PermissionDenied,
)


__all__ = [
    "ExpectedMore",
    "InterfaceNotFound",
    "InvalidParameter",
    "MethodNotFound",
    "MethodNotImplemented",
    "PermissionDenied",
    "VarlinkServiceInterface",
]


class VarlinkServiceInterface(VarlinkInterface, name="org.varlink.service"):
    """The Varlink Service Interface is provided by every varlink service. It
    describes the service and the interfaces it implements.
    """

    errors = (
        InterfaceNotFound,
        MethodNotFound,
        MethodNotImplemented,
        InvalidParameter,
        PermissionDenied,
        ExpectedMore,
    )

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
        *,
        comments: bool = True,
    ):
        """Construct an introspection interface object from the given
        metadata and a VarlinkInterfaceRegistry for introspection. If
        comments is enabled (default), __doc__ strings will be included in
        interface descriptions as comments.
        """
        self._info: VarlinkServiceInterface._GetInfoResult = {
            "vendor": vendor,
            "product": product,
            "version": version,
            "url": url,
            "interfaces": [],
        }
        self._registry = registry
        self._comments = comments

    @varlinkmethod
    def GetInfo(self) -> _GetInfoResult:
        """Get a list of all the interfaces a service provides and information
        about the implementation.
        """
        return self._info | {
            "interfaces": sorted(iface.name for iface in self._registry)
        }

    @varlinkmethod(return_parameter="description")
    def GetInterfaceDescription(self, *, interface: str) -> str:
        """Get the description of an interface that is implemented by this
        service.
        """
        try:
            iface = self._registry[interface]
        except KeyError:
            raise InterfaceNotFound(interface=interface) from None
        return iface.render_interface_description(comments=self._comments)
