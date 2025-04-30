# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""Representation of varlink method calls and replies as Python objects."""

import dataclasses

from .types import JSONObject, JSONValue, validate_interface, validate_name


@dataclasses.dataclass
class VarlinkMethodCall:
    """Represent a parsed and roughly validated varlink method call."""

    method: str
    parameters: JSONObject
    oneway: bool = False
    more: bool = False
    upgrade: bool = False
    extensions: JSONObject = dataclasses.field(default_factory=dict)

    @property
    def method_interface(self) -> str:
        """Return the interface portion of the method string. May raise a
        ValueError.
        """
        interface, dot, _ = self.method.rpartition(".")
        if dot != ".":
            raise ValueError("unqualified method string")
        return interface

    @property
    def method_name(self) -> str:
        """Return the unqualified name portion of the method string."""
        return self.method.rpartition(".")[2]

    def __post_init__(self) -> None:
        validate_interface(self.method_interface)
        validate_name(self.method_name)

    @classmethod
    def fromjson(cls, obj: JSONValue) -> "VarlinkMethodCall":
        """Parse a JSON value into a validated VarlinkMethodCall. May raise
        TypeError and ValueError.
        """
        if not isinstance(obj, dict):
            raise TypeError(
                f"call object must be a map, is {obj.__class__.__name__}"
            )
        extensions = obj.copy()
        try:
            method = extensions.pop("method")
        except KeyError:
            raise ValueError("call object must have a method") from None
        if not isinstance(method, str):
            raise TypeError(
                f"method field of call object must be a str, is "
                f"{method.__class__.__name__}"
            )
        parameters = extensions.pop("parameters", {})
        if not isinstance(parameters, dict):
            raise TypeError(
                f"call parameters must be map, are "
                f"{parameters.__class__.__name__}"
            )
        oneway = extensions.pop("oneway", False)
        if not isinstance(oneway, bool):
            raise TypeError(
                f"call property oneay must be bool, is "
                f"{oneway.__class__.__name__}"
            )
        more = extensions.pop("more", False)
        if not isinstance(more, bool):
            raise TypeError(
                f"call property more must be bool, is "
                f"{oneway.__class__.__name__}"
            )
        upgrade = extensions.pop("upgrade", False)
        if not isinstance(upgrade, bool):
            raise TypeError(
                f"call property upgrade must be bool, is "
                f"{oneway.__class__.__name__}"
            )
        if sum((oneway, more, upgrade)) > 1:
            raise ValueError("cannot combine oneway, more or upgrade")
        return cls(
            method,
            parameters,
            oneway,
            more,
            upgrade,
            extensions,
        )

    def tojson(self) -> JSONObject:
        """Export as a JSONObject suitable for json.dumps."""
        result: JSONObject = {"method": self.method}
        if self.parameters:
            result["parameters"] = self.parameters
        if self.oneway:
            result["oneway"] = True
        if self.more:
            result["more"] = True
        if self.upgrade:
            result["upgrade"] = True
        result.update(self.extensions)
        return result


@dataclasses.dataclass
class VarlinkMethodReply:
    """Represent a parsed and roughly validated varlink method reply."""

    parameters: JSONObject
    continues: bool = False
    error: str | None = None
    extensions: JSONObject = dataclasses.field(default_factory=dict)

    @property
    def error_interface(self) -> str:
        """Return the interface portion of the error string if any. May raise a
        ValueError.
        """
        if self.error is None:
            raise ValueError("not an error")
        interface, dot, _ = self.error.rpartition(".")
        if dot != ".":
            raise ValueError("unqualified error string")
        return interface

    @property
    def error_name(self) -> str:
        """Return the unqualified name portion of the error string if any. May
        raise a ValueError.
        """
        if self.error is None:
            raise ValueError("not an error")
        return self.error.rpartition(".")[2]

    def __post_init__(self) -> None:
        if self.error is not None:
            validate_interface(self.error_interface)
            validate_name(self.error_name)

    @classmethod
    def fromjson(cls, obj: JSONValue) -> "VarlinkMethodReply":
        """Parse a JSON value into a validated VarlinkMethodReply. May raise
        TypeError and ValueError.
        """
        if not isinstance(obj, dict):
            raise TypeError(
                f"call object must be a map, is {obj.__class__.__name__}"
            )
        extensions = obj.copy()
        parameters = extensions.pop("parameters", {})
        if not isinstance(parameters, dict):
            raise TypeError(
                f"reply parameters must be map, are "
                f"{parameters.__class__.__name__}"
            )
        continues = extensions.pop("continues", False)
        if not isinstance(continues, bool):
            raise TypeError(
                f"reply property continues must be bool, is "
                f"{continues.__class__.__name__}"
            )
        error = extensions.pop("error", None)
        if error is not None and not isinstance(error, str):
            raise TypeError(
                f"reply property error must be str, is "
                f"{error.__class__.__name__}"
            )
        return VarlinkMethodReply(parameters, continues, error, extensions)

    def tojson(self) -> JSONObject:
        """Export as a JSONObject suitable for json.dumps."""
        result: JSONObject = {}
        if self.continues:
            result["continues"] = True
        if self.error:
            result["error"] = self.error
        if self.parameters:
            result["parameters"] = self.parameters
        result.update(self.extensions)
        return result
