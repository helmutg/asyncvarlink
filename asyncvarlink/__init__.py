# Copyright 2024 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: GPL-2+

"""A varlink implementation in pure Python with a few key design choices:

* asyncio: There is no synchronous support.
* file descriptor passing: Even though the varlink faq says that passing file
  descriptors is out of scope, systemd does this and it and this library
  supports such use.
* automatic introspection via type annotations: Rather than having to write a
  .varlink description file supporting introspection. This is being computed
  from Python type annotations.
"""

from .clientprotocol import *
from .conversion import *
from .error import *
from .interface import *
from .message import *
from .protocol import *
from .serverprotocol import *
from .types import *
from .util import *
