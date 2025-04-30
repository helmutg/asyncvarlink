# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""Represent a varlink error values in Python."""

import functools
import typing

from .conversion import ObjectVarlinkType, VarlinkType
from .message import VarlinkMethodReply
from .types import JSONObject, JSONValue


class _VarlinkErrorParameterDescriptor:
    def __init__(self, name: str):
        # Usually, this would be __set_name__, but as we assign with setattr,
        # we handle the name propagation in the ctor.
        self.name = name

    def __get__(self, obj: "TypedVarlinkErrorReply", owner: type) -> JSONValue:
        try:
            # mypy does not know that obj.parameters is a JSONObject.
            return typing.cast(JSONValue, obj.parameters[self.name])
        except KeyError as err:
            raise AttributeError(f"no attribute named {self.name}") from err

    def __set__(self, obj: "TypedVarlinkErrorReply", value: JSONValue) -> None:
        obj.parameters[self.name] = value


class VarlinkErrorReply(Exception):
    """Base class for protocol-level errors communicated via Varlink."""

    name: str
    """The name of the error prefixed with the interface name in reverse-domain
    notation. This may be a class attribute or an instance attribute.
    """

    def paramstojson(self) -> JSONObject:
        """Represent the parameters as a JSONObject."""
        raise NotImplementedError

    def toreply(self) -> VarlinkMethodReply:
        """Represent the error as a VarlinkMethodReply."""
        return VarlinkMethodReply(self.paramstojson(), error=self.name)

    def tojson(self) -> JSONObject:
        """Represent the entire error as a JSONObject."""
        return self.toreply().tojson()


class GenericVarlinkErrorReply(VarlinkErrorReply):
    """An untyped VarlinkErrorReply subclass representing errors for which no
    TypedVarlinkErrorReply subclass exists.
    """

    def __init__(self, error: str, parameters: JSONObject | None = None):
        """In general, a varlink error object consists of a fully qualified
        error name and a parameter object and that's what should be supplied
        here.
        """
        super().__init__()
        self.name = error
        self.parameters = {} if parameters is None else parameters

    def paramstojson(self) -> JSONObject:
        return self.parameters

    def __repr__(self) -> str:
        if not self.parameters:
            return f"{self.__class__.__name__}({self.name!r})"
        return f"{self.__class__.__name__}({self.name!r}, {self.parameters!r})"


@typing.dataclass_transform()
class TypedVarlinkErrorReply(VarlinkErrorReply):
    """Class hierarchy for type-driven protocol-level errors communicated via
    Varlink. The name attribute on this hierarchy is a class attribute and not
    an instance attribute. It may be specified in one of the following ways.
     * Set the name class attribute to a fully qualified name.
     * Pass a fully qualified name as keyword argument name for class
       inheritance.
     * Pass a qualified interface as keyword argument interface for class
       inheritance. In this case the actual name will be constructed from
       the interface and the name of the derived class.
    Each subclass must provide a parameter specification via one of the
    following means.
     * Annotate the attribute parameters as a typing.TypedDict.
     * Create an inner class named Parameters whose attribute annotations
       are used to construct a typing.TypedDict.
    A suitable __init__ method setting the parameters instance attribute will
    be created automatically. For each parameter, a matching descriptor
    attribute will be added. The prefix of these attributes may be customized
    via the paramprefix keyword argument for class inheritance and defaults to
    "p_". If no parameters clash with keywords or methods, it is suggested to
    request an empty paramprefix.
    """

    paramtype: ObjectVarlinkType
    """A VarlinkType instance representing the type of the parameters. It can
    be used for converting a Python representation and a JSON representation
    of the parameters back and forth. It is constructed by the
    __init_subclass__ hook from the annotations of the parameters attribute or
    the Parameters class attribute.
    """

    def __init_subclass__(
        cls: type["TypedVarlinkErrorReply"], **kwargs: typing.Any
    ) -> None:
        set_name = False
        try:
            name = cls.name
        except AttributeError:
            if "name" in kwargs:
                name = kwargs.pop("name")
                set_name = True
            elif "interface" in kwargs:
                name = kwargs.pop("interface") + "." + cls.__name__
                set_name = True
            else:
                raise RuntimeError(
                    "For instantiating a TypedVarlinkErrorReply subclass you "
                    "must set on of the name class attribute, name class "
                    "keyword argument or interface class keyword argument."
                ) from None
        paramprefix = kwargs.pop("paramprefix", "p_")
        super().__init_subclass__(**kwargs)
        if set_name:
            cls.name = name
        try:
            paramtype = cls.__annotations__["parameters"]
        except KeyError:
            try:
                paramcls = getattr(cls, "Parameters")
            except AttributeError:
                raise RuntimeError(
                    "For instantiating a TypedVarlinkErrorReply subclass you "
                    "must provide a type annotation for the parameters "
                    "attribute or add a type annotated inner class named "
                    "Parameters."
                ) from None
            # mypy does not like to construct a TypedDict with non-constant
            # arguments, but that's exactly what we're up to here. It also
            # objects to assigning a type hint to a variable assigned elsewhere
            # as a TypedDict may already be defined as an annotation of the
            # the parameters attribute. Silence both.
            paramtype = typing.TypedDict(  # type: ignore[misc,no-redef]
                f"{cls.__name__}Parameters", paramcls.__annotations__
            )
            cls.__annotations__["parameters"] = paramtype
        else:
            if not typing.is_typeddict(paramtype):
                raise RuntimeError(
                    "The parameters attribute must be annotated as a "
                    "typing.TypedDict."
                )
        vtype = VarlinkType.from_type_annotation(paramtype)
        # As we pass a TypedDict, we actually get an ObjectVarlinkType.
        assert isinstance(vtype, ObjectVarlinkType)
        cls.paramtype = vtype

        # The type annotation given here does not really matter as we override
        # it in the next step.
        @functools.wraps(cls.__init__)
        def subclass_init(
            self: typing.Any,
            _mapping: JSONObject | None = None,
            /,
            **kwargs: JSONValue,
        ) -> None:
            super().__init__()
            if _mapping is None:
                self.parameters = kwargs
            else:
                self.parameters = _mapping | kwargs

        subclass_init.__annotations__ = {
            "_mapping": paramtype,
        } | paramtype.__annotations__
        setattr(cls, "__init__", subclass_init)
        for key, ann in paramtype.__annotations__.items():
            desc = _VarlinkErrorParameterDescriptor(key)
            descname = paramprefix + key
            setattr(cls, descname, desc)
            cls.__annotations__[descname] = ann

    def paramstojson(self) -> JSONObject:
        ret = self.paramtype.tojson(self.parameters)
        assert isinstance(ret, dict)  # self.parameters is a dict.
        return ret

    if typing.TYPE_CHECKING:
        # Pretend that we have an __init__ as mypy does not recognize the
        # generated __init__ method.
        def __init__(
            self, _mapping: JSONObject | None = None, /, **_kwargs: JSONValue
        ): ...

        # Pretend that we have __getattr__ such that mypy and pylint do not
        # moan about accessing our descriptor objects. Unfortunately, this
        # entirely breaks checking attributes.
        def __getattr__(self, attr: str) -> typing.Any: ...
