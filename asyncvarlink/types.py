# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

"""Basic type definitions."""

import asyncio
import logging
import os
import re
import typing


_logger_fd = logging.getLogger("asyncvarlink.filedescriptor")


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


class FileDescriptor:
    """Wrap various file descriptor objects of different types in a
    recognizable type for using in a varlink interface.
    """

    def __init__(self, fd: int | HasFileno | None, should_close: bool = False):
        """Wrap a file descriptor object that may be one of an integer, a
        higher level object providing a fileno method or None representing a
        invalid or closed file descriptor. Optionally, if the should_close flag
        is set and the FileDescriptor object is garbage collected without being
        closed, a warning is logged.
        """

        self.should_close: bool
        self.fd: int | HasFileno | None
        if isinstance(fd, int) and fd < 0:
            fd = None
        elif isinstance(fd, FileDescriptor):
            if should_close and fd.should_close:
                raise RuntimeError(
                    "FileDescriptor references another FileDescriptor and "
                    "both are flagged should_close"
                )
            self.fd = fd.fd
        else:
            self.fd = fd
        self.should_close = should_close

    def __bool__(self) -> bool:
        """Indicate whether the object refers to a plausibly open file
        descriptor.
        """
        return self.fd is not None

    def fileno(self) -> int:
        """Return the underlying file descriptor, i.e. self. Raises a
        ValueError when closed.
        """
        if self.fd is None:
            raise ValueError("closed or released file descriptor")
        if isinstance(self.fd, int):
            return self.fd
        return self.fd.fileno()

    __int__ = fileno

    def close(self) -> None:
        """Close the file descriptor. Idempotent. If the underlying file
        descriptor has a close method, it is used. Otherwise, os.close is used.
        """
        if self.fd is None:
            return
        try:
            try:
                close = getattr(self.fd, "close")
            except AttributeError:
                os.close(self.fileno())
            else:
                close()
        finally:
            self.fd = None

    def __eq__(self, other: typing.Any) -> bool:
        """Compare two file descriptors. Comparison to integers, None or
        objects with a fileno method may succeed. Ownership is not considered
        for comparison.
        """
        if self.fd is None:
            return (
                other is None
                or (isinstance(other, int) and other < 0)
                or (isinstance(other, FileDescriptor) and other.fd is None)
            )
        if isinstance(other, int):
            return self.fileno() == other
        try:
            filenometh = getattr(other, "fileno")
        except AttributeError:
            return False
        otherfileno = filenometh()
        if not isinstance(otherfileno, int):
            return False
        return self.fileno() == otherfileno

    def __enter__(self) -> typing.Self:
        """Implement the context manager protocol yielding self and closing
        the file descriptor on exit. The object will be marked as being closed.
        """
        self.should_close = True
        return self

    def __exit__(self, *exc_info: typing.Any) -> None:
        """Close the file descriptor on context manager exit."""
        self.close()

    def take(self) -> HasFileno | int | None:
        """Return and disassociate the wrapped file descriptor object. The
        FileDescriptor must be responsible for closing and thus marked with
        the should_close flag. This responsibility is transferred to the
        caller.
        """
        if not self.should_close:
            _logger_fd.warning(
                "unowned FileDescriptor %r being taken", self.fd
            )
        try:
            return self.fd
        finally:
            self.fd = None

    def __del__(self) -> None:
        """If the FileDescriptor is marked with the should_close flag, close it
        on garbage collection and issue a warning about closing it explicitly.
        """
        if self.fd is None or not self.should_close:
            return
        _logger_fd.warning(
            "owned FileDescriptor %r was never closed explicitly", self.fd
        )
        self.close()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.fd!r})"


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
        self._fds: list[FileDescriptor] = [
            FileDescriptor(fd, should_close=True) for fd in fds or ()
        ]

    def __bool__(self) -> bool:
        """Are there any owned file descriptors in the array?"""
        return any(self._fds)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileDescriptorArray):
            return False
        if len(self._fds) != len(other._fds):
            return False
        return all(fd1 == fd2 for fd1, fd2 in zip(self._fds, other._fds))

    def __len__(self) -> int:
        return len(self._fds)

    def __getitem__(self, index: int) -> FileDescriptor:
        return self._fds[index]

    def __iter__(self) -> typing.Iterator[FileDescriptor]:
        return iter(self._fds)

    def close(self) -> None:
        """Close all owned file descriptors. Idempotent."""
        for fd in self:
            fd.close()

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
