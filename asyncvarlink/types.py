# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""Basic type definitions."""

import re
import typing


JSONValue = typing.Union[
    None, bool, float, int, str, list["JSONValue"], "JSONObject"
]


JSONObject = dict[str, JSONValue]


# pylint: disable=too-few-public-methods  # It's that one method we describe.
class HasFileno(typing.Protocol):
    """A typing protocol representing a file-like object and looking up the
    underlying file descriptor.
    """

    def fileno(self) -> int:
        """Returns the underlying file descriptor."""


class FileDescriptor(int):
    """An integer that happens to represent a file descriptor meant for type
    checking.
    """

    def fileno(self) -> int:
        """Returns the underlying file descriptor, i.e. self."""
        return self


def validate_interface(interface: str) -> None:
    """Validate a varlink interface in reverse-domain notation. May raise a
    ValueError.
    """
    if not re.match(
        r"[A-Za-z](?:-*[A-Za-z0-9])*(?:\.[A-Za-z0-9](?:-*[A-Za-z0-9])*)+",
        interface,
    ):
        raise ValueError(f"invalid varlink interface {interface!r}")


def validate_name(name: str) -> None:
    """Validate a varlink name. May raise a ValueError."""
    if not re.match(r"^[A-Z][A-Za-z0-9]*$", name):
        raise ValueError(f"invalid varlink name {name!r}")
