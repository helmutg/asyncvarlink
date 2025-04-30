# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""Python classes for errors of the org.varlink.service interface."""

from .error import TypedVarlinkErrorReply


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


class MethodNotImplemented(
    TypedVarlinkErrorReply, interface="org.varlink.service"
):
    """The interface defines the requested method, but the service does not
    implement it.
    """

    class Parameters:
        method: str


class InvalidParameter(
    TypedVarlinkErrorReply, interface="org.varlink.service"
):
    """A call parameter could not be validated to the expected type."""

    class Parameters:
        parameter: str


class PermissionDenied(
    TypedVarlinkErrorReply, interface="org.varlink.service"
):
    """The service refuses to service the request as it denies access."""

    class Parameters:
        pass


class ExpectedMore(TypedVarlinkErrorReply, interface="org.varlink.service"):
    """A method was expecting to be called with "more": true, but was called
    without.
    """

    class Parameters:
        pass
