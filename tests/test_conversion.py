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
    FileDescriptorVarlinkType,
    ForeignVarlinkType,
    JSONValue,
    ListVarlinkType,
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


def representable(vt: VarlinkType) -> st.SearchStrategy:
    if isinstance(vt, (SimpleVarlinkType, EnumVarlinkType)):
        if vt.as_type == float:
            return st.floats(allow_nan=False)
        return st.from_type(vt.as_type)
    if isinstance(vt, FileDescriptorVarlinkType):
        return st.integers(min_value=0)
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
                for key, value in vt._required_keys.items()
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


class ConversionTests(unittest.TestCase):
    @hypothesis.given(type_annotations, st.data())
    def test_round_trip(self, ta: type, data) -> None:
        vt = VarlinkType.from_type_annotation(ta)
        obj = data.draw(representable(vt))
        oob: dict[type, typing.Any] = {}
        val = vt.tojson(obj, oob)
        obj_again = vt.fromjson(val, oob)
        self.assertEqual(obj, obj_again)

    @hypothesis.given(type_annotations, json_values)
    def test_exception(self, ta: type, val: JSONValue) -> None:
        vt = VarlinkType.from_type_annotation(ta)
        try:
            vt.fromjson(val)
        except ConversionError:
            pass
