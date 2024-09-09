# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""Helper for converting between Python objects and JSONValues."""

import contextlib
import enum
import types
import typing

from .types import FileDescriptor, JSONValue


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
        if origin is None:
            if isinstance(tobj, type):
                if issubclass(tobj, bool):
                    return SimpleVarlinkType("bool", bool)
                if issubclass(tobj, int):
                    if issubclass(tobj, FileDescriptor):
                        return FileDescriptorVarlinkType()
                    return SimpleVarlinkType("int", int)
                if issubclass(tobj, float):
                    return SimpleVarlinkType("float", float, int)
                if issubclass(tobj, str):
                    return SimpleVarlinkType("string", str)
                if issubclass(tobj, enum.Enum):
                    return EnumVarlinkType(tobj)
            if typing.is_typeddict(tobj):
                return ObjectVarlinkType(
                    {
                        name: cls.from_type_annotation(
                            tobj.__annotations__[name]
                        )
                        for name in tobj.__required_keys__
                    },
                    {
                        name: cls.from_type_annotation(
                            tobj.__annotations__[name]
                        )
                        for name in tobj.__optional_keys__
                    },
                )
        elif origin is typing.Union or origin is types.UnionType:
            if any(arg is types.NoneType for arg in typing.get_args(tobj)):
                remaining = [
                    alt
                    for alt in typing.get_args(tobj)
                    if alt is not types.NoneType
                ]
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
            args = typing.get_args(tobj)
            if len(args) == 1:
                return ListVarlinkType(cls.from_type_annotation(args[0]))
        elif origin is dict:
            args = typing.get_args(tobj)
            if len(args) == 2 and args[0] is str:
                return DictVarlinkType(cls.from_type_annotation(args[1]))
        return ForeignVarlinkType()


class SimpleVarlinkType(VarlinkType):
    """A varlink type representing a base type such as int or str."""

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


class ListVarlinkType(VarlinkType):
    """A varlink type representing a homogeneous array/list value."""

    def __init__(self, elttype: VarlinkType):
        self._elttype = elttype
        # mypy cannot runtime-constructed type hints.
        self.as_type = list[elttype.as_type]  # type: ignore
        self.as_varlink = "[]" + elttype.as_varlink

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
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
        self.as_type = dict[str, elttype.as_type]  # type: ignore
        self.as_varlink = "[string]" + elttype.as_varlink

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
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


class ObjectVarlinkType(VarlinkType):
    """A varlink type representing a map/dict with string keys and
    inhomogeneous per-key types. On the Python side, this resembles a
    typing.TypedDict.
    """

    def __init__(
        self,
        required: dict[str, VarlinkType],
        optional: dict[str, VarlinkType],
    ):
        if badkeys := set(required).intersection(optional):
            raise RuntimeError(
                f"keys {badkeys} are both optional and required"
            )
        self._required_keys = required
        self._optional_keys = optional
        typemap = required | {
            name: OptionalVarlinkType(tobj) for name, tobj in optional.items()
        }
        # mypy cannot runtime-constructed type hints.
        self.as_type = typing.TypedDict(  # type: ignore
            "ObjectVarlinkTypedDict",
            {name: tobj.as_type for name, tobj in typemap.items()},
        )
        self.as_varlink = "(%s)" % ", ".join(
            f"{name}: {tobj.as_varlink}"
            for name, tobj in sorted(typemap.items())
        )

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
        if not isinstance(obj, dict):
            raise ConversionError.expected("dict", obj)
        result = {}
        for key, vtype in self._required_keys.items():
            try:
                value = obj[key]
            except KeyError as err:
                raise ConversionError(
                    f"missing required key {key} in given dict"
                ) from err
            with ConversionError.context(key):
                result[key] = vtype.tojson(value, oobstate)
        for key, value in obj.items():
            try:
                vtype = self._optional_keys[key]
            except KeyError as err:
                if key not in result:
                    raise ConversionError(f"no type for key {key}") from err
            else:
                if value is not None:
                    with ConversionError.context(key):
                        result[key] = vtype.tojson(value, oobstate)
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        if not isinstance(obj, dict):
            raise ConversionError.expected("map", obj)
        result = {}
        for key, vtype in self._required_keys.items():
            try:
                value = obj[key]
            except KeyError as err:
                raise ConversionError(
                    f"missing required key {key} in given dict"
                ) from err
            with ConversionError.context(key):
                result[key] = vtype.fromjson(value, oobstate)
        for key, value in obj.items():
            try:
                vtype = self._optional_keys[key]
            except KeyError as err:
                if key not in result:
                    raise ConversionError(f"no type for key {key}") from err
            else:
                if value is not None:
                    with ConversionError.context(key):
                        result[key] = vtype.fromjson(value, oobstate)
        return result

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}({self._required_keys!r}, "
            f"{self._optional_keys!r})"
        )


