# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import asyncio
import contextlib
import os
import socket
import unittest
import unittest.mock
from unittest.mock import Mock

from asyncvarlink import (
    VarlinkBaseProtocol,
    VarlinkMethodCall,
    VarlinkMethodReply,
    VarlinkProtocol,
    VarlinkTransport,
)
from asyncvarlink.types import JSONObject, JSONValue

from helpers import defer, StrictAsyncioTestCase


class TransportTests(StrictAsyncioTestCase):
    async def test_receive_socket(self) -> None:
        protocol = VarlinkBaseProtocol()
        protocol.message_received = Mock(return_value=None)
        protocol.connection_lost = Mock(return_value=None)
        sock1, sock2 = socket.socketpair()
        with contextlib.closing(sock1):
            with contextlib.closing(sock2):
                transport = VarlinkTransport(
                    asyncio.get_running_loop(), sock1, sock1, protocol
                )
                protocol.eof_received = transport.close
                sock2.send(b"hello")
                await defer(until_called=protocol.message_received)
                protocol.message_received.assert_called_once_with(
                    b"hello", None
                )
            await defer(until_called=protocol.connection_lost)
            protocol.connection_lost.assert_called_once_with(None)

    async def test_receive_socket_eof(self) -> None:
        protocol = VarlinkBaseProtocol()
        protocol.eof_received = Mock()
        sock1, sock2 = socket.socketpair()
        with contextlib.closing(sock1), contextlib.closing(sock2):
            VarlinkTransport(
                asyncio.get_running_loop(), sock1, sock1, protocol
            )
            sock2.close()
            await defer(until_called=protocol.eof_received)
        protocol.eof_received.assert_called_once_with()

    async def test_receive_pipe(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkBaseProtocol()
        protocol.message_received = Mock(return_value=None)
        pipe1, pipe2 = os.pipe()
        with contextlib.closing(
            VarlinkTransport(loop, pipe1, pipe2, protocol)
        ):
            os.write(pipe2, b"hello")
            await defer(until_called=protocol.message_received)
        protocol.message_received.assert_called_once_with(b"hello", None)

    async def test_receive_pipe_eof(self) -> None:
        protocol = VarlinkBaseProtocol()
        protocol.eof_received = Mock()
        pipe1, pipe2 = os.pipe()
        VarlinkTransport(asyncio.get_running_loop(), pipe1, pipe2, protocol)
        os.close(pipe2)
        await defer(until_called=protocol.eof_received)
        protocol.eof_received.assert_called_once_with()

    async def test_send_socket(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkBaseProtocol()
        sock1, sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        with contextlib.closing(sock1), contextlib.closing(sock2):
            transport = VarlinkTransport(loop, sock1, sock1, protocol)
            fut1 = transport.send_message(b"hello")
            fut2 = transport.send_message(b"world")
            self.assertEqual(b"helloworld", await loop.sock_recv(sock2, 1024))
            self.assertIsNone(fut1.result())
            self.assertIsNone(fut2.result())
            transport.close()
            fut3 = transport.send_message(b"fail")
            with self.assertRaises(OSError):
                fut3.result()
            # transport.close can defer closing, but we want it to complete
            # before exiting the context to avoid exceptions in callbacks.
            await transport.closed_future

    async def test_fd_association(self) -> None:
        protocol = VarlinkBaseProtocol()
        protocol.message_received = Mock(return_value=None)
        with contextlib.ExitStack() as stack:
            sock1, sock2 = socket.socketpair()
            stack.callback(sock1.close)
            stack.callback(sock2.close)
            fd_to_send = os.open("/dev/null", os.O_RDONLY)
            stack.callback(os.close, fd_to_send)

            transport = VarlinkTransport(
                asyncio.get_running_loop(),
                sock1,
                sock1,
                protocol,
            )

            socket.send_fds(sock2, [b"1\0"], [])
            socket.send_fds(sock2, [b"2\0"], [fd_to_send])

            await defer(until_called=protocol.message_received)
            protocol.message_received.assert_called()
            self.assertEqual(
                unittest.mock.call(b"1\0", None),
                protocol.message_received.call_args_list[0],
            )
            if protocol.message_received.call_count < 2:
                protocol.message_received.reset_mock()
                await defer(until_called=protocol.message_received)
                protocol.message_received.assert_called()
            call2_data, call2_fds = protocol.message_received.call_args.args
            self.assertEqual(b"2\0", call2_data)
            self.assertSequenceEqual([unittest.mock.ANY], call2_fds)


class ProtocolTests(StrictAsyncioTestCase):
    async def test_receive(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        with contextlib.closing(
            VarlinkTransport(loop, pipe1, pipe2, protocol)
        ):
            protocol.request_received = Mock()
            protocol.message_received(b'{"hello":"world"}\0', None)
            await defer(until_called=protocol.request_received)
            protocol.request_received.assert_called_once_with(
                {"hello": "world"}, None
            )

    async def test_receive_error(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        with contextlib.closing(
            VarlinkTransport(loop, pipe1, pipe2, protocol)
        ):
            protocol.error_received = Mock(wraps=protocol.error_received)
            protocol.message_received(b"}\0", None)
            await defer(until_called=protocol.error_received)
            protocol.error_received.assert_called_once()

    async def test_receive_pause(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        with contextlib.closing(
            VarlinkTransport(loop, pipe1, pipe2, protocol)
        ) as transport:
            futs = [loop.create_future(), loop.create_future()]
            protocol.request_received = Mock(side_effect=futs)
            await defer()
            self.assertFalse(transport._paused)
            protocol.message_received(b'{"a":0}\0{"b":0}\0', None)
            await defer()
            self.assertTrue(transport._paused)
            protocol.request_received.assert_called_once_with({"a": 0}, None)
            self.assertTrue(transport._paused)
            futs[0].set_result(None)
            await defer()
            self.assertTrue(transport._paused)
            protocol.request_received.assert_called_with({"b": 0}, None)
            await defer()
            self.assertTrue(transport._paused)
            futs[1].set_result(None)
            await defer()
            self.assertFalse(transport._paused)

    async def test_receive_multiple(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        with contextlib.closing(
            VarlinkTransport(loop, pipe1, pipe2, protocol)
        ):
            protocol.request_received = Mock()
            await asyncio.sleep(0)
            protocol.message_received(b'{"a":0}\0{"b":0}\0', None)
            await defer(until_called=protocol.request_received)
            protocol.request_received.assert_called_once_with({"a": 0}, None)
            await asyncio.sleep(0)
            protocol.request_received.assert_called_with({"b": 0}, None)

    async def test_send(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
        sock1, sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        with (
            contextlib.closing(VarlinkTransport(loop, sock1, sock1, protocol)),
            contextlib.closing(sock2),
        ):
            await asyncio.sleep(0)
            protocol.send_message({"hello": "world"}, [])
            self.assertEqual(
                b'{"hello":"world"}\0', await loop.sock_recv(sock2, 1024)
            )


class CallReplyTest(unittest.TestCase):
    def test_valid_call(self) -> None:
        cases: list[JSONObject] = [
            {},
            {"parameters": {"ingredient": "egg"}},
            {"oneway": True},
            {"more": True},
            {"upgrade": True},
            {"more": True},
            {"com.example.Extension": "spam"},
        ]
        for jobj in cases:
            jobj["method"] = "com.example.Spam"
            call = VarlinkMethodCall.fromjson(jobj)
            self.assertEqual(jobj, call.tojson())

    def test_invalid_call(self) -> None:
        cases: list[tuple[type[Exception], JSONValue]] = [
            (TypeError, 42),
            (ValueError, {}),
            (TypeError, {"method": 42}),
            (ValueError, {"method": "Unqualified"}),
            (ValueError, {"method": "com.example.lowercase"}),
            (TypeError, {"method": "com.example.Spam", "oneway": "egg"}),
            (TypeError, {"method": "com.example.Spam", "more": "egg"}),
            (TypeError, {"method": "com.example.Spam", "upgrade": "egg"}),
            (
                ValueError,
                {"method": "com.example.Spam", "more": True, "oneway": True},
            ),
            (TypeError, {"method": "com.example.Spam", "parameters": "egg"}),
        ]
        for exc, jobj in cases:
            with self.assertRaises(exc):
                VarlinkMethodCall.fromjson(jobj)

    def test_valid_reply(self) -> None:
        cases: list[JSONObject] = [
            {},
            {"error": "com.example.Spam"},
            {"parameters": {"ingredients": "egg"}},
            {"continues": True},
        ]
        for jobj in cases:
            reply = VarlinkMethodReply.fromjson(jobj)
            self.assertEqual(jobj, reply.tojson())

    def test_invalid_reply(self) -> None:
        cases: list[tuple[type[Exception], JSONValue]] = [
            (TypeError, 42),
            (TypeError, {"error": 42}),
            (ValueError, {"error": "Unqualified"}),
            (ValueError, {"error": "com.example.lowercase"}),
            (TypeError, {"parameters": "egg"}),
            (TypeError, {"continues": "egg"}),
        ]
        for exc, jobj in cases:
            with self.assertRaises(exc):
                VarlinkMethodReply.fromjson(jobj)
