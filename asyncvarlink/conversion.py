# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

"""Helper for converting between Python objects and JSONValues."""

import contextlib
import dataclasses
import enum
import types
import typing

from .types import FileDescriptor, FileDescriptorArray, JSONValue, JSONObject


class ConversionError(Exception):
    """A failure to convert a Python value from or to a JSONValue."""

    def __init__(self, message: str):
        self.location: list[str | int] = []
        self.message = message

    @classmethod
    def expected(cls, what: str, obj: typing.Any) -> "ConversionError":
        """Construct a Conversion error indicating that something described by
        what was expected whereas a different type was found.
        """
        return cls(f"expected {what}, but got a {type(obj).__name__}")

    @classmethod
    @contextlib.contextmanager
    def context(cls, where: str | int) -> typing.Iterator[None]:
        """If a ConversionError passes through this context manager, push the
        location where onto the location stack as it passes through.
        """
        try:
            yield
        except ConversionError as err:
            err.location.insert(0, where)
            raise


OOBTypeState = dict[type["VarlinkType"], typing.Any] | None


class VarlinkType:
    """A type abstraction that is exportable simultaneously to Python type
    annotations and varlink interface descriptions.
    """

    as_type: typing.Any
    """A Python type representing the varlink type suitable for a type
    annotation.
    """

    as_varlink: str
    """A varlink interface description representation of the varlink type."""

    typedefs: dict[str, str] = {}
    """Varlink type definitions used in the varlink type."""

    contains_fds: bool
    """Indicate whether fds requiring lifetime management are contained
    somewhere inside this type.
    """

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
        """Convert a Python object conforming to the as_type type annotation to
        a json-convertible object suitable for consumption by varlink. A
        conversion may use the optional out-of-band state object using its own
        type as key and should otherwise forward the oobstate during recursion.
        """
        raise NotImplementedError

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        """Convert a json-decoded Python object to a Python object conforming
        to the as_type type annotation. A conversion may use the optional
        out-of-band state object using its own type as key and should otherwise
        forward the oobstate during recursion.
        """
        raise NotImplementedError

    @classmethod
    def from_type_annotation(cls, tobj: typing.Any) -> "VarlinkType":
        """Convert a Python type annotation object into the VarlinkType
        abstraction. Note that this conversion is lossy and will convert
        unknown types to typing.Any/"object".
        """
        origin = typing.get_origin(tobj)
        args = typing.get_args(tobj)
        if origin is None:
            if isinstance(tobj, type):
                if issubclass(tobj, bool):
                    return SimpleVarlinkType("bool", bool)
                if issubclass(tobj, int):
                    return SimpleVarlinkType("int", int)
                if issubclass(tobj, float):
                    return SimpleVarlinkType("float", float, int)
                if issubclass(tobj, str):
                    return SimpleVarlinkType("string", str)
                if issubclass(tobj, enum.Enum):
                    return EnumVarlinkType(tobj)
                if issubclass(tobj, FileDescriptor):
                    return FileDescriptorVarlinkType()
            if typing.is_typeddict(tobj):
                # Do not iterate __*_keys__ as their order is unstable.
                return ObjectVarlinkType(
                    {
                        name: (
                            cls.from_type_annotation(elemtype).optional()
                            if name in tobj.__optional_keys__
                            else cls.from_type_annotation(elemtype)
                        )
                        for name, elemtype in tobj.__annotations__.items()
                    },
                )
            if dataclasses.is_dataclass(tobj):
                # is_dataclass also returns True for instances.
                assert isinstance(tobj, type)
                return DataclassVarlinkType(tobj)
        elif origin is typing.Literal:
            return LiteralVarlinkType(args)
        elif origin is typing.Union or origin is types.UnionType:
            if any(arg is types.NoneType for arg in args):
                remaining = [alt for alt in args if alt is not types.NoneType]
                if remaining:
                    if len(remaining) == 1:
                        result = cls.from_type_annotation(remaining[0])
                    else:
                        result = cls.from_type_annotation(
                            typing.Union[tuple(remaining)]
                        )
                    if isinstance(
                        result, (ForeignVarlinkType, OptionalVarlinkType)
                    ):
                        return result
                    return OptionalVarlinkType(result)
        elif origin is list:
            if len(args) == 1:
                return ListVarlinkType(cls.from_type_annotation(args[0]))
        elif origin is dict:
            if len(args) == 2 and args[0] is str:
                return DictVarlinkType(cls.from_type_annotation(args[1]))
        elif origin is set:
            if len(args) == 1 and args[0] is str:
                return SetVarlinkType()
        return ForeignVarlinkType()

    def optional(self) -> "OptionalVarlinkType":
        """Wrap the type representation in an OptionalVarlinkType."""
        return OptionalVarlinkType(self)


