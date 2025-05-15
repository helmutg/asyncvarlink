# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""Model a varlink interface method and its type."""

import dataclasses
import functools
import inspect
import typing

from .conversion import ObjectVarlinkType, VarlinkType
from .types import JSONObject, validate_interface


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

    parameter_type: ObjectVarlinkType
    """A type for the call parameters."""

    return_type: ObjectVarlinkType
    """A type for the return parameters."""


@dataclasses.dataclass
class AnnotatedResult:
    """A wrapper for unconverted varlink reply values to affect protocol-level
    details.
    """

    parameters: dict[str, typing.Any]
    """Parameter mapping that would normally returned without this wrapper."""

    _: dataclasses.KW_ONLY

    continues: bool = False
    """Indicates whether another reply follows this reply. Setting this is only
    valid if the caller enabled the "more" flag. If setting this to True,
    another reply must follow. Otherwise, this must be the last reply.
    """

    extensions: JSONObject = dataclasses.field(default_factory=dict)
    """May be used to supply extension fields on the varlink reply level."""


class LastResult(Exception):
    """An varlink-specific exception for signalling a non-continued reply after
    a sequence of continued replies."""

    def __init__(self, value: typing.Any):
        """Return the given value as the last reply."""
        if isinstance(value, AnnotatedResult) and value.continues:
            raise RuntimeError(
                "An AnnotatedResult raised via LastResult cannot continue."
            )
        self.value = value


_MethodResultType = (
    AnnotatedResult
    | typing.Iterable[AnnotatedResult]
    | typing.AsyncIterable[AnnotatedResult]
)


def _params_to_varlinkobj(
    params: typing.Iterator[tuple[str, inspect.Parameter]],
) -> ObjectVarlinkType:
    return ObjectVarlinkType(
        {
            name: (
                VarlinkType.from_type_annotation(tobj.annotation)
                if tobj.default is tobj.empty
                else VarlinkType.from_type_annotation(
                    tobj.annotation
                ).optional()
            )
            for name, tobj in params
        },
    )


@typing.overload
def varlinkmethod(
    function: typing.Callable[_P, _R],
    *,
    return_parameter: str | None = None,
    delay_generator: bool = True,
) -> typing.Callable[_P, _MethodResultType]: ...


@typing.overload
def varlinkmethod(
    *, return_parameter: str | None = None, delay_generator: bool = True
) -> typing.Callable[
    [typing.Callable[_P, _R]], typing.Callable[_P, _MethodResultType]
]: ...


