# Copyright 2026 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import asyncio
import os
import socket
import unittest.mock


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