def _merge_typedefs(
    typedefs: dict[str, str], *moretypedefs: dict[str, str]
) -> None:
    """Update the given typedefs in-place effectively computing a union of all
    given moretypedefs. Conflicting definitions are rejected with a
    RuntimeError.
    """
    for defmap in moretypedefs:
        for name, tdef in defmap.items():
            try:
                olddef = typedefs[name]
            except KeyError:
                typedefs[name] = tdef
            else:
                if olddef != tdef:
                    raise RuntimeError(
                        "conflicting type definitions for "
                        f"{name}: {olddef} vs {tdef}"
                    )


class SimpleVarlinkType(VarlinkType):
    """A varlink type representing a base type such as int or str."""

    contains_fds = False

    def __init__(self, varlinktype: str, pythontype: type, *convertible: type):
        self.as_type = pythontype
        self.as_varlink = varlinktype
        self._convertible = tuple(convertible)

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
        if isinstance(obj, self.as_type):
            return typing.cast(JSONValue, obj)
        if isinstance(obj, self._convertible):
            try:
                return typing.cast(JSONValue, self.as_type(obj))
            except Exception as exc:
                raise ConversionError(
                    f"expected {self.as_varlink}, but failed to convert from "
                    f"{type(obj).__name__}"
                ) from exc
        raise ConversionError.expected(self.as_varlink, obj)

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if isinstance(obj, self.as_type):
            return obj
        if isinstance(obj, self._convertible):
            try:
                return self.as_type(obj)
            except Exception as exc:
                raise ConversionError(
                    f"expected {self.as_varlink}, but failed to convert from "
                    f"{type(obj).__name__}"
                ) from exc
        raise ConversionError.expected(self.as_varlink, obj)

    def __repr__(self) -> str:
        typestr = ", ".join(
            tobj.__name__ if tobj in {bool, float, int, str} else repr(tobj)
            for tobj in (self.as_type,) + self._convertible
        )
        return f"{self.__class__.__name__}({self.as_varlink!r}, {typestr})"


class OptionalVarlinkType(VarlinkType):
    """A varlink type that allows an optional null/None value."""

    def __init__(self, vtype: VarlinkType):
        if isinstance(vtype, OptionalVarlinkType):
            raise RuntimeError("cannot nest OptionalVarlinkTypes")
        self._vtype = vtype
        self.as_type = vtype.as_type | None
        self.as_varlink = "?" + vtype.as_varlink
        self.typedefs = vtype.typedefs
        self.contains_fds = vtype.contains_fds

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
        if obj is None:
            return None
        return self._vtype.tojson(obj, oobstate)

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if obj is None:
            return None
        return self._vtype.fromjson(obj, oobstate)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._vtype!r})"

    def optional(self) -> typing.Self:
        return self


class ListVarlinkType(VarlinkType):
    """A varlink type representing a homogeneous array/list value."""

    def __init__(self, elttype: VarlinkType):
        self._elttype = elttype
        # mypy cannot runtime-constructed type hints.
        self.as_type = list[elttype.as_type]  # type: ignore[name-defined]
        self.as_varlink = "[]" + elttype.as_varlink
        self.typedefs = elttype.typedefs
        self.contains_fds = elttype.contains_fds

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> list[JSONValue]:
        if not isinstance(obj, list):
            raise ConversionError.expected("list", obj)
        result: list[JSONValue] = []
        for elt in obj:
            with ConversionError.context(len(result)):
                result.append(self._elttype.tojson(elt, oobstate))
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not isinstance(obj, list):
            raise ConversionError.expected("list", obj)
        result: list[typing.Any] = []
        for elt in obj:
            with ConversionError.context(len(result)):
                result.append(self._elttype.fromjson(elt, oobstate))
        return result

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._elttype!r})"


class DictVarlinkType(VarlinkType):
    """A varlink type representing a map/dict with string keys and homogeneous
    value types.
    """

    def __init__(self, elttype: VarlinkType):
        self._elttype = elttype
        # mypy cannot runtime-constructed type hints.
        self.as_type = dict[str, elttype.as_type]  # type: ignore[name-defined]
        self.as_varlink = "[string]" + elttype.as_varlink
        self.typedefs = elttype.typedefs
        self.contains_fds = elttype.contains_fds

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONObject:
        if not isinstance(obj, dict):
            raise ConversionError.expected("dict", obj)
        result = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ConversionError.expected("str as dict key", key)
            with ConversionError.context(key):
                result[key] = self._elttype.tojson(value, oobstate)
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not isinstance(obj, dict):
            raise ConversionError.expected("map", obj)
        result = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ConversionError.expected("string as map key", key)
            with ConversionError.context(key):
                result[key] = self._elttype.fromjson(value, oobstate)
        return result

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._elttype!r})"


