# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

import os
import unittest
import unittest.mock

from asyncvarlink import get_listen_fd


class GetListenFdTests(unittest.TestCase):
    def test_missing(self) -> None:
        pid = os.getpid()
        with unittest.mock.patch.dict(
            "os.environ", {"LISTEN_FDS": "1"}, clear=True
        ):
            self.assertIsNone(get_listen_fd("spam"), "missing LISTEN_PID")
        with unittest.mock.patch.dict(
            "os.environ",
            {"LISTEN_FDS": "1", "LISTEN_PID": str(pid + 1)},
            clear=True,
        ):
            self.assertIsNone(get_listen_fd("spam"), "wrong LISTEN_PID")
        with unittest.mock.patch.dict(
            "os.environ",
            {"LISTEN_FDS": "2", "LISTEN_PID": str(pid)},
            clear=True,
        ):
            self.assertIsNone(get_listen_fd("spam"), "missing LISTEN_FDNAMES")
        with unittest.mock.patch.dict(
            "os.environ",
            {
                "LISTEN_FDS": "1",
                "LISTEN_PID": str(pid),
                "LISTEN_FDNAMES": "beacon",
            },
            clear=True,
        ):
            self.assertIsNone(get_listen_fd("spam"), "mismatch LISTEN_FDNAMES")
        with unittest.mock.patch.dict(
            "os.environ",
            {
                "LISTEN_FDS": "2",
                "LISTEN_PID": str(pid),
                "LISTEN_FDNAMES": "beacon",
            },
            clear=True,
        ):
            self.assertIsNone(get_listen_fd("spam"), "bad LISTEN_FDNAMES")
        with unittest.mock.patch.dict(
            "os.environ",
            {
                "LISTEN_FDS": "2",
                "LISTEN_PID": str(pid),
                "LISTEN_FDNAMES": "beacon:egg",
            },
            clear=True,
        ):
            self.assertIsNone(
                get_listen_fd("spam"), "name not in LISTEN_FDNAMES"
            )

    def test_found(self) -> None:
        pid = str(os.getpid())
        with unittest.mock.patch.dict(
            "os.environ", {"LISTEN_FDS": "1", "LISTEN_PID": pid}, clear=True
        ):
            # If there is only one fd and no LISTEN_FDNAMES, expect success for
            # backwards compatibility.
            self.assertEqual(get_listen_fd("spam"), 3)
        with unittest.mock.patch.dict(
            "os.environ",
            {"LISTEN_FDS": "1", "LISTEN_PID": pid, "LISTEN_FDNAMES": "spam"},
            clear=True,
        ):
            self.assertEqual(get_listen_fd("spam"), 3)
        with unittest.mock.patch.dict(
            "os.environ",
            {
                "LISTEN_FDS": "2",
                "LISTEN_PID": pid,
                "LISTEN_FDNAMES": "baecon:spam",
            },
            clear=True,
        ):
            self.assertEqual(get_listen_fd("spam"), 4)
