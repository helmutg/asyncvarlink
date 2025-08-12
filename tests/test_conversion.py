# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

import dataclasses
import enum
import typing
import unittest

import hypothesis
import hypothesis.strategies as st

from asyncvarlink import (
    ConversionError,
    DataclassVarlinkType,
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


@dataclasses.dataclass
class Quantity:
    value: float
    unit: str


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
def type_annotations() -> st.SearchStrategy[type]:
    return st.one_of(
        st.just(bool),
        st.just(int),
        st.just(FileDescriptor),
        st.just(float),
        st.just(Quantity),
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


class MockedFd:
    """Looks like file descriptor, but isn't and swallows close."""

    def __init__(self, fd: int, check_del: bool = False):
        self.fd = fd
        self.check_del = check_del

    def fileno(self) -> int:
        return self.fd

    def close(self) -> None:
        assert self.fd >= 0
        self.fd = -1

    def __del__(self) -> None:
        assert self.fd < 0 or not self.check_del


def representable(vt: VarlinkType) -> st.SearchStrategy[typing.Any]:
    if isinstance(vt, (SimpleVarlinkType, EnumVarlinkType)):
        if vt.as_type == float:
            return st.floats(allow_nan=False)
        return st.from_type(vt.as_type)
    if isinstance(vt, LiteralVarlinkType):
        return st.sampled_from(sorted(vt._values))
    if isinstance(vt, FileDescriptorVarlinkType):
        # hypothesis appears to create more MockedFds than it returns. Do not
        # check __del__.
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
    if isinstance(vt, DataclassVarlinkType):
        assert vt.as_type is Quantity
        return st.builds(
            Quantity,
            value=st.floats(allow_nan=False),
            unit=st.from_type(str),
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


def close_all_fds(obj: typing.Any):
    if isinstance(obj, FileDescriptor):
        obj.close()
    elif isinstance(obj, list):
        for elem in obj:
            close_all_fds(elem)
    elif isinstance(obj, dict):
        for elem in obj.values():
            close_all_fds(elem)


class ConversionTests(unittest.TestCase):
    @hypothesis.given(type_annotations, st.data())
    def test_round_trip(self, ta: type, data) -> None:
        vt = VarlinkType.from_type_annotation(ta)
        obj = data.draw(representable(vt))
        fdlist: list[int] = []
        oobto: dict[type, typing.Any] = {FileDescriptorVarlinkType: fdlist}
        val = vt.tojson(obj, oobto)
        # We don't have to dispose obj as its MockedFds don't check __del__,
        # but we want the array to be checked.
        ownedfds = FileDescriptorArray(
            fdlist, [MockedFd(fd, check_del=True) for fd in fdlist]
        )
        oobfrom: dict[type, typing.Any] = {FileDescriptorVarlinkType: ownedfds}
        obj_again = vt.fromjson(val, oobfrom)
        self.assertEqual(obj, obj_again)
        close_all_fds(obj_again)
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
