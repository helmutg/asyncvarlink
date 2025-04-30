# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

import typing
import unittest

from asyncvarlink.interface import (
    AnnotatedResult,
    LastResult,
    VarlinkInterface,
    varlinkmethod,
)


class ResultWrapper(typing.TypedDict):
    result: str


def res(val: str) -> ResultWrapper:
    return {"result": val}


class TestInterface(unittest.TestCase):
    def test_synchronous(self) -> None:
        class SyncInterface(VarlinkInterface):
            name = "com.example.SyncInterface"

            def __init__(self) -> None:
                self.gen_state = -1
                self.genr_state = -1
                self.geni_state = -1

            @varlinkmethod
            def simple(self) -> ResultWrapper:
                return res("simple")

            @varlinkmethod
            def annotated(self) -> ResultWrapper:
                return AnnotatedResult(res("annotated"))

            @varlinkmethod(return_parameter="result")
            def named(self) -> str:
                return "named"

            @varlinkmethod(return_parameter="result")
            def annotated_named(self) -> str:
                return AnnotatedResult(res("annotated_named"))

            @varlinkmethod(return_parameter="result")
            def iterable(self) -> list[int]:
                return []

            @varlinkmethod
            def gen(self) -> typing.Iterator[ResultWrapper]:
                self.gen_state = 0
                yield AnnotatedResult(res("gen0"), continues=True)
                self.gen_state = 1
                yield res("gen1")
                self.gen_state = 2
                yield res("gen2")
                self.gen_state = 3

            @varlinkmethod(return_parameter="result")
            def gen_raise(self) -> typing.Iterator[str]:
                self.genr_state = 0
                yield "genr0"
                self.genr_state = 1
                raise LastResult("genr1")

            @varlinkmethod(delay_generator=False, return_parameter="result")
            def gen_immediate(self) -> typing.Iterator[str]:
                self.geni_state = 0
                yield AnnotatedResult(res("geni0"), continues=True)
                self.geni_state = 1
                yield "geni1"
                self.geni_state = 2
                raise LastResult("geni2")

            @varlinkmethod
            def optional(self, *, optional: int | None = None) -> None:
                if optional is not None:
                    raise ValueError("unexpected optional value")

        iface = SyncInterface()
        self.assertEqual(iface.simple(), AnnotatedResult(res("simple")))
        self.assertEqual(iface.annotated(), AnnotatedResult(res("annotated")))
        self.assertEqual(iface.named(), AnnotatedResult(res("named")))
        self.assertEqual(
            iface.annotated_named(), AnnotatedResult(res("annotated_named"))
        )
        self.assertEqual(iface.iterable(), AnnotatedResult(res([])))

        it = iface.gen()
        self.assertEqual(iface.gen_state, -1)
        self.assertEqual(
            next(it), AnnotatedResult(res("gen0"), continues=True)
        )
        self.assertEqual(iface.gen_state, 0)
        self.assertEqual(
            next(it), AnnotatedResult(res("gen1"), continues=True)
        )
        self.assertGreater(iface.gen_state, 1)
        self.assertLess(iface.gen_state, 3)
        self.assertEqual(next(it), AnnotatedResult(res("gen2")))
        self.assertEqual(iface.gen_state, 3)
        self.assertRaises(StopIteration, next, it)

        it = iface.gen_raise()
        self.assertEqual(iface.genr_state, -1)
        self.assertEqual(
            next(it), AnnotatedResult(res("genr0"), continues=True)
        )
        self.assertEqual(iface.genr_state, 1)
        self.assertEqual(next(it), AnnotatedResult(res("genr1")))
        self.assertRaises(StopIteration, next, it)

        it = iface.gen_immediate()
        self.assertEqual(iface.geni_state, -1)
        self.assertEqual(
            next(it), AnnotatedResult(res("geni0"), continues=True)
        )
        self.assertEqual(iface.geni_state, 0)
        self.assertEqual(
            next(it), AnnotatedResult(res("geni1"), continues=True)
        )
        self.assertEqual(iface.geni_state, 1)
        self.assertEqual(next(it), AnnotatedResult(res("geni2")))
        self.assertEqual(iface.geni_state, 2)
        self.assertRaises(StopIteration, next, it)
        iface.optional()


