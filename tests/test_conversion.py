# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

import enum
import typing
import unittest

import hypothesis
import hypothesis.strategies as st

from asyncvarlink import (
    ConversionError,
    DictVarlinkType,
    EnumVarlinkType,
    FileDescriptor,
    FileDescriptorArray,
    FileDescriptorVarlinkType,
    ForeignVarlinkType,
    JSONValue,
    ListVarlinkType,
    LiteralVarlinkType,
    ObjectVarlinkType,
    OptionalVarlinkType,
    SetVarlinkType,
    SimpleVarlinkType,
    VarlinkType,
)


class TriState(enum.Enum):
    rock = "rock"
    paper = "paper"
    scissors = "scissors"


class Digits(enum.Enum):
    zero = "zero"
    one = "one"
    two = "two"
    three = "three"
    four = "four"
    five = "five"
    six = "six"
    seven = "seven"
    eight = "eight"
    nine = "nine"


class Dummy:
    pass


@st.deferred
def type_annotations() -> st.SearchStrategy:
    return st.one_of(
        st.just(bool),
        st.just(int),
        st.just(FileDescriptor),
        st.just(float),
        st.just(str),
        st.just(TriState),
        st.just(typing.Literal["on", "off"]),
        st.just(Digits),
        st.just(set[str]),
        st.builds(
            lambda fields: typing.TypedDict(
                "AnonTypedDict",
                {
                    key: ta if required else typing.NotRequired[ta]
                    for key, (ta, required) in fields.items()
                },
            ),
            st.dictionaries(
                st.text(),
                st.tuples(type_annotations, st.booleans()),
            ),
        ),
        st.builds(lambda ta: ta | None, type_annotations),
        st.builds(lambda ta: list[ta], type_annotations),
        st.builds(lambda ta: dict[str, ta], type_annotations),
        st.builds(lambda: Dummy),
    )


class MockedFd(FileDescriptor):
    """Looks like file descriptor, but isn't and swallows close."""

    def close(self) -> None:
        pass


def representable(vt: VarlinkType) -> st.SearchStrategy:
    if isinstance(vt, (SimpleVarlinkType, EnumVarlinkType)):
        if vt.as_type == float:
            return st.floats(allow_nan=False)
        return st.from_type(vt.as_type)
    if isinstance(vt, LiteralVarlinkType):
        return st.sampled_from(sorted(vt._values))
    if isinstance(vt, FileDescriptorVarlinkType):
        return st.builds(MockedFd, st.integers(min_value=0))
    if isinstance(vt, OptionalVarlinkType):
        return st.one_of(st.none(), representable(vt._vtype))
    if isinstance(vt, ListVarlinkType):
        return st.lists(representable(vt._elttype))
    if isinstance(vt, DictVarlinkType):
        return st.dictionaries(st.text(), representable(vt._elttype))
    if isinstance(vt, SetVarlinkType):
        return st.sets(st.text())
    if isinstance(vt, ObjectVarlinkType):
        return st.builds(
            dict,
            **{
                key: representable(value)
                for key, value in vt._typemap.items()
                if not isinstance(value, OptionalVarlinkType)
            },
        )
    assert isinstance(vt, ForeignVarlinkType)
    return st.just(object())


json_values = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False),
        st.text(),
    ),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
)


class Coordinate(typing.TypedDict):
    x: float
    y: float


class ConversionTests(unittest.TestCase):
    @hypothesis.given(type_annotations, st.data())
    def test_round_trip(self, ta: type, data) -> None:
        vt = VarlinkType.from_type_annotation(ta)
        obj = data.draw(representable(vt))
        fdlist: list[int | None] = []
        oobto: dict[type, typing.Any] = {FileDescriptorVarlinkType: fdlist}
        val = vt.tojson(obj, oobto)
        ownedfds = FileDescriptorArray(fdlist, fdlist)
        oobfrom: dict[type, typing.Any] = {FileDescriptorVarlinkType: ownedfds}
        obj_again = vt.fromjson(val, oobfrom)
        self.assertEqual(obj, obj_again)
        self.assertFalse(bool(ownedfds))
        ownedfds.release(fdlist)

    @hypothesis.given(type_annotations, json_values)
    def test_exception(self, ta: type, val: JSONValue) -> None:
        vt = VarlinkType.from_type_annotation(ta)
        try:
            vt.fromjson(val)
        except ConversionError:
            pass

    def test_invalid(self) -> None:
        for ta, jvals, pvals in [
            (bool, ["fuzzy"], ["fuzzy"]),
            (int, [2.5], [2.5]),
            (float, ["fuzzy", 1 << 9999], ["fuzzy"]),
            (str, [42], [42]),
            (list[str], [2, [2]], [2, [2]]),
            (set[str], [2, {1: {}}], [1, {1}]),
            (dict[str, str], [2, {"x": 1}], [1, [("x", "y")], {1: 2}]),
            (
                Coordinate,
                [
                    2,
                    {"x": 1.0},
                    {"x": 1, "y": "bad"},
                    {"x": 1.0, "y": 1.0, "z": 1.0},
                ],
                [
                    2,
                    {"x": 1.0},
                    {"x": 1, "y": "bad"},
                    {"x": 1.0, "y": 1.0, "z": 1.0},
                ],
            ),
            (TriState, [2, "well"], [2, "well"]),
            (typing.Literal["on", "off"], [2, "maybe"], [2, "maybe"]),
            (FileDescriptor, ["stdin", -1], ["stdin"]),
        ]:
            vt = VarlinkType.from_type_annotation(ta)
            for jval in jvals:
                oobstate = {
                    FileDescriptorVarlinkType: FileDescriptorArray(self),
                }
                with self.subTest(ta=ta, jval=jval):
                    with self.assertRaises(ConversionError):
                        vt.fromjson(jval, oobstate=oobstate)
            for pval in pvals:
                with self.subTest(ta=ta, pval=pval):
                    oobstate = {FileDescriptorVarlinkType: []}
                    with self.assertRaises(ConversionError):
                        vt.tojson(pval, oobstate=oobstate)