class SetVarlinkType(VarlinkType):
    """A varlink type that represents a set of strings as a map on the varlink
    side.
    """

    as_type = set[str]
    as_varlink = "[string]()"
    contains_fds = False

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONObject:
        if not isinstance(obj, set):
            raise ConversionError.expected("set", obj)
        result: dict[str, JSONValue] = {}
        for elem in obj:
            if not isinstance(elem, str):
                raise ConversionError.expected("str as set element", elem)
            # Assign an empty struct as value.
            result[elem] = {}
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not isinstance(obj, dict):
            raise ConversionError.expected("map", obj)
        result = set()
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ConversionError.expected("string as map key", key)
            if value != {}:
                with ConversionError.context(key):
                    raise ConversionError.expected(
                        "empty struct as map value", value
                    )
            result.add(key)
        return result

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


class ObjectVarlinkType(VarlinkType):
    """A varlink type representing a map/dict with string keys and
    inhomogeneous per-key types. On the Python side, this resembles a
    typing.TypedDict.
    """

    def __init__(
        self,
        typemap: dict[str, VarlinkType],
    ):
        self._typemap = typemap
        # mypy cannot runtime-constructed type hints.
        self.as_type = typing.TypedDict(  # type: ignore[misc]
            "ObjectVarlinkTypedDict",
            {name: tobj.as_type for name, tobj in typemap.items()},
        )
        self.as_varlink = "(%s)" % ", ".join(
            f"{name}: {tobj.as_varlink}" for name, tobj in typemap.items()
        )
        self.typedefs = {}
        _merge_typedefs(
            self.typedefs, *(tobj.typedefs for tobj in typemap.values())
        )
        self.contains_fds = any(tobj.contains_fds for tobj in typemap.values())

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONObject:
        if not isinstance(obj, dict):
            raise ConversionError.expected("dict", obj)
        result = {}
        for key, vtype in self._typemap.items():
            try:
                value = obj[key]
            except KeyError as err:
                if isinstance(vtype, OptionalVarlinkType):
                    continue
                raise ConversionError(
                    f"missing required key {key} in given dict"
                ) from err
            with ConversionError.context(key):
                result[key] = vtype.tojson(value, oobstate)
        for key in obj:
            if key not in self._typemap:
                raise ConversionError(f"no type for key {key}")
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not isinstance(obj, dict):
            raise ConversionError.expected("map", obj)
        result = {}
        for key, vtype in self._typemap.items():
            try:
                value = obj[key]
            except KeyError as err:
                if isinstance(vtype, OptionalVarlinkType):
                    continue
                raise ConversionError(
                    f"missing required key {key} in given dict"
                ) from err
            with ConversionError.context(key):
                result[key] = vtype.fromjson(value, oobstate)
        for key in obj:
            if key not in self._typemap:
                raise ConversionError(f"no type for key {key}")
        return result

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._typemap!r})"


class DataclassVarlinkType(VarlinkType):
    """A varlink type representing an object as a dataclasses.dataclass.
    Only fields with init=True will be considered."""

    def __init__(self, dataclasstype: type):
        assert dataclasses.is_dataclass(dataclasstype)
        self.as_type = dataclasstype
        self.as_varlink = dataclasstype.__name__
        self._typemap = {
            field.name: VarlinkType.from_type_annotation(field.type)
            for field in dataclasses.fields(dataclasstype)
            if field.init
        }
        self.typedefs = {}
        _merge_typedefs(
            self.typedefs,
            *(vtype.typedefs for vtype in self._typemap.values()),
            {
                dataclasstype.__name__: "(%s)"
                % ", ".join(
                    f"{name}: {vtype.as_varlink}"
                    for name, vtype in self._typemap.items()
                ),
            },
        )
        self.contains_fds = any(
            tobj.contains_fds for tobj in self._typemap.values()
        )

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONObject:
        if not dataclasses.is_dataclass(obj):
            raise ConversionError.expected("a dataclass", obj)
        result = {}
        for name, vtype in self._typemap.items():
            with ConversionError.context(name):
                result[name] = vtype.tojson(getattr(obj, name), oobstate)
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not isinstance(obj, dict):
            raise ConversionError.expected("map", obj)
        excess_keys = set(obj.keys())
        excess_keys.difference_update(self._typemap.keys())
        if excess_keys:
            raise ConversionError(
                "unexpected dataclass fields " + ", ".join(excess_keys)
            )
        fields = {}
        for name, vtype in self._typemap.items():
            try:
                value = obj[name]
            except KeyError as err:
                if not isinstance(vtype, OptionalVarlinkType):
                    raise ConversionError(
                        f"missing object key {name}"
                    ) from err
                value = None
            with ConversionError.context(name):
                fields[name] = vtype.fromjson(value, oobstate)
        return self.as_type(**fields)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.as_type})"


