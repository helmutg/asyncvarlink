# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""Basic type definitions."""

import asyncio
import os
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
        """Return the underlying file descriptor."""


class FileDescriptor(int):
    """An integer that happens to represent a file descriptor meant for type
    checking.
    """

    def fileno(self) -> int:
        """Return the underlying file descriptor, i.e. self."""
        return self

    @classmethod
    def upgrade(cls, fdlike: HasFileno | int) -> HasFileno:
        """Upgrade an int into a FileDescriptor or return an object that
        already has a fileno method unmodified.
        """
        if hasattr(fdlike, "fileno"):
            return fdlike
        assert isinstance(fdlike, int)
        return cls(fdlike)


def close_fileno(thing: HasFileno) -> None:
    """Close something that has a fileno. Use .close() if available to improve
    behaviour on sockets and buffered files.
    """
    try:
        closemeth = getattr(thing, "close")
    except AttributeError:
        os.close(thing.fileno())
    else:
        closemeth()


class FutureCounted:
    """A reference counting base class. References are not simply counted.
    Instead referees are tracked individually. Any referee must be released
    eventually by calling release. Once all referees are gone, the destroy
    method is called once.
    """

    def __init__(self, initial: typing.Any) -> None:
        """The constructor consumes an initial referee. Otherwise, it would be
        immediately destroyed.
        """
        self._references: set[int] = {id(initial)}

    def reference(self, referee: typing.Any) -> None:
        """Record an object as referee. The referee should be either passed to
        release once or garbage collected by Python.
        """
        if not self._references:
            raise RuntimeError("cannot reference destroyed object")
        objid = id(referee)
        assert objid not in self._references
        self._references.add(objid)

    def reference_until_done(self, fut: asyncio.Future[typing.Any]) -> None:
        """Reference this object until the passed future is done."""
        self.reference(fut)
        fut.add_done_callback(self.release)

    def release(self, referee: typing.Any) -> None:
        """Release the reference identified by the given referee. If this was
        the last reference, this object is destroyed. Releasing a referee that
        was not referenced is an error as is releasing a referee twice.
        """
        objid = id(referee)
        try:
            self._references.remove(objid)
        except KeyError:
            raise RuntimeError(
                f"releasing reference to unregistered object {referee!r}"
            ) from None
        if not self._references:
            self.destroy()

    def destroy(self) -> None:
        """Called when the last reference is released."""
        raise NotImplementedError


class FileDescriptorArray(FutureCounted):
    """Represent an array of file descriptors owned and eventually released by
    the array. The lifetime can be controlled in two ways. Responsibility for
    individual file descriptors can be assumed by using the take method and
    thus removing them from the array. The lifetime of the entire array can
    be extended using the FutureCounted mechanism inherited from.
    """

    def __init__(
        self,
        initial_referee: typing.Any,
        fds: typing.Iterable[HasFileno | int | None] | None = None,
    ):
        super().__init__(initial_referee)
        self._fds: list[HasFileno | None] = [
            fd if fd is None else FileDescriptor.upgrade(fd)
            for fd in fds or ()
        ]

    def __bool__(self) -> bool:
        """Are there any owned file descriptors in the array?"""
        return any(fd is not None for fd in self._fds)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileDescriptorArray):
            return False
        if len(self._fds) != len(other._fds):
            return False
        return all(
            (
                fd1.fileno() == fd2.fileno()
                if fd1 is not None and fd2 is not None
                else fd1 is fd2
            )
            for fd1, fd2 in zip(self._fds, other._fds)
        )

    def take(self, index: int) -> HasFileno:
        """Return and consume a file descriptor from the array. Once returned
        the caller is responsible for closing the file descriptor eventually.
        """
        fd = self._fds[index]
        if fd is None:
            raise IndexError("index points at released entry")
        self._fds[index] = None
        return fd

    def close(self) -> None:
        """Close all owned file descriptors. Idempotent."""
        for index in range(len(self._fds)):
            try:
                fd = self.take(index)
            except IndexError:
                pass
            else:
                close_fileno(fd)

    __del__ = close
    destroy = close


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