class TestAsyncInterface(unittest.IsolatedAsyncioTestCase):
    async def test_async(self) -> None:
        class AsyncInterface(VarlinkInterface):
            name = "com.example.AsyncInterface"

            def __init__(self) -> None:
                self.gen_state = -1
                self.genr_state = -1
                self.geni_state = -1

            @varlinkmethod
            async def simple(self) -> ResultWrapper:
                return res("simple")

            @varlinkmethod
            async def annotated(self) -> ResultWrapper:
                return AnnotatedResult(res("annotated"))

            @varlinkmethod(return_parameter="result")
            async def named(self) -> str:
                return "named"

            @varlinkmethod(return_parameter="result")
            async def annotated_named(self) -> str:
                return AnnotatedResult(res("annotated_named"))

            @varlinkmethod
            async def gen(self) -> typing.AsyncIterator[ResultWrapper]:
                self.gen_state = 0
                yield AnnotatedResult(res("gen0"), continues=True)
                self.gen_state = 1
                yield res("gen1")
                self.gen_state = 2
                yield res("gen2")
                self.gen_state = 3

            @varlinkmethod(return_parameter="result")
            async def gen_raise(self) -> typing.AsyncIterator[str]:
                self.genr_state = 0
                yield "genr0"
                self.genr_state = 1
                raise LastResult("genr1")

            @varlinkmethod(delay_generator=False, return_parameter="result")
            async def gen_immediate(self) -> typing.AsyncIterator[str]:
                self.geni_state = 0
                yield AnnotatedResult(res("geni0"), continues=True)
                self.geni_state = 1
                yield "geni1"
                self.geni_state = 2
                raise LastResult("geni2")

            @varlinkmethod
            async def optional(self, *, optional: int | None = None) -> None:
                if optional is not None:
                    raise ValueError("unexpected optional value")

        iface = AsyncInterface()
        self.assertEqual(await iface.simple(), AnnotatedResult(res("simple")))
        self.assertEqual(
            await iface.annotated(), AnnotatedResult(res("annotated"))
        )
        self.assertEqual(await iface.named(), AnnotatedResult(res("named")))
        self.assertEqual(
            await iface.annotated_named(),
            AnnotatedResult(res("annotated_named")),
        )

        it = iface.gen()
        self.assertEqual(iface.gen_state, -1)
        self.assertEqual(
            await anext(it), AnnotatedResult(res("gen0"), continues=True)
        )
        self.assertEqual(iface.gen_state, 0)
        self.assertEqual(
            await anext(it), AnnotatedResult(res("gen1"), continues=True)
        )
        self.assertGreater(iface.gen_state, 1)
        self.assertLess(iface.gen_state, 3)
        self.assertEqual(await anext(it), AnnotatedResult(res("gen2")))
        self.assertEqual(iface.gen_state, 3)
        with self.assertRaises(StopAsyncIteration):
            await anext(it)

        it = iface.gen_raise()
        self.assertEqual(iface.genr_state, -1)
        self.assertEqual(
            await anext(it), AnnotatedResult(res("genr0"), continues=True)
        )
        self.assertEqual(iface.genr_state, 1)
        self.assertEqual(await anext(it), AnnotatedResult(res("genr1")))
        with self.assertRaises(StopAsyncIteration):
            await anext(it)

        it = iface.gen_immediate()
        self.assertEqual(iface.geni_state, -1)
        self.assertEqual(
            await anext(it), AnnotatedResult(res("geni0"), continues=True)
        )
        self.assertEqual(iface.geni_state, 0)
        self.assertEqual(
            await anext(it), AnnotatedResult(res("geni1"), continues=True)
        )
        self.assertEqual(iface.geni_state, 1)
        self.assertEqual(await anext(it), AnnotatedResult(res("geni2")))
        self.assertEqual(iface.geni_state, 2)
        with self.assertRaises(StopAsyncIteration):
            await anext(it)
        await iface.optional()