class EnumVarlinkType(VarlinkType):
    """A varlink type representing an enum as an enum.Enum."""

    contains_fds = False

    def __init__(self, enumtype: type[enum.Enum]) -> None:
        if not issubclass(enumtype, enum.Enum):
            raise TypeError("a subclass of Enum is required")
        self.as_type = enumtype
        self.as_varlink = enumtype.__name__
        self.typedefs = {
            enumtype.__name__: "(%s)" % ", ".join(enumtype.__members__)
        }

    def tojson(self, obj: typing.Any, oobstate: OOBTypeState = None) -> str:
        if not isinstance(obj, self.as_type):
            raise ConversionError.expected(f"enum {self.as_type!r}", obj)
        assert isinstance(obj, enum.Enum)
        return obj.name

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not isinstance(obj, str):
            raise ConversionError.expected("string as enum value", obj)
        try:
            return self.as_type.__members__[obj]
        except KeyError as err:
            raise ConversionError(
                f"enum {self.as_type!r} value {obj!r} not known"
            ) from err

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.as_type!r})"


class LiteralVarlinkType(VarlinkType):
    """A varlink type representation for an enum represented as
    typing.Literal.
    """

    contains_fds = False

    def __init__(self, values: tuple[str, ...]):
        self._values = values
        # mypy cannot handle dynamic literals
        self.as_type = typing.Literal[values]
        self.as_varlink = "(%s)" % ", ".join(values)

    def tojson(self, obj: typing.Any, oobstate: OOBTypeState = None) -> str:
        if not (isinstance(obj, str) and obj in self._values):
            raise ConversionError(f"invalid literal value {obj!r}")
        return obj

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not (isinstance(obj, str) and obj in self._values):
            raise ConversionError(f"invalid literal value {obj!r}")
        return obj

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._values!r})"


class FileDescriptorVarlinkType(VarlinkType):
    """Represent a file descriptor in a varlink message. On the specification
    side, this use is explicitly ruled out. systemd does it anyway and so
    does this class.
    """

    as_type = FileDescriptor
    as_varlink = "int"
    contains_fds = True

    @classmethod
    def _get_oob(cls, oobstate: OOBTypeState) -> typing.Any:
        if oobstate is None:
            raise ConversionError(
                "cannot convert a file descriptor without oobstate"
            )
        try:
            return oobstate[cls]
        except KeyError:
            raise ConversionError(
                "cannot convert a file descriptor without associated oobstate"
            ) from None

    def tojson(self, obj: typing.Any, oobstate: OOBTypeState = None) -> int:
        """Represent a file descriptor. It may be conveyed as int | HasFileno.
        The actual file descriptor is appended to the out-of-band state array
        and the returned json value is the index into said array. A list[int]
        should be conveyed as out-of-band state.
        """
        if not isinstance(obj, int):
            try:
                filenomethod = getattr(obj, "fileno")
            except AttributeError:
                raise ConversionError.expected(
                    "int or fileno()-like", obj
                ) from None
            obj = filenomethod()
        fdlist = self._get_oob(oobstate)
        if not isinstance(fdlist, list):
            raise ConversionError(
                f"out-of-band state for {self.__class__.__name__} should be "
                f"list[int], is {type(fdlist)}"
            )
        result = len(fdlist)
        fdlist.append(obj)
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        """Unrepresent a file descriptor. The int value is understood as an
        index into the out-of-band state object of type FileDescriptorArray. A
        file descriptor is looked up at the index and the position is released
        there.
        """
        if not isinstance(obj, int):
            raise ConversionError.expected("int", obj)
        if oobstate is None:
            raise ConversionError(
                "cannot unrepresent a file descriptor without oobstate"
            )
        fds = self._get_oob(oobstate)
        if fds is None:
            raise ConversionError(
                f"attempt to convert invalid file descriptor index {obj}"
            )
        if not isinstance(fds, FileDescriptorArray):
            raise ConversionError(
                f"out-of-band state for {self.__class__.__name__} should be "
                f"FileDescriptorArray, is {type(fds)}"
            )
        try:
            return fds[obj]
        except IndexError as err:
            raise ConversionError(
                f"attempt to convert invalid file descriptor index {obj}"
            ) from err


class ForeignVarlinkType(VarlinkType):
    """A varlink type skipping representing a foreign object or typing.Any
    and skipping value conversion steps.
    """

    as_type = typing.Any
    contains_fds = False

    def __init__(self, varlink: str = "object"):
        self.as_varlink = varlink

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
        # We have no guarantuee that the object actually is a JSONValue and
        # hope that the user is doing things correctly.
        return typing.cast(JSONValue, obj)

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        return obj
