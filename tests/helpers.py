# Copyright 2026 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import asyncio
import os


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