class EnumVarlinkType(VarlinkType):
    """A varlink type represening an enum/enum.Enum."""

    def __init__(self, enumtype: type[enum.Enum]) -> None:
        if not issubclass(enumtype, enum.Enum):
            raise TypeError("a subclass of Enum is required")
        self.as_type = enumtype
        self.as_varlink = "(%s)" % ", ".join(enumtype.__members__)

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
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


class FileDescriptorVarlinkType(VarlinkType):
    """Represent a file descriptor in a varlink message. On the specification
    side, this use is explicitly ruled out. systemd does it anyway and so
    does this class.
    """

    as_type = FileDescriptor
    as_varlink = "int"

    def tojson(
        self, obj: typing.Any, oobstate: OOBTypeState = None
    ) -> JSONValue:
        """Represent a file descriptor. It may be conveyed as int | HasFileno.
        The actual file descriptor is appended to the out-of-band state array
        and the returned json value is the index into said array.
        """
        if not isinstance(obj, int):
            if not hasattr(obj, "fileno"):
                raise ConversionError.expected("int or fileno()-like", obj)
            obj = obj.fileno()
            assert isinstance(obj, int)
        if not isinstance(obj, FileDescriptor):
            obj = FileDescriptor(obj)
        if oobstate is None:
            raise ConversionError(
                "cannot represent a file descriptor without oobstate"
            )
        fdlist = oobstate.setdefault(self.__class__, [])
        assert isinstance(fdlist, list)
        result = len(fdlist)
        fdlist.append(FileDescriptor(obj))
        return result

    def fromjson(
        self, obj: JSONValue, oobstate: OOBTypeState = None
    ) -> typing.Any:
        """Unrepresent a file descriptor. The int value is understood as an
        index into the out-of-band state array of actual file descriptors. A
        file descriptor is looked up at the index and the position is assigned
        None. Hence each index must be unique and any unconverted file
        descriptors can be collected at the end of the conversion.
        """
        if not isinstance(obj, int):
            raise ConversionError.expected("int", obj)
        if oobstate is None:
            raise ConversionError(
                "cannot unrepresent a file descriptor without oobstate"
            )
        try:
            fdlist = oobstate[self.__class__]
        except KeyError:
            raise ConversionError(
                "cannot unrepresent a file descriptor without associated "
                "oobstate"
            ) from None
        assert isinstance(fdlist, list)
        if 0 <= obj < len(fdlist):
            if (fd := fdlist[obj]) is None:
                raise ConversionError(
                    f"attempt to reference file descriptor index {obj} twice"
                )
            fdlist[obj] = None
            return fd
        raise ConversionError(
            f"file descriptor index {obj} out of bound for oobstate"
        )


class ForeignVarlinkType(VarlinkType):
    """A varlink type skipping representing a foreign object or typing.Any
    and skipping value conversion steps.
    """

    as_type = typing.Any
    as_varlink = "object"

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