# Whilst the Python documentation says the implementation should be untyped,
# mypy insists on having a type to type check the body of the function.
# https://github.com/python/mypy/issues/3360
def varlinkmethod(
    function: typing.Callable[_P, _R] | None = None,
    *,
    return_parameter: str | None = None,
    delay_generator: bool = True,
) -> (
    typing.Callable[
        [typing.Callable[_P, _R]],
        typing.Callable[_P, _MethodResultType],
    ]
    | typing.Callable[_P, _MethodResultType]
):
    """Decorator for fully type annotated methods to be callable from varlink.
    The function may be a generator, in which case it should be called
    with the "more" field set on the varlink side.

    The function has multiple options to return or yield values. It may produce
    AnnotatedResult instances. If return_parameter is None, it may produce a
    dict and a bare value to be wrapped in a dict with return_parameter as key
    otherwise.

    If the function is a generator (async or not), AnnotatedResult are
    forwarded immediately. They must correctly indicate whether the generator
    produces another element by setting the continues field appropriately. If
    delay_generator is True, other values will be delayed until the next
    element is produced (and thus the continues field for the previous element
    is known). Otherwise, all values are forwarded immediately assuming that
    generator never finishes via StopIteration or AsyncStopIteration. In the
    latter case, a final value may produced by raising it inside as LastResult
    exception. The function must produce at least one result or raise as
    LastResult exception.
    """

    def wrap(
        function: typing.Callable[_P, _R],
    ) -> typing.Callable[_P, AnnotatedResult]:
        asynchronous = inspect.iscoroutinefunction(function)
        asyncgen = inspect.isasyncgenfunction(function)
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
            typing.AsyncIterator if asyncgen else typing.Iterator,
        ):
            return_type = typing.get_args(return_type)[0]
            more = True
        return_vtype = VarlinkType.from_type_annotation(return_type)
        make_result: typing.Callable[[_R], AnnotatedResult]
        if return_parameter is not None:
            return_vtype = ObjectVarlinkType({return_parameter: return_vtype})

            def make_result(result: _R) -> AnnotatedResult:
                return AnnotatedResult({return_parameter: result})

        elif return_type is None:
            return_vtype = ObjectVarlinkType({})

            def make_result(_result: _R) -> AnnotatedResult:
                return AnnotatedResult({})

        elif not isinstance(return_vtype, ObjectVarlinkType):
            raise TypeError("a varlinkmethod must return a mapping")
        else:
            # mypy cannot figure out that a type also is a Callable.
            make_result = typing.cast(
                typing.Callable[[_R], AnnotatedResult], AnnotatedResult
            )

        vlsig = VarlinkMethodSignature(
            asyncgen or asynchronous,
            more,
            _params_to_varlinkobj(param_iterator),
            return_vtype,
        )
        wrapped: typing.Callable[_P, typing.Any]
        if more and asyncgen:
            asynciterfunction = typing.cast(
                typing.Callable[_P, typing.AsyncIterable[_R]], function
            )

            if delay_generator:

                @functools.wraps(function)
                async def wrapped(
                    *args: _P.args, **kwargs: _P.kwargs
                ) -> typing.AsyncGenerator[AnnotatedResult, None]:
                    pending = None
                    try:
                        async for result in asynciterfunction(*args, **kwargs):
                            if pending is not None:
                                pending.continues = True
                                yield pending
                                pending = None
                            if isinstance(result, AnnotatedResult):
                                yield result
                            else:
                                pending = make_result(result)
                    except LastResult as exc:
                        if pending is not None:
                            pending.continues = True
                            yield pending
                        if isinstance(exc.value, AnnotatedResult):
                            yield exc.value
                        else:
                            yield make_result(exc.value)
                    else:
                        if pending is not None:
                            yield pending

            else:

                @functools.wraps(function)
                async def wrapped(
                    *args: _P.args, **kwargs: _P.kwargs
                ) -> typing.AsyncGenerator[AnnotatedResult, None]:
                    try:
                        async for result in asynciterfunction(*args, **kwargs):
                            if isinstance(result, AnnotatedResult):
                                yield result
                            else:
                                ares = make_result(result)
                                ares.continues = True
                                yield ares
                    except LastResult as exc:
                        if isinstance(exc.value, AnnotatedResult):
                            yield exc.value
                        else:
                            yield make_result(exc.value)

        elif more:
            iterfunction = typing.cast(
                typing.Callable[_P, typing.Iterable[_R]], function
            )

            if delay_generator:

                @functools.wraps(function)
                def wrapped(
                    *args: _P.args, **kwargs: _P.kwargs
                ) -> typing.Generator[AnnotatedResult, None, None]:
                    pending = None
                    try:
                        for result in iterfunction(*args, **kwargs):
                            if pending is not None:
                                pending.continues = True
                                yield pending
                                pending = None
                            if isinstance(result, AnnotatedResult):
                                yield result
                            else:
                                pending = make_result(result)
                    except LastResult as exc:
                        if pending is not None:
                            pending.continues = True
                            yield pending
                        if isinstance(exc.value, AnnotatedResult):
                            yield exc.value
                        else:
                            yield make_result(exc.value)
                    else:
                        if pending is not None:
                            yield pending

            else:

                @functools.wraps(function)
                def wrapped(
                    *args: _P.args, **kwargs: _P.kwargs
                ) -> typing.Generator[AnnotatedResult, None, None]:
                    try:
                        for result in iterfunction(*args, **kwargs):
                            if isinstance(result, AnnotatedResult):
                                yield result
                            else:
                                ares = make_result(result)
                                ares.continues = True
                                yield ares
                    except LastResult as exc:
                        if isinstance(exc.value, AnnotatedResult):
                            yield exc.value
                        else:
                            yield make_result(exc.value)

        elif asynchronous:
            asyncfunction = typing.cast(
                typing.Callable[_P, typing.Awaitable[_R]], function
            )

            @functools.wraps(function)
            async def wrapped(
                *args: _P.args, **kwargs: _P.kwargs
            ) -> AnnotatedResult:
                try:
                    result = await asyncfunction(*args, **kwargs)
                except LastResult as exc:
                    result = exc.value
                if isinstance(result, AnnotatedResult):
                    return result
                return make_result(result)

        else:

            @functools.wraps(function)
            def wrapped(
                *args: _P.args, **kwargs: _P.kwargs
            ) -> AnnotatedResult:
                try:
                    result = function(*args, **kwargs)
                except LastResult as exc:
                    result = exc.value
                if isinstance(result, AnnotatedResult):
                    return result
                return make_result(result)

        # Employ setattr instead of directly setting it as that makes mypy and
        # pylint a lot happier.
        setattr(wrapped, "_varlink_signature", vlsig)
        return wrapped

    if function is None:
        # The decorator is called with parens.
        return wrap
    return wrap(function)


def varlinksignature(
    method: typing.Callable[_P, _R],
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

    def __init_subclass__(
        cls: type["VarlinkInterface"], *, name: str | None = None
    ) -> None:
        try:
            cls.name
        except AttributeError:
            if name is None:
                raise RuntimeError(
                    "VarlinkInterface subclasses must define an interface name"
                ) from None
            cls.name = name
        else:
            if name is not None:
                raise RuntimeError(
                    "cannot specify VarlinkInterface name both via "
                    "inheritance and attribute"
                )
        try:
            validate_interface(cls.name)
        except ValueError as err:
            raise RuntimeError("invalid VarlinkInterface name") from err

    @classmethod
    def render_interface_description(cls) -> str:
        """Render a varlink interface description from this interface.
        Refer to https://varlink.org/Interface-Definition.
        """
        lines = [f"interface {cls.name}", ""]
        for name in dir(cls):
            obj = getattr(cls, name)
            if (signature := varlinksignature(obj)) is None:
                continue
            lines.append(
                f"method {name}{signature.parameter_type.as_varlink} -> "
                f"{signature.return_type.as_varlink}"
            )
        lines.append("")
        return "\n".join(lines)
