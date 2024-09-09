import asyncio
import contextlib
import os
import socket
import unittest
from unittest.mock import Mock

from asyncvarlink import VarlinkProtocol, VarlinkTransport


async def wait_called(mock: Mock) -> None:
    for delay in range(100):
        if mock.called:
            return
        await asyncio.sleep(0.01 * delay)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_socket(self) -> None:
        protocol = VarlinkProtocol()
        protocol.message_received = Mock(return_value=None)
        sock1, sock2 = socket.socketpair()
        with contextlib.closing(sock1), contextlib.closing(sock2):
            VarlinkTransport(
                asyncio.get_running_loop(), sock1, sock1, protocol
            )
            sock2.send(b"hello")
            await wait_called(protocol.message_received)
        protocol.message_received.assert_called_once_with(b"hello", [])

    async def test_receive_socket_eof(self) -> None:
        protocol = VarlinkProtocol()
        protocol.eof_received = Mock()
        sock1, sock2 = socket.socketpair()
        with contextlib.closing(sock1), contextlib.closing(sock2):
            VarlinkTransport(
                asyncio.get_running_loop(), sock1, sock1, protocol
            )
            sock2.close()
            await wait_called(protocol.eof_received)
        protocol.eof_received.assert_called_once_with()

    async def test_receive_pipe(self) -> None:
        protocol = VarlinkProtocol()
        protocol.message_received = Mock(return_value=None)
        pipe1, pipe2 = os.pipe()
        try:
            VarlinkTransport(
                asyncio.get_running_loop(), pipe1, pipe2, protocol
            )
            os.write(pipe2, b"hello")
            await wait_called(protocol.message_received)
        finally:
            os.close(pipe2)
            os.close(pipe1)
        protocol.message_received.assert_called_once_with(b"hello", [])

    async def test_receive_pipe_eof(self) -> None:
        protocol = VarlinkProtocol()
        protocol.eof_received = Mock()
        pipe1, pipe2 = os.pipe()
        VarlinkTransport(asyncio.get_running_loop(), pipe1, pipe2, protocol)
        os.close(pipe2)
        await wait_called(protocol.eof_received)
        protocol.eof_received.assert_called_once_with()

    async def test_send_socket(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
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


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive(self) -> None:
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        try:
            VarlinkTransport(
                asyncio.get_running_loop(), pipe1, pipe2, protocol
            )
            protocol.request_received = Mock()
            protocol.message_received(b'{"hello":"world"}\0', [])
            await wait_called(protocol.request_received)
            protocol.request_received.assert_called_once_with(
                {"hello": "world"}, []
            )
        finally:
            os.close(pipe2)
            os.close(pipe1)

    async def test_receive_error(self) -> None:
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        try:
            VarlinkTransport(
                asyncio.get_running_loop(), pipe1, pipe2, protocol
            )
            protocol.error_received = Mock(wraps=protocol.error_received)
            protocol.message_received(b"}\0", [])
            await wait_called(protocol.error_received)
            protocol.error_received.assert_called_once()
        finally:
            os.close(pipe2)
            os.close(pipe1)

    async def test_receive_pause(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        try:
            transport = VarlinkTransport(loop, pipe1, pipe2, protocol)
            futs = [loop.create_future(), loop.create_future()]
            protocol.request_received = Mock(side_effect=futs)
            await asyncio.sleep(0)
            self.assertFalse(transport._paused)
            protocol.message_received(b'{"a":0}\0{"b":0}\0', [])
            await asyncio.sleep(0)
            self.assertTrue(transport._paused)
            protocol.request_received.assert_called_once_with({"a": 0}, [])
            self.assertTrue(transport._paused)
            futs[0].set_result(None)
            await asyncio.sleep(0)
            self.assertTrue(transport._paused)
            protocol.request_received.assert_called_with({"b": 0}, [])
            await asyncio.sleep(0)
            self.assertTrue(transport._paused)
            futs[1].set_result(None)
            await asyncio.sleep(0)
            self.assertFalse(transport._paused)
        finally:
            os.close(pipe2)
            os.close(pipe1)

    async def test_receive_multiple(self) -> None:
        protocol = VarlinkProtocol()
        pipe1, pipe2 = os.pipe()
        try:
            VarlinkTransport(
                asyncio.get_running_loop(), pipe1, pipe2, protocol
            )
            protocol.request_received = Mock()
            await asyncio.sleep(0)
            protocol.message_received(b'{"a":0}\0{"b":0}\0', [])
            await wait_called(protocol.request_received)
            protocol.request_received.assert_called_once_with({"a": 0}, [])
            await asyncio.sleep(0)
            protocol.request_received.assert_called_with({"b": 0}, [])
        finally:
            os.close(pipe2)
            os.close(pipe1)

    async def test_send(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = VarlinkProtocol()
        sock1, sock2 = socket.socketpair(
            type=socket.SOCK_STREAM | socket.SOCK_NONBLOCK
        )
        with contextlib.closing(sock1), contextlib.closing(sock2):
            VarlinkTransport(loop, sock1, sock1, protocol)
            await asyncio.sleep(0)
            protocol.send_message({"hello": "world"}, [])
            self.assertEqual(
                b'{"hello": "world"}\0', await loop.sock_recv(sock2, 1024)
            )
