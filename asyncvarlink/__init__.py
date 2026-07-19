# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

"""A varlink implementation in pure Python with a few key design choices:

* asyncio: There is no synchronous support.
* file descriptor passing: Even though the varlink faq says that passing file
  descriptors is out of scope, systemd does this and it and this library
  supports such use.
* automatic introspection via type annotations: Rather than having to write a
  .varlink description file supporting introspection. This is being computed
  from Python type annotations.
"""

from .clientprotocol import VarlinkClientProtocol, VarlinkInterfaceProxy

# Not re-exporting .conversion
from .error import (
    GenericVarlinkErrorReply,
    TypedVarlinkErrorReply,
    VarlinkErrorReply,
)
from .interface import (
    AnnotatedResult,
    LastResult,
    varlinkmethod,
    varlinksignature,
    VarlinkMethodSignature,
    VarlinkInterface,
)
from .message import VarlinkMethodCall, VarlinkMethodReply
from .protocol import VarlinkBaseProtocol, VarlinkProtocol, VarlinkTransport
from .serverprotocol import (
    VarlinkInterfaceRegistry,
    VarlinkServerProtocol,
    VarlinkInterfaceServerProtocol,
)

# Not re-exporting .serviceinterface
from .types import FileDescriptor, FileDescriptorArray
from .util import (
    connect_unix_varlink,
    create_unix_server,
    get_listen_fd,
    VarlinkUnixServer,
)

__all__ = [
    "AnnotatedResult",
    "FileDescriptor",
    "FileDescriptorArray",
    "GenericVarlinkErrorReply",
    "LastResult",
    "TypedVarlinkErrorReply",
    "VarlinkBaseProtocol",
    "VarlinkClientProtocol",
    "VarlinkErrorReply",
    "VarlinkInterface",
    "VarlinkInterfaceProxy",
    "VarlinkInterfaceRegistry",
    "VarlinkInterfaceServerProtocol",
    "VarlinkMethodCall",
    "VarlinkMethodReply",
    "VarlinkMethodSignature",
    "VarlinkProtocol",
    "VarlinkServerProtocol",
    "VarlinkTransport",
    "VarlinkUnixServer",
    "connect_unix_varlink",
    "create_unix_server",
    "get_listen_fd",
    "varlinkmethod",
    "varlinksignature",
]
