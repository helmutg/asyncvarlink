# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-3

"""Model a varlink interface method and its type."""

import dataclasses
import functools
import inspect
import typing

from .conversion import ObjectVarlinkType, VarlinkType


_P = typing.ParamSpec("_P")
_R = typing.TypeVar("_R")


@dataclasses.dataclass
class VarlinkMethodSignature:
    """Annotate methods with such a signature object to indicate that they are
    meant to be called via varlink. The signature contains prepared information
    about the parameter conversion and how to call it.
    """

    asynchronous: bool
    """Indicates whether the method is async or not."""

    more: bool
    """Indicates whether the method returns and iterable or not."""

    parameter_types: dict[str, VarlinkType]
    """A map of types for the parameters."""

    return_type: ObjectVarlinkType
    """A type for the return parameters."""


@typing.overload
def varlinkmethod(
    function: typing.Callable[_P, _R], *, return_parameter: str | None = None
) -> typing.Callable[_P, _R]: ...


@typing.overload
def varlinkmethod(
    *, return_parameter: str | None = None
) -> typing.Callable[[typing.Callable[_P, _R]], typing.Callable[_P, _R]]: ...


# Whilst the Python documentation says the implementation should be untyped,
# mypy insists on having a type to type check the body of the function.
# https://github.com/python/mypy/issues/3360
def varlinkmethod(
    function: typing.Callable[_P, _R] | None = None,
    *,
    return_parameter: str | None = None,
) -> (
    typing.Callable[[typing.Callable[_P, _R]], typing.Callable[_P, typing.Any]]
    | typing.Callable[_P, typing.Any]
):
    """Decorator for flagging fully type annotated methods as callable from
    varlink. The function may be a generator, in which case it should be called
    with the "more" field set on the varlink side. The function may be a
    coroutine. The function (or generator, async or not) must return a dict
    (more precisely a typing.TypedDict) unless return_parameter is set, in
    which case the result is (or the results are) wrapped in a dict with the
    return_parameter as key.
    """

    def wrap(function: typing.Callable[_P, _R]) -> typing.Callable[_P, _R]:
        asynchronous = inspect.iscoroutinefunction(function)
        signature = inspect.signature(function)
        param_iterator = iter(signature.parameters.items())
        if next(param_iterator)[0] != "self":
            raise RuntimeError(
                "first argument of a method should be named self"
            )
        return_type = signature.return_annotation
        more = False
        ret_origin = typing.get_origin(return_type)
        if ret_origin is not None and issubclass(
            ret_origin,
            typing.AsyncIterable if asynchronous else typing.Iterable,
        ):
            return_type = typing.get_args(return_type)[0]
            more = True
        return_vtype = VarlinkType.from_type_annotation(return_type)
        if return_parameter is not None:
            return_vtype = ObjectVarlinkType(
                {return_parameter: return_vtype}, {}
            )
        elif not isinstance(return_vtype, ObjectVarlinkType):
            raise TypeError("a varlinkmethod must return a mapping")
        vlsig = VarlinkMethodSignature(
            asynchronous,
            more,
            {
                name: VarlinkType.from_type_annotation(tobj.annotation)
                for name, tobj in param_iterator
            },
            return_vtype,
        )
        wrapped: typing.Callable[_P, typing.Any]
        if return_parameter is None:
            wrapped = function
        elif more and asynchronous:
            asynciterfunction = typing.cast(
                typing.Callable[_P, typing.AsyncIterable[_R]], function
            )

            @functools.wraps(function)
            async def wrapped(
                *args: _P.args, **kwargs: _P.kwargs
            ) -> typing.AsyncGenerator[dict[str, _R], None]:
                async for result in asynciterfunction(*args, **kwargs):
                    yield {return_parameter: result}

        elif more:
            iterfunction = typing.cast(
                typing.Callable[_P, typing.Iterable[_R]], function
            )

            @functools.wraps(function)
            def wrapped(
                *args: _P.args, **kwargs: _P.kwargs
            ) -> typing.Generator[dict[str, _R], None, None]:
                for result in iterfunction(*args, **kwargs):
                    yield {return_parameter: result}

        elif asynchronous:
            asyncfunction = typing.cast(
                typing.Callable[_P, typing.Awaitable[_R]], function
            )

            @functools.wraps(function)
            async def wrapped(
                *args: _P.args, **kwargs: _P.kwargs
            ) -> dict[str, _R]:
                return {return_parameter: await asyncfunction(*args, **kwargs)}

        else:

            @functools.wraps(function)
            def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> dict[str, _R]:
                return {return_parameter: function(*args, **kwargs)}

        # Employ setattr instead of directly setting it as that makes mypy and
        # pylint a lot happier.
        setattr(wrapped, "_varlink_signature", vlsig)
        return wrapped

    if function is None:
        # The decorator is called with parens.
        return wrap
    return wrap(function)


def varlinksignature(
    method: typing.Callable[_P, _R]
) -> VarlinkMethodSignature | None:
    """Return the signature object constructed by the varlinkmethod decorator
    if the given method has been decorated.
    """
    # Wrap the access in getattr as that makes mypy and pylint a lot happier.
    return getattr(method, "_varlink_signature", None)


class VarlinkInterface:
    """A base class for varlink interface implementations.

    Deriving classes should set the name class attribute to the interface name
    and mark the interface methods with the varlinkmethod decorator.
    """

    name: str
    """The name of the varlink interface in dotted reverse domain notation."""

    def render_interface_description(self) -> str:
        """Render a varlink interface description from this interface.
        Refer to https://varlink.org/Interface-Definition.
        """
        lines = [f"interface {self.name}", ""]
        for name in dir(self):
            obj = getattr(self, name)
            if (signature := varlinksignature(obj)) is None:
                continue
            param_desc = ", ".join(
                f"{name}: {vtype.as_varlink}"
                for name, vtype in signature.parameter_types.items()
            )
            lines.append(
                f"method {name}({param_desc}) -> "
                f"{signature.return_type.as_varlink}"
            )
        lines.append("")
        return "\n".join(lines)
