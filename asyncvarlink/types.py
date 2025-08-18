# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

"""Basic type definitions."""

import asyncio
import contextlib
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
        if self.fd is None:
            return False
        if isinstance(self.fd, int):
            return True
        try:
            fd = self.fd.fileno()
        except ValueError:
            return False
        return fd >= 0

    def fileno(self) -> int:
        """Return the underlying file descriptor, i.e. self. Raises a
        ValueError when closed.
        """
        if self.fd is None:
            raise ValueError("closed or released file descriptor")
        if isinstance(self.fd, int):
            return self.fd
        fd = self.fd.fileno()
        if fd < 0:
            raise ValueError("closed or released file descriptor")
        return fd

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

    def release(self) -> None:
        """Close the file descriptor if it should be closed. Do nothing
        otherwise.
        """
        if self.should_close:
            self.close()

    def __eq__(self, other: typing.Any) -> bool:
        """Compare two file descriptors. Comparison to integers, None or
        objects with a fileno method may succeed. Ownership is not considered
        for comparison.
        """
        try:
            selffileno = self.fileno()
        except ValueError:
            selffileno = None
        if other is None:
            otherfileno = None
        elif isinstance(other, int):
            if other < 0:
                otherfileno = None
            else:
                otherfileno = other
        else:
            try:
                fileno_meth = getattr(other, "fileno")
            except AttributeError:
                return False
            try:
                otherfileno = fileno_meth()
            except ValueError:
                otherfileno = None
            else:
                if otherfileno < 0:
                    otherfileno = None
        return selffileno == otherfileno

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

    @classmethod
    @contextlib.contextmanager
    def new_managed(cls) -> typing.Iterator[typing.Self]:
        """Create a managed object whose lifetime is managed in a context
        manager.
        """
        sentinel = object()
        obj = cls(sentinel)
        try:
            yield obj
        finally:
            obj.release(sentinel)

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
    """Represent an array of observed or owned file descriptors. When the array
    is released, the contained file descriptors are released which amounts to
    closing the owned ones. The lifetime can be controlled in two ways.
    Responsibility for individual file descriptors can be assumed by using the
    take method and thus removing them from the array. The lifetime of the
    entire array can be extended using the FutureCounted mechanism inherited
    from. Each file descriptor in the array must be unique.
    """

    def __init__(
        self,
        initial_referee: typing.Any,
        fds: typing.Iterable[HasFileno | int | None] | None = None,
    ):
        """Construct a FileDescriptorArray. The initial_referee is passed to
        the FutureCounted constructor. The fds iterable must not contain any
        fileno several times. Note that file descriptors not wrapped in
        FileDescriptor are assumed to be owned and shall be closed.
        """
        super().__init__(initial_referee)
        self._by_position: list[FileDescriptor] = []
        self._by_fileno: dict[int, int] = {}
        for fdlike in fds or ():
            if fdlike is None:
                fileno = None
            else:
                try:
                    fileno_meth = getattr(fdlike, "fileno")
                except AttributeError:
                    assert isinstance(fdlike, int)
                    fileno = fdlike
                else:
                    try:
                        fileno = fileno_meth()
                    except ValueError:
                        fileno = None
                    else:
                        assert isinstance(fileno, int)
                        if fileno < 0:
                            fileno = None
            if fileno is None:
                self._by_position.append(FileDescriptor(None))
            elif fileno in self._by_fileno:
                raise ValueError(
                    f"file descriptor {fileno} passed several times"
                )
            else:
                fd = FileDescriptor(fdlike, should_close=True)
                if fileno is not None:
                    self._by_fileno[fileno] = len(self._by_position)
                self._by_position.append(fd)

    def __bool__(self) -> bool:
        """Are there any owned file descriptors in the array?"""
        return any(self._by_position)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileDescriptorArray):
            return False
        if len(self._by_position) != len(other._by_position):
            return False
        return all(
            fd1 == fd2
            for fd1, fd2 in zip(self._by_position, other._by_position)
        )

    def __len__(self) -> int:
        return len(self._by_position)

    def __getitem__(self, index: int) -> FileDescriptor:
        return self._by_position[index]

    def __iter__(self) -> typing.Iterator[FileDescriptor]:
        return iter(self._by_position)

    def destroy(self) -> None:
        """Release all file descriptors. This amounts to closing owned ones.
        Idempotent.
        """
        for fd in self:
            fd.release()

    def add(self, fdlike: HasFileno | int | None) -> int:
        """Append a file descriptor to the array and return its position. If
        None or a closed file descriptor are passed, a ValueError is raised.
        If the file descriptor already is in the array, the add call must pass
        the same representation (equal integer or identical object) and the
        existing index is returned. Descriptors not wrapped in the
        FileDescriptor class are closed eventually.
        """
        if isinstance(fdlike, FileDescriptor):
            # Propagate ValueError
            fileno = fdlike.fileno()
        elif fdlike is None:
            raise ValueError("attempt to add closed file descriptor")
        else:
            try:
                fileno_meth = getattr(fdlike, "fileno")
            except AttributeError:
                assert isinstance(fdlike, int)
                if fdlike < 0:
                    raise ValueError(
                        "attempt to add negative file descriptor"
                    ) from None
                fileno = fdlike
            else:
                # Propagate ValueError
                fileno = fileno_meth()
                if fileno < 0:
                    raise ValueError("attempt to add negative file descriptor")
        try:
            index = self._by_fileno[fileno]
        except KeyError:
            if not isinstance(fdlike, FileDescriptor):
                fdlike = FileDescriptor(fdlike, True)
            index = len(self._by_position)
            self._by_position.append(fdlike)
            self._by_fileno[fileno] = index
            return index
        other = self._by_position[index]
        if other is fdlike or other.fd is fdlike or isinstance(fdlike, int):
            return index
        raise ValueError(
            f"attempt to add {fileno} with different representation "
            f"{fdlike!r} than existing entry {other.fd!r}"
        )

    __del__ = destroy


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
