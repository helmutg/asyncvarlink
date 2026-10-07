# Copyright 2026 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import asyncio
import os
import socket
import unittest
import unittest.mock

from asyncvarlink.types import override


def async_read_fd(fd: int, size: int) -> asyncio.Future[bytes]:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def readable() -> None:
        loop.remove_reader(fd)
        try:
            data = os.read(fd, size)
        except Exception as exc:
            fut.set_exception(exc)
        else:
            fut.set_result(data)

    loop.add_reader(fd, readable)
    return fut


def _send_fds_writable(
    sock: socket.socket, data: bytes, fds: list[int], fut: asyncio.Future[None]
) -> None:
    loop = asyncio.get_running_loop()
    try:
        sent = socket.send_fds(sock, [data], fds)
    except Exception as exc:
        loop.remove_writer(sock)
        fut.set_exception(exc)
    else:
        if sent >= len(data):
            loop.remove_writer(sock)
            fut.set_result(None)
        else:
            loop.add_writer(
                sock, _send_fds_writable, sock, data[sent:], [], fut
            )


def async_send_fds(
    sock: socket.socket, data: bytes, fds: list[int] | None = None
) -> asyncio.Future[None]:
    loop = asyncio.get_running_loop()
    if fds is None:
        fds = []
    fut = loop.create_future()
    loop.add_writer(sock, _send_fds_writable, sock, data, fds, fut)
    return fut


async def defer(
    count: int = 100, until_called: unittest.mock.Mock | None = None
) -> None:
    for _ in range(count):
        if until_called is not None and until_called.called:
            return
        await asyncio.sleep(0)


class StrictAsyncioTestCase(unittest.IsolatedAsyncioTestCase):
    """Allow writing asyncio-based test cases like
    unittest.IsolatedAsyncioTestCase, but also fail if any exception escapes
    from a callback.
    """

    def _exception_handler(
        self, loop: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> None:
        self._observed_exceptions.append(context)

    @override
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self._observed_exceptions: list[dict[str, object]] = []
        asyncio.get_running_loop().set_exception_handler(
            self._exception_handler
        )

    @override
    async def asyncTearDown(self) -> None:
        count = len(self._observed_exceptions)
        if count > 0:
            if count == 1:
                self.fail(
                    f"An exception escaped from a callback: {self._observed_exceptions[0]!r}"
                )
            else:
                self.fail(
                    f"{count} exceptions escaped from callbacks {self._observed_exceptions!r}"
                )
        await super().asyncTearDown()
