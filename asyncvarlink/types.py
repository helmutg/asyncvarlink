# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""Basic type definitions."""

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
